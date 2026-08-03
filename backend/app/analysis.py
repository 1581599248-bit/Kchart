"""兼容入口：旧 /api/analysis 统一代理到安全生产引擎。

旧接口没有传入标的类型，因此默认按个股安全模式处理；指数完整反转结构由
/api/chart 统一接口返回。该代理确保任何降级路径都不会重新输出旧EMA交叉、
结构失效或未经验证的经典形态。
"""
from __future__ import annotations

import pandas as pd

from . import analysis_v7 as _engine

ANALYSIS_VERSION = _engine.ANALYSIS_VERSION


def analyze(df: pd.DataFrame, timeframe: str = "1d",
            asset_kind: str = "equity") -> dict:
    return _engine.analyze(df, timeframe, asset_kind=asset_kind)
