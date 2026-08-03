"""全历史斐波那契重要位触达。

每个波段端点必须先完成右侧确认，之后才开始观察0.382/0.5/0.618/0.786。
只在价格真实进入该价位附近时标注；不把任意斐波那契线机械铺满全图。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

RATIOS = (0.382, 0.5, 0.618, 0.786)
MIN_SWING_PCT = 0.08
MIN_SWING_BARS = 8
MAX_TRACK_BARS = 120
TOUCH_TOL = 0.006


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _touch_event(df: pd.DataFrame, idx: int, ratio: float, level: float,
                 direction: str, swing_start: int, swing_end: int) -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(level), 4),
        "kind": "fibonacci",
        "label": f"Fib {ratio:g}",
        "direction": direction,
        "star": False,
        "detail": (
            f"{_date(df, idx)} 价格触及已确认波段的{ratio:g}回撤位{level:.2f}；"
            "斐波那契仅作为位置参考，不单独构成方向判断。"
        ),
        "lines": [{
            "t1": _date(df, swing_end), "p1": round(float(level), 4),
            "t2": _date(df, idx), "p2": round(float(level), 4),
            "style": "dashed",
        }],
        "zones": [],
        "polylines": [],
        "active": idx >= len(df) - 80,
        "_score": 48,
        "_grp": f"fib:{swing_start}:{swing_end}:{ratio}",
    }


def find_fibonacci_touches(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    zz = piv_mod.alternating(pivots).reset_index(drop=True)
    if len(zz) < 2:
        return []

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    out: list[dict] = []

    for j in range(1, len(zz)):
        a, b = zz.iloc[j - 1], zz.iloc[j]
        ia, ib = int(a["idx"]), int(b["idx"])
        pa, pb = float(a["price"]), float(b["price"])
        if ib - ia < MIN_SWING_BARS or pa <= 0 or abs(pb / pa - 1.0) < MIN_SWING_PCT:
            continue

        up_swing = pb > pa
        hi, lo = max(pa, pb), min(pa, pb)
        amplitude = hi - lo
        if amplitude <= 0:
            continue
        levels = {
            r: (hi - r * amplitude if up_swing else lo + r * amplitude)
            for r in RATIOS
        }
        # 端点只有到 confirmed_at_idx 才可知；观察必须从确认之后开始。
        start = max(int(b["confirmed_at_idx"]), ib + 1)
        stop = min(len(df), start + MAX_TRACK_BARS)
        hit: set[float] = set()

        for i in range(start, stop):
            # 新高/新低明显越过原端点，原回撤框架结束。
            if up_swing and high[i] > hi * 1.025:
                break
            if not up_swing and low[i] < lo * 0.975:
                break
            for ratio, level in levels.items():
                if ratio in hit:
                    continue
                traded_through = low[i] <= level <= high[i]
                near_close = abs(close[i] / level - 1.0) <= TOUCH_TOL
                if not (traded_through or near_close):
                    continue
                # 上涨腿回撤位属于潜在支撑；下跌腿反弹位属于潜在压力。
                direction = "bull" if up_swing else "bear"
                out.append(_touch_event(df, i, ratio, level, direction, ia, ib))
                hit.add(ratio)
    out.sort(key=lambda e: (e["bar_idx"], e["label"]))
    return out
