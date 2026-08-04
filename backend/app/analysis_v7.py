"""Production analysis adapter: 结构识别引擎 v16（大级别优先·严格几何·全因果）。"""
from __future__ import annotations

from . import structure_engine_v16 as engine

ANALYSIS_VERSION = "analysis_v16.0-structure-first"


def analyze(df, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    result = engine.analyze(df, timeframe=timeframe, asset_kind=asset_kind)
    diagnostics = result.setdefault("diagnostics", {})
    diagnostics["analysis_version"] = ANALYSIS_VERSION
    diagnostics["engine_version"] = engine.ENGINE_VERSION
    return result
