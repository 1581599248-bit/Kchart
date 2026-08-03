"""v9顶部结构：捕捉真实的近期M顶，同时保持确认纪律。

关键修正：
- 两顶间隔与“大结构跨度”分开判断；两顶可相隔20根起，但左顶到跌破确认
  必须形成足够长、足够深的完整结构；
- 允许右顶略低于左顶，适配指数常见的次高右顶；
- 颈线固定为两顶之间真实最低pivot，描摹严格连接左顶—谷底—右顶。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod
from . import strict_tops_v7 as legacy

M_MIN_PEAK_GAP = 20
M_MAX_PEAK_GAP = 120
M_MIN_TOTAL_SPAN = 40
M_RIGHT_TOP_DOWN_TOL = 0.055
M_RIGHT_TOP_UP_TOL = 0.035
M_MIN_DEPTH_PCT = 0.030
M_MIN_DEPTH_ATR = 2.50
M_PRIOR_UP_PCT = 0.060
M_PRIOR_UP_ATR = 4.0
M_TOP_POSITION_MIN = 0.66
DEDUP_BARS = 45


def _atr_at(df: pd.DataFrame, idx: int) -> float:
    if "ATR14" not in df.columns:
        return np.nan
    value = float(df["ATR14"].iloc[int(idx)])
    return value if np.isfinite(value) and value > 0 else np.nan


def _top_position_ok(df: pd.DataFrame, idx: int, price: float) -> bool:
    start = max(0, int(idx) - legacy.POSITION_BARS + 1)
    hi = float(df["high"].iloc[start:int(idx) + 1].max())
    lo = float(df["low"].iloc[start:int(idx) + 1].min())
    if hi <= lo:
        return False
    return (price - lo) / (hi - lo) >= M_TOP_POSITION_MIN


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


def _tops_close(left: float, right: float) -> bool:
    if left <= 0 or right <= 0:
        return False
    rel = right / left - 1.0
    return -M_RIGHT_TOP_DOWN_TOL <= rel <= M_RIGHT_TOP_UP_TOL


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
            peak_gap = int(right["idx"] - left["idx"])
            if peak_gap > M_MAX_PEAK_GAP:
                break
            if peak_gap < M_MIN_PEAK_GAP:
                continue

            middle_lows = [p for p in ap[left_i + 1:right_i] if p["kind"] == "L"]
            if not middle_lows:
                continue
            valley = min(middle_lows, key=lambda p: float(p["price"]))
            p1, p2 = float(left["price"]), float(right["price"])
            if not _tops_close(p1, p2):
                continue

            # 中间若有明显更高的第三个峰，不把整段硬凑成M顶。
            highs_between = [p for p in ap[left_i + 1:right_i] if p["kind"] == "H"]
            if any(float(p["price"]) > max(p1, p2) * 1.005 for p in highs_between):
                continue

            valley_price = float(valley["price"])
            reference_peak = min(p1, p2)
            if not _depth_ok(df, int(left["idx"]), reference_peak, valley_price):
                continue
            if not _top_position_ok(df, int(left["idx"]), max(p1, p2)):
                continue
            if not _prior_uptrend_ok(df, int(left["idx"]), p1):
                continue

            neckline = valley_price
            confirm = legacy._find_down_confirm(df, right, lambda _: neckline)
            if confirm is None:
                continue
            total_span = int(confirm - int(left["idx"]))
            if total_span < M_MIN_TOTAL_SPAN:
                continue

            depth = (reference_peak - valley_price) / reference_peak
            target = neckline - (max(p1, p2) - neckline)
            atr = _atr_at(df, int(left["idx"]))
            atr_depth = (reference_peak - valley_price) / atr if np.isfinite(atr) else np.nan
            atr_text = f"、约{atr_depth:.1f}倍ATR" if np.isfinite(atr_depth) else ""
            note = (
                f"确认M顶：两顶间隔{peak_gap}根、完整跨度{total_span}根K线，"
                f"右顶较左顶{p2 / p1 - 1.0:+.1%}，中间回撤{depth:.1%}{atr_text}；"
                f"{legacy._date(df, confirm)}有效跌破真实谷底颈线{neckline:.2f}。"
            )
            out.append(legacy._event(
                df, "double_top", "M顶", [left, valley, right], confirm,
                {"neckline": neckline, "high1": p1, "high2": p2,
                 "measure_target": target, "invalidation": max(p1, p2),
                 "peak_gap": peak_gap, "total_span": total_span},
                note, 94, valley, valley,
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
