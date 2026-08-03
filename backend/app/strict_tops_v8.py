"""v8大级别顶部审查。

M顶继续要求大跨度、双顶接近和有效跌破颈线，但把固定百分比门槛改为
“最低百分比 + 最低ATR倍数”双重约束，使低波动指数不会被18%固定涨幅误伤，
高波动股票也不能用普通小抖动冒充大结构。头肩顶沿用v7严格规则。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod
from . import strict_tops_v7 as legacy

M_MIN_PEAK_GAP = 45
M_MIN_SPAN = 60
M_MAX_SPAN = 260
M_TOP_TOL = 0.035
M_MIN_DEPTH_PCT = 0.06
M_MIN_DEPTH_ATR = 4.0
M_PRIOR_UP_PCT = 0.08
M_PRIOR_UP_ATR = 6.0
DEDUP_BARS = 45


def _atr_at(df: pd.DataFrame, idx: int) -> float:
    if "ATR14" not in df.columns:
        return np.nan
    value = float(df["ATR14"].iloc[int(idx)])
    return value if np.isfinite(value) and value > 0 else np.nan


def _prior_uptrend_ok(df: pd.DataFrame, idx: int, price: float) -> bool:
    start = max(0, int(idx) - legacy.POSITION_BARS)
    prior_low = float(df["low"].iloc[start:int(idx) + 1].min())
    if prior_low <= 0 or price <= prior_low:
        return False
    advance = price - prior_low
    pct_ok = advance / prior_low >= M_PRIOR_UP_PCT
    atr = _atr_at(df, idx)
    atr_ok = True if not np.isfinite(atr) else advance >= M_PRIOR_UP_ATR * atr
    return pct_ok and atr_ok


def _depth_ok(df: pd.DataFrame, peak_idx: int, peak: float, valley: float) -> bool:
    if peak <= valley or peak <= 0:
        return False
    decline = peak - valley
    pct_ok = decline / peak >= M_MIN_DEPTH_PCT
    atr = _atr_at(df, peak_idx)
    atr_ok = True if not np.isfinite(atr) else decline >= M_MIN_DEPTH_ATR * atr
    return pct_ok and atr_ok


def _m_tops(df: pd.DataFrame, ap: list[dict]) -> list[dict]:
    out: list[dict] = []
    for right_i in range(2, len(ap)):
        right = ap[right_i]
        if right["kind"] != "H":
            continue
        for left_i in range(right_i - 2, -1, -2):
            left = ap[left_i]
            if left["kind"] != "H":
                continue
            gap = int(right["idx"] - left["idx"])
            if gap > M_MAX_SPAN:
                break
            if gap < M_MIN_PEAK_GAP or gap < M_MIN_SPAN:
                continue

            middle = [p for p in ap[left_i + 1:right_i] if p["kind"] == "L"]
            if not middle:
                continue
            valley = min(middle, key=lambda p: float(p["price"]))
            p1, p2 = float(left["price"]), float(right["price"])
            avg = (p1 + p2) / 2.0
            if avg <= 0 or abs(p2 - p1) / avg > M_TOP_TOL:
                continue

            highs_between = [p for p in ap[left_i + 1:right_i] if p["kind"] == "H"]
            if any(float(p["price"]) > max(p1, p2) * 1.005 for p in highs_between):
                continue

            valley_price = float(valley["price"])
            reference_peak = min(p1, p2)
            if not _depth_ok(df, int(left["idx"]), reference_peak, valley_price):
                continue
            if not legacy._top_position_ok(df, int(left["idx"]), max(p1, p2)):
                continue
            if not _prior_uptrend_ok(df, int(left["idx"]), p1):
                continue

            neckline = valley_price
            confirm = legacy._find_down_confirm(df, right, lambda _: neckline)
            if confirm is None:
                continue

            depth = (reference_peak - valley_price) / reference_peak
            target = neckline - (max(p1, p2) - neckline)
            atr = _atr_at(df, int(left["idx"]))
            atr_depth = (reference_peak - valley_price) / atr if np.isfinite(atr) else np.nan
            atr_text = f"、约{atr_depth:.1f}倍ATR" if np.isfinite(atr_depth) else ""
            note = (
                f"大级别M顶：两顶间隔{gap}根K线，高度差{abs(p2-p1)/avg:.1%}，"
                f"中间回撤{depth:.1%}{atr_text}；"
                f"{legacy._date(df, confirm)}有效跌破颈线{neckline:.2f}，"
                f"量比{legacy._vol_ratio(df, confirm):.2f}。"
            )
            out.append(legacy._event(
                df, "double_top", "M顶", [left, valley, right], confirm,
                {"neckline": neckline, "high1": p1, "high2": p2,
                 "measure_target": target, "invalidation": max(p1, p2)},
                note, 92, valley, valley,
            ))
            break
    return out


def find_strict_top_patterns(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    confirmed = piv_mod.pivots_asof(pivots, len(df) - 1)
    ap = piv_mod.alternating(confirmed).to_dict("records")
    candidates = _m_tops(df, ap) + legacy._head_shoulders_tops(df, ap)
    candidates.sort(key=lambda e: (int(e["confirm_idx"]), -int(e["score"])))

    out: list[dict] = []
    for event in candidates:
        conflict = [
            old for old in out
            if abs(int(old["confirm_idx"]) - int(event["confirm_idx"])) <= DEDUP_BARS
        ]
        if not conflict:
            out.append(event)
            continue
        best = max(conflict + [event], key=lambda e: int(e["score"]))
        if best is event:
            for old in conflict:
                out.remove(old)
            out.append(event)
    return sorted(out, key=lambda e: int(e["confirm_idx"]))
