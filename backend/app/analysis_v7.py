"""Production analysis adapter for the high-recall M/W geometry engine."""
from __future__ import annotations

from . import investment_engine_v15 as engine

ANALYSIS_VERSION = "analysis_v15.0-mw-recall"


def analyze(df, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    result = engine.analyze(df, timeframe=timeframe, asset_kind=asset_kind)
    diagnostics = result.setdefault("diagnostics", {})
    diagnostics["analysis_version"] = ANALYSIS_VERSION
    diagnostics["engine_version"] = engine.ENGINE_VERSION
    return result
