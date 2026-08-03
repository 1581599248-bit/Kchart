"""大结构斐波那契回撤提示。

只基于15%级别ZigZag主波段计算，并同时要求波段持续时间和ATR振幅足够。
仅保留0.5与0.618两个回撤位置；主图只显示精确比例标签，不绘制横线、区域或折线。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

RATIOS = (0.5, 0.618)
MAJOR_ZZ_PCT = 0.15
MIN_SWING_PCT = 0.15
MIN_SWING_ATR = 6.0
MIN_SWING_BARS = 25
MAX_TRACK_BARS = 120
TOUCH_TOL = 0.004
DEDUP_BARS = 20

_RATIO_SCORE = {0.5: 58, 0.618: 64}


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _touch_event(df: pd.DataFrame, idx: int, ratio: float, level: float,
                 swing_start: int, swing_end: int, up_swing: bool,
                 amplitude: float) -> dict:
    role = "潜在支撑" if up_swing else "潜在压力"
    return {
        "bar_idx": int(idx),
        "price": round(float(level), 4),
        "kind": "fibonacci",
        "label": f"{ratio:g}",
        "direction": "range",
        "star": False,
        "detail": (
            f"{_date(df, idx)} 价格触及大结构已确认波段的{ratio:g}回撤位"
            f"{level:.2f}（{role}）；该位置只作结构参考，不单独构成买卖信号。"
        ),
        "lines": [],
        "zones": [],
        "polylines": [],
        "active": idx >= len(df) - 100,
        "_score": _RATIO_SCORE.get(ratio, 50),
        "_grp": f"fib:{swing_start}:{swing_end}:{ratio}",
        "_amp": round(float(amplitude), 4),
    }


def find_fibonacci_touches(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    del pivots  # 统一使用大结构15% ZigZag，避免小pivot生成伪Fib。
    zz = piv_mod.zigzag(df, min_pct=MAJOR_ZZ_PCT).reset_index(drop=True)
    if len(zz) < 2:
        return []

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = (
        df["ATR14"].to_numpy(dtype=float)
        if "ATR14" in df.columns
        else np.full(len(df), np.nan)
    )
    candidates: list[dict] = []

    for j in range(1, len(zz)):
        a, b = zz.iloc[j - 1], zz.iloc[j]
        ia, ib = int(a["idx"]), int(b["idx"])
        pa, pb = float(a["price"]), float(b["price"])
        span = ib - ia
        pct = abs(pb / pa - 1.0) if pa > 0 else 0.0
        amplitude = abs(pb - pa)
        atr_ref = float(atr[ib]) if 0 <= ib < len(atr) and np.isfinite(atr[ib]) else 0.0

        if span < MIN_SWING_BARS or pa <= 0 or pct < MIN_SWING_PCT:
            continue
        if atr_ref > 0 and amplitude < atr_ref * MIN_SWING_ATR:
            continue

        up_swing = pb > pa
        hi, lo = max(pa, pb), min(pa, pb)
        levels = {
            r: (hi - r * amplitude if up_swing else lo + r * amplitude)
            for r in RATIOS
        }
        start = max(int(b["confirmed_at_idx"]), ib + 1)
        stop = min(len(df), start + MAX_TRACK_BARS)
        hit: set[float] = set()

        for i in range(start, stop):
            if up_swing and high[i] > hi * 1.02:
                break
            if not up_swing and low[i] < lo * 0.98:
                break
            for ratio, level in levels.items():
                if ratio in hit or level <= 0:
                    continue
                traded = low[i] <= level <= high[i]
                near_close = abs(close[i] / level - 1.0) <= TOUCH_TOL
                reaction = (
                    traded and close[i] >= level if up_swing
                    else traded and close[i] <= level
                )
                if not (reaction or near_close):
                    continue
                candidates.append(_touch_event(
                    df, i, ratio, level, ia, ib, up_swing, amplitude
                ))
                hit.add(ratio)

    candidates.sort(key=lambda e: (e["bar_idx"], e["label"], -e["_amp"]))
    out: list[dict] = []
    for event in candidates:
        duplicate = [
            old for old in out
            if old["label"] == event["label"]
            and abs(int(old["bar_idx"]) - int(event["bar_idx"])) <= DEDUP_BARS
        ]
        if not duplicate:
            out.append(event)
            continue
        best = max(duplicate + [event], key=lambda e: float(e.get("_amp", 0.0)))
        if best is event:
            for old in duplicate:
                out.remove(old)
            out.append(event)

    out.sort(key=lambda e: (e["bar_idx"], e["label"]))
    for event in out:
        event.pop("_amp", None)
    return out
