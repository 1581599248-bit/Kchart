"""统一 chart bundle 服务。

一次数据装载完成 K线、指标和推背图，避免原先三个接口重复读取、重复计算。
已缓存结果采用 stale-while-revalidate：先秒回旧结果，后台刷新到最新交易日。
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

from . import analysis as analysis_mod
from . import chart_cache, indicators, ts_api
from . import main as legacy

log = logging.getLogger("ryan.chart_service")
_SH_TZ = ZoneInfo("Asia/Shanghai")
_ANALYSIS_BARS = 800
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_refreshing: set[str] = set()
_refresh_guard = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="chart-refresh")


def _lock_for(code: str, timeframe: str) -> threading.Lock:
    key = chart_cache.make_key(code, timeframe)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _cache_stale(payload: dict) -> bool:
    """不请求上游 API 的轻量时效判断。

    交易日 15:20 后要求缓存当天盘后生成；盘前允许沿用上一盘后结果。
    周末允许沿用最近 72 小时结果。节假日即使 asof 不变，刷新 cached_at 后也视为最新。
    """
    if payload.get("analysis_version") != analysis_mod.ANALYSIS_VERSION:
        return True
    cached_at = payload.get("cached_at")
    if not cached_at:
        return True
    now = dt.datetime.now(_SH_TZ)
    made = dt.datetime.fromtimestamp(int(cached_at), _SH_TZ)
    age = (now - made).total_seconds()
    if now.weekday() >= 5:
        return age > 72 * 3600
    if (now.hour, now.minute) >= (15, 20):
        return not (made.date() == now.date() and (made.hour, made.minute) >= (15, 20))
    return age > 24 * 3600


def _bars_payload(df: pd.DataFrame) -> list[dict]:
    epochs = legacy._to_epoch_sec(df["ts"])
    return [
        {
            "time": int(t),
            "o": legacy._json_safe(r.open),
            "h": legacy._json_safe(r.high),
            "l": legacy._json_safe(r.low),
            "c": legacy._json_safe(r.close),
            "v": legacy._json_safe(r.vol),
            "amount": legacy._json_safe(r.amount),
        }
        for t, r in zip(epochs, df.itertuples())
    ]


def _indicator_payload(computed: pd.DataFrame) -> dict:
    epochs = legacy._to_epoch_sec(computed["ts"])
    out = {
        "times": [int(x) for x in epochs],
        "vol": [legacy._json_safe(v) for v in computed["vol"]],
    }
    for col in legacy._INDICATOR_COLS:
        if col in computed.columns:
            out[col] = [legacy._json_safe(v) for v in computed[col]]
    return out


def build(code: str, timeframe: str = "1d", force: bool = False) -> dict:
    """同步生成完整 bundle；同标的并发请求只计算一次。"""
    code = code.upper().strip()
    lock = _lock_for(code, timeframe)
    with lock:
        cached = chart_cache.get(code, timeframe)
        if not force and cached is not None and not _cache_stale(cached):
            return cached

        df = legacy._load_bars_df(code, timeframe, None, None)
        if df.empty or len(df) < 60:
            raise ValueError(f"数据不足以生成图表: {code} {timeframe}")

        computed = indicators.compute_all(df.copy())
        # 历史形态扫描是主要耗时。K线与指标仍返回完整展示区间，
        # 推背图只扫描最近约 800 个交易日，覆盖当前及近三年的有效结构。
        analysis_df = computed.tail(_ANALYSIS_BARS).reset_index(drop=True)
        work = analysis_df.rename(columns={"ts": "trade_date"})
        analysis = analysis_mod.analyze(work, timeframe)
        analysis["annotations"] = legacy._annotations_to_epoch(
            analysis["annotations"], analysis_df
        )
        asof = str(pd.to_datetime(df["ts"].iloc[-1]).date())
        name = ts_api.get_security_name(code) or code
        analysis.update({
            "ts_code": code,
            "timeframe": timeframe,
            "asof_date": asof,
            "name": name,
        })
        payload = {
            "ts_code": code,
            "timeframe": timeframe,
            "name": name,
            "bars": _bars_payload(df),
            "indicators": _indicator_payload(computed),
            "analysis": analysis,
            "data_asof": asof,
            "analysis_version": analysis_mod.ANALYSIS_VERSION,
            "currency_note": "个股OHLC为前复权价(元); 指数无复权; vol单位: 日线=手; amount单位: 日线=千元",
            "meta": {"cache_hit": False, "refreshing": False},
        }
        return chart_cache.save(code, timeframe, payload)


def _refresh_job(code: str, timeframe: str) -> None:
    key = chart_cache.make_key(code, timeframe)
    try:
        build(code, timeframe, force=True)
        log.info("chart bundle 已刷新: %s %s", code, timeframe)
    except Exception:
        log.exception("chart bundle 刷新失败: %s %s", code, timeframe)
    finally:
        with _refresh_guard:
            _refreshing.discard(key)


def refresh_async(code: str, timeframe: str = "1d") -> bool:
    key = chart_cache.make_key(code, timeframe)
    with _refresh_guard:
        if key in _refreshing:
            return False
        _refreshing.add(key)
    _executor.submit(_refresh_job, code, timeframe)
    return True


def get(code: str, timeframe: str = "1d", refresh: bool = False) -> dict:
    code = code.upper().strip()
    cached = chart_cache.get(code, timeframe)
    if refresh:
        result = dict(build(code, timeframe, force=True))
        result["meta"] = {"cache_hit": False, "refreshing": False}
        return result

    if cached is None:
        result = dict(build(code, timeframe))
        result["meta"] = {"cache_hit": False, "refreshing": False}
        return result

    stale = _cache_stale(cached)
    if stale:
        refresh_async(code, timeframe)
    result = dict(cached)
    result["meta"] = {"cache_hit": True, "refreshing": stale}
    return result


def refresh_many(codes: list[str], timeframe: str = "1d") -> None:
    for code in dict.fromkeys(codes):
        try:
            build(code, timeframe, force=True)
        except Exception:
            log.exception("盘后批量刷新失败（跳过）: %s", code)
