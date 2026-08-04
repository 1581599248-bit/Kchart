"""Production analysis adapter for the fully integrated investment-grade engine."""
from __future__ import annotations

from . import investment_engine_v14 as engine

ANALYSIS_VERSION = "analysis_v14.0-investment-grade"


def analyze(df, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    result = engine.analyze(df, timeframe=timeframe, asset_kind=asset_kind)
    diagnostics = result.setdefault("diagnostics", {})
    diagnostics["analysis_version"] = ANALYSIS_VERSION
    diagnostics["engine_version"] = engine.ENGINE_VERSION
    return result
