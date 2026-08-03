"""机构级斐波那契位置提示。

仅跟踪右侧确认后的主要波段；价格触达重要回撤位且当根出现支撑/压力反应时，
保留一个中性位置标签。主图不绘制斐波那契横线、区域或折线。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

RATIOS = (0.382, 0.5, 0.618, 0.786)
MIN_SWING_PCT = 0.10
MIN_SWING_ATR = 4.0
MIN_SWING_BARS = 12
MAX_TRACK_BARS = 90
TOUCH_TOL = 0.004
DEDUP_BARS = 15

_RATIO_SCORE = {0.382: 50, 0.5: 54, 0.618: 58, 0.786: 52}


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
        "label": f"Fib{ratio:g}",
        # Fib是位置工具而非独立买卖信号，使用中性标记，避免“看多/看跌”误导。
        "direction": "range",
        "star": False,
        "detail": (
            f"{_date(df, idx)} 价格触及已确认主要波段的{ratio:g}回撤位"
            f"{level:.2f}（{role}）；当根出现价格反应，但仍需结合趋势与结构。"
        ),
        "lines": [],
        "zones": [],
        "polylines": [],
        "active": idx >= len(df) - 80,
        "_score": _RATIO_SCORE.get(ratio, 50),
        "_grp": f"fib:{swing_start}:{swing_end}:{ratio}",
        "_amp": round(float(amplitude), 4),
    }


def find_fibonacci_touches(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    zz = piv_mod.alternating(pivots).reset_index(drop=True)
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

        # 波段端点在 confirmed_at_idx 才可知，任何提示都必须发生在此之后。
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
                # 仅“穿过”价位不够：上涨腿回撤需收回位上，下跌腿反弹需收回位下。
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

    # 重叠波段经常在同一根附近给出重复Fib标签；同一比例仅保留振幅更大的主波段。
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
