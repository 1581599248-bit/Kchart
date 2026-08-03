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
from . import chart_cache, config, indicators, ts_api
from . import main as legacy

log = logging.getLogger("ryan.chart_service")
_SH_TZ = ZoneInfo("Asia/Shanghai")
_ANALYSIS_BARS = 800
# 前端实际使用的指标；不再把未展示的 ADX/ROC/ATR 等数组塞进大 JSON。
_BUNDLE_INDICATOR_COLS = (
    "MA5", "MA10", "MA20", "MA60",
    "BOLL_MID", "BOLL_UP", "BOLL_DN",
    "DIF", "DEA", "MACD_HIST",
    "K", "D", "J",
    "RSI6", "RSI12",
    "WR6", "WR10",
)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_refreshing: set[str] = set()
_refresh_guard = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="chart-refresh")


def _lock_for(code: str, timeframe: str) -> threading.Lock:
    key = chart_cache.make_key(code, timeframe)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _expected_trade_date(now: dt.datetime | None = None) -> dt.date | None:
    """按上海日期读取交易日历，避免 Render 的 UTC 日期影响判断。"""
    now = now or dt.datetime.now(_SH_TZ)
    try:
        cal = ts_api._calendar_df()
        today = pd.Timestamp(now.date())
        opened = cal[(cal["is_open"] == 1) & (cal["cal_date"] <= today)]["cal_date"]
        return None if opened.empty else opened.max().date()
    except Exception:
        log.exception("读取最新交易日失败，退回时间TTL判断")
        return None


def _cache_stale(payload: dict) -> bool:
    """判断 bundle 是否需要后台刷新。

    关键修复：盘后不仅看 cached_at，还要检查 data_asof。上游在15:20尚未发布
    当日日线时，旧数据不会再被误标成“今天已更新”，后续访问会继续触发刷新。
    """
    if payload.get("analysis_version") != analysis_mod.ANALYSIS_VERSION:
        return True
    cached_at = payload.get("cached_at")
    if not cached_at:
        return True

    now = dt.datetime.now(_SH_TZ)
    made = dt.datetime.fromtimestamp(int(cached_at), _SH_TZ)
    age = (now - made).total_seconds()
    try:
        data_asof = dt.date.fromisoformat(str(payload.get("data_asof")))
    except (TypeError, ValueError):
        return True

    if now.weekday() >= 5:
        return age > 72 * 3600

    # 16:00后要求数据日期追上交易日历；未追上则持续后台重试。
    if (now.hour, now.minute) >= (16, 0):
        expected = _expected_trade_date(now)
        if expected is not None and data_asof < expected:
            return True
        # 即使数据日期已到今天，也要求本次缓存是在收盘后生成，避免盘中半成品。
        if expected == now.date():
            return not (made.date() == now.date() and (made.hour, made.minute) >= (15, 20))
        return age > 24 * 3600

    return age > 24 * 3600


def _load_live_daily(code: str) -> pd.DataFrame:
    """统一接口始终走可增量追新的 API 缓存，不使用可能滞后的 baked 快照。"""
    if ts_api.is_index(code):
        df = ts_api.load_index_daily(code, start=config.KLINE_DISPLAY_START)
    else:
        df = ts_api.load_daily_qfq(code, start=config.KLINE_DISPLAY_START)
    return df.rename(columns={"trade_date": "ts"})


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
    for col in _BUNDLE_INDICATOR_COLS:
        if col in computed.columns:
            out[col] = [legacy._json_safe(v) for v in computed[col]]
    return out


def build(code: str, timeframe: str = "1d", force: bool = False) -> dict:
    """同步生成完整 bundle；同标的并发请求只计算一次。"""
    code = code.upper().strip()
    if timeframe != "1d":
        raise ValueError("高速统一接口当前仅支持日线")
    lock = _lock_for(code, timeframe)
    with lock:
        cached = chart_cache.get(code, timeframe)
        if not force and cached is not None and not _cache_stale(cached):
            return cached

        df = _load_live_daily(code)
        if df.empty or len(df) < 60:
            raise ValueError(f"数据不足以生成图表: {code} {timeframe}")
        asof = str(pd.to_datetime(df["ts"].iloc[-1]).date())

        # 强制刷新时若上游仍未产生新K线，不重复跑高耗时形态分析。
        if (cached is not None
                and cached.get("data_asof") == asof
                and cached.get("analysis_version") == analysis_mod.ANALYSIS_VERSION
                and cached.get("analysis")):
            expected = _expected_trade_date()
            if expected is not None and dt.date.fromisoformat(asof) >= expected:
                return chart_cache.save(code, timeframe, cached)
            return cached

        computed = indicators.compute_all(df.copy())
        # 历史形态扫描是主要耗时。K线与指标仍返回完整展示区间，
        # 推背图只扫描最近约 800 个交易日，覆盖当前及近三年的有效结构。
        analysis_df = computed.tail(_ANALYSIS_BARS).reset_index(drop=True)
        work = analysis_df.rename(columns={"ts": "trade_date"})
        analysis = analysis_mod.analyze(work, timeframe)
        analysis["annotations"] = legacy._annotations_to_epoch(
            analysis["annotations"], analysis_df
        )
        name = config.BROAD_INDEX_NAMES.get(code) or ts_api.get_security_name(code) or code
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


def refresh_many(codes: list[str], timeframe: str = "1d") -> dict:
    report = {"requested": 0, "updated": 0, "failed": 0}
    for code in dict.fromkeys(codes):
        report["requested"] += 1
        before = chart_cache.get(code, timeframe)
        before_asof = before.get("data_asof") if before else None
        try:
            after = build(code, timeframe, force=True)
            if after.get("data_asof") != before_asof:
                report["updated"] += 1
        except Exception:
            report["failed"] += 1
            log.exception("盘后批量刷新失败（跳过）: %s", code)
    return report
