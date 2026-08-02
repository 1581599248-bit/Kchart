"""tushare 兼容 HTTP API 数据层（替代原 db.py 的本地 DuckDB 数据源）。

- 端点：POST {TS_URL}，body {"api_name", "token", "params", "fields"}，
  响应 {"code":0,"data":{"fields":[...],"items":[[...]]}}；code!=0 时抛 TsApiError。
- token 从环境变量 TS_TOKEN 读取（禁止写进任何被 git 跟踪的文件）。
- 全局限流：线程锁 + 最小调用间隔 TS_MIN_INTERVAL 秒（默认 0.45s ≈ 133次/分，低于 150次/分上限）；
  并发限制：信号量控制在飞请求数 ≤ TS_MAX_INFLIGHT（默认 2，上游对并发请求数有限制，
  超限会返回「并发请求过多」——此类业务错误按瞬时错误自动退避重试）。
- 同一缓存键（ts_code 等）的拉取有 per-key 锁：并发请求同一标的时只真正拉取一次。
- 磁盘缓存 data/api_cache/（JSON）：
  * stock_basic / trade_cal：12 小时有效期整包重拉；
  * K 线类（daily / adj_factor / index_daily）：按 ts_code 单文件缓存原始数据，
    缓存内最大日期 < 最新交易日时增量拉取（start_date=最大日+1）合并去重写回；
    前复权在 load_daily_qfq 读取时计算（OHLC × adj_factor / max(adj_factor)，
    max 取该股缓存全历史最新因子，与原 db.py 口径一致）。

对外函数签名/返回对齐原 db.py：trade_date 列为 datetime64，按日期升序。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time

import pandas as pd
import requests

from . import config

_CACHE_DIR = config.DATA_DIR / "api_cache"

# 基础信息类缓存有效期（秒）
_META_TTL = 12 * 3600

# 全局限流器（多线程共用：ThreadPoolExecutor 拉全市场日线时也走同一把锁）
_rate_lock = threading.Lock()
_last_call = [0.0]

# 并发限制：上游对在飞请求数有限制（超限返回「并发请求过多」），信号量控制在飞数
_inflight = threading.Semaphore(config.TS_MAX_INFLIGHT)

# 同一缓存键的拉取锁：并发的 bars/indicators/analysis 命中同一标的时只真正拉取一次
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()

# 业务错误中的瞬时错误关键词（可重试）：并发/频率类限制
_TRANSIENT_MARKERS = ("并发", "频繁", "过多", "上限", "超限", "限制", "频率", "limit", "rate")


def _is_transient_msg(msg: str) -> bool:
    m = (msg or "").lower()
    return any(k in m for k in _TRANSIENT_MARKERS)


def _lock_for(name: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(name, threading.Lock())

# list_securities 进程内缓存（10 分钟，对齐原 main._search_df 行为）
_sec_cache = {"df": None, "loaded_at": 0.0}
_SEC_TTL = 600


class TsApiError(RuntimeError):
    """API 业务错误（code!=0）或 token 缺失等不可重试错误。"""


def _token() -> str:
    tok = config.TS_TOKEN
    if not tok:
        raise TsApiError(
            "未设置环境变量 TS_TOKEN：请先设置 tushare 兼容 API 的 token 再访问行情数据"
        )
    return tok


def call_api(api_name: str, params: dict | None = None, fields: str | None = None,
             retries: int = 4) -> pd.DataFrame:
    """调用 API → DataFrame（列序 = 响应 fields）。

    网络错误/5xx 指数退避重试；code!=0 中含并发/频率类关键词的按瞬时错误
    退避重试，其余业务错误（如 token 无效）直接抛 TsApiError 不重试。
    """
    body = {"api_name": api_name, "token": _token(), "params": params or {}}
    if fields:
        body["fields"] = fields
    last_exc = None
    for attempt in range(retries):
        with _rate_lock:
            wait = config.TS_MIN_INTERVAL - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
        try:
            with _inflight:  # 控制在飞请求数，避免触发上游并发限制
                resp = requests.post(
                    config.TS_URL, json=body, timeout=60,
                    headers={"Accept-Encoding": "gzip"},
                )
            if resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            payload = resp.json()
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            time.sleep(min(2 ** attempt, 8))  # 1s, 2s, 4s, 8s 指数退避
            continue
        if payload.get("code") != 0:
            msg = str(payload.get("msg"))
            if _is_transient_msg(msg):
                last_exc = TsApiError(f"{api_name} 返回错误: {msg}")
                time.sleep(min(2 * (attempt + 1), 8))  # 2s, 4s, 6s, 8s 退避后重试
                continue
            raise TsApiError(f"{api_name} 返回错误: {msg}")
        data = payload.get("data") or {}
        return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])
    if isinstance(last_exc, TsApiError):
        raise last_exc
    raise TsApiError(f"{api_name} 网络错误（重试 {retries} 次仍失败）: {last_exc}")


# ---------------- 磁盘缓存 ----------------

def _cache_path(name: str):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{name}.json"


def _read_cache(name: str) -> dict | None:
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(name: str, obj: dict) -> None:
    _cache_path(name).write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _cached_meta(name: str, api_name: str, params: dict, fields: str) -> pd.DataFrame:
    """基础信息类：12 小时整包缓存。per-key 锁防并发重复拉取。"""
    with _lock_for(name):
        c = _read_cache(name)
        if c and time.time() - c.get("fetched_at", 0) < _META_TTL:
            return pd.DataFrame(c["rows"])
        df = call_api(api_name, params=params, fields=fields)
        _write_cache(name, {"fetched_at": time.time(),
                            "rows": df.to_dict(orient="records")})
        return df


def _cached_kline(name: str, api_name: str, key: str, fields: str) -> pd.DataFrame:
    """K 线类：单标的缓存原始数据，按最新交易日判定过期并增量拉取合并。

    返回 DataFrame 含 trade_date（datetime64）且按日期升序。per-key 锁防并发重复拉取。
    """
    with _lock_for(name):
        c = _read_cache(name) or {}
        rows = c.get("rows") or []
        df = pd.DataFrame(rows)
        latest = latest_trade_date()
        max_d = None
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            max_d = df["trade_date"].max().date()
        if max_d is None or max_d < latest:
            # 增量：从缓存最大日+1 拉起；空缓存则全历史一次拉取
            params = dict(key)
            if max_d is not None:
                params["start_date"] = (max_d + dt.timedelta(days=1)).strftime("%Y%m%d")
            new = call_api(api_name, params=params, fields=fields)
            if not new.empty:
                new["trade_date"] = pd.to_datetime(new["trade_date"], format="%Y%m%d")
                df = pd.concat([df, new], ignore_index=True)
                df = df.drop_duplicates(subset=["trade_date"], keep="last")
                df = df.sort_values("trade_date").reset_index(drop=True)
                out = df.copy()
                out["trade_date"] = out["trade_date"].dt.strftime("%Y%m%d")
                _write_cache(name, {"rows": out.to_dict(orient="records")})
            elif df.empty:
                return df
        return df.reset_index(drop=True)


# ---------------- 日历 ----------------

def _calendar_df() -> pd.DataFrame:
    """SSE 交易日历（cal_date: datetime64, is_open: int），12 小时缓存。"""
    df = _cached_meta(
        "trade_cal", "trade_cal",
        params={"exchange": "SSE", "start_date": "19901219",
                "end_date": (dt.date.today() + dt.timedelta(days=31)).strftime("%Y%m%d")},
        fields="cal_date,is_open",
    )
    df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d")
    df["is_open"] = df["is_open"].astype(int)
    return df.sort_values("cal_date").reset_index(drop=True)


def latest_trade_date() -> dt.date:
    """SSE 最近一个已开市交易日（不晚于今天）。"""
    cal = _calendar_df()
    today = pd.Timestamp(dt.date.today())
    open_days = cal[(cal["is_open"] == 1) & (cal["cal_date"] <= today)]["cal_date"]
    if open_days.empty:
        raise TsApiError("trade_cal 无已开市交易日")
    return open_days.max().date()


def trade_calendar(start, end) -> list[dt.date]:
    """[start, end] 内 SSE 开市交易日列表（升序）。"""
    cal = _calendar_df()
    s = pd.Timestamp(_norm_date(start))
    e = pd.Timestamp(_norm_date(end))
    sel = cal[(cal["is_open"] == 1)
              & (cal["cal_date"] >= s) & (cal["cal_date"] <= e)]["cal_date"]
    return [d.date() for d in sel]


# ---------------- 证券清单 ----------------

def _market_of(ts_code: str) -> str:
    """由 ts_code 后缀推市场（SH/SZ/BJ/CSI）。"""
    return ts_code.split(".")[-1] if "." in ts_code else ""


def list_securities() -> pd.DataFrame:
    """全市场个股+指数清单 → DataFrame[ts_code, name, kind, market, list_date]。

    个股取 stock_basic(list_status='L')；指数取 config 配置的 8 个宽基指数。
    进程内缓存 10 分钟（对齐原搜索清单行为）。
    """
    if (_sec_cache["df"] is not None
            and time.time() - _sec_cache["loaded_at"] < _SEC_TTL):
        return _sec_cache["df"]
    df = _cached_meta("stock_basic_L", "stock_basic",
                      params={"list_status": "L"},
                      fields="ts_code,name,list_date")
    eq = pd.DataFrame({
        "ts_code": df["ts_code"], "name": df["name"],
        "kind": "equity", "market": df["ts_code"].map(_market_of),
        "list_date": df.get("list_date"),
    })
    idx = pd.DataFrame({
        "ts_code": config.BROAD_INDEX_CODES,
        "name": [config.BROAD_INDEX_NAMES.get(c, c) for c in config.BROAD_INDEX_CODES],
        "kind": "index",
        "market": [_market_of(c) for c in config.BROAD_INDEX_CODES],
        "list_date": None,
    })
    out = (pd.concat([eq, idx], ignore_index=True)
           .drop_duplicates(subset=["ts_code", "kind"]).reset_index(drop=True))
    _sec_cache["df"] = out
    _sec_cache["loaded_at"] = time.time()
    return out


def get_security_name(ts_code: str) -> str | None:
    """单标的名称（个股优先，其次指数）。"""
    df = list_securities()
    hit = df.loc[df["ts_code"] == ts_code, "name"]
    return hit.iloc[0] if len(hit) else None


def is_index(ts_code: str) -> bool:
    """ts_code 是否为指数（config 8 宽基之一）。"""
    return ts_code in config.BROAD_INDEX_CODES


# ---------------- K 线 ----------------

def _norm_date(d):
    if d is None:
        return None
    if isinstance(d, (dt.date, dt.datetime)):
        return d if isinstance(d, dt.date) and not isinstance(d, dt.datetime) else d.date()
    return dt.datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def _clip(df: pd.DataFrame, col: str, start, end) -> pd.DataFrame:
    """函数内按 [start, end]（'YYYY-MM-DD' 或 None）裁剪并升序。"""
    if df.empty:
        return df
    if start is not None:
        df = df[df[col] >= pd.Timestamp(_norm_date(start))]
    if end is not None:
        df = df[df[col] <= pd.Timestamp(_norm_date(end))]
    return df.sort_values(col).reset_index(drop=True)


def load_index_daily(ts_code: str, start=None, end=None) -> pd.DataFrame:
    """指数日线（无复权）→ DataFrame[trade_date, open, high, low, close, vol, amount]。"""
    df = _cached_kline(
        f"index_daily_{ts_code}", "index_daily", {"ts_code": ts_code},
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )
    cols = ["trade_date", "open", "high", "low", "close", "vol", "amount"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    return _clip(df[cols], "trade_date", start, end)


def load_daily_qfq(ts_code: str, start=None, end=None) -> pd.DataFrame:
    """个股前复权日线 → DataFrame[trade_date, open, high, low, close, vol, amount]，日期升序。

    口径严格同原 db.py：OHLC × adj_factor / max(adj_factor)（max 取该股全历史
    最新因子），vol/amount 不动。
    """
    daily = _cached_kline(
        f"daily_{ts_code}", "daily", {"ts_code": ts_code},
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )
    fac = _cached_kline(
        f"adj_factor_{ts_code}", "adj_factor", {"ts_code": ts_code},
        fields="ts_code,trade_date,adj_factor",
    )
    cols = ["trade_date", "open", "high", "low", "close", "vol", "amount"]
    if daily.empty or fac.empty:
        return pd.DataFrame(columns=cols)
    max_f = fac["adj_factor"].max()  # 全历史最新因子（因子单调不减）
    df = daily.merge(fac[["trade_date", "adj_factor"]], on="trade_date", how="inner")
    ratio = df["adj_factor"] / max_f
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * ratio
    return _clip(df[cols], "trade_date", start, end)


if __name__ == "__main__":
    print("latest_trade_date:", latest_trade_date())
    s = list_securities()
    print("list_securities rows:", len(s), "| equity:", (s.kind == "equity").sum(),
          "| index:", (s.kind == "index").sum())
    d = load_daily_qfq("600519.SH", start="2026-01-01")
    print("load_daily_qfq 600519.SH rows:", len(d), "| last close:", d["close"].iloc[-1])
    i = load_index_daily("000001.SH")
    print("load_index_daily 000001.SH rows:", len(i))
    print("calendar 2026-07 len:", len(trade_calendar("2026-07-01", "2026-07-31")))
