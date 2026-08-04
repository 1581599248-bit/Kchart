"""Investment-grade technical-analysis selector.

All legacy recognizers remain candidate generators.  This module decides what is
large, confirmed and useful enough to reach the price chart.  Selection is causal:
only data available on the annotation bar may be used.  Post-signal performance is
never used to accept or reject a candidate.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from . import analysis_v5 as legacy
from . import fibonacci_history
from . import harmonics_history
from . import indicators
from . import patterns as pattern_mod
from . import patterns_ext
from . import pivots as piv_mod

ENGINE_VERSION = "investment_engine_v12.0"

# Broad, volatility-normalised limits.  Scores rank valid candidates; they never
# turn an invalid candidate into a valid one.
MIN_REVERSAL_BARS = 75
MIN_CONTINUATION_BARS = 50
MIN_WAVE_BARS = 100
MAX_PATTERN_EVENTS = 6
MAX_INDICATOR_EVENTS = 8
PATTERN_OVERLAP_LIMIT = 0.52

_PATTERN_PRIORITY = {
    "macro_double_top": 110,
    "macro_double_bottom": 110,
    "head_shoulders_top": 100,
    "head_shoulders_bottom": 100,
    "triple_top": 94,
    "triple_bottom": 94,
    "double_top": 90,
    "double_bottom": 90,
    "arc_top": 86,
    "arc_bottom": 86,
    "wave_5_up": 84,
    "wave_5_down": 84,
    "abc_up": 82,
    "abc_down": 82,
    "ascending_triangle": 76,
    "descending_triangle": 76,
    "symmetric_triangle": 72,
    "rising_wedge": 70,
    "falling_wedge": 70,
    "bull_flag": 66,
    "bear_flag": 66,
    "box": 58,
    "range_box": 58,
    "broadening_triangle": 54,
    "trendline_break": 48,
}

_PATTERN_NAMES = {
    "macro_double_top": "大M顶",
    "macro_double_bottom": "大W底",
    "double_top": "M顶",
    "double_bottom": "W底",
    "triple_top": "三重顶",
    "triple_bottom": "三重底",
    "head_shoulders_top": "头肩顶",
    "head_shoulders_bottom": "头肩底",
    "arc_top": "圆弧顶",
    "arc_bottom": "圆弧底",
    "bull_flag": "牛旗形",
    "bear_flag": "熊旗形",
    "rising_wedge": "熊楔形",
    "falling_wedge": "牛楔形",
    "ascending_triangle": "看涨三角",
    "descending_triangle": "看跌三角",
    "symmetric_triangle": "对称三角",
    "broadening_triangle": "扩散结构",
    "box": "矩形整理",
    "range_box": "矩形整理",
    "wave_5_up": "上升五浪",
    "wave_5_down": "下跌五浪",
    "abc_up": "上升ABC",
    "abc_down": "下跌ABC",
    "trendline_break": "趋势突破",
}


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(pd.to_datetime(df["trade_date"].iloc[int(idx)]).date())


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _atr(df: pd.DataFrame, idx: int) -> float:
    if "ATR14" not in df.columns:
        return float("nan")
    value = float(df["ATR14"].iloc[int(idx)])
    return value if math.isfinite(value) and value > 0 else float("nan")


def _median_atr_pct(df: pd.DataFrame, end: int | None = None) -> float:
    stop = len(df) if end is None else min(len(df), int(end) + 1)
    start = max(0, stop - 252)
    close = df["close"].iloc[start:stop].to_numpy(dtype=float)
    atr = df["ATR14"].iloc[start:stop].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = atr / close
    ratio = ratio[np.isfinite(ratio) & (ratio > 0)]
    return float(np.median(ratio)) if len(ratio) else 0.018


def _line_points(event: dict) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for line in event.get("trace") or []:
        for point in line.get("points") or []:
            if point.get("t") is not None and _finite(point.get("p")):
                out.append((str(point["t"])[:10], float(point["p"])))
    return out


def _event_amplitude(df: pd.DataFrame, event: dict) -> tuple[float, float]:
    start = max(0, int(event.get("start_idx", 0)))
    stop = min(len(df) - 1, int(event.get("confirm_idx") or event.get("end_idx", start)))
    if stop <= start:
        return 0.0, 0.0
    hi = float(df["high"].iloc[start:stop + 1].max())
    lo = float(df["low"].iloc[start:stop + 1].min())
    mid = max((hi + lo) / 2.0, 1e-9)
    amp_pct = (hi - lo) / mid
    atr = _atr(df, int(event.get("end_idx", stop)))
    amp_atr = (hi - lo) / atr if math.isfinite(atr) else float("nan")
    return amp_pct, amp_atr


def _trace_is_anchored(df: pd.DataFrame, event: dict) -> bool:
    """Reject decorative lines that do not touch actual candles."""
    points = _line_points(event)
    if not points:
        return True
    date_to_idx = {_date(df, i): i for i in range(len(df))}
    tested = 0
    touching = 0
    for date, price in points:
        idx = date_to_idx.get(date)
        if idx is None:
            continue
        tested += 1
        atr = _atr(df, idx)
        tolerance = max(float(df["close"].iloc[idx]) * 0.008,
                        (atr * 0.80 if math.isfinite(atr) else 0.0))
        low = float(df["low"].iloc[idx]) - tolerance
        high = float(df["high"].iloc[idx]) + tolerance
        if low <= price <= high:
            touching += 1
    return tested == 0 or touching / tested >= 0.70


def _large_pattern(df: pd.DataFrame, event: dict) -> bool:
    if event.get("confirm_idx") is None:
        return False
    start = int(event.get("start_idx", 0))
    confirm = int(event.get("confirm_idx", event.get("end_idx", start)))
    span = confirm - start
    kind = str(event.get("kind") or "")
    amp_pct, amp_atr = _event_amplitude(df, event)
    volatility_ok = math.isfinite(amp_atr)

    if kind.startswith("macro_double_"):
        min_bars, pct_floor, atr_floor = MIN_REVERSAL_BARS, 0.09, 5.0
    elif kind in {
        "double_top", "double_bottom", "triple_top", "triple_bottom",
        "head_shoulders_top", "head_shoulders_bottom", "arc_top", "arc_bottom",
    }:
        min_bars, pct_floor, atr_floor = MIN_REVERSAL_BARS, 0.10, 6.0
    elif "wave" in kind or kind.startswith("abc_"):
        min_bars, pct_floor, atr_floor = MIN_WAVE_BARS, 0.15, 8.0
    else:
        min_bars, pct_floor, atr_floor = MIN_CONTINUATION_BARS, 0.08, 5.0

    magnitude_ok = amp_pct >= pct_floor or (volatility_ok and amp_atr >= atr_floor)
    return span >= min_bars and magnitude_ok and _trace_is_anchored(df, event)


def _pattern_overlap(a: dict, b: dict) -> float:
    a0, a1 = int(a.get("start_idx", 0)), int(a.get("confirm_idx", a.get("end_idx", 0)))
    b0, b1 = int(b.get("start_idx", 0)), int(b.get("confirm_idx", b.get("end_idx", 0)))
    overlap = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return overlap / shorter


def _pattern_rank(df: pd.DataFrame, event: dict) -> tuple[float, int, int]:
    kind = str(event.get("kind") or "")
    amp_pct, amp_atr = _event_amplitude(df, event)
    span = int(event.get("confirm_idx", 0)) - int(event.get("start_idx", 0))
    magnitude = amp_pct * 100.0 + (min(amp_atr, 12.0) if math.isfinite(amp_atr) else 0.0)
    return (
        float(_PATTERN_PRIORITY.get(kind, 40)) + magnitude + min(span, 180) / 15.0,
        int(event.get("confirm_idx", 0)),
        int(event.get("score", 0)),
    )


def _cluster_levels(levels: list[dict], atr_pct: float) -> list[dict]:
    clusters: list[dict] = []
    for item in sorted(levels, key=lambda x: float(x["price"])):
        price = float(item["price"])
        matched = None
        for cluster in clusters:
            tolerance = max(float(cluster["price"]) * 0.018, float(cluster["price"]) * atr_pct * 0.80)
            if abs(price - float(cluster["price"])) <= tolerance:
                matched = cluster
                break
        if matched is None:
            clusters.append({"price": price, "items": [item]})
        else:
            matched["items"].append(item)
            matched["price"] = float(np.median([float(x["price"]) for x in matched["items"]]))
    return clusters


def _confirm_break(df: pd.DataFrame, start: int, level: float, direction: str,
                   invalidation: float) -> int | None:
    close = df["close"].to_numpy(dtype=float)
    outside = 0
    for idx in range(max(1, int(start)), len(df)):
        atr = _atr(df, idx)
        confirm_buffer = max(level * 0.01, atr * 0.50 if math.isfinite(atr) else 0.0)
        invalid_buffer = max(invalidation * 0.004, atr * 0.25 if math.isfinite(atr) else 0.0)
        if direction == "bear":
            if close[idx] > invalidation + invalid_buffer:
                return None
            outside = outside + 1 if close[idx] < level else 0
            decisive = close[idx] <= level - confirm_buffer
        else:
            if close[idx] < invalidation - invalid_buffer:
                return None
            outside = outside + 1 if close[idx] > level else 0
            decisive = close[idx] >= level + confirm_buffer
        if outside >= 2 and decisive:
            return idx
    return None


def _prior_move(df: pd.DataFrame, idx: int, price: float, direction: str) -> tuple[float, float]:
    start = max(0, idx - 180)
    atr = _atr(df, idx)
    if direction == "bear":
        ref = float(df["low"].iloc[start:idx + 1].min())
        absolute = price - ref
        pct = absolute / max(ref, 1e-9)
    else:
        ref = float(df["high"].iloc[start:idx + 1].max())
        absolute = ref - price
        pct = absolute / max(price, 1e-9)
    return pct, absolute / atr if math.isfinite(atr) else float("nan")


def _position(df: pd.DataFrame, idx: int, price: float, direction: str) -> float:
    start = max(0, idx - 251)
    hi = float(df["high"].iloc[start:idx + 1].max())
    lo = float(df["low"].iloc[start:idx + 1].min())
    if hi <= lo:
        return 0.5
    percentile = (price - lo) / (hi - lo)
    return percentile if direction == "bear" else 1.0 - percentile


def _macro_reversal_candidates(df: pd.DataFrame) -> list[dict]:
    """Large M/W structures with a support/resistance *zone*, not a single arbitrary trough."""
    atr_pct = _median_atr_pct(df)
    zigzag_pct = float(np.clip(atr_pct * 3.0, 0.04, 0.08))
    pivots = piv_mod.zigzag(df, min_pct=zigzag_pct)
    pivots = piv_mod.pivots_asof(pivots, len(df) - 1)
    points = pivots.to_dict("records")
    events: list[dict] = []

    for direction, extreme_kind, middle_kind in (
        ("bear", "H", "L"), ("bull", "L", "H")
    ):
        extremes = [p for p in points if p["kind"] == extreme_kind]
        for right_pos in range(1, len(extremes)):
            right = extremes[right_pos]
            for left_pos in range(right_pos - 1, -1, -1):
                left = extremes[left_pos]
                gap = int(right["idx"] - left["idx"])
                if gap > 210:
                    break
                if gap < 30:
                    continue
                p1, p2 = float(left["price"]), float(right["price"])
                mean_extreme = (p1 + p2) / 2.0
                if mean_extreme <= 0 or abs(p2 - p1) / mean_extreme > 0.085:
                    continue

                middle = [
                    p for p in points
                    if p["kind"] == middle_kind and int(left["idx"]) < int(p["idx"]) < int(right["idx"])
                ]
                if not middle:
                    continue
                clusters = _cluster_levels(middle, atr_pct)
                # Prefer a repeatedly tested platform; if there is only one macro pivot,
                # the deepest central pivot is still allowed but receives no touch bonus.
                if direction == "bear":
                    clusters.sort(key=lambda c: (len(c["items"]), -float(c["price"])), reverse=True)
                else:
                    clusters.sort(key=lambda c: (len(c["items"]), float(c["price"])), reverse=True)
                cluster = clusters[0]
                level = float(cluster["price"])
                anchor = min(cluster["items"], key=lambda x: int(x["idx"]))

                if direction == "bear":
                    depth_abs = min(p1, p2) - level
                    invalidation = max(p1, p2)
                else:
                    depth_abs = level - max(p1, p2)
                    invalidation = min(p1, p2)
                if depth_abs <= 0:
                    continue
                depth_pct = depth_abs / max(mean_extreme, 1e-9)
                atr_left = _atr(df, int(left["idx"]))
                depth_atr = depth_abs / atr_left if math.isfinite(atr_left) else float("nan")
                if depth_pct < 0.055 and (not math.isfinite(depth_atr) or depth_atr < 4.0):
                    continue
                prior_pct, prior_atr = _prior_move(df, int(left["idx"]), p1, direction)
                if prior_pct < 0.10 and (not math.isfinite(prior_atr) or prior_atr < 5.5):
                    continue
                if _position(df, int(left["idx"]), p1, direction) < 0.68:
                    continue

                start_confirm = max(int(right["idx"]) + 1, int(right["confirmed_at_idx"]))
                confirm_idx = _confirm_break(df, start_confirm, level, direction, invalidation)
                if confirm_idx is None or confirm_idx - int(left["idx"]) < MIN_REVERSAL_BARS:
                    continue

                kind = "macro_double_top" if direction == "bear" else "macro_double_bottom"
                name = _PATTERN_NAMES[kind]
                target = level - depth_abs if direction == "bear" else level + depth_abs
                touches = len(cluster["items"])
                score = int(round(
                    72 + min(touches, 3) * 4 + min(depth_pct / 0.10, 1.0) * 8
                    + min((confirm_idx - int(left["idx"])) / 150.0, 1.0) * 6
                ))
                trace = [
                    {"points": [
                        {"t": _date(df, int(left["idx"])), "p": round(p1, 4)},
                        {"t": _date(df, int(anchor["idx"])), "p": round(level, 4)},
                        {"t": _date(df, int(right["idx"])), "p": round(p2, 4)},
                    ], "style": "solid"},
                    {"points": [
                        {"t": _date(df, int(anchor["idx"])), "p": round(level, 4)},
                        {"t": _date(df, confirm_idx), "p": round(level, 4)},
                    ], "style": "dashed"},
                ]
                note = (
                    f"{name}：{_date(df, int(left['idx']))}至{_date(df, int(right['idx']))}形成"
                    f"大级别双峰/双谷，颈线按{touches}个宏观承接点聚类为{level:.2f}；"
                    f"{_date(df, confirm_idx)}连续收盘有效{'跌破' if direction == 'bear' else '突破'}。"
                )
                events.append({
                    "kind": kind,
                    "name": name,
                    "direction": direction,
                    "start_idx": int(left["idx"]),
                    "middle_idx": int(anchor["idx"]),
                    "end_idx": int(right["idx"]),
                    "confirm_idx": int(confirm_idx),
                    "key_levels": {
                        "neckline": round(level, 4),
                        "measure_target": round(target, 4),
                        "invalidation": round(invalidation, 4),
                        "extreme1": round(p1, 4),
                        "extreme2": round(p2, 4),
                    },
                    "score": score,
                    "star": True,
                    "note": note,
                    "trace": trace,
                    "active": confirm_idx >= len(df) - 180,
                    "causal": True,
                    "touches": touches,
                })
    return events


def _select_macro_reversals(df: pd.DataFrame, candidates: list[dict]) -> list[dict]:
    """Prefer the larger explanatory structure over a recent nested mini-pattern."""
    ordered = sorted(
        candidates,
        key=lambda e: (
            int(e["confirm_idx"]) - int(e["start_idx"]),
            int(e.get("touches", 0)),
            int(e.get("score", 0)),
            int(e["confirm_idx"]),
        ),
        reverse=True,
    )
    selected: list[dict] = []
    for event in ordered:
        if any(_pattern_overlap(event, old) >= 0.58 for old in selected):
            continue
        selected.append(event)
        if len(selected) >= 3:
            break
    return sorted(selected, key=lambda e: int(e["confirm_idx"]))


def find_investment_patterns(df: pd.DataFrame, timeframe: str = "1d") -> list[dict]:
    pivots = piv_mod.find_pivots(df)
    raw = pattern_mod.find_patterns(df, pivots, asof_bar=len(df) - 1, timeframe=timeframe)
    raw += patterns_ext.find_patterns_ext(df, pivots, timeframe=timeframe)
    raw = legacy._dedupe_patterns(raw)

    macro = _select_macro_reversals(df, _macro_reversal_candidates(df))
    # Macro M/W replaces all small geometric double-top/bottom candidates.
    raw = [e for e in raw if str(e.get("kind")) not in {"double_top", "double_bottom"}]
    valid = macro + [dict(e) for e in raw if _large_pattern(df, e)]

    ordered = sorted(valid, key=lambda e: _pattern_rank(df, e), reverse=True)
    selected: list[dict] = []
    for event in ordered:
        if any(_pattern_overlap(event, old) >= PATTERN_OVERLAP_LIMIT for old in selected):
            continue
        selected.append(event)
        if len(selected) >= MAX_PATTERN_EVENTS:
            break
    return sorted(selected, key=lambda e: int(e.get("confirm_idx", 0)))


def _pattern_name(event: dict) -> str:
    kind = str(event.get("kind") or "")
    if kind in {"box", "range_box"}:
        return "牛矩形" if event.get("direction") == "bull" else "熊矩形"
    if kind == "symmetric_triangle":
        return "看涨三角" if event.get("direction") == "bull" else "看跌三角"
    if kind == "broadening_triangle":
        return "扩散上破" if event.get("direction") == "bull" else "扩散下破"
    return _PATTERN_NAMES.get(kind, str(event.get("name") or "大结构"))


def pattern_annotations(df: pd.DataFrame, patterns: list[dict]) -> list[dict]:
    out: list[dict] = []
    for event in patterns:
        direction = str(event.get("direction") or "range")
        end_idx = int(event.get("end_idx", 0))
        confirm_idx = int(event.get("confirm_idx", end_idx))
        name = _pattern_name(event)
        detail = str(event.get("note") or "")
        formation_price = float(df["low" if direction == "bull" else "high"].iloc[end_idx])
        out.append({
            "bar_idx": end_idx,
            "price": formation_price,
            "kind": "pattern",
            "label": name[:8],
            "direction": direction,
            "star": False,
            "detail": detail,
            "lines": [],
            "zones": [],
            "polylines": event.get("trace") or [],
            "trace_only": True,
            "history_label": True,
            "active": bool(event.get("active", True)),
            "_score": int(event.get("score", 70)),
            "_grp": f"structure:{event.get('kind')}:{event.get('start_idx')}:{confirm_idx}",
            "structure_id": f"{event.get('kind')}:{event.get('start_idx')}:{confirm_idx}",
        })
        levels = event.get("key_levels") or {}
        has_neckline = _finite(levels.get("neckline"))
        if has_neckline:
            label = "突破颈线" if direction == "bull" else "跌破颈线"
        else:
            label = "向上突破" if direction == "bull" else "向下跌破"
        out.append({
            "bar_idx": confirm_idx,
            "price": float(df["low" if direction == "bull" else "high"].iloc[confirm_idx]),
            "kind": "pattern",
            "label": label,
            "direction": direction,
            "star": True,
            "detail": detail,
            "lines": [],
            "zones": [],
            "polylines": [],
            "active": bool(event.get("active", True)),
            "_score": int(event.get("score", 70)) + 5,
            "_grp": f"structure_confirm:{event.get('kind')}:{confirm_idx}",
            "structure_id": f"{event.get('kind')}:{event.get('start_idx')}:{confirm_idx}",
        })
    return out


def _event(df: pd.DataFrame, idx: int, label: str, direction: str, detail: str,
           score: int, group: str, kind: str = "indicator") -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(df["close"].iloc[int(idx)]), 4),
        "kind": kind,
        "label": label,
        "direction": direction,
        "star": False,
        "detail": detail,
        "lines": [], "zones": [], "polylines": [],
        "active": idx >= len(df) - 180,
        "_score": int(score),
        "_grp": group,
    }


def rsi_extreme_signals(df: pd.DataFrame) -> list[dict]:
    """RSI>90 is overbought and RSI<10 is oversold; only confirmed exits are shown."""
    rsi = df["RSI6"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ma10 = df["MA10"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    out: list[dict] = []
    state: dict | None = None
    last = {"bear": -10_000, "bull": -10_000}

    for idx in range(1, len(df)):
        if not np.isfinite(rsi[idx]):
            continue
        if state is None:
            if rsi[idx] > 90:
                state = {"direction": "bear", "entry": idx, "extreme": float(rsi[idx]), "price": float(high[idx])}
            elif rsi[idx] < 10:
                state = {"direction": "bull", "entry": idx, "extreme": float(rsi[idx]), "price": float(low[idx])}
            continue

        direction = str(state["direction"])
        if direction == "bear":
            state["extreme"] = max(float(state["extreme"]), float(rsi[idx]))
            state["price"] = max(float(state["price"]), float(high[idx]))
            confirmed = rsi[idx] < 85 and close[idx] < ma10[idx] and close[idx] < close[idx - 1]
            expired = idx - int(state["entry"]) > 24 or rsi[idx] < 60
            if confirmed and idx - last[direction] >= 60:
                out.append(_event(
                    df, idx, "RSI超买", "bear",
                    f"RSI6最高{state['extreme']:.1f}（>90）后跌回85下方并失守MA10；"
                    f"前高{state['price']:.2f}收复则风险信号失效。",
                    62, f"rsi90:{idx}",
                ))
                last[direction] = idx
                state = None
            elif expired:
                state = None
        else:
            state["extreme"] = min(float(state["extreme"]), float(rsi[idx]))
            state["price"] = min(float(state["price"]), float(low[idx]))
            confirmed = rsi[idx] > 15 and close[idx] > ma10[idx] and close[idx] > close[idx - 1]
            expired = idx - int(state["entry"]) > 24 or rsi[idx] > 40
            if confirmed and idx - last[direction] >= 60:
                out.append(_event(
                    df, idx, "RSI超卖", "bull",
                    f"RSI6最低{state['extreme']:.1f}（<10）后升回15上方并站回MA10；"
                    f"前低{state['price']:.2f}失守则修复信号失效。",
                    62, f"rsi10:{idx}",
                ))
                last[direction] = idx
                state = None
            elif expired:
                state = None
    return out


def ema_regime_signals(df: pd.DataFrame) -> list[dict]:
    """State machine: entanglement -> expansion -> breakout -> locked trend."""
    close = df["close"].to_numpy(dtype=float)
    e20 = df["EMA20"].to_numpy(dtype=float)
    e60 = df["EMA60"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)
    vol = df["vol"].to_numpy(dtype=float)
    out: list[dict] = []
    locked: str | None = None
    pending: dict | None = None
    cool_until = -1

    for idx in range(70, len(df)):
        values = (close[idx], e20[idx], e60[idx], atr[idx], adx[idx])
        if not all(np.isfinite(v) for v in values) or atr[idx] <= 0:
            continue
        spread = abs(e20[idx] - e60[idx])
        entangled = spread < max(atr[idx] * 0.30, close[idx] * 0.0025)
        slope20 = e20[idx] - e20[idx - 8]
        slope60 = e60[idx] - e60[idx - 15]

        if locked is not None:
            broken = (
                entangled
                or (locked == "bull" and close[idx] < e60[idx] and slope20 < 0)
                or (locked == "bear" and close[idx] > e60[idx] and slope20 > 0)
            )
            if broken:
                locked = None
                pending = None
                cool_until = idx + 20
            continue
        if idx < cool_until or entangled:
            pending = None
            continue

        bull = close[idx] > e20[idx] > e60[idx] and slope20 > atr[idx] * 0.20 and slope60 > 0
        bear = close[idx] < e20[idx] < e60[idx] and slope20 < -atr[idx] * 0.20 and slope60 < 0
        direction = "bull" if bull else "bear" if bear else None
        if direction is None:
            pending = None
            continue
        if pending is None or pending["direction"] != direction:
            pending = {"direction": direction, "start": idx, "count": 1}
        else:
            pending["count"] += 1
        if int(pending["count"]) < 4:
            continue

        previous_high = float(np.max(df["high"].iloc[idx - 21:idx]))
        previous_low = float(np.min(df["low"].iloc[idx - 21:idx]))
        volume_base = float(np.mean(vol[idx - 20:idx]))
        volume_ratio = vol[idx] / volume_base if volume_base > 0 else 1.0
        adx_rising = adx[idx] >= 20 and adx[idx] >= adx[idx - 5]
        breakout = close[idx] > previous_high if direction == "bull" else close[idx] < previous_low
        evidence = int(adx_rising) + int(volume_ratio >= 1.15) + int(abs(e20[idx] - e60[idx]) >= atr[idx] * 0.55)
        if not breakout or evidence < 2:
            continue

        label = "趋势启动" if direction == "bull" else "趋势转弱"
        out.append(_event(
            df, idx, label, direction,
            f"EMA20/60持续张口4根K线并突破近20日区间；ADX、量能、均线扩张三项中{evidence}项确认。",
            78, f"ema_regime:{direction}:{idx}", kind="trend",
        ))
        locked = direction
        pending = None
    return out


def macd_divergence_signals(df: pd.DataFrame) -> list[dict]:
    """Only major pivot divergence confirmed by MA20 and histogram reversal."""
    pivots = piv_mod.zigzag(df, min_pct=float(np.clip(_median_atr_pct(df) * 2.5, 0.035, 0.07)))
    pivots = piv_mod.pivots_asof(pivots, len(df) - 1).to_dict("records")
    dif = df["DIF"].to_numpy(dtype=float)
    hist = df["MACD_HIST"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ma20 = df["MA20"].to_numpy(dtype=float)
    out: list[dict] = []
    last = {"bear": -10_000, "bull": -10_000}

    for kind, direction in (("H", "bear"), ("L", "bull")):
        points = [p for p in pivots if p["kind"] == kind]
        for pos in range(1, len(points)):
            first, second = points[pos - 1], points[pos]
            gap = int(second["idx"] - first["idx"])
            if not 30 <= gap <= 180:
                continue
            i1, i2 = int(first["idx"]), int(second["idx"])
            if not all(np.isfinite(x) for x in (dif[i1], dif[i2], first["price"], second["price"])):
                continue
            if direction == "bear":
                price_ok = float(second["price"]) >= float(first["price"]) * 0.985
                indicator_ok = dif[i2] <= dif[i1] - max(abs(dif[i1]) * 0.15, close[i2] * 0.001)
            else:
                price_ok = float(second["price"]) <= float(first["price"]) * 1.015
                indicator_ok = dif[i2] >= dif[i1] + max(abs(dif[i1]) * 0.15, close[i2] * 0.001)
            if not (price_ok and indicator_ok):
                continue
            start = max(i2 + 1, int(second["confirmed_at_idx"]))
            for idx in range(start, min(len(df), start + 15)):
                confirmed = (
                    close[idx] < ma20[idx] and hist[idx] < 0 if direction == "bear"
                    else close[idx] > ma20[idx] and hist[idx] > 0
                )
                if confirmed and idx - last[direction] >= 80:
                    label = "MACD顶背离" if direction == "bear" else "MACD底背离"
                    out.append(_event(
                        df, idx, label, direction,
                        f"价格主拐点未按MACD动能方向确认，且价格已{'跌破' if direction == 'bear' else '站回'}MA20。",
                        72, f"macd_div:{direction}:{idx}",
                    ))
                    last[direction] = idx
                    break
    return out


def _filter_fibonacci(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for raw in events:
        event = dict(raw)
        label = str(event.get("label") or "").replace("Fib", "").strip()
        if label not in {"0.5", "0.500", "0.618"}:
            continue
        event["label"] = "0.5" if label in {"0.5", "0.500"} else "0.618"
        event["lines"] = []
        event["zones"] = []
        event["polylines"] = []
        event["_score"] = 55
        event["_grp"] = f"fib:{event['label']}:{event.get('bar_idx')}"
        out.append(event)
    return out


def _select_indicator_events(events: list[dict]) -> list[dict]:
    priority = {"trend": 4, "MACD顶背离": 3, "MACD底背离": 3, "RSI超买": 2, "RSI超卖": 2}
    ordered = sorted(
        events,
        key=lambda e: (
            int(e.get("bar_idx", 0)),
            priority.get(str(e.get("label")), priority.get(str(e.get("kind")), 1)),
            int(e.get("_score", 0)),
        ),
    )
    kept: list[dict] = []
    for event in ordered:
        conflicts = [
            old for old in kept
            if old.get("direction") == event.get("direction")
            and abs(int(old.get("bar_idx", 0)) - int(event.get("bar_idx", 0))) <= 15
        ]
        if conflicts:
            candidates = conflicts + [event]
            best = max(candidates, key=lambda e: (
                priority.get(str(e.get("label")), priority.get(str(e.get("kind")), 1)),
                int(e.get("_score", 0)),
            ))
            if best is event:
                for old in conflicts:
                    kept.remove(old)
                kept.append(event)
            continue
        if kept and int(event.get("bar_idx", 0)) - int(kept[-1].get("bar_idx", 0)) < 35:
            continue
        kept.append(event)
    return kept[-MAX_INDICATOR_EVENTS:]


def _clean(events: list[dict]) -> list[dict]:
    clean: list[dict] = []
    for raw in sorted(events, key=lambda e: int(e.get("bar_idx", 0))):
        event = dict(raw)
        event["label"] = str(event.get("label") or "")[:8]
        event.pop("_score", None)
        event.pop("_grp", None)
        clean.append(event)
    return clean


def _summary(df: pd.DataFrame, pivots: pd.DataFrame, patterns: list[dict]) -> dict:
    summary = legacy._summary(df, pivots, patterns)
    close = float(df["close"].iloc[-1])
    e20 = float(df["EMA20"].iloc[-1])
    e60 = float(df["EMA60"].iloc[-1])
    adx = float(df["ADX"].iloc[-1]) if _finite(df["ADX"].iloc[-1]) else 0.0
    rsi = float(df["RSI6"].iloc[-1])
    dif = float(df["DIF"].iloc[-1])
    dea = float(df["DEA"].iloc[-1])

    if close > e20 > e60 and e20 > float(df["EMA20"].iloc[-9]):
        trend = f"中期多头，ADX={adx:.0f}"
    elif close < e20 < e60 and e20 < float(df["EMA20"].iloc[-9]):
        trend = f"中期空头，ADX={adx:.0f}"
    else:
        trend = f"趋势未确认，ADX={adx:.0f}"
    if rsi > 90:
        rsi_state = "极端超买"
    elif rsi < 10:
        rsi_state = "极端超卖"
    else:
        rsi_state = "非极端"
    momentum = f"RSI6={rsi:.1f}（{rsi_state}）；MACD动能{'偏多' if dif > dea else '偏空'}"
    latest = max(patterns, key=lambda e: int(e.get("confirm_idx", 0))) if patterns else None
    structure = (
        f"{_pattern_name(latest)}，{_date(df, int(latest['confirm_idx']))}确认"
        if latest else "当前无通过投资级筛选的大结构"
    )
    summary["trend"] = trend
    summary["momentum"] = momentum
    summary["structure"] = structure
    summary["outlook_text"] = (
        f"截至{_date(df, len(df)-1)}，{trend}。结构：{structure}。"
        f"动量：{momentum}。主图仅展示完成确认且达到时间、幅度与波动门槛的大结构；"
        "指标信号必须经过价格突破或趋势破坏确认，不把机械交叉视为交易结论。"
    )
    return summary


def analyze(df: pd.DataFrame, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 120:
        return {"annotations": [], "summary": {}, "diagnostics": {"analysis_version": ENGINE_VERSION}}

    pivots = piv_mod.find_pivots(d)
    patterns = find_investment_patterns(d, timeframe=timeframe)
    pattern_events = pattern_annotations(d, patterns)
    indicator_events = _select_indicator_events(
        ema_regime_signals(d) + macd_divergence_signals(d) + rsi_extreme_signals(d)
    )
    fib_events = _filter_fibonacci(fibonacci_history.find_fibonacci_touches(d, pivots))
    harmonic_events = harmonics_history.find_harmonic_annotations(d, pivots)

    annotations = _clean(pattern_events + indicator_events + fib_events + harmonic_events)
    return {
        "annotations": annotations,
        "summary": _summary(d, pivots, patterns),
        "diagnostics": {
            "analysis_version": ENGINE_VERSION,
            "asset_kind": "index" if str(asset_kind).lower() == "index" else "equity",
            "bars_scanned": len(d),
            "raw_patterns": len(pattern_mod.find_patterns(d, pivots, asof_bar=len(d)-1, timeframe=timeframe)),
            "patterns_displayed": len(patterns),
            "indicator_events": len(indicator_events),
            "fibonacci_events": len(fib_events),
            "harmonic_events": len(harmonic_events),
            "causal": True,
            "selection": "large_confirmed_non_overlapping",
        },
    }
