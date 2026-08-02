"""FastAPI 入口（ARCHITECTURE.md 第3节 main.py 规范）。

静态托管 frontend/ 于 /；API 前缀 /api。
已实现：/api/meta /api/search /api/bars /api/indicators；
其余（/api/analysis /api/top20 /api/backtest /api/precompute）501 占位，后续任务实现。

时间口径说明：
- time 输出 UNIX 秒整数。日线取当日 00:00，60m 用真实 trade_time；
  均按 naive 时间戳直接转 epoch（即当作 UTC 处理，lightweight-charts 约定）。
- db_sha256：权威库 17.9GiB，全量哈希成本高；启动时后台线程计算全量 SHA-256，
  完成前 /api/meta 返回文件指纹（size+mtime 采样哈希），完成后缓存进 results_db
  system_meta 供后续启动直接读取。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import math
import threading
import time as _time

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, indicators, resample, results_db

log = logging.getLogger("ryan.main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="RYAN K线推背图", version=config.MODEL_VERSION)

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

# 进程内状态
_state = {"db_sha256": None, "search_df": None, "search_loaded_at": 0.0}


# ---------------- 启动校验与库指纹 ----------------

def _fingerprint(path: str) -> str:
    """快速文件指纹：size + 头/尾 4MB 采样哈希（全量 SHA-256 的廉价替代）。"""
    import os
    h = hashlib.sha256()
    size = os.path.getsize(path)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(4 * 1024 * 1024))
        if size > 4 * 1024 * 1024:
            f.seek(-4 * 1024 * 1024, 2)
            h.update(f.read(4 * 1024 * 1024))
    return h.hexdigest()


def _full_sha256_worker(path: str) -> None:
    """后台线程：全量 SHA-256（17.9GiB，约需数十秒），完成后写入缓存。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        _state["db_sha256"] = digest
        results_db.set_meta("auth_db_sha256", digest)
        log.info("权威库全量 SHA-256: %s", digest)
    except Exception:
        log.exception("全量 SHA-256 计算失败，沿用采样指纹")


def _init_sha256() -> None:
    cached = results_db.get_meta("auth_db_sha256")
    fp = _fingerprint(config.AUTH_DB_PATH)
    cached_fp = results_db.get_meta("auth_db_fingerprint")
    if cached and cached_fp == fp:
        # 指纹未变 → 直接沿用已缓存的全量哈希
        _state["db_sha256"] = cached
        log.info("权威库 SHA-256（缓存）: %s", cached)
        return
    _state["db_sha256"] = fp  # 先返回采样指纹
    results_db.set_meta("auth_db_fingerprint", fp)
    threading.Thread(target=_full_sha256_worker, args=(config.AUTH_DB_PATH,), daemon=True).start()
    log.info("权威库采样指纹: %s（全量 SHA-256 后台计算中）", fp)


def _startup_check() -> None:
    import os
    if not os.path.exists(config.AUTH_DB_PATH):
        raise RuntimeError(f"权威库不可达: {config.AUTH_DB_PATH}")
    required = [
        "daily_bars_full", "adj_factors_full",
        "research_daily_bars_strict",
        "index_daily_bars", "index_master", "security_master_history", "trading_calendar",
    ]
    if not config.SNAPSHOT:
        # 完整权威库才有 60 分钟线（快照库不含，见 scripts/export_snapshot.py）
        required += ["hourly_bars_qfq", "research_hourly_bars_strict"]
    with db.get_con() as con:
        existing = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
    missing = [t for t in required if t not in existing]
    if missing:
        raise RuntimeError(f"权威库缺少表/视图: {missing}")
    results_db.get_con().close()  # 建 results 库
    _init_sha256()
    log.info("最新交易日: %s | model_version: %s | snapshot: %s",
             db.latest_trade_date(), config.MODEL_VERSION, config.SNAPSHOT)


# ---------------- 数据装载 ----------------

def _load_bars_df(ts_code: str, timeframe: str, start, end) -> pd.DataFrame:
    """按标的类型与时间粒度装载 bars DataFrame（含 trade_time/trade_date + OHLCV）。"""
    is_idx = db.is_index(ts_code)
    if timeframe == "60m":
        if is_idx:
            # 权威库无指数小时线：走新浪公开接口在线源（sina_index.py，只读+本地缓存）
            from . import sina_index
            df = sina_index.fetch_index_60min(ts_code)
            if df.empty:
                raise HTTPException(400, "该指数暂无60分钟线数据")
            return df.rename(columns={"trade_time": "ts"})
        if config.SNAPSHOT:
            raise HTTPException(400, "快照版暂无个股60分钟线（部署数据不含小时线）")
        df = db.load_hourly(ts_code, start, end)
        return df.rename(columns={"trade_time": "ts"})
    df = db.load_index_daily(ts_code, start, end) if is_idx else db.load_daily_qfq(ts_code, start, end)
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
    idx_rows = []
    from . import sina_index
    with db.get_con() as con:
        for r in con.execute(
            "SELECT ts_code, name FROM index_master WHERE ts_code = ANY(?)",
            [config.BROAD_INDEX_CODES],
        ).fetchall():
            idx_rows.append({"ts_code": r[0], "name": r[1],
                             "has_60m": r[0] in sina_index._SINA_SYMBOL
                             and sina_index._SINA_SYMBOL[r[0]] is not None})
    order = {c: i for i, c in enumerate(config.BROAD_INDEX_CODES)}
    idx_rows.sort(key=lambda x: order.get(x["ts_code"], 99))
    return {
        "latest_trade_date": str(db.latest_trade_date()),
        "db_sha256": _state["db_sha256"],
        "model_version": config.MODEL_VERSION,
        "snapshot": config.SNAPSHOT,
        "index_list": idx_rows,
    }


def _search_df() -> pd.DataFrame:
    """搜索清单进程内缓存（10分钟有效期，避免每次全表 UNION）。"""
    if _state["search_df"] is None or _time.time() - _state["search_loaded_at"] > 600:
        _state["search_df"] = db.list_securities()
        _state["search_loaded_at"] = _time.time()
    return _state["search_df"]


@app.get("/api/search")
def api_search(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    df = _search_df()
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
        "name": db.get_security_name(ts_code),
        "currency_note": "个股OHLC为前复权价(元); 指数无复权; vol单位: 日线=手/小时线=股; amount单位: 日线=千元/小时线=元",
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
        start_d = db.latest_trade_date() - dt.timedelta(days=lookback)
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
    result["name"] = db.get_security_name(ts_code)
    try:
        results_db.save_analysis(ts_code, cache_tf, asof_date, result)
    except Exception:
        log.exception("analysis 缓存写入失败（不影响返回）")
    return result


# ---------------- 501 占位（后续任务实现） ----------------


# ---------------- TOP20 打分 ----------------

_GROUP_CN = {"G1": "短期反转", "G2": "换手量能", "G3": "趋势质量", "G4": "波动彩票", "G5": "结构形态"}
_state["precompute_running"] = False
_state["backtest_jobs"] = {}  # run_id -> 'running' | 'done' | 'error:...'


def _latest_scored_date() -> str | None:
    with results_db.get_con() as con:
        row = con.execute("SELECT max(trade_date) FROM scores_daily").fetchone()
    return str(row[0]) if row and row[0] else None


def _precompute_worker() -> None:
    import subprocess, sys
    try:
        subprocess.run([sys.executable, "scripts/precompute_scores.py"],
                       cwd=str(config.BASE_DIR), timeout=3600, check=False)
    except Exception:
        log.exception("预计算打分失败")
    finally:
        _state["precompute_running"] = False


@app.get("/api/top20")
def api_top20(date: str | None = None, refresh: int = 0):
    import json as _json
    scored_date = date or _latest_scored_date()
    if scored_date is None or refresh:
        if not _state["precompute_running"]:
            _state["precompute_running"] = True
            threading.Thread(target=_precompute_worker, daemon=True).start()
        return {"status": "computing", "items": []}
    df = results_db.get_scores(scored_date)
    if df.empty:
        return {"status": "computing", "items": []}
    top = df.head(20)
    codes = top["ts_code"].tolist()
    # 最新涨跌幅（原始价口径 pct_chg，仅展示用）
    pct = {}
    with db.get_con() as con:
        for r in con.execute(
            "SELECT ts_code, pct_chg FROM daily_bars_full "
            "WHERE trade_date = ? AND ts_code = ANY(?)", [scored_date, codes],
        ).fetchall():
            pct[r[0]] = r[1]
    items = []
    for r in top.itertuples():
        groups = _json.loads(r.group_json)
        g_sorted = sorted(((k, v) for k, v in groups.items() if v is not None),
                          key=lambda kv: kv[1])
        weak = _GROUP_CN.get(g_sorted[0][0], "") if g_sorted else ""
        strong = _GROUP_CN.get(g_sorted[-1][0], "") if g_sorted else ""
        items.append({
            "rank": int(r.rank), "ts_code": r.ts_code,
            "name": db.get_security_name(r.ts_code),
            "score": round(float(r.score), 1),
            "group_scores": groups,
            "change_pct": _json_safe(pct.get(r.ts_code)),
            "analysis_brief": f"{strong}面最强、{weak}面最弱" if strong else "",
        })
    return {"status": "ok", "date": scored_date, "items": items}


# ---------------- 回测 ----------------

def _backtest_worker(run_id: str, start: str, end: str | None, top_n: int) -> None:
    try:
        from . import backtest as bt_mod
        bt_mod.run_backtest(start=start, end=end, top_n=top_n,
                            save=True, run_id=run_id, verbose=False)
        _state["backtest_jobs"][run_id] = "done"
    except Exception as e:
        log.exception("回测失败 %s", run_id)
        _state["backtest_jobs"][run_id] = f"error:{e}"


@app.get("/api/backtest")
def api_backtest(start: str = "2016-01-01", end: str | None = None, top_n: int = 10):
    end_s = end or str(db.latest_trade_date())
    run_id = f"bt_{start.replace('-', '')}_{end_s.replace('-', '')}_top{top_n}"

    cached = results_db.get_backtest(run_id)
    if cached is not None:
        nav = [
            {"trade_date": str(r.trade_date)[:10], "nav": r.nav,
             "bench_nav": r.bench_nav, "pool_nav": r.pool_nav}
            for r in cached["nav"].itertuples()
        ]
        return {"status": "done", "run_id": run_id, "params": cached["params"],
                "metrics": cached["metrics"], "nav": nav}

    job = _state["backtest_jobs"].get(run_id)
    if job and job.startswith("error:"):
        raise HTTPException(500, f"回测失败: {job[6:]}")
    if job != "running":
        _state["backtest_jobs"][run_id] = "running"
        threading.Thread(target=_backtest_worker,
                         args=(run_id, start, end_s, top_n), daemon=True).start()
    return {"status": "running", "run_id": run_id,
            "eta_hint": "全量10年回测约4分钟，小样本约1分钟，请轮询"}


@app.post("/api/precompute")
def api_precompute():
    if _state["precompute_running"]:
        return {"status": "running"}
    _state["precompute_running"] = True
    threading.Thread(target=_precompute_worker, daemon=True).start()
    return {"status": "started"}


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
