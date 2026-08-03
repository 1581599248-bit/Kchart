"""线程安全、严格因果的指数M顶/W底引擎。

生产只恢复这两类已经通过真实六指数回放的反转结构。有效性只取决于确认日
及以前的数据；软评分只用于同一区域候选排序，不参与是否成立的判断。
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from . import pivots as piv_mod

# 主级别拐点与几何硬规则。
ZIGZAG_PCT = 0.03
MIN_EXTREME_GAP = 15
MAX_EXTREME_GAP = 100
MIN_TOTAL_SPAN = 30
MAX_TOTAL_SPAN = 180
MAX_EXTREME_DIFF = 0.035
MIN_DEPTH_PCT = 0.03
MIN_DEPTH_ATR = 2.0
MIN_PRIOR_MOVE_PCT = 0.08
MIN_PRIOR_MOVE_ATR = 5.0
MIN_POSITION = 0.72

# 颈线确认：必须有决定性穿越，并连续两根收盘位于颈线外侧。
CONFIRM_PCT = 0.01
CONFIRM_ATR = 0.50
INVALID_PCT = 0.004
INVALID_ATR = 0.25

# 同一段行情只保留最新完成的解释；主图最多三段彼此分离的历史结构。
CLUSTER_CONFIRM_BARS = 45
CLUSTER_OVERLAP = 0.50
MIN_HISTORY_SEPARATION = 180
MAX_DISPLAY_EVENTS = 3


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(pd.to_datetime(df["trade_date"].iloc[int(idx)]).date())


def _atr(df: pd.DataFrame, idx: int) -> float:
    if "ATR14" not in df.columns:
        return float("nan")
    value = float(df["ATR14"].iloc[int(idx)])
    return value if math.isfinite(value) and value > 0 else float("nan")


def _position(df: pd.DataFrame, idx: int, price: float, is_top: bool) -> float:
    start = max(0, int(idx) - 251)
    high = float(df["high"].iloc[start:idx + 1].max())
    low = float(df["low"].iloc[start:idx + 1].min())
    if high <= low:
        return 0.5
    percentile = (price - low) / (high - low)
    return percentile if is_top else 1.0 - percentile


def _prior_move(df: pd.DataFrame, idx: int, price: float,
                is_top: bool) -> tuple[float, float]:
    start = max(0, int(idx) - 120)
    if is_top:
        reference = float(df["low"].iloc[start:idx + 1].min())
        absolute = price - reference
        relative = absolute / reference if reference > 0 else 0.0
    else:
        reference = float(df["high"].iloc[start:idx + 1].max())
        absolute = reference - price
        relative = absolute / price if price > 0 else 0.0
    atr = _atr(df, idx)
    return relative, absolute / atr if math.isfinite(atr) else float("nan")


def _find_confirm(df: pd.DataFrame, last_pivot: dict, neckline: float,
                  is_top: bool, invalidation: float) -> int | None:
    close = df["close"].to_numpy(dtype=float)
    atr = (
        df["ATR14"].to_numpy(dtype=float)
        if "ATR14" in df.columns else np.full(len(df), np.nan)
    )
    start = max(
        int(last_pivot["idx"]) + 1,
        int(last_pivot["confirmed_at_idx"]),
    )
    outside_count = 0
    for idx in range(start, len(df)):
        atr_value = float(atr[idx]) if math.isfinite(float(atr[idx])) else 0.0
        confirm_buffer = max(neckline * CONFIRM_PCT, atr_value * CONFIRM_ATR)
        invalid_buffer = max(invalidation * INVALID_PCT, atr_value * INVALID_ATR)
        if is_top:
            if close[idx] > invalidation + invalid_buffer:
                return None
            outside_count = outside_count + 1 if close[idx] < neckline else 0
            decisive = close[idx] <= neckline - confirm_buffer
        else:
            if close[idx] < invalidation - invalid_buffer:
                return None
            outside_count = outside_count + 1 if close[idx] > neckline else 0
            decisive = close[idx] >= neckline + confirm_buffer
        if decisive and outside_count >= 2:
            return idx
    return None


def _soft_score(extreme_diff: float, depth_pct: float, depth_atr: float,
                prior_pct: float, prior_atr: float, position: float,
                left_gap: int, right_gap: int, confirm_atr: float) -> int:
    """只用于同区间排序；任何分数都不能否决通过硬规则的结构。"""
    similarity = max(0.0, 1.0 - extreme_diff / MAX_EXTREME_DIFF) * 24.0
    depth = min(1.0, depth_pct / 0.08) * 16.0
    depth_vol = min(1.0, depth_atr / 5.0) * 10.0 if math.isfinite(depth_atr) else 5.0
    prior = min(1.0, prior_pct / 0.20) * 12.0
    prior_vol = min(1.0, prior_atr / 10.0) * 8.0 if math.isfinite(prior_atr) else 4.0
    position_score = min(1.0, max(0.0, (position - MIN_POSITION) / (1.0 - MIN_POSITION))) * 10.0
    symmetry = min(left_gap, right_gap) / max(left_gap, right_gap)
    confirmation = min(1.0, max(0.0, confirm_atr) / 1.5) * 10.0
    return int(round(
        similarity + depth + depth_vol + prior + prior_vol
        + position_score + symmetry * 10.0 + confirmation
    ))


def _trace(df: pd.DataFrame, first: dict, middle: dict, second: dict,
           confirm_idx: int, neckline: float) -> list[dict]:
    return [
        {
            "points": [
                {"t": _date(df, int(first["idx"])), "p": round(float(first["price"]), 4)},
                {"t": _date(df, int(middle["idx"])), "p": round(float(middle["price"]), 4)},
                {"t": _date(df, int(second["idx"])), "p": round(float(second["price"]), 4)},
            ],
            "style": "solid",
        },
        {
            "points": [
                {"t": _date(df, int(middle["idx"])), "p": round(neckline, 4)},
                {"t": _date(df, confirm_idx), "p": round(neckline, 4)},
            ],
            "style": "dashed",
        },
    ]


def _candidate(df: pd.DataFrame, first: dict, middle: dict, second: dict,
               is_top: bool) -> dict | None:
    extreme_gap = int(second["idx"] - first["idx"])
    if not (MIN_EXTREME_GAP <= extreme_gap <= MAX_EXTREME_GAP):
        return None
    left_gap = int(middle["idx"] - first["idx"])
    right_gap = int(second["idx"] - middle["idx"])
    if min(left_gap, right_gap) < 4:
        return None

    p1 = float(first["price"])
    neckline = float(middle["price"])
    p2 = float(second["price"])
    average = (p1 + p2) / 2.0
    if average <= 0:
        return None
    extreme_diff = abs(p2 - p1) / average
    if extreme_diff > MAX_EXTREME_DIFF:
        return None

    if is_top:
        reference_extreme = min(p1, p2)
        depth_abs = reference_extreme - neckline
        invalidation = max(p1, p2)
    else:
        reference_extreme = max(p1, p2)
        depth_abs = neckline - reference_extreme
        invalidation = min(p1, p2)
    if depth_abs <= 0 or reference_extreme <= 0:
        return None
    depth_pct = depth_abs / reference_extreme
    atr_at_first = _atr(df, int(first["idx"]))
    depth_atr = depth_abs / atr_at_first if math.isfinite(atr_at_first) else float("nan")
    if depth_pct < MIN_DEPTH_PCT:
        return None
    if math.isfinite(depth_atr) and depth_atr < MIN_DEPTH_ATR:
        return None

    position = _position(df, int(first["idx"]), invalidation, is_top)
    if position < MIN_POSITION:
        return None
    prior_pct, prior_atr = _prior_move(df, int(first["idx"]), p1, is_top)
    if prior_pct < MIN_PRIOR_MOVE_PCT:
        return None
    if math.isfinite(prior_atr) and prior_atr < MIN_PRIOR_MOVE_ATR:
        return None

    confirm_idx = _find_confirm(df, second, neckline, is_top, invalidation)
    if confirm_idx is None:
        return None
    total_span = confirm_idx - int(first["idx"])
    if not (MIN_TOTAL_SPAN <= total_span <= MAX_TOTAL_SPAN):
        return None

    atr_at_confirm = _atr(df, confirm_idx)
    close_at_confirm = float(df["close"].iloc[confirm_idx])
    confirm_atr = (
        abs(close_at_confirm - neckline) / atr_at_confirm
        if math.isfinite(atr_at_confirm) else 0.0
    )
    score = _soft_score(
        extreme_diff, depth_pct, depth_atr, prior_pct, prior_atr,
        position, left_gap, right_gap, confirm_atr,
    )

    name = "M顶" if is_top else "W底"
    kind = "double_top" if is_top else "double_bottom"
    direction = "bear" if is_top else "bull"
    target = neckline - depth_abs if is_top else neckline + depth_abs
    note = (
        f"{name}：主拐点{_date(df, int(first['idx']))}—"
        f"{_date(df, int(middle['idx']))}—{_date(df, int(second['idx']))}；"
        f"双{'顶' if is_top else '底'}差{extreme_diff:.1%}，结构深度{depth_pct:.1%}，"
        f"{_date(df, confirm_idx)}收盘确认{'跌破' if is_top else '突破'}"
        f"真实颈线{neckline:.2f}。"
    )
    return {
        "kind": kind,
        "name": name,
        "direction": direction,
        "start_idx": int(first["idx"]),
        "middle_idx": int(middle["idx"]),
        "end_idx": int(second["idx"]),
        "confirm_idx": int(confirm_idx),
        "key_levels": {
            "neckline": round(neckline, 4),
            "measure_target": round(target, 4),
            "invalidation": round(invalidation, 4),
            "extreme1": round(p1, 4),
            "extreme2": round(p2, 4),
        },
        "score": score,
        "star": True,
        "note": note,
        "trace": _trace(df, first, middle, second, confirm_idx, neckline),
        "active": confirm_idx >= len(df) - 160,
        "causal": True,
    }


def find_confirmed_candidates(df: pd.DataFrame) -> list[dict]:
    """返回全部通过硬规则的已确认候选，不做事后结果筛选。"""
    zigzag = piv_mod.zigzag(df, min_pct=ZIGZAG_PCT)
    zigzag = piv_mod.pivots_asof(zigzag, len(df) - 1)
    points = zigzag.to_dict("records")
    events: list[dict] = []
    for idx in range(2, len(points)):
        first, middle, second = points[idx - 2:idx + 1]
        kinds = f"{first['kind']}{middle['kind']}{second['kind']}"
        event = None
        if kinds == "HLH":
            event = _candidate(df, first, middle, second, is_top=True)
        elif kinds == "LHL":
            event = _candidate(df, first, middle, second, is_top=False)
        if event is not None:
            events.append(event)
    return events


def _overlap(a: dict, b: dict) -> float:
    a0, a1 = int(a["start_idx"]), int(a["confirm_idx"])
    b0, b1 = int(b["start_idx"]), int(b["confirm_idx"])
    intersection = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return intersection / shorter


def _same_story(a: dict, b: dict) -> bool:
    return (
        abs(int(a["confirm_idx"]) - int(b["confirm_idx"]))
        <= CLUSTER_CONFIRM_BARS
        or _overlap(a, b) >= CLUSTER_OVERLAP
    )


def _preference(event: dict) -> tuple[int, int, int, int]:
    """同一触发区优先最新右顶/右底，其次确认日、起点和软评分。"""
    return (
        int(event["end_idx"]),
        int(event["confirm_idx"]),
        int(event["start_idx"]),
        int(event.get("score", 0)),
    )


def _cluster_latest(events: Iterable[dict]) -> list[dict]:
    selected: list[dict] = []
    for event in sorted(events, key=_preference, reverse=True):
        if any(_same_story(event, old) for old in selected):
            continue
        selected.append(dict(event))
    return sorted(selected, key=lambda e: int(e["confirm_idx"]))


def find_index_reversals(df: pd.DataFrame) -> list[dict]:
    """主图最多返回三段彼此分离、已确认且严格因果的M顶/W底。"""
    clustered = _cluster_latest(find_confirmed_candidates(df))
    chosen: list[dict] = []
    for event in sorted(clustered, key=_preference, reverse=True):
        if any(
            abs(int(event["confirm_idx"]) - int(old["confirm_idx"]))
            < MIN_HISTORY_SEPARATION
            for old in chosen
        ):
            continue
        chosen.append(event)
        if len(chosen) >= MAX_DISPLAY_EVENTS:
            break
    return sorted(chosen, key=lambda e: int(e["confirm_idx"]))
