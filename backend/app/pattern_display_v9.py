"""v9主图结构筛选：少而准，同一区域只留一个解释。

反转形态的级别使用“结构起点→颈线确认日”的完整跨度；整理形态仍使用
形成区间跨度。这样近期M顶不会因两顶间隔较短而被错误删除。
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

_PRIORITY = {
    "head_shoulders_top": 120, "head_shoulders_bottom": 120,
    "double_top": 118, "double_bottom": 118,
    "triple_top": 114, "triple_bottom": 114,
    "arc_top": 108, "arc_bottom": 108,
    "wave_impulse_up": 96, "wave_impulse_down": 96,
    "wave_abc_up": 90, "wave_abc_down": 90,
    "broadening_up": 84, "broadening_down": 84,
    "bullish_triangle_directional": 80, "bearish_triangle_directional": 80,
    "symmetric_triangle_directional": 76,
    "bull_wedge_directional": 78, "bear_wedge_directional": 78,
    "bull_flag_directional": 74, "bear_flag_directional": 74,
    "bull_rectangle": 70, "bear_rectangle": 70,
}

_REVERSALS = {
    "head_shoulders_top", "head_shoulders_bottom", "double_top", "double_bottom",
    "triple_top", "triple_bottom", "arc_top", "arc_bottom",
}
_WAVES = {"wave_impulse_up", "wave_impulse_down", "wave_abc_up", "wave_abc_down"}
_RECTANGLES = {"bull_rectangle", "bear_rectangle"}
_CONTINUATIONS = {
    "bullish_triangle_directional", "bearish_triangle_directional",
    "symmetric_triangle_directional", "bull_wedge_directional",
    "bear_wedge_directional", "bull_flag_directional", "bear_flag_directional",
    "broadening_up", "broadening_down",
}


def _kind(event: dict) -> str:
    return str(event.get("kind") or "")


def _formation_span(event: dict) -> tuple[int, int, int]:
    start = int(event.get("start_idx", 0))
    end = int(event.get("end_idx", start))
    lo, hi = min(start, end), max(start, end)
    return lo, hi, hi - lo + 1


def _effective_span(event: dict) -> tuple[int, int, int]:
    start, end, _ = _formation_span(event)
    if _kind(event) in _REVERSALS and event.get("confirm_idx") is not None:
        end = max(end, int(event["confirm_idx"]))
    return start, end, end - start + 1


def _price_range(df: pd.DataFrame, event: dict) -> tuple[float, float, float]:
    start, end, _ = _effective_span(event)
    start, end = max(0, start), min(len(df) - 1, end)
    if start > end:
        return 0.0, 0.0, 0.0
    high = float(df["high"].iloc[start:end + 1].max())
    low = float(df["low"].iloc[start:end + 1].min())
    amplitude = (high - low) / low if low > 0 else 0.0
    return low, high, amplitude


def _atr_threshold(df: pd.DataFrame, event: dict) -> float:
    start, end, _ = _effective_span(event)
    start, end = max(0, start), min(len(df) - 1, end)
    if start > end or "ATR14" not in df.columns:
        return 0.05
    atr = pd.to_numeric(df["ATR14"].iloc[start:end + 1], errors="coerce")
    close = pd.to_numeric(df["close"].iloc[start:end + 1], errors="coerce")
    valid = atr.notna() & close.notna() & (close > 0)
    if not valid.any():
        return 0.05
    ratio = float(np.nanmedian((atr[valid] / close[valid]).to_numpy(dtype=float)))
    if not math.isfinite(ratio) or ratio <= 0:
        return 0.05
    return min(0.20, max(0.05, 3.5 * ratio))


def _min_span(kind: str) -> int:
    if kind in {"double_top", "double_bottom"}:
        return 40
    if kind in _REVERSALS:
        return 60
    if kind in _WAVES or kind in _RECTANGLES:
        return 60
    if kind in _CONTINUATIONS:
        return 45
    return 10_000


def _is_major(df: pd.DataFrame, event: dict) -> bool:
    kind = _kind(event)
    if kind not in _PRIORITY or event.get("confirm_idx") is None:
        return False
    _, _, span = _effective_span(event)
    if span < _min_span(kind):
        return False
    _, _, amplitude = _price_range(df, event)
    threshold = _atr_threshold(df, event)
    if kind in {"wave_impulse_up", "wave_impulse_down"}:
        return amplitude >= max(0.12, threshold)
    return amplitude >= threshold


def _time_overlap(a: dict, b: dict) -> float:
    a0, a1, _ = _effective_span(a)
    b0, b1, _ = _effective_span(b)
    overlap = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return overlap / shorter


def _price_overlap(df: pd.DataFrame, a: dict, b: dict) -> float:
    al, ah, _ = _price_range(df, a)
    bl, bh, _ = _price_range(df, b)
    overlap = max(0.0, min(ah, bh) - max(al, bl))
    shorter = max(1e-9, min(ah - al, bh - bl))
    return overlap / shorter


def _same_region(df: pd.DataFrame, a: dict, b: dict) -> bool:
    time_ratio = _time_overlap(a, b)
    price_ratio = _price_overlap(df, a, b)
    if time_ratio >= 0.45 and price_ratio >= 0.35:
        return True
    ac, bc = a.get("confirm_idx"), b.get("confirm_idx")
    return (
        ac is not None and bc is not None
        and time_ratio >= 0.25 and price_ratio >= 0.50
        and abs(int(ac) - int(bc)) <= 30
    )


def _rank(df: pd.DataFrame, event: dict) -> tuple[int, int, int, float]:
    _, _, span = _effective_span(event)
    _, _, amplitude = _price_range(df, event)
    return _PRIORITY.get(_kind(event), 0), int(event.get("score", 0)), span, amplitude


def select_display_patterns(df: pd.DataFrame, events: Iterable[dict]) -> list[dict]:
    candidates = [dict(event) for event in events if _is_major(df, event)]
    candidates.sort(key=lambda event: _rank(df, event), reverse=True)
    selected: list[dict] = []
    for event in candidates:
        if any(_same_region(df, event, old) for old in selected):
            continue
        item = dict(event)
        item["display_major"] = True
        item["display_span"] = _effective_span(item)[2]
        item["display_amplitude"] = round(_price_range(df, item)[2], 6)
        selected.append(item)
    return sorted(selected, key=lambda e: int(e.get("confirm_idx") or e.get("end_idx", 0)))
