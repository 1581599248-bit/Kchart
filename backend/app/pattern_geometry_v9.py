"""v9结构几何校验：上下沿必须锚定真实pivot触点。

只处理三角、楔形、旗形和矩形。找不到至少两个有效触点、边界被明显穿越，
或两条边界方向不符合形态定义时，候选结构直接退出主图。
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from . import pivots as piv_mod

_GEOMETRY_KINDS = {
    "bullish_triangle_directional", "bearish_triangle_directional",
    "symmetric_triangle_directional", "bull_wedge_directional",
    "bear_wedge_directional", "bull_flag_directional", "bear_flag_directional",
    "bull_rectangle", "bear_rectangle",
}


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _line_value(idx: int, a: dict, b: dict) -> float:
    x1, x2 = int(a["idx"]), int(b["idx"])
    y1, y2 = float(a["price"]), float(b["price"])
    if x2 == x1:
        return y2
    return float(y1 + (y2 - y1) * (idx - x1) / (x2 - x1))


def _tolerance(df: pd.DataFrame, start: int, end: int, price: float) -> float:
    if "ATR14" in df.columns:
        atr = pd.to_numeric(df["ATR14"].iloc[start:end + 1], errors="coerce")
        value = float(np.nanmedian(atr.to_numpy(dtype=float))) if len(atr) else np.nan
        if np.isfinite(value) and value > 0:
            return max(value * 0.55, price * 0.004)
    return max(price * 0.007, 1e-9)


def _best_boundary(points: list[dict], upper: bool, min_span: int,
                   tolerance: float) -> tuple[dict, dict, int, float] | None:
    """选择通过真实触点的边界；上沿不得被高点显著穿越，下沿镜像。"""
    best = None
    for a, b in itertools.combinations(points, 2):
        span = int(b["idx"] - a["idx"])
        if span < min_span:
            continue
        residuals = []
        violation = 0.0
        touches = 0
        for p in points:
            value = _line_value(int(p["idx"]), a, b)
            residual = float(p["price"]) - value
            residuals.append(abs(residual))
            if upper:
                violation = max(violation, residual)
            else:
                violation = max(violation, -residual)
            if abs(residual) <= tolerance:
                touches += 1
        if violation > tolerance or touches < 2:
            continue
        mean_error = float(np.mean(residuals)) if residuals else math.inf
        score = touches * 1000 + span * 2 - mean_error / max(tolerance, 1e-9)
        candidate = (score, a, b, touches, mean_error)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    _, a, b, touches, error = best
    return a, b, int(touches), float(error)


def _slope(a: dict, b: dict, reference: float) -> float:
    span = max(1, int(b["idx"] - a["idx"]))
    return (float(b["price"]) - float(a["price"])) / span / max(reference, 1e-9)


def _shape_valid(kind: str, upper: tuple, lower: tuple, reference: float) -> bool:
    ua, ub = upper[0], upper[1]
    la, lb = lower[0], lower[1]
    su, sl = _slope(ua, ub, reference), _slope(la, lb, reference)
    flat = 0.00075

    start = max(int(ua["idx"]), int(la["idx"]))
    end = min(int(ub["idx"]), int(lb["idx"]))
    if end <= start:
        return False
    gap_start = _line_value(start, ua, ub) - _line_value(start, la, lb)
    gap_end = _line_value(end, ua, ub) - _line_value(end, la, lb)
    if gap_start <= 0 or gap_end <= 0:
        return False

    if kind == "bullish_triangle_directional":
        return abs(su) <= flat * 1.5 and sl > flat * 0.35 and gap_end < gap_start
    if kind == "bearish_triangle_directional":
        return su < -flat * 0.35 and abs(sl) <= flat * 1.5 and gap_end < gap_start
    if kind == "symmetric_triangle_directional":
        return su < -flat * 0.25 and sl > flat * 0.25 and gap_end < gap_start
    if kind == "bull_wedge_directional":
        return su < 0 and sl < 0 and su < sl and gap_end < gap_start
    if kind == "bear_wedge_directional":
        return su > 0 and sl > 0 and sl > su and gap_end < gap_start
    if kind == "bull_flag_directional":
        return su <= flat and sl <= flat and abs(su - sl) <= flat * 1.8
    if kind == "bear_flag_directional":
        return su >= -flat and sl >= -flat and abs(su - sl) <= flat * 1.8
    if kind in {"bull_rectangle", "bear_rectangle"}:
        return abs(su) <= flat and abs(sl) <= flat
    return False


def _trace(df: pd.DataFrame, pivots: list[dict], upper: tuple, lower: tuple) -> list[dict]:
    ua, ub = upper[0], upper[1]
    la, lb = lower[0], lower[1]
    return [
        {
            "points": [
                {"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
                for p in pivots
            ],
            "style": "solid",
        },
        {
            "points": [
                {"t": _date(df, int(ua["idx"])), "p": round(float(ua["price"]), 4)},
                {"t": _date(df, int(ub["idx"])), "p": round(float(ub["price"]), 4)},
            ],
            "style": "dashed",
        },
        {
            "points": [
                {"t": _date(df, int(la["idx"])), "p": round(float(la["price"]), 4)},
                {"t": _date(df, int(lb["idx"])), "p": round(float(lb["price"]), 4)},
            ],
            "style": "dashed",
        },
    ]


def apply_geometry(df: pd.DataFrame, pivots: pd.DataFrame,
                   events: list[dict]) -> list[dict]:
    confirmed = piv_mod.pivots_asof(pivots, len(df) - 1)
    records = confirmed.to_dict("records")
    out: list[dict] = []

    for raw in events:
        event = dict(raw)
        kind = str(event.get("kind") or "")
        if kind not in _GEOMETRY_KINDS:
            out.append(event)
            continue

        start = max(0, int(event.get("start_idx", 0)))
        end = min(len(df) - 1, int(event.get("end_idx", start)))
        local = [p for p in records if start <= int(p["idx"]) <= end]
        highs = [p for p in local if p["kind"] == "H"]
        lows = [p for p in local if p["kind"] == "L"]
        if len(highs) < 2 or len(lows) < 2:
            continue

        reference = float(df["close"].iloc[end])
        tolerance = _tolerance(df, start, end, reference)
        min_span = max(8, int((end - start + 1) * 0.30))
        upper = _best_boundary(highs, True, min_span, tolerance)
        lower = _best_boundary(lows, False, min_span, tolerance)
        if upper is None or lower is None:
            continue
        if not _shape_valid(kind, upper, lower, reference):
            continue

        item = dict(event)
        item["trace"] = _trace(df, sorted(local, key=lambda p: int(p["idx"])), upper, lower)
        levels = dict(item.get("key_levels") or {})
        levels.update({
            "upper": round(_line_value(end, upper[0], upper[1]), 4),
            "lower": round(_line_value(end, lower[0], lower[1]), 4),
            "upper_touches": int(upper[2]),
            "lower_touches": int(lower[2]),
        })
        item["key_levels"] = levels
        item["geometry_validated"] = True
        item["note"] = (
            str(item.get("note") or "")
            + f"；上下沿分别由{upper[2]}/{lower[2]}个真实pivot触点校验。"
        )
        out.append(item)
    return out
