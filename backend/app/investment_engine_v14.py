"""Final integration layer for investment-grade technical structures.

Candidate coverage:
- macro M/W reversals with clustered necklines;
- strict head-and-shoulders tops plus large legacy bottom/reversal candidates;
- directionally named flags, wedges, triangles and rectangles;
- pivot-anchored boundary validation for all consolidation structures;
- strict completed up/down impulses and up/down ABC corrections;
- strict six-point broadening breaks.

Only confirmed, large, non-overlapping structures reach the main chart.  The
underlying recognizers remain available as candidates and are never accepted by
post-confirmation performance.
"""
from __future__ import annotations

import pandas as pd

from . import investment_engine_v12 as rules
from . import investment_engine_v13 as macro
from . import pattern_geometry_v9
from . import pattern_taxonomy_v8
from . import strict_tops_v7
from . import structures_v7
from . import waves_v7

ENGINE_VERSION = "investment_engine_v14.0"
MIN_REVERSAL_BARS = macro.MIN_REVERSAL_BARS
MIN_CONTINUATION_BARS = macro.MIN_CONTINUATION_BARS
MIN_WAVE_BARS = macro.MIN_WAVE_BARS
MAX_PATTERN_EVENTS = macro.MAX_PATTERN_EVENTS
MAX_INDICATOR_EVENTS = macro.MAX_INDICATOR_EVENTS
PATTERN_OVERLAP_LIMIT = macro.PATTERN_OVERLAP_LIMIT

_large_pattern = macro._large_pattern
_pattern_overlap = macro._pattern_overlap
_filter_fibonacci = macro._filter_fibonacci

# Local priority uses the canonical directional kinds produced by taxonomy_v8.
_PRIORITY = {
    "macro_double_top": 140, "macro_double_bottom": 140,
    "head_shoulders_top": 132, "head_shoulders_bottom": 132,
    "triple_top": 126, "triple_bottom": 126,
    "arc_top": 120, "arc_bottom": 120,
    "wave_impulse_up": 112, "wave_impulse_down": 112,
    "wave_abc_up": 106, "wave_abc_down": 106,
    "broadening_break": 98,
    "bullish_triangle_directional": 94,
    "bearish_triangle_directional": 94,
    "symmetric_triangle_directional": 90,
    "bull_wedge_directional": 92, "bear_wedge_directional": 92,
    "bull_flag_directional": 88, "bear_flag_directional": 88,
    "bull_rectangle": 82, "bear_rectangle": 82,
    "trendline_break": 64,
}

# Replaced by strict implementations in this integration layer.
_WEAK_WAVE_KINDS = {"wave_up5", "wave_down_abc"}
_WEAK_TOP_KINDS = {"double_top", "head_shoulders_top"}
_WEAK_BROADENING_KINDS = {"broadening_triangle"}


def _rank(df: pd.DataFrame, event: dict) -> tuple[float, int, int]:
    amplitude_pct, amplitude_atr = rules._event_amplitude(df, event)
    span = int(event.get("confirm_idx", 0)) - int(event.get("start_idx", 0))
    atr_bonus = min(float(amplitude_atr), 12.0) if rules._finite(amplitude_atr) else 0.0
    age = len(df) - 1 - int(event.get("confirm_idx", 0))
    recent_bonus = max(0.0, 20.0 - age / 16.0)
    quality = (
        float(_PRIORITY.get(str(event.get("kind") or ""), 50))
        + amplitude_pct * 100.0 + atr_bonus + min(span, 240) / 18.0
        + recent_bonus + float(event.get("score", 0)) / 20.0
    )
    return quality, int(event.get("confirm_idx", 0)), int(event.get("score", 0))


def _strict_candidates(df: pd.DataFrame, pivots: pd.DataFrame,
                       timeframe: str) -> list[dict]:
    legacy = rules.pattern_mod.find_patterns(
        df, pivots, asof_bar=len(df) - 1, timeframe=timeframe
    )
    legacy += rules.patterns_ext.find_patterns_ext(df, pivots, timeframe=timeframe)
    legacy = rules.legacy._dedupe_patterns(legacy)
    legacy = [
        dict(event) for event in legacy
        if str(event.get("kind") or "") not in (
            _WEAK_WAVE_KINDS | _WEAK_TOP_KINDS | _WEAK_BROADENING_KINDS
        )
    ]

    strict_tops = [
        event for event in strict_tops_v7.find_strict_top_patterns(df, pivots)
        if str(event.get("kind") or "") == "head_shoulders_top"
    ]
    strict_waves = waves_v7.find_waves(df)
    strict_broadening = structures_v7.find_broadening_breaks(df, pivots)

    directional = pattern_taxonomy_v8.apply_pattern_taxonomy(
        legacy + strict_tops + strict_waves + strict_broadening
    )
    # Geometry module is a hard gate: directional consolidation candidates whose
    # upper/lower boundaries cannot be anchored to real pivots disappear here.
    return pattern_geometry_v9.apply_geometry(df, pivots, directional)


def _select(events: list[dict], df: pd.DataFrame) -> list[dict]:
    valid = [dict(event) for event in events if rules._large_pattern(df, event)]
    ordered = sorted(valid, key=lambda event: _rank(df, event), reverse=True)
    selected: list[dict] = []
    for event in ordered:
        if any(rules._pattern_overlap(event, old) >= PATTERN_OVERLAP_LIMIT for old in selected):
            continue
        selected.append(event)
        if len(selected) >= MAX_PATTERN_EVENTS:
            break
    return selected


def find_investment_patterns(df: pd.DataFrame, timeframe: str = "1d") -> list[dict]:
    pivots = rules.piv_mod.find_pivots(df)
    macro_events = macro._select_macro_reversals(
        df, macro._macro_reversal_candidates(df)
    )
    selected = _select(macro_events + _strict_candidates(df, pivots, timeframe), df)

    # The latest macro reversal is portfolio-relevant and must not be displaced by
    # a lower-priority continuation interpretation of the same region.
    if macro_events:
        latest_macro = max(macro_events, key=lambda event: int(event["confirm_idx"]))
        if latest_macro not in selected:
            selected = [
                old for old in selected
                if rules._pattern_overlap(latest_macro, old) < PATTERN_OVERLAP_LIMIT
            ]
            selected.append(latest_macro)
            selected = sorted(selected, key=lambda event: _rank(df, event), reverse=True)[:MAX_PATTERN_EVENTS]
    return sorted(selected, key=lambda event: int(event.get("confirm_idx", 0)))


def analyze(df: pd.DataFrame, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    d = df if "DIF" in df.columns else rules.indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 120:
        return {"annotations": [], "summary": {}, "diagnostics": {"analysis_version": ENGINE_VERSION}}

    pivots = rules.piv_mod.find_pivots(d)
    patterns = find_investment_patterns(d, timeframe=timeframe)
    pattern_events = rules.pattern_annotations(d, patterns)
    indicator_events = macro._select_indicator_events(
        rules.ema_regime_signals(d)
        + rules.macd_divergence_signals(d)
        + rules.rsi_extreme_signals(d)
    )
    fib_events = rules._filter_fibonacci(
        rules.fibonacci_history.find_fibonacci_touches(d, pivots)
    )
    harmonic_events = rules.harmonics_history.find_harmonic_annotations(d, pivots)
    annotations = rules._clean(
        pattern_events + indicator_events + fib_events + harmonic_events
    )

    raw_patterns = rules.pattern_mod.find_patterns(
        d, pivots, asof_bar=len(d) - 1, timeframe=timeframe
    )
    return {
        "annotations": annotations,
        "summary": rules._summary(d, pivots, patterns),
        "diagnostics": {
            "analysis_version": ENGINE_VERSION,
            "asset_kind": "index" if str(asset_kind).lower() == "index" else "equity",
            "bars_scanned": len(d),
            "raw_patterns": len(raw_patterns),
            "patterns_displayed": len(patterns),
            "pattern_families": sorted({str(event.get("kind") or "") for event in patterns}),
            "indicator_events": len(indicator_events),
            "fibonacci_events": len(fib_events),
            "harmonic_events": len(harmonic_events),
            "candidate_modules": [
                "macro_reversals", "legacy_patterns", "strict_tops",
                "strict_waves", "strict_broadening", "pivot_geometry",
            ],
            "causal": True,
            "selection": "large_confirmed_geometry_validated_non_overlapping",
        },
    }
