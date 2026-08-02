"""指数 60 分钟线在线数据源（新浪公开行情接口）。

背景：权威库与 teajoin/gyzcloud 代理均无指数小时线（2026-08-02 三源实测）。
新浪公开接口 CN_MarketDataService.getKLineData?scale=60 提供指数 60 分钟K线，
已与权威库日线收盘价交叉验证一致（000001.SH/399006.SZ/000688.SH 逐值吻合）。

纪律：
- 只读在线获取，结果缓存到 data/cache/（git 忽略），绝不写入权威库；
- 缓存 15 分钟新鲜度，盘中自动刷新；
- 单次 datalen≤1023 根（约 255 个交易日 ≈ 1 年 60 分钟线），满足看板需求。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request

import pandas as pd

from . import config

log = logging.getLogger("ryan.sina_index")

# 看板 8 宽基 → 新浪代码（实测可用；932000.CSI 新浪无对应，返回 None 时前端隐藏 60m）
_SINA_SYMBOL = {
    "000001.SH": "sh000001",
    "399001.SZ": "sz399001",
    "399006.SZ": "sz399006",
    "000688.SH": "sh000688",
    "000300.SH": "sh000300",
    "000905.SH": "sh000905",
    "000852.SH": "sh000852",
    "932000.CSI": None,
}

_CACHE_TTL = 15 * 60  # 秒


def _cache_path(ts_code: str):
    d = config.DATA_DIR / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"index60m_{ts_code.replace('.', '_')}.json"


def fetch_index_60min(ts_code: str, datalen: int = 1023) -> pd.DataFrame:
    """指数 60 分钟K线 → DataFrame[trade_time, open, high, low, close, vol, amount] 升序。

    无对应新浪代码或拉取失败时返回空 DataFrame。
    """
    sym = _SINA_SYMBOL.get(ts_code)
    if sym is None:
        return pd.DataFrame()

    cp = _cache_path(ts_code)
    if cp.exists() and time.time() - cp.stat().st_mtime < _CACHE_TTL:
        try:
            return pd.read_json(cp, orient="records", convert_dates=["trade_time"])
        except Exception:
            pass  # 缓存损坏则重新拉取

    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/x/"
           "CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=60&ma=no&datalen={datalen}")
    try:
        raw = urllib.request.urlopen(url, timeout=20).read().decode("utf-8", "ignore")
        inner = raw[raw.find("(") + 1: raw.rfind(")")]
        rows = json.loads(inner)
    except Exception:
        log.exception("新浪指数60分钟拉取失败 %s", ts_code)
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame({
        "trade_time": pd.to_datetime([r["day"] for r in rows]),
        "open": [float(r["open"]) for r in rows],
        "high": [float(r["high"]) for r in rows],
        "low": [float(r["low"]) for r in rows],
        "close": [float(r["close"]) for r in rows],
        "vol": [float(r.get("volume") or 0) for r in rows],
        "amount": [float(r.get("amount") or 0) for r in rows],
    }).sort_values("trade_time").reset_index(drop=True)

    try:
        df.to_json(cp, orient="records", date_format="iso", force_ascii=False)
    except Exception:
        log.exception("指数60分钟缓存写入失败（不影响返回）")
    return df
