"""周线/月线聚合（ARCHITECTURE.md 第3节 resample.py 规范）。

由日线聚合，不使用外部源。周期标签取区间内最后一个真实交易日
（不用日历周五/月末，防止把假期当周错标）。
"""
from __future__ import annotations

import pandas as pd

_RULES = {"W": "W", "M": "M"}  # pandas to_period 频率（Period 只认 'M'，不认 'ME'）


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """日线 → 周线('W')/月线('M')。

    输入 df 需含列 trade_date, open, high, low, close, vol, amount（按日期升序）。
    聚合：open=first, high=max, low=min, close=last, vol/amount=sum；
    输出 trade_date = 区间内最后交易日，按日期升序。
    """
    if rule not in _RULES:
        raise ValueError(f"rule must be one of {sorted(_RULES)}, got {rule!r}")
    if df.empty:
        return df.copy()

    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.sort_values("trade_date").reset_index(drop=True)
    period = out["trade_date"].dt.to_period(_RULES[rule])

    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "vol": "sum", "amount": "sum", "trade_date": "last"}
    cols = [c for c in agg if c in out.columns]
    res = out.groupby(period, sort=True)[cols].agg({c: agg[c] for c in cols})
    return res.reset_index(drop=True)


if __name__ == "__main__":
    from . import db

    d = db.load_daily_qfq("600519.SH", start="2025-01-01")
    for rule in ("W", "M"):
        r = resample_ohlcv(d, rule)
        print(f"600519.SH {rule}: {len(d)} daily -> {len(r)} bars")
        print(r.tail(2).to_string())
    # 一致性校验：周线 close 应等于该周最后交易日日线 close
    w = resample_ohlcv(d, "W")
    m = d.merge(w[["trade_date", "close"]], on="trade_date", suffixes=("_d", "_w"))
    assert (m["close_d"] == m["close_w"]).all(), "周线标签/收盘不一致"
    print("resample 自检通过")
