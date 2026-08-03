"""v12反转引擎入口：硬规则决定有效性，评分只用于候选排序。

v11的几何、颈线、确认、历史跟随和聚类逻辑保持不变；调用期间关闭旧的
MIN_SCORE二次否决，避免已经通过全部硬规则的真实结构因时间对称性或右顶
略低等软特征被错误删除。评分仍保留在事件中，仅供同区域候选排序。
"""
from __future__ import annotations

import pandas as pd

from . import reversal_patterns_v11 as _engine


def find_index_reversals(df: pd.DataFrame) -> list[dict]:
    old_threshold = _engine.MIN_SCORE
    try:
        _engine.MIN_SCORE = -1
        return _engine.find_index_reversals(df)
    finally:
        _engine.MIN_SCORE = old_threshold
