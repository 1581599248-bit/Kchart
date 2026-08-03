"""v14指数反转引擎：严格颈线确认，最新右顶优先。

确认规则：收盘穿越颈线至少1%或0.5ATR，并且连续两根收盘位于颈线外侧。
同一触发区的嵌套结构，优先选择右顶/右底日期最新者，再比较确认日期与评分。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import pivots as piv_mod
from . import reversal_patterns_v11 as _engine

CONFIRM_PCT = 0.01
CONFIRM_ATR = 0.50
INVALID_PCT = 0.004
INVALID_ATR = 0.25


def _strict_confirm(df: pd.DataFrame, last: dict, neckline: float, top: bool,
                    invalidation: float) -> int | None:
    close = df["close"].to_numpy(dtype=float)
    atr = (
        df["ATR14"].to_numpy(dtype=float)
        if "ATR14" in df.columns else np.full(len(df), np.nan)
    )
    start = max(int(last["idx"]) + 1, int(last["confirmed_at_idx"]))
    outside_count = 0
    for i in range(start, len(df)):
        atr_i = float(atr[i]) if math.isfinite(float(atr[i])) else 0.0
        confirm_buffer = max(neckline * CONFIRM_PCT, atr_i * CONFIRM_ATR)
        invalid_buffer = max(invalidation * INVALID_PCT, atr_i * INVALID_ATR)
        if top:
            if close[i] > invalidation + invalid_buffer:
                return None
            outside_count = outside_count + 1 if close[i] < neckline else 0
            decisive = close[i] <= neckline - confirm_buffer
        else:
            if close[i] < invalidation - invalid_buffer:
                return None
            outside_count = outside_count + 1 if close[i] > neckline else 0
            decisive = close[i] >= neckline + confirm_buffer
        if decisive and outside_count >= 2:
            return i
    return None


def _overlap(a: dict, b: dict) -> float:
    a0, a1 = int(a["start_idx"]), int(a["confirm_idx"])
    b0, b1 = int(b["start_idx"]), int(b["confirm_idx"])
    inter = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return inter / shorter


def _same_story(a: dict, b: dict) -> bool:
    return (
        abs(int(a["confirm_idx"]) - int(b["confirm_idx"]))
        <= _engine.CLUSTER_CONFIRM_BARS
        or _overlap(a, b) >= _engine.CLUSTER_OVERLAP
    )


def _preference(event: dict) -> tuple[int, int, int, int]:
    return (
        int(event["end_idx"]),
        int(event["confirm_idx"]),
        int(event["start_idx"]),
        int(event.get("score", 0)),
    )


def _raw_candidates(df: pd.DataFrame) -> list[dict]:
    old_score = _engine.MIN_SCORE
    old_confirm = _engine._find_confirm
    try:
        _engine.MIN_SCORE = -1
        _engine._find_confirm = _strict_confirm
        zz = piv_mod.zigzag(df, min_pct=_engine.INDEX_ZIGZAG_PCT)
        zz = piv_mod.pivots_asof(zz, len(df) - 1)
        points = zz.to_dict("records")
        out: list[dict] = []
        for i in range(2, len(points)):
            a, middle, b = points[i - 2:i + 1]
            kinds = f"{a['kind']}{middle['kind']}{b['kind']}"
            event = None
            if kinds == "HLH":
                event = _engine._candidate(df, a, middle, b, top=True)
            elif kinds == "LHL":
                event = _engine._candidate(df, a, middle, b, top=False)
            if event is not None:
                out.append(event)
        return out
    finally:
        _engine.MIN_SCORE = old_score
        _engine._find_confirm = old_confirm


def _cluster(candidates: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for event in sorted(candidates, key=_preference, reverse=True):
        if any(_same_story(event, old) for old in selected):
            continue
        selected.append(event)
    return sorted(selected, key=lambda e: int(e["confirm_idx"]))


def find_index_reversals(df: pd.DataFrame) -> list[dict]:
    clustered = _cluster(_raw_candidates(df))
    chosen: list[dict] = []
    for event in sorted(clustered, key=_preference, reverse=True):
        if any(
            abs(int(event["confirm_idx"]) - int(old["confirm_idx"]))
            < _engine.MIN_HISTORY_SEPARATION
            for old in chosen
        ):
            continue
        chosen.append(event)
        if len(chosen) >= _engine.MAX_DISPLAY_EVENTS:
            break
    return sorted(chosen, key=lambda e: int(e["confirm_idx"]))
