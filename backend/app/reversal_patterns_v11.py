"""v11指数反转结构：只识别确认后的M顶与W底。

设计原则：
- 使用3%主级别ZigZag，避免5日局部pivot制造大量嵌套候选；
- 形态必须是连续H-L-H或L-H-L主拐点；
- 颈线固定为真实中间拐点，不拟合、不平移；
- 只在收盘有效突破颈线后确认；
- 历史形态必须先达到半个量度目标，未产生真实跟随的旧候选不显示；
- 同一段行情只保留一个最高质量解释。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import pivots as piv_mod

INDEX_ZIGZAG_PCT = 0.03
MIN_PEAK_GAP = 15
MAX_PEAK_GAP = 100
MIN_TOTAL_SPAN = 30
MAX_TOTAL_SPAN = 180
MAX_EXTREME_DIFF = 0.035
MIN_DEPTH_PCT = 0.03
MIN_DEPTH_ATR = 2.0
MIN_PRIOR_MOVE_PCT = 0.08
MIN_PRIOR_MOVE_ATR = 5.0
MIN_POSITION = 0.72
CONFIRM_PCT = 0.006
CONFIRM_ATR = 0.35
HISTORY_LOOKAHEAD = 80
HISTORY_TARGET_FRACTION = 0.50
MIN_SCORE = 76
CLUSTER_CONFIRM_BARS = 45
CLUSTER_OVERLAP = 0.50
MAX_DISPLAY_EVENTS = 3
MIN_HISTORY_SEPARATION = 180


@dataclass(frozen=True)
class Outcome:
    target_hit: bool
    invalidated: bool
    target_idx: int | None
    invalid_idx: int | None


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(pd.to_datetime(df["trade_date"].iloc[int(idx)]).date())


def _atr(df: pd.DataFrame, idx: int) -> float:
    if "ATR14" not in df.columns:
        return float("nan")
    value = float(df["ATR14"].iloc[int(idx)])
    return value if math.isfinite(value) and value > 0 else float("nan")


def _trailing_position(df: pd.DataFrame, idx: int, price: float, top: bool) -> float:
    start = max(0, int(idx) - 251)
    high = float(df["high"].iloc[start:idx + 1].max())
    low = float(df["low"].iloc[start:idx + 1].min())
    if high <= low:
        return 0.5
    pos = (price - low) / (high - low)
    return pos if top else 1.0 - pos


def _prior_move(df: pd.DataFrame, idx: int, price: float, top: bool) -> tuple[float, float]:
    start = max(0, int(idx) - 120)
    if top:
        ref = float(df["low"].iloc[start:idx + 1].min())
        move = price - ref
        pct = move / ref if ref > 0 else 0.0
    else:
        ref = float(df["high"].iloc[start:idx + 1].max())
        move = ref - price
        pct = move / price if price > 0 else 0.0
    atr = _atr(df, idx)
    return pct, move / atr if math.isfinite(atr) else float("nan")


def _find_confirm(df: pd.DataFrame, last: dict, neckline: float, top: bool,
                  invalidation: float) -> int | None:
    close = df["close"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float) if "ATR14" in df.columns else np.full(len(df), np.nan)
    start = max(int(last["idx"]) + 1, int(last["confirmed_at_idx"]))
    below_count = 0
    above_count = 0
    for i in range(start, len(df)):
        a = float(atr[i]) if math.isfinite(float(atr[i])) else 0.0
        confirm_buffer = max(neckline * CONFIRM_PCT, a * CONFIRM_ATR)
        invalid_buffer = max(invalidation * 0.004, a * 0.25)
        if top:
            if close[i] > invalidation + invalid_buffer:
                return None
            below_count = below_count + 1 if close[i] < neckline else 0
            if close[i] <= neckline - confirm_buffer or below_count >= 2:
                return i
        else:
            if close[i] < invalidation - invalid_buffer:
                return None
            above_count = above_count + 1 if close[i] > neckline else 0
            if close[i] >= neckline + confirm_buffer or above_count >= 2:
                return i
    return None


def _outcome(df: pd.DataFrame, event: dict) -> Outcome:
    confirm = int(event["confirm_idx"])
    levels = event["key_levels"]
    target = float(levels["validation_target"])
    invalidation = float(levels["invalidation"])
    direction = str(event["direction"])
    close = df["close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    end = min(len(df), confirm + HISTORY_LOOKAHEAD + 1)
    for i in range(confirm + 1, end):
        if direction == "bear":
            if high[i] > invalidation:
                return Outcome(False, True, None, i)
            if low[i] <= target:
                return Outcome(True, False, i, None)
        else:
            if low[i] < invalidation:
                return Outcome(False, True, None, i)
            if high[i] >= target:
                return Outcome(True, False, i, None)
    return Outcome(False, False, None, None)


def _score(extreme_diff: float, depth_pct: float, depth_atr: float,
           prior_pct: float, prior_atr: float, position: float,
           left_gap: int, right_gap: int, confirm_strength_atr: float) -> int:
    similarity = max(0.0, 1.0 - extreme_diff / MAX_EXTREME_DIFF) * 24.0
    depth = min(1.0, depth_pct / 0.08) * 16.0
    depth_vol = min(1.0, depth_atr / 5.0) * 10.0 if math.isfinite(depth_atr) else 5.0
    prior = min(1.0, prior_pct / 0.20) * 12.0
    prior_vol = min(1.0, prior_atr / 10.0) * 8.0 if math.isfinite(prior_atr) else 4.0
    pos = min(1.0, max(0.0, (position - MIN_POSITION) / (1.0 - MIN_POSITION))) * 10.0
    symmetry = min(left_gap, right_gap) / max(left_gap, right_gap)
    symmetry_score = symmetry * 10.0
    confirm = min(1.0, max(0.0, confirm_strength_atr) / 1.5) * 10.0
    return int(round(similarity + depth + depth_vol + prior + prior_vol + pos + symmetry_score + confirm))


def _trace(df: pd.DataFrame, a: dict, middle: dict, b: dict,
           confirm: int, neckline: float) -> list[dict]:
    return [
        {
            "points": [
                {"t": _date(df, int(a["idx"])), "p": round(float(a["price"]), 4)},
                {"t": _date(df, int(middle["idx"])), "p": round(float(middle["price"]), 4)},
                {"t": _date(df, int(b["idx"])), "p": round(float(b["price"]), 4)},
            ],
            "style": "solid",
        },
        {
            "points": [
                {"t": _date(df, int(middle["idx"])), "p": round(neckline, 4)},
                {"t": _date(df, confirm), "p": round(neckline, 4)},
            ],
            "style": "dashed",
        },
    ]


def _candidate(df: pd.DataFrame, a: dict, middle: dict, b: dict, top: bool) -> dict | None:
    peak_gap = int(b["idx"] - a["idx"])
    if not (MIN_PEAK_GAP <= peak_gap <= MAX_PEAK_GAP):
        return None
    left_gap = int(middle["idx"] - a["idx"])
    right_gap = int(b["idx"] - middle["idx"])
    if min(left_gap, right_gap) < 4:
        return None

    p1, pm, p2 = float(a["price"]), float(middle["price"]), float(b["price"])
    ref = (p1 + p2) / 2.0
    extreme_diff = abs(p2 - p1) / ref if ref > 0 else 1.0
    if extreme_diff > MAX_EXTREME_DIFF:
        return None

    if top:
        extreme = min(p1, p2)
        depth_abs = extreme - pm
        invalidation = max(p1, p2)
    else:
        extreme = max(p1, p2)
        depth_abs = pm - extreme
        invalidation = min(p1, p2)
    if depth_abs <= 0:
        return None
    depth_pct = depth_abs / extreme
    atr_ref = _atr(df, int(a["idx"]))
    depth_atr = depth_abs / atr_ref if math.isfinite(atr_ref) else float("nan")
    if depth_pct < MIN_DEPTH_PCT or (math.isfinite(depth_atr) and depth_atr < MIN_DEPTH_ATR):
        return None

    position = _trailing_position(df, int(a["idx"]), invalidation, top)
    if position < MIN_POSITION:
        return None
    prior_pct, prior_atr = _prior_move(df, int(a["idx"]), p1, top)
    if prior_pct < MIN_PRIOR_MOVE_PCT or (math.isfinite(prior_atr) and prior_atr < MIN_PRIOR_MOVE_ATR):
        return None

    neckline = pm
    confirm = _find_confirm(df, b, neckline, top, invalidation)
    if confirm is None:
        return None
    total_span = int(confirm - int(a["idx"]))
    if not (MIN_TOTAL_SPAN <= total_span <= MAX_TOTAL_SPAN):
        return None

    atr_confirm = _atr(df, confirm)
    close_confirm = float(df["close"].iloc[confirm])
    confirm_strength = abs(close_confirm - neckline) / atr_confirm if math.isfinite(atr_confirm) else 0.0
    score = _score(extreme_diff, depth_pct, depth_atr, prior_pct, prior_atr,
                   position, left_gap, right_gap, confirm_strength)
    if score < MIN_SCORE:
        return None

    full_target = neckline - depth_abs if top else neckline + depth_abs
    validation_target = (
        neckline - depth_abs * HISTORY_TARGET_FRACTION
        if top else neckline + depth_abs * HISTORY_TARGET_FRACTION
    )
    direction = "bear" if top else "bull"
    name = "M顶" if top else "W底"
    kind = "double_top" if top else "double_bottom"
    note = (
        f"{name}：主拐点{_date(df, int(a['idx']))}—{_date(df, int(middle['idx']))}—"
        f"{_date(df, int(b['idx']))}；双{'顶' if top else '底'}差{extreme_diff:.1%}，"
        f"结构深度{depth_pct:.1%}，{_date(df, confirm)}收盘确认"
        f"{'跌破' if top else '突破'}颈线{neckline:.2f}。"
    )
    event = {
        "kind": kind,
        "name": name,
        "direction": direction,
        "start_idx": int(a["idx"]),
        "end_idx": int(b["idx"]),
        "confirm_idx": int(confirm),
        "key_levels": {
            "neckline": round(neckline, 4),
            "measure_target": round(full_target, 4),
            "validation_target": round(validation_target, 4),
            "invalidation": round(invalidation, 4),
            "extreme1": round(p1, 4),
            "extreme2": round(p2, 4),
        },
        "score": score,
        "star": True,
        "note": note,
        "trace": _trace(df, a, middle, b, confirm, neckline),
        "active": confirm >= len(df) - 160,
        "validated_history": False,
    }
    outcome = _outcome(df, event)
    event["validated_history"] = outcome.target_hit
    event["outcome"] = {
        "target_hit": outcome.target_hit,
        "invalidated": outcome.invalidated,
        "target_idx": outcome.target_idx,
        "invalid_idx": outcome.invalid_idx,
    }
    # 已有足够观察期但既未达到半目标、又不再活跃的旧结构不进入候选集。
    age = len(df) - 1 - confirm
    if age > HISTORY_LOOKAHEAD and not outcome.target_hit:
        return None
    if outcome.invalidated and not outcome.target_hit:
        return None
    return event


def _overlap(a: dict, b: dict) -> float:
    a0, a1 = int(a["start_idx"]), int(a["confirm_idx"])
    b0, b1 = int(b["start_idx"]), int(b["confirm_idx"])
    inter = max(0, min(a1, b1) - max(a0, b0) + 1)
    short = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return inter / short


def _cluster(events: list[dict]) -> list[dict]:
    ranked = sorted(events, key=lambda e: (int(e["score"]), int(e["confirm_idx"])), reverse=True)
    selected: list[dict] = []
    for event in ranked:
        conflict = any(
            abs(int(event["confirm_idx"]) - int(old["confirm_idx"])) <= CLUSTER_CONFIRM_BARS
            or _overlap(event, old) >= CLUSTER_OVERLAP
            for old in selected
        )
        if not conflict:
            selected.append(event)
    return sorted(selected, key=lambda e: int(e["confirm_idx"]))


def find_index_reversals(df: pd.DataFrame) -> list[dict]:
    """返回少量、已确认、非重叠的指数M顶/W底。"""
    zz = piv_mod.zigzag(df, min_pct=INDEX_ZIGZAG_PCT)
    zz = piv_mod.pivots_asof(zz, len(df) - 1)
    points = zz.to_dict("records")
    candidates: list[dict] = []
    for i in range(2, len(points)):
        a, middle, b = points[i - 2:i + 1]
        kinds = f"{a['kind']}{middle['kind']}{b['kind']}"
        event = None
        if kinds == "HLH":
            event = _candidate(df, a, middle, b, top=True)
        elif kinds == "LHL":
            event = _candidate(df, a, middle, b, top=False)
        if event is not None:
            candidates.append(event)

    clustered = _cluster(candidates)
    # 最新结构优先；更早历史结构必须彼此至少相隔约9个月。
    chosen: list[dict] = []
    for event in sorted(clustered, key=lambda e: int(e["confirm_idx"]), reverse=True):
        if chosen and any(abs(int(event["confirm_idx"]) - int(x["confirm_idx"])) < MIN_HISTORY_SEPARATION for x in chosen):
            continue
        chosen.append(event)
        if len(chosen) >= MAX_DISPLAY_EVENTS:
            break
    return sorted(chosen, key=lambda e: int(e["confirm_idx"]))
