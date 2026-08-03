"""v7补充结构：严格扩散三角。

扩散三角必须由至少三个递增高点和三个递减低点组成，区间持续扩大；
主图只在价格有效、放量突破边界后显示“扩散上破/扩散下破”，不显示模糊的构筑标签。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

MIN_SPAN = 45
MIN_GAP = 6
MIN_EXPANSION = 1.35
MIN_STEP_PCT = 0.004
BREAK_PCT = 0.01
BREAK_ATR = 0.45
VOL_MULT = 1.15
COOLDOWN = 80


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _line(i: int, a: dict, b: dict) -> float:
    i1, i2 = int(a["idx"]), int(b["idx"])
    p1, p2 = float(a["price"]), float(b["price"])
    if i2 == i1:
        return p2
    return float(p1 + (p2 - p1) * (i - i1) / (i2 - i1))


def _vol_ratio(df: pd.DataFrame, idx: int) -> float:
    if "vol" not in df.columns or idx < 20:
        return 1.0
    vol = df["vol"].to_numpy(dtype=float)
    base = float(np.nanmean(vol[idx - 20:idx]))
    return float(vol[idx] / base) if base > 0 else 1.0


def _trace(df: pd.DataFrame, pts: list[dict], highs: list[dict], lows: list[dict], end: int) -> list[dict]:
    return [
        {
            "points": [
                {"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
                for p in pts
            ],
            "style": "solid",
        },
        {
            "points": [
                {"t": _date(df, int(highs[0]["idx"])), "p": round(float(highs[0]["price"]), 4)},
                {"t": _date(df, end), "p": round(_line(end, highs[0], highs[-1]), 4)},
            ],
            "style": "dashed",
        },
        {
            "points": [
                {"t": _date(df, int(lows[0]["idx"])), "p": round(float(lows[0]["price"]), 4)},
                {"t": _date(df, end), "p": round(_line(end, lows[0], lows[-1]), 4)},
            ],
            "style": "dashed",
        },
    ]


def find_broadening_breaks(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    confirmed = piv_mod.pivots_asof(pivots, len(df) - 1)
    ap = piv_mod.alternating(confirmed).to_dict("records")
    if len(ap) < 6:
        return []

    close = df["close"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float) if "ATR14" in df.columns else np.full(len(df), np.nan)
    out: list[dict] = []
    last_confirm = -10_000

    for end in range(5, len(ap)):
        pts = ap[end - 5:end + 1]
        kinds = "".join(str(p["kind"]) for p in pts)
        if kinds not in ("HLHLHL", "LHLHLH"):
            continue
        gaps = [int(pts[i + 1]["idx"]) - int(pts[i]["idx"]) for i in range(5)]
        span = int(pts[-1]["idx"]) - int(pts[0]["idx"])
        if min(gaps) < MIN_GAP or span < MIN_SPAN:
            continue

        highs = [p for p in pts if p["kind"] == "H"]
        lows = [p for p in pts if p["kind"] == "L"]
        hp = [float(p["price"]) for p in highs]
        lp = [float(p["price"]) for p in lows]
        if len(highs) != 3 or len(lows) != 3:
            continue
        if not all(hp[i + 1] >= hp[i] * (1 + MIN_STEP_PCT) for i in range(2)):
            continue
        if not all(lp[i + 1] <= lp[i] * (1 - MIN_STEP_PCT) for i in range(2)):
            continue

        first_width = hp[0] - lp[0]
        last_width = hp[-1] - lp[-1]
        if first_width <= 0 or last_width / first_width < MIN_EXPANSION:
            continue
        upper_slope = (hp[-1] - hp[0]) / max(1, int(highs[-1]["idx"]) - int(highs[0]["idx"]))
        lower_slope = (lp[-1] - lp[0]) / max(1, int(lows[-1]["idx"]) - int(lows[0]["idx"]))
        if upper_slope <= 0 or lower_slope >= 0:
            continue

        start = max(int(pts[-1]["confirmed_at_idx"]), int(pts[-1]["idx"]) + 1)
        confirm = None
        direction = None
        boundary = None
        for i in range(start, len(df)):
            upper = _line(i, highs[0], highs[-1])
            lower = _line(i, lows[0], lows[-1])
            atr_buffer = float(atr[i]) * BREAK_ATR if np.isfinite(atr[i]) else 0.0
            up_buffer = max(upper * BREAK_PCT, atr_buffer)
            dn_buffer = max(lower * BREAK_PCT, atr_buffer)
            if close[i] >= upper + up_buffer and _vol_ratio(df, i) >= VOL_MULT:
                confirm, direction, boundary = i, "bull", upper
                break
            if close[i] <= lower - dn_buffer and _vol_ratio(df, i) >= VOL_MULT:
                confirm, direction, boundary = i, "bear", lower
                break
        if confirm is None or confirm - last_confirm < COOLDOWN:
            continue
        last_confirm = confirm
        name = "扩散上破" if direction == "bull" else "扩散下破"
        note = (
            f"六点扩散结构：三个高点逐级抬高、三个低点逐级降低，"
            f"末端宽度为初始宽度的{last_width / first_width:.2f}倍；"
            f"{_date(df, confirm)}收盘{'上破上轨' if direction == 'bull' else '下破下轨'}"
            f"{boundary:.2f}，量比{_vol_ratio(df, confirm):.2f}。"
        )
        out.append({
            "kind": "broadening_break",
            "name": name,
            "direction": direction,
            "start_idx": int(pts[0]["idx"]),
            "end_idx": int(pts[-1]["idx"]),
            "confirm_idx": int(confirm),
            "key_levels": {
                "upper": round(_line(confirm, highs[0], highs[-1]), 4),
                "lower": round(_line(confirm, lows[0], lows[-1]), 4),
                "invalidation": round(float(boundary), 4),
            },
            "score": 84,
            "star": True,
            "note": note,
            "trace": _trace(df, pts, highs, lows, confirm),
            "active": confirm >= len(df) - 140,
        })
    return out
