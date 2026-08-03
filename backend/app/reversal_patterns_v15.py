"""v15因果反转引擎：后续结果不得决定历史形态是否成立。

沿用v14严格颈线确认与最新右顶优先；候选生成期间关闭v11的事后目标筛选。
形态有效性只使用确认日及以前数据。确认后的收益、目标命中和失效只允许在
独立评估脚本中统计，不参与生产候选选择。
"""
from __future__ import annotations

import pandas as pd

from . import reversal_patterns_v11 as _engine
from . import reversal_patterns_v14 as _strict


def _neutral_outcome(df: pd.DataFrame, event: dict) -> _engine.Outcome:
    del df, event
    return _engine.Outcome(False, False, None, None)


def _raw_candidates_causal(df: pd.DataFrame) -> list[dict]:
    old_score = _engine.MIN_SCORE
    old_confirm = _engine._find_confirm
    old_outcome = _engine._outcome
    old_lookahead = _engine.HISTORY_LOOKAHEAD
    try:
        _engine.MIN_SCORE = -1
        _engine._find_confirm = _strict._strict_confirm
        _engine._outcome = _neutral_outcome
        # 防止v11基于当前时点年龄删除未命中目标的历史候选。
        _engine.HISTORY_LOOKAHEAD = len(df) + 1
        return _strict._raw_candidates(df)
    finally:
        _engine.MIN_SCORE = old_score
        _engine._find_confirm = old_confirm
        _engine._outcome = old_outcome
        _engine.HISTORY_LOOKAHEAD = old_lookahead


def find_index_reversals(df: pd.DataFrame) -> list[dict]:
    clustered = _strict._cluster(_raw_candidates_causal(df))
    chosen: list[dict] = []
    for event in sorted(clustered, key=_strict._preference, reverse=True):
        if any(
            abs(int(event["confirm_idx"]) - int(old["confirm_idx"]))
            < _engine.MIN_HISTORY_SEPARATION
            for old in chosen
        ):
            continue
        item = dict(event)
        item.pop("outcome", None)
        item.pop("validated_history", None)
        item["causal"] = True
        chosen.append(item)
        if len(chosen) >= _engine.MAX_DISPLAY_EVENTS:
            break
    return sorted(chosen, key=lambda e: int(e["confirm_idx"]))
