"""v13反转引擎：硬规则判定，同一触发区优先最新完成的结构。

多个嵌套M顶可能在同一天跌破不同颈线。交易解释应选择距离触发日最近、
右顶最新的完整H-L-H，而不是选择跨度更大或软评分更高的旧候选。
"""
from __future__ import annotations

import pandas as pd

from . import pivots as piv_mod
from . import reversal_patterns_v11 as _engine


def _overlap(a: dict, b: dict) -> float:
    a0, a1 = int(a["start_idx"]), int(a["confirm_idx"])
    b0, b1 = int(b["start_idx"]), int(b["confirm_idx"])
    inter = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return inter / shorter


def _same_trigger_story(a: dict, b: dict) -> bool:
    return (
        abs(int(a["confirm_idx"]) - int(b["confirm_idx"]))
        <= _engine.CLUSTER_CONFIRM_BARS
        or _overlap(a, b) >= _engine.CLUSTER_OVERLAP
    )


def _preference(event: dict) -> tuple[int, int, int, int]:
    """确认越新、右顶/右底越接近确认、结构起点越新，最后才看软评分。"""
    return (
        int(event["confirm_idx"]),
        int(event["end_idx"]),
        int(event["start_idx"]),
        int(event.get("score", 0)),
    )


def _raw_candidates(df: pd.DataFrame) -> list[dict]:
    old_threshold = _engine.MIN_SCORE
    try:
        _engine.MIN_SCORE = -1
        zz = piv_mod.zigzag(df, min_pct=_engine.INDEX_ZIGZAG_PCT)
        zz = piv_mod.pivots_asof(zz, len(df) - 1)
        points = zz.to_dict("records")
        candidates: list[dict] = []
        for i in range(2, len(points)):
            a, middle, b = points[i - 2:i + 1]
            kinds = f"{a['kind']}{middle['kind']}{b['kind']}"
            event = None
            if kinds == "HLH":
                event = _engine._candidate(df, a, middle, b, top=True)
            elif kinds == "LHL":
                event = _engine._candidate(df, a, middle, b, top=False)
            if event is not None:
                candidates.append(event)
        return candidates
    finally:
        _engine.MIN_SCORE = old_threshold


def _cluster_latest(candidates: list[dict]) -> list[dict]:
    ranked = sorted(candidates, key=_preference, reverse=True)
    selected: list[dict] = []
    for event in ranked:
        if any(_same_trigger_story(event, old) for old in selected):
            continue
        selected.append(event)
    return sorted(selected, key=lambda e: int(e["confirm_idx"]))


def find_index_reversals(df: pd.DataFrame) -> list[dict]:
    clustered = _cluster_latest(_raw_candidates(df))
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
