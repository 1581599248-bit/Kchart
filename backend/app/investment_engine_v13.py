"""Second-stage investment selector built on v12 candidate generation.

Changes are deliberately broad rather than date-specific:
- lower the macro-pivot threshold only for low-volatility instruments;
- choose the largest structure inside each overlapping story, then keep the most
  recent independent stories;
- reserve a slot for the latest valid macro reversal;
- tighten chart density to four structures and five indicator events.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import investment_engine_v12 as base

ENGINE_VERSION = "investment_engine_v13.0"
MIN_REVERSAL_BARS = base.MIN_REVERSAL_BARS
MIN_CONTINUATION_BARS = base.MIN_CONTINUATION_BARS
MIN_WAVE_BARS = base.MIN_WAVE_BARS
MAX_PATTERN_EVENTS = 4
MAX_INDICATOR_EVENTS = 5
PATTERN_OVERLAP_LIMIT = base.PATTERN_OVERLAP_LIMIT

# Public helpers used by validation scripts.
_large_pattern = base._large_pattern
_pattern_overlap = base._pattern_overlap
_filter_fibonacci = base._filter_fibonacci


def _macro_reversal_candidates(df: pd.DataFrame) -> list[dict]:
    atr_pct = base._median_atr_pct(df)
    # 3% floor for low-volatility indices; volatile equities remain at 4-6.5%.
    zigzag_pct = float(np.clip(atr_pct * 2.2, 0.03, 0.065))
    pivots = base.piv_mod.zigzag(df, min_pct=zigzag_pct)
    pivots = base.piv_mod.pivots_asof(pivots, len(df) - 1)
    points = pivots.to_dict("records")
    events: list[dict] = []

    for direction, extreme_kind, middle_kind in (
        ("bear", "H", "L"), ("bull", "L", "H")
    ):
        extremes = [p for p in points if p["kind"] == extreme_kind]
        for right_pos in range(1, len(extremes)):
            right = extremes[right_pos]
            for left_pos in range(right_pos - 1, -1, -1):
                left = extremes[left_pos]
                gap = int(right["idx"] - left["idx"])
                if gap > 260:
                    break
                if gap < 30:
                    continue
                p1, p2 = float(left["price"]), float(right["price"])
                mean_extreme = (p1 + p2) / 2.0
                if mean_extreme <= 0 or abs(p2 - p1) / mean_extreme > 0.09:
                    continue

                middle = [
                    p for p in points
                    if p["kind"] == middle_kind
                    and int(left["idx"]) < int(p["idx"]) < int(right["idx"])
                ]
                if not middle:
                    continue
                clusters = base._cluster_levels(middle, atr_pct)
                # A repeatedly tested level is preferred.  With equal touches use
                # the deeper support/resistance level, not the latest local wiggle.
                if direction == "bear":
                    clusters.sort(key=lambda c: (len(c["items"]), -float(c["price"])), reverse=True)
                else:
                    clusters.sort(key=lambda c: (len(c["items"]), float(c["price"])), reverse=True)
                cluster = clusters[0]
                level = float(cluster["price"])
                anchor = min(cluster["items"], key=lambda x: int(x["idx"]))

                if direction == "bear":
                    depth_abs = min(p1, p2) - level
                    invalidation = max(p1, p2)
                else:
                    depth_abs = level - max(p1, p2)
                    invalidation = min(p1, p2)
                if depth_abs <= 0:
                    continue
                depth_pct = depth_abs / max(mean_extreme, 1e-9)
                atr_left = base._atr(df, int(left["idx"]))
                depth_atr = depth_abs / atr_left if math.isfinite(atr_left) else float("nan")
                if depth_pct < 0.04 and (not math.isfinite(depth_atr) or depth_atr < 3.2):
                    continue
                prior_pct, prior_atr = base._prior_move(df, int(left["idx"]), p1, direction)
                if prior_pct < 0.08 and (not math.isfinite(prior_atr) or prior_atr < 5.0):
                    continue
                if base._position(df, int(left["idx"]), p1, direction) < 0.66:
                    continue

                start_confirm = max(int(right["idx"]) + 1, int(right["confirmed_at_idx"]))
                confirm_idx = base._confirm_break(df, start_confirm, level, direction, invalidation)
                if confirm_idx is None or confirm_idx - int(left["idx"]) < MIN_REVERSAL_BARS:
                    continue

                kind = "macro_double_top" if direction == "bear" else "macro_double_bottom"
                name = base._PATTERN_NAMES[kind]
                target = level - depth_abs if direction == "bear" else level + depth_abs
                touches = len(cluster["items"])
                score = int(round(
                    72 + min(touches, 3) * 4 + min(depth_pct / 0.10, 1.0) * 8
                    + min((confirm_idx - int(left["idx"])) / 180.0, 1.0) * 6
                ))
                trace = [
                    {"points": [
                        {"t": base._date(df, int(left["idx"])), "p": round(p1, 4)},
                        {"t": base._date(df, int(anchor["idx"])), "p": round(level, 4)},
                        {"t": base._date(df, int(right["idx"])), "p": round(p2, 4)},
                    ], "style": "solid"},
                    {"points": [
                        {"t": base._date(df, int(anchor["idx"])), "p": round(level, 4)},
                        {"t": base._date(df, confirm_idx), "p": round(level, 4)},
                    ], "style": "dashed"},
                ]
                note = (
                    f"{name}：{base._date(df, int(left['idx']))}至{base._date(df, int(right['idx']))}"
                    f"形成大级别双峰/双谷，颈线按{touches}个宏观承接点聚类为{level:.2f}；"
                    f"{base._date(df, confirm_idx)}连续收盘有效"
                    f"{'跌破' if direction == 'bear' else '突破'}。"
                )
                events.append({
                    "kind": kind, "name": name, "direction": direction,
                    "start_idx": int(left["idx"]), "middle_idx": int(anchor["idx"]),
                    "end_idx": int(right["idx"]), "confirm_idx": int(confirm_idx),
                    "key_levels": {
                        "neckline": round(level, 4), "measure_target": round(target, 4),
                        "invalidation": round(invalidation, 4),
                        "extreme1": round(p1, 4), "extreme2": round(p2, 4),
                    },
                    "score": score, "star": True, "note": note, "trace": trace,
                    "active": confirm_idx >= len(df) - 180, "causal": True,
                    "touches": touches,
                })
    return events


def _macro_preference(event: dict) -> tuple[int, int, int, int]:
    return (
        int(event["confirm_idx"]) - int(event["start_idx"]),
        int(event.get("touches", 0)),
        int(event.get("score", 0)),
        int(event["end_idx"]),
    )


def _select_macro_reversals(df: pd.DataFrame, candidates: list[dict]) -> list[dict]:
    """Largest structure wins inside a story; latest independent stories win globally."""
    groups: list[list[dict]] = []
    for event in sorted(candidates, key=lambda e: int(e["confirm_idx"])):
        group = next((g for g in groups if any(
            base._pattern_overlap(event, old) >= 0.55
            or abs(int(event["confirm_idx"]) - int(old["confirm_idx"])) <= 55
            for old in g
        )), None)
        if group is None:
            groups.append([event])
        else:
            group.append(event)
    representatives = [max(group, key=_macro_preference) for group in groups]
    return sorted(
        sorted(representatives, key=lambda e: int(e["confirm_idx"]), reverse=True)[:3],
        key=lambda e: int(e["confirm_idx"]),
    )


def _rank(df: pd.DataFrame, event: dict) -> tuple[float, int, int]:
    base_rank = base._pattern_rank(df, event)[0]
    age = len(df) - 1 - int(event.get("confirm_idx", 0))
    recent_bonus = max(0.0, 22.0 - age / 14.0)
    return base_rank + recent_bonus, int(event.get("confirm_idx", 0)), int(event.get("score", 0))


def find_investment_patterns(df: pd.DataFrame, timeframe: str = "1d") -> list[dict]:
    pivots = base.piv_mod.find_pivots(df)
    raw = base.pattern_mod.find_patterns(df, pivots, asof_bar=len(df) - 1, timeframe=timeframe)
    raw += base.patterns_ext.find_patterns_ext(df, pivots, timeframe=timeframe)
    raw = base.legacy._dedupe_patterns(raw)

    macro = _select_macro_reversals(df, _macro_reversal_candidates(df))
    raw = [e for e in raw if str(e.get("kind")) not in {"double_top", "double_bottom"}]
    valid = macro + [dict(e) for e in raw if base._large_pattern(df, e)]
    ordered = sorted(valid, key=lambda e: _rank(df, e), reverse=True)

    selected: list[dict] = []
    for event in ordered:
        if any(base._pattern_overlap(event, old) >= PATTERN_OVERLAP_LIMIT for old in selected):
            continue
        selected.append(event)
        if len(selected) >= MAX_PATTERN_EVENTS:
            break

    # Always retain the latest valid macro reversal; replace a conflicting or the
    # lowest-ranked event rather than adding another label.
    if macro:
        latest_macro = max(macro, key=lambda e: int(e["confirm_idx"]))
        if latest_macro not in selected:
            conflicts = [old for old in selected if base._pattern_overlap(latest_macro, old) >= PATTERN_OVERLAP_LIMIT]
            for old in conflicts:
                selected.remove(old)
            selected.append(latest_macro)
            selected = sorted(selected, key=lambda e: _rank(df, e), reverse=True)[:MAX_PATTERN_EVENTS]
    return sorted(selected, key=lambda e: int(e.get("confirm_idx", 0)))


def _select_indicator_events(events: list[dict]) -> list[dict]:
    priority = {"trend": 4, "MACD顶背离": 3, "MACD底背离": 3, "RSI超买": 2, "RSI超卖": 2}
    ordered = sorted(events, key=lambda e: int(e.get("bar_idx", 0)))
    kept: list[dict] = []
    for event in ordered:
        conflicts = [
            old for old in kept
            if old.get("direction") == event.get("direction")
            and abs(int(old.get("bar_idx", 0)) - int(event.get("bar_idx", 0))) <= 18
        ]
        if conflicts:
            best = max(conflicts + [event], key=lambda e: (
                priority.get(str(e.get("label")), priority.get(str(e.get("kind")), 1)),
                int(e.get("_score", 0)),
            ))
            if best is event:
                for old in conflicts:
                    kept.remove(old)
                kept.append(event)
            continue
        if kept and int(event.get("bar_idx", 0)) - int(kept[-1].get("bar_idx", 0)) < 55:
            continue
        kept.append(event)
    return kept[-MAX_INDICATOR_EVENTS:]


def analyze(df: pd.DataFrame, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    d = df if "DIF" in df.columns else base.indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 120:
        return {"annotations": [], "summary": {}, "diagnostics": {"analysis_version": ENGINE_VERSION}}

    pivots = base.piv_mod.find_pivots(d)
    patterns = find_investment_patterns(d, timeframe=timeframe)
    pattern_events = base.pattern_annotations(d, patterns)
    indicator_events = _select_indicator_events(
        base.ema_regime_signals(d) + base.macd_divergence_signals(d) + base.rsi_extreme_signals(d)
    )
    fib_events = base._filter_fibonacci(base.fibonacci_history.find_fibonacci_touches(d, pivots))
    harmonic_events = base.harmonics_history.find_harmonic_annotations(d, pivots)
    annotations = base._clean(pattern_events + indicator_events + fib_events + harmonic_events)

    raw_count = len(base.pattern_mod.find_patterns(d, pivots, asof_bar=len(d)-1, timeframe=timeframe))
    return {
        "annotations": annotations,
        "summary": base._summary(d, pivots, patterns),
        "diagnostics": {
            "analysis_version": ENGINE_VERSION,
            "asset_kind": "index" if str(asset_kind).lower() == "index" else "equity",
            "bars_scanned": len(d), "raw_patterns": raw_count,
            "patterns_displayed": len(patterns), "indicator_events": len(indicator_events),
            "fibonacci_events": len(fib_events), "harmonic_events": len(harmonic_events),
            "causal": True, "selection": "large_confirmed_recent_non_overlapping",
        },
    }
