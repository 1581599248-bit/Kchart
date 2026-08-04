"""High-recall, geometry-correct investment structure engine.

The v14 selector was too destructive: it capped a multi-year chart at four
structures and used a clustered support/resistance level as the M/W neckline.
That could replace the actual valley/crest and distort the drawn letter.

v15 rules:
- scan several causal macro zigzag scales;
- M neckline = the principal confirmed valley between the two peaks;
- W neckline = the principal confirmed crest between the two troughs;
- draw every alternating macro pivot between the two extremes, so the M/W is
  visibly traced rather than represented by a generic label;
- retain independent structures across history with a dynamic display budget;
- deduplicate only near-identical readings, not every overlapping story.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import investment_engine_v12 as rules
from . import investment_engine_v14 as base

ENGINE_VERSION = "investment_engine_v15.0"
MIN_REVERSAL_BARS = 45
MIN_EXTREME_GAP = 24
MAX_EXTREME_GAP = 320
MAX_PATTERN_EVENTS = 12
MAX_INDICATOR_EVENTS = 10
PATTERN_OVERLAP_LIMIT = 0.86

_large_pattern = rules._large_pattern
_filter_fibonacci = rules._filter_fibonacci


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(pd.to_datetime(df["trade_date"].iloc[int(idx)]).date())


def _atr(df: pd.DataFrame, idx: int) -> float:
    if "ATR14" not in df.columns:
        return float("nan")
    value = float(df["ATR14"].iloc[int(idx)])
    return value if math.isfinite(value) and value > 0 else float("nan")


def _prior_move_ok(df: pd.DataFrame, idx: int, price: float, direction: str) -> bool:
    start = max(0, int(idx) - 180)
    if direction == "bear":
        prior = float(df["low"].iloc[start:int(idx) + 1].min())
        move = price / max(prior, 1e-9) - 1.0
    else:
        prior = float(df["high"].iloc[start:int(idx) + 1].max())
        move = prior / max(price, 1e-9) - 1.0
    atr = _atr(df, idx)
    atr_move = abs(price - prior) / atr if math.isfinite(atr) else 0.0
    return move >= 0.07 or atr_move >= 4.0


def _confirm(df: pd.DataFrame, start: int, level: float, direction: str,
             invalidation: float) -> int | None:
    close = df["close"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float) if "ATR14" in df.columns else np.full(len(df), np.nan)
    for i in range(max(1, int(start)), len(df)):
        buffer = max(abs(level) * 0.004, float(atr[i]) * 0.35 if np.isfinite(atr[i]) else 0.0)
        if direction == "bear":
            if close[i] > invalidation * 1.01:
                return None
            decisive = close[i] <= level - buffer
            two_close = close[i] < level and close[i - 1] < level
        else:
            if close[i] < invalidation * 0.99:
                return None
            decisive = close[i] >= level + buffer
            two_close = close[i] > level and close[i - 1] > level
        if decisive or two_close:
            return int(i)
    return None


def _trace(df: pd.DataFrame, segment: list[dict], neckline_idx: int,
           neckline: float, confirm_idx: int) -> list[dict]:
    return [
        {
            "points": [
                {"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
                for p in segment
            ],
            "style": "solid",
        },
        {
            "points": [
                {"t": _date(df, neckline_idx), "p": round(neckline, 4)},
                {"t": _date(df, confirm_idx), "p": round(neckline, 4)},
            ],
            "style": "dashed",
        },
    ]


def _shape_score(direction: str, p1: float, p2: float, neckline: float,
                 span: int, scale: float) -> float:
    mean_extreme = (p1 + p2) / 2.0
    similarity = 1.0 - min(abs(p2 - p1) / max(mean_extreme, 1e-9), 0.15) / 0.15
    depth = ((min(p1, p2) - neckline) if direction == "bear"
             else (neckline - max(p1, p2))) / max(mean_extreme, 1e-9)
    return 70.0 + similarity * 12.0 + min(depth / 0.10, 1.5) * 10.0 + min(span / 180.0, 1.0) * 6.0 + scale * 30.0


def _macro_candidates_at_scale(df: pd.DataFrame, scale: float) -> list[dict]:
    piv = rules.piv_mod.zigzag(df, min_pct=scale)
    piv = rules.piv_mod.pivots_asof(piv, len(df) - 1)
    points = rules.piv_mod.alternating(piv).to_dict("records")
    out: list[dict] = []

    for right_pos in range(2, len(points)):
        right = points[right_pos]
        direction = "bear" if right["kind"] == "H" else "bull"
        extreme_kind = right["kind"]
        middle_kind = "L" if extreme_kind == "H" else "H"

        for left_pos in range(right_pos - 2, -1, -2):
            left = points[left_pos]
            if left["kind"] != extreme_kind:
                continue
            gap = int(right["idx"]) - int(left["idx"])
            if gap > MAX_EXTREME_GAP:
                break
            if gap < MIN_EXTREME_GAP:
                continue

            segment = points[left_pos:right_pos + 1]
            middle = [p for p in segment[1:-1] if p["kind"] == middle_kind]
            if not middle:
                continue
            # Classical geometry: use the principal trough/crest, never a higher
            # clustered level chosen merely because it was touched more often.
            neck_pivot = (min(middle, key=lambda p: float(p["price"]))
                          if direction == "bear"
                          else max(middle, key=lambda p: float(p["price"])))
            neckline = float(neck_pivot["price"])
            p1, p2 = float(left["price"]), float(right["price"])
            mean_extreme = (p1 + p2) / 2.0
            if mean_extreme <= 0:
                continue
            extreme_diff = abs(p2 - p1) / mean_extreme
            if extreme_diff > 0.12:
                continue

            # A higher peak/lower trough inside the structure means these are not
            # the true two extremes and the candidate is a false M/W pairing.
            other_extremes = [p for p in segment[1:-1] if p["kind"] == extreme_kind]
            if direction == "bear" and any(float(p["price"]) > max(p1, p2) * 1.006 for p in other_extremes):
                continue
            if direction == "bull" and any(float(p["price"]) < min(p1, p2) * 0.994 for p in other_extremes):
                continue

            depth_abs = ((min(p1, p2) - neckline) if direction == "bear"
                         else (neckline - max(p1, p2)))
            if depth_abs <= 0:
                continue
            atr_ref = _atr(df, int(neck_pivot["idx"]))
            depth_pct = depth_abs / mean_extreme
            depth_atr = depth_abs / atr_ref if math.isfinite(atr_ref) else 0.0
            if depth_pct < 0.025 and depth_atr < 2.5:
                continue
            if not _prior_move_ok(df, int(left["idx"]), p1, direction):
                continue

            invalidation = max(p1, p2) if direction == "bear" else min(p1, p2)
            start_confirm = max(int(right["idx"]) + 1, int(right.get("confirmed_at_idx", right["idx"] + 1)))
            confirm_idx = _confirm(df, start_confirm, neckline, direction, invalidation)
            if confirm_idx is None or confirm_idx - int(left["idx"]) < MIN_REVERSAL_BARS:
                continue

            kind = "macro_double_top" if direction == "bear" else "macro_double_bottom"
            name = "大M顶" if direction == "bear" else "大W底"
            target = neckline - depth_abs if direction == "bear" else neckline + depth_abs
            score = int(round(_shape_score(direction, p1, p2, neckline, gap, scale)))
            note = (
                f"{name}：{_date(df, int(left['idx']))}至{_date(df, int(right['idx']))}"
                f"按真实宏观pivot描摹；颈线取两端极值之间的主"
                f"{'谷底' if direction == 'bear' else '峰顶'}{neckline:.2f}，"
                f"{_date(df, confirm_idx)}有效{'跌破' if direction == 'bear' else '突破'}。"
            )
            out.append({
                "kind": kind, "name": name, "direction": direction,
                "start_idx": int(left["idx"]), "middle_idx": int(neck_pivot["idx"]),
                "end_idx": int(right["idx"]), "confirm_idx": int(confirm_idx),
                "key_levels": {
                    "neckline": round(neckline, 4),
                    "measure_target": round(target, 4),
                    "invalidation": round(invalidation, 4),
                    "extreme1": round(p1, 4), "extreme2": round(p2, 4),
                    "neckline_source": "principal_intervening_pivot",
                },
                "score": score, "star": True, "note": note,
                "trace": _trace(df, segment, int(neck_pivot["idx"]), neckline, confirm_idx),
                "active": confirm_idx >= len(df) - 180, "causal": True,
                "scale": round(scale, 4), "pivot_count": len(segment),
            })
    return out


def _event_key(event: dict) -> tuple:
    return (
        str(event.get("kind")), int(event.get("start_idx", -1)),
        int(event.get("middle_idx", -1)), int(event.get("end_idx", -1)),
        int(event.get("confirm_idx", -1)),
    )


def _macro_candidates(df: pd.DataFrame) -> list[dict]:
    atr_pct = rules._median_atr_pct(df)
    scales = sorted({
        round(float(np.clip(atr_pct * m, 0.018, 0.075)), 4)
        for m in (1.35, 1.75, 2.20, 2.80)
    } | {0.025, 0.035, 0.05})
    unique: dict[tuple, dict] = {}
    for scale in scales:
        for event in _macro_candidates_at_scale(df, scale):
            key = _event_key(event)
            old = unique.get(key)
            if old is None or int(event["score"]) > int(old["score"]):
                unique[key] = event
    return list(unique.values())


def _near_duplicate(a: dict, b: dict) -> bool:
    if str(a.get("kind")) != str(b.get("kind")):
        return False
    endpoints_close = (
        abs(int(a["start_idx"]) - int(b["start_idx"])) <= 18
        and abs(int(a["end_idx"]) - int(b["end_idx"])) <= 18
        and abs(int(a["confirm_idx"]) - int(b["confirm_idx"])) <= 12
    )
    if not endpoints_close:
        return False
    an = float((a.get("key_levels") or {}).get("neckline", 0.0))
    bn = float((b.get("key_levels") or {}).get("neckline", 0.0))
    return abs(an - bn) / max(abs(an), abs(bn), 1e-9) <= 0.012


def _dynamic_pattern_budget(df: pd.DataFrame) -> int:
    # Roughly one major event per 150 bars, bounded for readability.  A six-year
    # daily chart therefore keeps about 10 rather than four structures.
    return int(np.clip(round(len(df) / 150), 6, MAX_PATTERN_EVENTS))


def _rank(df: pd.DataFrame, event: dict) -> tuple[float, int, int]:
    base_rank = base._rank(df, event)[0]
    kind = str(event.get("kind") or "")
    macro_bonus = 45.0 if kind in {"macro_double_top", "macro_double_bottom"} else 0.0
    return base_rank + macro_bonus, int(event.get("confirm_idx", 0)), int(event.get("score", 0))


def find_investment_patterns(df: pd.DataFrame, timeframe: str = "1d") -> list[dict]:
    pivots = rules.piv_mod.find_pivots(df)
    strict = base._strict_candidates(df, pivots, timeframe)
    candidates = _macro_candidates(df) + strict
    candidates = [dict(e) for e in candidates if rules._large_pattern(df, e)]
    candidates.sort(key=lambda e: _rank(df, e), reverse=True)

    selected: list[dict] = []
    budget = _dynamic_pattern_budget(df)
    for event in candidates:
        duplicate = next((old for old in selected if _near_duplicate(event, old)), None)
        if duplicate is not None:
            if _rank(df, event) > _rank(df, duplicate):
                selected.remove(duplicate)
                selected.append(event)
            continue
        # Only suppress a competing interpretation when both time overlap and
        # confirmation dates are nearly identical. Historical/nested structures
        # at different scales are allowed to coexist.
        conflict = next((
            old for old in selected
            if rules._pattern_overlap(event, old) >= PATTERN_OVERLAP_LIMIT
            and abs(int(event.get("confirm_idx", 0)) - int(old.get("confirm_idx", 0))) <= 25
        ), None)
        if conflict is not None:
            if _rank(df, event) > _rank(df, conflict):
                selected.remove(conflict)
                selected.append(event)
            continue
        selected.append(event)
        if len(selected) >= budget:
            break
    return sorted(selected, key=lambda e: int(e.get("confirm_idx", 0)))


def _select_indicator_events(events: list[dict], df: pd.DataFrame) -> list[dict]:
    ordered = sorted(events, key=lambda e: int(e.get("bar_idx", 0)))
    kept: list[dict] = []
    for event in ordered:
        # Same label/direction within one trading month is duplicate; different
        # indicator families and genuinely separated signals are retained.
        duplicate = next((
            old for old in kept
            if str(old.get("label")) == str(event.get("label"))
            and old.get("direction") == event.get("direction")
            and abs(int(old.get("bar_idx", 0)) - int(event.get("bar_idx", 0))) <= 22
        ), None)
        if duplicate is not None:
            if int(event.get("_score", 0)) >= int(duplicate.get("_score", 0)):
                kept.remove(duplicate)
                kept.append(event)
            continue
        kept.append(event)
    budget = int(np.clip(round(len(df) / 170), 6, MAX_INDICATOR_EVENTS))
    return kept[-budget:]


def analyze(df: pd.DataFrame, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    d = df if "DIF" in df.columns else rules.indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 120:
        return {"annotations": [], "summary": {}, "diagnostics": {"analysis_version": ENGINE_VERSION}}

    pivots = rules.piv_mod.find_pivots(d)
    patterns = find_investment_patterns(d, timeframe=timeframe)
    indicator_events = _select_indicator_events(
        rules.ema_regime_signals(d) + rules.macd_divergence_signals(d) + rules.rsi_extreme_signals(d), d
    )
    fib_events = rules._filter_fibonacci(rules.fibonacci_history.find_fibonacci_touches(d, pivots))
    harmonic_events = rules.harmonics_history.find_harmonic_annotations(d, pivots)
    annotations = rules._clean(
        rules.pattern_annotations(d, patterns) + indicator_events + fib_events + harmonic_events
    )
    return {
        "annotations": annotations,
        "summary": rules._summary(d, pivots, patterns),
        "diagnostics": {
            "analysis_version": ENGINE_VERSION,
            "asset_kind": "index" if str(asset_kind).lower() == "index" else "equity",
            "bars_scanned": len(d), "patterns_displayed": len(patterns),
            "pattern_budget": _dynamic_pattern_budget(d),
            "pattern_families": sorted({str(e.get("kind") or "") for e in patterns}),
            "indicator_events": len(indicator_events),
            "indicator_budget": int(np.clip(round(len(d) / 170), 6, MAX_INDICATOR_EVENTS)),
            "fibonacci_events": len(fib_events), "harmonic_events": len(harmonic_events),
            "causal": True,
            "selection": "multi_scale_principal_neckline_dynamic_recall",
        },
    }
