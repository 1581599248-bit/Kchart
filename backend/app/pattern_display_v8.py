"""大级别结构显示筛选器。

识别层可以保留多个候选；主图只描摹跨度、幅度足够且已确认的核心结构。
同一时间与价格区域存在多个解释时，只保留优先级最高的一项，避免一段K线
被矩形、三角、楔形、波浪等反复描摹。
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

# 反转结构优先于波浪，波浪优先于整理/中继。
_PRIORITY = {
    "head_shoulders_top": 120,
    "head_shoulders_bottom": 120,
    "double_top": 116,
    "double_bottom": 116,
    "triple_top": 114,
    "triple_bottom": 114,
    "arc_top": 108,
    "arc_bottom": 108,
    "wave_impulse_up": 96,
    "wave_impulse_down": 96,
    "wave_abc_up": 90,
    "wave_abc_down": 90,
    "broadening_up": 84,
    "broadening_down": 84,
    "bullish_triangle_directional": 80,
    "bearish_triangle_directional": 80,
    "symmetric_triangle_directional": 76,
    "bull_wedge_directional": 78,
    "bear_wedge_directional": 78,
    "bull_flag_directional": 74,
    "bear_flag_directional": 74,
    "bull_rectangle": 70,
    "bear_rectangle": 70,
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


def _span(event: dict) -> tuple[int, int, int]:
    start = int(event.get("start_idx", 0))
    end = int(event.get("end_idx", start))
    lo, hi = min(start, end), max(start, end)
    return lo, hi, hi - lo + 1


def _price_range(df: pd.DataFrame, event: dict) -> tuple[float, float, float, float]:
    start, end, _ = _span(event)
    start = max(0, start)
    end = min(len(df) - 1, end)
    if start > end:
        return 0.0, 0.0, 0.0, 0.0
    high = float(df["high"].iloc[start:end + 1].max())
    low = float(df["low"].iloc[start:end + 1].min())
    mid = (high + low) / 2.0 if high > 0 and low > 0 else 0.0
    amplitude = (high - low) / low if low > 0 else 0.0
    return low, high, mid, amplitude


def _atr_threshold(df: pd.DataFrame, event: dict) -> float:
    """波动率自适应的大结构振幅门槛。

    低波动指数最低5.5%；高波动个股按4倍ATR/价格抬高门槛，最高20%。
    """
    start, end, _ = _span(event)
    start = max(0, start)
    end = min(len(df) - 1, end)
    if start > end or "ATR14" not in df.columns:
        return 0.055
    atr = pd.to_numeric(df["ATR14"].iloc[start:end + 1], errors="coerce")
    close = pd.to_numeric(df["close"].iloc[start:end + 1], errors="coerce")
    valid = atr.notna() & close.notna() & (close > 0)
    if not valid.any():
        return 0.055
    ratio = float(np.nanmedian((atr[valid] / close[valid]).to_numpy(dtype=float)))
    if not math.isfinite(ratio) or ratio <= 0:
        return 0.055
    return min(0.20, max(0.055, 4.0 * ratio))


def _min_span(kind: str) -> int:
    if kind in _REVERSALS:
        return 60
    if kind in _WAVES:
        return 60
    if kind in _RECTANGLES:
        return 60
    if kind in _CONTINUATIONS:
        return 45
    return 10_000  # 未列入白名单的普通趋势线/小结构不画主图。


def _is_major(df: pd.DataFrame, event: dict) -> bool:
    kind = _kind(event)
    if kind not in _PRIORITY:
        return False
    if event.get("confirm_idx") is None:
        return False
    _, _, span = _span(event)
    if span < _min_span(kind):
        return False
    _, _, _, amplitude = _price_range(df, event)
    threshold = _atr_threshold(df, event)
    # 推动浪本身已要求15%幅度；其他结构使用波动率自适应门槛。
    if kind in {"wave_impulse_up", "wave_impulse_down"}:
        return amplitude >= max(0.12, threshold)
    return amplitude >= threshold


def _time_overlap(a: dict, b: dict) -> float:
    a0, a1, _ = _span(a)
    b0, b1, _ = _span(b)
    overlap = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return overlap / shorter


def _price_overlap(df: pd.DataFrame, a: dict, b: dict) -> float:
    al, ah, _, _ = _price_range(df, a)
    bl, bh, _, _ = _price_range(df, b)
    overlap = max(0.0, min(ah, bh) - max(al, bl))
    shorter = max(1e-9, min(ah - al, bh - bl))
    return overlap / shorter


def _same_region(df: pd.DataFrame, a: dict, b: dict) -> bool:
    time_ratio = _time_overlap(a, b)
    price_ratio = _price_overlap(df, a, b)
    if time_ratio >= 0.45 and price_ratio >= 0.35:
        return True
    ac, bc = a.get("confirm_idx"), b.get("confirm_idx")
    if ac is None or bc is None:
        return False
    # 时间区间明显交叠且确认点靠得很近，也属于同一段走势的竞争解释。
    return time_ratio >= 0.25 and price_ratio >= 0.50 and abs(int(ac) - int(bc)) <= 30


def _rank(df: pd.DataFrame, event: dict) -> tuple[int, int, int, float]:
    _, _, span = _span(event)
    _, _, _, amplitude = _price_range(df, event)
    return (
        _PRIORITY.get(_kind(event), 0),
        int(event.get("score", 0)),
        span,
        amplitude,
    )


def select_display_patterns(df: pd.DataFrame, events: Iterable[dict]) -> list[dict]:
    """只返回主图应描摹的大级别、非重复结构。"""
    candidates = [dict(event) for event in events if _is_major(df, event)]
    candidates.sort(key=lambda event: _rank(df, event), reverse=True)

    selected: list[dict] = []
    for event in candidates:
        if any(_same_region(df, event, old) for old in selected):
            continue
        item = dict(event)
        item["display_major"] = True
        item["display_span"] = _span(item)[2]
        item["display_amplitude"] = round(_price_range(df, item)[3], 6)
        selected.append(item)

    return sorted(
        selected,
        key=lambda event: int(event.get("confirm_idx") or event.get("end_idx", 0)),
    )
