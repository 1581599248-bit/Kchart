"""FastAPI 入口（ARCHITECTURE.md 第3节 main.py 规范）。

静态托管 frontend/ 于 /；API 前缀 /api。
已实现：/api/meta /api/search /api/bars /api/indicators /api/analysis /api/top20。

数据源：backend/app/ts_api.py（tushare 兼容 HTTP API + 本地磁盘缓存），
token 从环境变量 TS_TOKEN 读取。60m 周期 API 版暂不支持（一律 400）。

TOP20：本地烘焙脚本 scripts/bake_top20.py 生成静态文件 data/baked_top20.json，
/api/top20 只读该文件，不在服务端触发任何预计算。

时间口径说明：
- time 输出 UNIX 秒整数。日线取当日 00:00；
  均按 naive 时间戳直接转 epoch（即当作 UTC 处理，lightweight-charts 约定）。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import config, indicators, resample, results_db, ts_api
from .ts_api import TsApiError

log = logging.getLogger("ryan.main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="RYAN K线推背图", version=config.MODEL_VERSION)


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

# TOP20 烘焙产物（scripts/bake_top20.py 生成，提交进 git）
_BAKED_TOP20 = config.DATA_DIR / "baked_top20.json"


# ---------------- 启动校验 ----------------

def _startup_check() -> None:
    if not config.TS_TOKEN:
        # 未设 token 时行情类接口不可用，但 /api/top20 的烘焙数据仍可读，照常启动
        log.warning("=" * 60)
        log.warning("未设置环境变量 TS_TOKEN：行情接口（bars/indicators/analysis/search）")
        log.warning("将不可用；/api/top20 烘焙榜单不受影响。请设置 TS_TOKEN 后重启。")
        log.warning("=" * 60)
    else:
        try:
            log.info("最新交易日: %s", ts_api.latest_trade_date())
        except Exception:
            log.exception("启动时获取最新交易日失败（首次请求时会重试）")
    results_db.get_con().close()  # 建 results 库（analysis 缓存）
    log.info("model_version: %s | TS_URL: %s", config.MODEL_VERSION, config.TS_URL)


# ---------------- 数据装载 ----------------

def _load_bars_df(ts_code: str, timeframe: str, start, end) -> pd.DataFrame:
    """按标的类型与时间粒度装载 bars DataFrame（含 trade_date + OHLCV，输出列名 ts）。"""
    if timeframe == "60m":
        raise HTTPException(400, "API 版暂无 60 分钟线")
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
        lookback = _ANALYSIS_LOOKBACK[timeframe]
        start_d = ts_api.latest_trade_date() - dt.timedelta(days=lookback)
    else:
        start_d = dt.datetime.strptime(start[:10], "%Y-%m-%d").date()

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


# ---------------- TOP20 榜单（本地烘焙静态文件） ----------------

@app.get("/api/top20")
def api_top20(date: str | None = None, refresh: int = 0):
    """读 scripts/bake_top20.py 烘焙的 data/baked_top20.json；refresh 参数忽略。

    文件不存在（或请求日期与榜单日期不符）时返回 computing，前端沿用现有轮询提示，
    服务端不再触发任何预计算——榜单更新靠本地跑烘焙脚本并提交 JSON。
    """
    payload = None
    if _BAKED_TOP20.exists():
        try:
            payload = json.loads(_BAKED_TOP20.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("baked_top20.json 读取失败")
    if payload is None or (date and date != payload.get("date")):
        return {"status": "computing", "items": []}
    return {"status": "ok", "date": payload.get("date"),
            "items": payload.get("items", [])}


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
