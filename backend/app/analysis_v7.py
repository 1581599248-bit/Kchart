"""推背图 v7：严格波浪、大级别Fib、确认型MACD与历史结构描摹。"""
from __future__ import annotations

from . import analysis_v5 as base
from . import fibonacci_history
from . import harmonics_history
from . import indicators
from . import patterns as base_patterns
from . import patterns_ext
from . import pivots as piv_mod
from . import signals_v7
from . import structures_v7
from . import waves_v7

ANALYSIS_VERSION = "analysis_v7.0"

_OLD_WAVE_KINDS = {"wave_up5", "wave_down_abc", "wave_up_abc", "wave_down5"}


def _strict_patterns(df, pivots, timeframe: str) -> list[dict]:
    original = base_patterns.find_patterns(
        df, pivots, asof_bar=len(df) - 1, timeframe=timeframe
    )
    # 旧波浪计数过宽，旧五点扩散三角含义不清，全部由v7严格模块替代。
    original = [e for e in original if str(e.get("kind")) not in _OLD_WAVE_KINDS]
    extra = [
        e for e in patterns_ext.find_patterns_ext(df, pivots, timeframe=timeframe)
        if str(e.get("kind")) != "broadening_triangle"
    ]
    return base._dedupe_patterns(
        original
        + extra
        + waves_v7.find_waves(df)
        + structures_v7.find_broadening_breaks(df, pivots)
    )


def _historical_traces(pattern_annotations: list[dict]) -> tuple[list[dict], list[dict]]:
    """已确认/已失效历史结构保留描摹线；构筑中结构不进入主图。"""
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
    patterns = _strict_patterns(d, pivots, timeframe)
    pattern_annotations, patterns_enriched = base._pattern_annotations(d, pivots, patterns)
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
        # 斐波那契标签已直接使用完整比例；其他标签仍限制为8字。
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
            "patterns": len(patterns_enriched),
            "historical_traces": len(trace_events),
            "annotations": len(annotations),
            "causal": True,
        },
    }
