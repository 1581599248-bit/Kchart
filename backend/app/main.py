"""FastAPI 入口（ARCHITECTURE.md 第3节 main.py 规范）。

静态托管 frontend/ 于 /；API 前缀 /api。
已实现：/api/meta /api/search /api/bars /api/indicators /api/analysis。

数据源：backend/app/ts_api.py（tushare 兼容 HTTP API + 本地磁盘缓存），
token 从环境变量 TS_TOKEN 读取。60m 周期 API 版暂不支持（一律 400）。

8 宽基指数的 1d K线与推背图分析为本地烘焙静态文件 data/baked_charts.json
（scripts/bake_charts.py 生成提交）：冷启动直出烘焙数据秒回，启动后后台线程
增量追新，追新完成自动切回实时数据。

时间口径说明：
- time 输出 UNIX 秒整数。日线取当日 00:00；
  均按 naive 时间戳直接转 epoch（即当作 UTC 处理，lightweight-charts 约定）。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import threading

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from . import config, indicators, resample, results_db, ts_api
from .ts_api import TsApiError

log = logging.getLogger("ryan.main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="RYAN K线推背图", version=config.MODEL_VERSION)
# K线/推背图响应几百 KB 的 JSON，gzip 后传输量降约 5 倍（线上跨网段提速明显）
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.exception_handler(TsApiError)
async def _ts_api_error_handler(request, exc):
    """API 数据源错误（token 缺失/限流/网络）→ 503 + 中文提示，而非裸 500。"""
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=503)

_TIMEFRAMES = {"60m", "1d", "1w", "1M"}
_INDICATOR_COLS = [
    "MA5", "MA10", "MA20", "MA60", "MA120", "MA250",
    "EMA12", "EMA26", "EMA50",
    "BOLL_MID", "BOLL_UP", "BOLL_DN",
    "DIF", "DEA", "MACD_HIST",
    "K", "D", "J",
    "RSI6", "RSI12", "RSI24",
    "WR6", "WR10",
    "ATR14", "OBV",
    "ADX", "PDI", "MDI",
    "ROC20", "ROC60",
]

# ---------------- 烘焙 K线/分析（data/baked_charts.json，冷启动秒开） ----------------

_BAKED_CHARTS_PATH = config.DATA_DIR / "baked_charts.json"
_baked_state = {"mtime": None, "data": {}}
_baked_lock = threading.Lock()


def _baked_charts() -> dict:
    """懒加载 data/baked_charts.json 进内存（mtime 变化自动重读，线程安全）；不存在返回 {}。"""
    try:
        mtime = _BAKED_CHARTS_PATH.stat().st_mtime
    except OSError:
        with _baked_lock:
            _baked_state["mtime"] = None
            _baked_state["data"] = {}
        return {}
    if _baked_state["mtime"] == mtime:
        return _baked_state["data"]
    with _baked_lock:
        if _baked_state["mtime"] == mtime:
            return _baked_state["data"]
        try:
            payload = json.loads(_BAKED_CHARTS_PATH.read_text(encoding="utf-8"))
            data = payload.get("symbols") or {}
        except (OSError, ValueError):
            log.exception("baked_charts.json 读取失败，按无烘焙处理")
            data = {}
        _baked_state["data"] = data
        _baked_state["mtime"] = mtime
        if data:
            log.info("baked_charts.json 已加载：%d 只标的", len(data))
        return data


def _kline_cache_file(ts_code: str):
    """该标的在 api_cache 的 K线缓存文件路径（指数/个股按 is_index 区分）。"""
    name = f"index_daily_{ts_code}" if ts_api.is_index(ts_code) else f"daily_{ts_code}"
    return ts_api._cache_path(name)


def _baked_bars_df(entry: dict, start, end) -> pd.DataFrame:
    """baked bars（epoch 秒）→ 与 ts_api 返回一致的 df（trade_date datetime64 + OHLCV），按 [start,end] 裁剪。"""
    df = pd.DataFrame(entry.get("bars") or [])
    if df.empty:
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "vol", "amount"])
    df["trade_date"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "vol"})
    df = df[["trade_date", "open", "high", "low", "close", "vol", "amount"]]
    if start is not None:
        df = df[df["trade_date"] >= pd.Timestamp(str(start)[:10])]
    if end is not None:
        df = df[df["trade_date"] <= pd.Timestamp(str(end)[:10])]
    return df.sort_values("trade_date").reset_index(drop=True)


# ---------------- 启动校验 ----------------

def _startup_check() -> None:
    if not config.TS_TOKEN:
        # 未设 token 时行情类接口不可用，但烘焙内容（baked_charts）仍可读，照常启动
        log.warning("=" * 60)
        log.warning("未设置环境变量 TS_TOKEN：行情接口（bars/indicators/analysis/search）")
        log.warning("将不可用；烘焙的指数 K线/推背图不受影响。请设置 TS_TOKEN 后重启。")
        log.warning("=" * 60)
    else:
        try:
            log.info("最新交易日: %s", ts_api.latest_trade_date())
        except Exception:
            log.exception("启动时获取最新交易日失败（首次请求时会重试）")
    results_db.get_con().close()  # 建 results 库（analysis 缓存）
    log.info("model_version: %s | TS_URL: %s", config.MODEL_VERSION, config.TS_URL)
    _warm_baked_symbols()


# ---------------- 后台追新（冷启动用烘焙，追新后切实时） ----------------

def _warm_baked_symbols() -> None:
    """启动 daemon 线程对 baked 标的增量追新；TS_TOKEN 缺失或无烘焙文件时跳过。"""
    if not config.TS_TOKEN:
        log.info("无 TS_TOKEN，跳过 baked 标的追新")
        return
    symbols = list(_baked_charts().keys())
    if not symbols:
        return
    threading.Thread(target=_warm_worker, args=(symbols,), daemon=True,
                     name="baked-warmer").start()
    log.info("baked 追新线程已启动：%d 只标的", len(symbols))


def _warm_worker(symbols: list) -> None:
    """逐标的增量拉 K 线写 api_cache（semaphore 限并发），再预计算 8 指数分析缓存。

    全部 try/except 包裹：失败只记日志，不影响服务。
    """
    from concurrent.futures import ThreadPoolExecutor

    sem = threading.Semaphore(4)

    def _fetch(code):
        with sem:
            try:
                if ts_api.is_index(code):
                    ts_api.load_index_daily(code)
                else:
                    ts_api.load_daily_qfq(code)
            except Exception:
                log.exception("追新失败（跳过）: %s", code)

    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(_fetch, symbols))
        log.info("baked 标的 K线追新完成（%d 只），开始预计算指数分析缓存", len(symbols))
    except Exception:
        log.exception("baked 追新阶段失败")

    # 8 指数分析预计算：缓存键与端点一致 1d@<缓存第一根K线日期>#ANALYSIS_VERSION
    try:
        from . import analysis as analysis_mod
        for code in config.BROAD_INDEX_CODES:
            try:
                df = _load_bars_df(code, "1d", None, None)
                if df.empty or len(df) < 60:
                    continue
                asof = str(pd.to_datetime(df["ts"].iloc[-1]).date())
                # 缓存键与端点缺省窗口一致（KLINE_DISPLAY_START），预热才能命中
                start_d = config.KLINE_DISPLAY_START
                cache_tf = f"1d@{start_d}#{analysis_mod.ANALYSIS_VERSION}"
                if results_db.get_analysis(code, cache_tf, asof) is not None:
                    continue
                result = analysis_mod.analyze(df.rename(columns={"ts": "trade_date"}), "1d")
                result["annotations"] = _annotations_to_epoch(result["annotations"], df)
                result["ts_code"] = code
                result["timeframe"] = "1d"
                result["asof_date"] = asof
                result["name"] = ts_api.get_security_name(code)
                results_db.save_analysis(code, cache_tf, asof, result)
                log.info("指数分析缓存已预计算: %s asof=%s", code, asof)
            except Exception:
                log.exception("指数分析预计算失败（跳过）: %s", code)
        log.info("warmer 全部完成")
    except Exception:
        log.exception("指数分析预计算阶段失败")


# ---------------- 数据装载 ----------------

def _load_bars_df(ts_code: str, timeframe: str, start, end) -> pd.DataFrame:
    """按标的类型与时间粒度装载 bars DataFrame（含 trade_date + OHLCV，输出列名 ts）。

    start 缺省时套用展示下限 KLINE_DISPLAY_START（2020 起，缩短加载）；
    指标 warmup 由调用方显式传更早的 start，不受此限。
    """
    if timeframe == "60m":
        raise HTTPException(400, "API 版暂无 60 分钟线")
    if start is None:
        start = config.KLINE_DISPLAY_START
    if timeframe == "1d":
        # 冷启动无本地 K线缓存时直出烘焙数据；后台追新写入 api_cache 后自动切回实时路径
        entry = _baked_charts().get(ts_code)
        if entry is not None and not _kline_cache_file(ts_code).exists():
            df = _baked_bars_df(entry, start, end)
            return df.rename(columns={"trade_date": "ts"})
    df = (ts_api.load_index_daily(ts_code, start, end) if ts_api.is_index(ts_code)
          else ts_api.load_daily_qfq(ts_code, start, end))
    if timeframe in ("1w", "1M"):
        df = resample.resample_ohlcv(df, "W" if timeframe == "1w" else "M")
    df = df.rename(columns={"trade_date": "ts"})
    return df


def _to_epoch_sec(series: pd.Series) -> pd.Series:
    """naive 日期/时间戳 → UNIX 秒整数（当作 UTC 处理；与底层 ns/us 单位无关）。"""
    s = pd.to_datetime(series)
    return ((s - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).astype("int64")


def _json_safe(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    return v


# ---------------- API ----------------

@app.get("/api/meta")
def api_meta():
    idx_rows = [{"ts_code": c, "name": config.BROAD_INDEX_NAMES.get(c, c),
                 "has_60m": False} for c in config.BROAD_INDEX_CODES]
    try:
        latest = str(ts_api.latest_trade_date())
    except Exception:
        latest = None  # token 缺失或 API 不可达时降级，其余字段照常返回
    return {
        "latest_trade_date": latest,
        "model_version": config.MODEL_VERSION,
        "snapshot": False,  # 保留字段兼容前端；API 版无快照概念
        "index_list": idx_rows,
    }


@app.get("/api/search")
def api_search(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    df = ts_api.list_securities()
    ql = q.strip().lower()
    code = df["ts_code"].str.lower()
    name = df["name"].str.lower()
    mask = code.str.contains(ql, regex=False) | name.str.contains(ql, regex=False)
    hit = df[mask].copy()
    # 排序：代码前缀匹配 > 名称前缀匹配 > 其他包含匹配
    hit["_rank"] = (
        (~code[mask].str.startswith(ql)).astype(int) * 2
        + (~name[mask].str.startswith(ql)).astype(int)
    )
    hit = hit.sort_values(["_rank", "ts_code"]).head(limit)
    return [{"ts_code": r.ts_code, "name": r.name, "kind": r.kind, "market": r.market}
            for r in hit.itertuples()]


@app.get("/api/bars")
def api_bars(
    ts_code: str,
    timeframe: str = "1d",
    start: str | None = None,
    end: str | None = None,
):
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"timeframe 须为 {sorted(_TIMEFRAMES)} 之一")
    df = _load_bars_df(ts_code, timeframe, start, end)
    if df.empty:
        raise HTTPException(404, f"无数据: {ts_code} {timeframe}")
    ts = _to_epoch_sec(df["ts"])
    bars = [
        {
            "time": int(t),
            "o": _json_safe(r.open), "h": _json_safe(r.high),
            "l": _json_safe(r.low), "c": _json_safe(r.close),
            "v": _json_safe(r.vol), "amount": _json_safe(r.amount),
        }
        for t, r in zip(ts, df.itertuples())
    ]
    return {
        "bars": bars,
        "name": ts_api.get_security_name(ts_code),
        "currency_note": "个股OHLC为前复权价(元); 指数无复权; vol单位: 日线=手; amount单位: 日线=千元",
    }


@app.get("/api/indicators")
def api_indicators(
    ts_code: str,
    timeframe: str = "1d",
    start: str | None = None,
    end: str | None = None,
):
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"timeframe 须为 {sorted(_TIMEFRAMES)} 之一")
    # 指标需要 warmup（MA250 需 250 根历史），start 向前扩 3 年，算完再裁剪
    if start is not None:
        warm_start = (dt.datetime.strptime(start[:10], "%Y-%m-%d").date()
                      - dt.timedelta(days=3 * 365))
    else:
        warm_start = None
    df = _load_bars_df(ts_code, timeframe, warm_start, end)
    if df.empty:
        raise HTTPException(404, f"无数据: {ts_code} {timeframe}")
    out = indicators.compute_all(df)
    if start is not None:
        start_d = dt.datetime.strptime(start[:10], "%Y-%m-%d").date()
        out = out[pd.to_datetime(out["ts"]).dt.date >= start_d].reset_index(drop=True)
    ts = _to_epoch_sec(out["ts"])
    resp = {"times": [int(t) for t in ts], "vol": [_json_safe(v) for v in out["vol"]]}
    for col in _INDICATOR_COLS:
        resp[col] = [_json_safe(v) for v in out[col]]
    return resp


# ---------------- 推背图分析 ----------------

# 各周期默认分析深度（用户口径：60m≥6个月、日线≥1年、周线≥3年、月线≥10年）
_ANALYSIS_LOOKBACK = {"60m": 190, "1d": 400, "1w": 3 * 366, "1M": 10 * 366}


def _date_str_to_epoch(s: str) -> int:
    return int((dt.datetime.strptime(s[:10], "%Y-%m-%d")
                - dt.datetime(1970, 1, 1)).total_seconds())


def _annotations_to_epoch(annotations: list, df: pd.DataFrame) -> list:
    """把 analyze() 输出的 bar_idx/日期字符串统一换算为与 /api/bars 一致的 UNIX 秒。"""
    ts_epoch = _to_epoch_sec(df["ts"]).tolist()
    n = len(ts_epoch)

    def conv_bar(i):
        return ts_epoch[min(max(int(i), 0), n - 1)] if n else None

    for a in annotations:
        a["time"] = conv_bar(a.pop("bar_idx", len(ts_epoch) - 1))
        for ln in a.get("lines", []):
            ln["t1"] = _date_str_to_epoch(str(ln["t1"]))
            ln["t2"] = _date_str_to_epoch(str(ln["t2"]))
        for z in a.get("zones", []):
            z["t1"] = _date_str_to_epoch(str(z["t1"]))
            z["t2"] = _date_str_to_epoch(str(z["t2"]))
        for pl in a.get("polylines", []):
            for pt in pl.get("points", []):
                pt["t"] = _date_str_to_epoch(str(pt["t"]))
    return annotations


def _baked_analysis_usable(ts_code: str, entry: dict, start_d) -> bool:
    """baked analysis 是否可直接回：请求窗口覆盖烘焙窗口，且本地无更新的 K线缓存。"""
    baked = entry.get("analysis")
    bars = entry.get("bars") or []
    if not baked or not bars:
        return False
    first_d = dt.datetime.fromtimestamp(bars[0]["time"], dt.timezone.utc).date()  # 烘焙分析窗口起点（time 口径=naive 当 UTC）
    if start_d > first_d:
        return False  # 请求窗口未覆盖烘焙窗口（比烘焙更短的窗口不走烘焙，保持与实时计算口径一致）
    p = _kline_cache_file(ts_code)
    if not p.exists():
        return True   # 冷启动：无追新缓存，烘焙即最新
    # 已有追新缓存：缓存最大日期不新于烘焙 asof 时才仍用烘焙
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows") or []
        max_d = max((r["trade_date"] for r in rows), default=None)
    except (OSError, ValueError):
        return False
    baked_asof = str(baked.get("asof_date") or "").replace("-", "")
    return bool(max_d and baked_asof) and max_d <= baked_asof


@app.get("/api/analysis")
def api_analysis(
    ts_code: str,
    timeframe: str = "1d",
    start: str | None = None,
    refresh: int = 0,
):
    from . import analysis as analysis_mod

    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"timeframe 须为 {sorted(_TIMEFRAMES)} 之一")
    if start is None:
        # 缺省窗口 = 展示下限（前端分析请求与K线并行发出，不再显式传 start）
        start_d = dt.date.fromisoformat(config.KLINE_DISPLAY_START)
    else:
        start_d = dt.datetime.strptime(start[:10], "%Y-%m-%d").date()

    # 烘焙直出（冷启动秒回）：窗口覆盖且本地无更新 K线缓存时
    if timeframe == "1d" and not refresh:
        entry = _baked_charts().get(ts_code)
        if entry and _baked_analysis_usable(ts_code, entry, start_d):
            result = entry["analysis"]
            result.setdefault("name", entry.get("name") or ts_api.get_security_name(ts_code))
            return result

    df = _load_bars_df(ts_code, timeframe, start_d, None)
    if df.empty or len(df) < 60:
        raise HTTPException(404, f"数据不足以分析: {ts_code} {timeframe}")

    asof_date = str(pd.to_datetime(df["ts"].iloc[-1]).date())
    # 缓存键纳入分析窗口起点与算法版本：同一 asof 不同窗口（默认 lookback vs 全历史）
    # 不可混用；分析算法升级后旧缓存自动失效（否则 refresh=0 永远拿到旧逻辑结果）
    cache_tf = f"{timeframe}@{start_d}#{analysis_mod.ANALYSIS_VERSION}"
    if not refresh:
        cached = results_db.get_analysis(ts_code, cache_tf, asof_date)
        if cached is not None:
            return cached

    work = df.rename(columns={"ts": "trade_date"})
    result = analysis_mod.analyze(work, timeframe)
    result["annotations"] = _annotations_to_epoch(result["annotations"], df)
    result["ts_code"] = ts_code
    result["timeframe"] = timeframe
    result["asof_date"] = asof_date
    result["name"] = ts_api.get_security_name(ts_code)
    try:
        results_db.save_analysis(ts_code, cache_tf, asof_date, result)
    except Exception:
        log.exception("analysis 缓存写入失败（不影响返回）")
    return result


# 静态托管 frontend/ 于 /（挂载在 API 路由之后）
# 前端资源一律 no-cache：版本迭代时用户浏览器不得拿到旧 html/css/js
@app.middleware("http")
async def _no_cache_frontend(request, call_next):
    resp = call_next and await call_next(request)
    if request.url.path.endswith((".html", ".css", ".js")) or request.url.path in ("/", ""):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    _startup_check()
    uvicorn.run(app, host=config.HOST, port=config.PORT)
