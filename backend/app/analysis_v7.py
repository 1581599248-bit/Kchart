"""推背图 v7.3：近期M顶、真实触点边界与EMA趋势状态机。"""
from __future__ import annotations

from . import analysis_v5 as base
from . import fibonacci_history
from . import harmonics_history
from . import indicators
from . import pattern_display_v8
from . import pattern_geometry_v9
from . import pattern_taxonomy_v8
from . import patterns as base_patterns
from . import patterns_ext
from . import pivots as piv_mod
from . import signals_v7
from . import strict_tops_v9
from . import structures_v7
from . import waves_v7

ANALYSIS_VERSION = "analysis_v7.3"

_OLD_WAVE_KINDS = {"wave_up5", "wave_down_abc", "wave_up_abc", "wave_down5"}
_REPLACED_TOP_KINDS = {"double_top", "head_shoulders_top"}


def _strict_patterns(df, pivots, timeframe: str) -> list[dict]:
    original = base_patterns.find_patterns(
        df, pivots, asof_bar=len(df) - 1, timeframe=timeframe
    )
    original = [
        e for e in original
        if str(e.get("kind")) not in (_OLD_WAVE_KINDS | _REPLACED_TOP_KINDS)
    ]
    extra = [
        e for e in patterns_ext.find_patterns_ext(df, pivots, timeframe=timeframe)
        if str(e.get("kind")) != "broadening_triangle"
    ]
    detected = base._dedupe_patterns(
        original
        + extra
        + strict_tops_v9.find_strict_top_patterns(df, pivots)
        + waves_v7.find_waves(df)
        + structures_v7.find_broadening_breaks(df, pivots)
    )
    directional = pattern_taxonomy_v8.apply_pattern_taxonomy(detected)
    # 整理/中继形态只有在上下沿经过真实pivot触点校验后才能进入显示候选。
    return pattern_geometry_v9.apply_geometry(df, pivots, directional)


def _historical_traces(pattern_annotations: list[dict]) -> tuple[list[dict], list[dict]]:
    """已筛选的大结构保留描摹，并在结构末端标一次形态名称。"""
    visible: list[dict] = []
    traces: list[dict] = []
    for event in pattern_annotations:
        if event.get("kind") != "pattern":
            visible.append(event)
            continue
        detail = str(event.get("detail") or "")
        polylines = event.get("polylines") or []
        if polylines:
            if "构筑中" in detail:
                continue
            trace = dict(event)
            trace.update({
                "trace_only": True,
                "history_label": True,
                "star": False,
                "lines": [],
                "zones": [],
                "_score": 1,
            })
            traces.append(trace)
            continue
        visible.append(event)
    return visible, traces


def analyze(df, timeframe: str = "1d") -> dict:
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 60:
        return {"annotations": [], "summary": {}}

    pivots = piv_mod.find_pivots(d)
    patterns_all = _strict_patterns(d, pivots, timeframe)

    _, patterns_enriched = base._pattern_annotations(d, pivots, patterns_all)
    display_patterns = pattern_display_v8.select_display_patterns(d, patterns_all)
    pattern_annotations, _ = base._pattern_annotations(d, pivots, display_patterns)
    pattern_visible, trace_events = _historical_traces(pattern_annotations)

    annotations = (
        trace_events
        + pattern_visible
        + signals_v7.all_signals(d)
        + fibonacci_history.find_fibonacci_touches(d, pivots)
        + harmonics_history.find_harmonic_annotations(d, pivots)
    )
    annotations = base._density(annotations)

    for event in annotations:
        if event.get("kind") == "fibonacci":
            event["label"] = str(event.get("label") or "")
        else:
            event["label"] = str(event.get("label") or "")[:8]
        event.pop("_score", None)
        event.pop("_grp", None)

    return {
        "annotations": annotations,
        "summary": base._summary(d, pivots, patterns_enriched),
        "diagnostics": {
            "analysis_version": ANALYSIS_VERSION,
            "bars_scanned": len(d),
            "patterns_detected": len(patterns_enriched),
            "patterns_displayed": len(display_patterns),
            "historical_traces": len(trace_events),
            "annotations": len(annotations),
            "causal": True,
        },
    }
