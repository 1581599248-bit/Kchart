"""严格的大级别顶部结构。

只输出已经完成颈线确认的M顶与头肩顶；宁可漏掉边缘形态，也不把普通震荡
误判为顶部。所有pivot必须已右侧确认，突破确认只能发生在最后组成pivot可知之后。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

# M顶：两顶之间至少约9周，完整结构至少约3个月。
M_MIN_PEAK_GAP = 45
M_MIN_SPAN = 60
M_MAX_SPAN = 260
M_TOP_TOL = 0.035
M_MIN_DEPTH = 0.09
M_PRIOR_UP_PCT = 0.18

# 头肩顶：左肩到右肩至少约4个月。
HS_MIN_SPAN = 80
HS_MAX_SPAN = 320
HS_SHOULDER_TOL = 0.04
HS_HEAD_PROM = 0.045
HS_MIN_DEPTH = 0.10
HS_TIME_RATIO = (0.60, 1.67)
HS_PRIOR_UP_PCT = 0.20
HS_MAX_NECK_SLOPE_ATR = 0.18

TOP_POSITION_MIN = 0.72
POSITION_BARS = 252
CONFIRM_PCT = 0.01
CONFIRM_ATR = 0.50
VOLUME_MULT = 1.10
DEDUP_BARS = 45


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _line(i: int, a: dict, b: dict) -> float:
    i1, i2 = int(a["idx"]), int(b["idx"])
    p1, p2 = float(a["price"]), float(b["price"])
    if i2 == i1:
        return p2
    return float(p1 + (p2 - p1) * (i - i1) / (i2 - i1))


def _top_position_ok(df: pd.DataFrame, idx: int, price: float) -> bool:
    start = max(0, int(idx) - POSITION_BARS + 1)
    hi = float(df["high"].iloc[start:idx + 1].max())
    lo = float(df["low"].iloc[start:idx + 1].min())
    if hi <= lo:
        return False
    return (price - lo) / (hi - lo) >= TOP_POSITION_MIN


def _prior_uptrend_ok(df: pd.DataFrame, idx: int, price: float, required: float) -> bool:
    start = max(0, int(idx) - POSITION_BARS)
    prior_low = float(df["low"].iloc[start:idx + 1].min())
    return prior_low > 0 and price / prior_low - 1.0 >= required


def _vol_ratio(df: pd.DataFrame, idx: int) -> float:
    if "vol" not in df.columns or idx < 20:
        return 1.0
    vol = df["vol"].to_numpy(dtype=float)
    base = float(np.nanmean(vol[idx - 20:idx]))
    return float(vol[idx] / base) if base > 0 else 1.0


def _find_down_confirm(df: pd.DataFrame, last_pivot: dict, level_fn) -> int | None:
    """有效跌破：1%或0.5ATR；否则需要连续两根收盘位于颈线下方。"""
    close = df["close"].to_numpy(dtype=float)
    atr = (
        df["ATR14"].to_numpy(dtype=float)
        if "ATR14" in df.columns else np.full(len(df), np.nan)
    )
    start = max(int(last_pivot["idx"]) + 1, int(last_pivot["confirmed_at_idx"]))
    for i in range(start, len(df)):
        level = float(level_fn(i))
        if level <= 0:
            continue
        buffer = max(level * CONFIRM_PCT, float(atr[i]) * CONFIRM_ATR if np.isfinite(atr[i]) else 0.0)
        decisive = close[i] <= level - buffer
        two_closes = (
            i > start
            and close[i] < level
            and close[i - 1] < float(level_fn(i - 1))
        )
        if decisive and (_vol_ratio(df, i) >= VOLUME_MULT or two_closes):
            return i
    return None


def _trace(df: pd.DataFrame, pts: list[dict], neck_a: dict, neck_b: dict,
           confirm: int) -> list[dict]:
    return [
        {
            "points": [
                {"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
                for p in pts
            ],
            "style": "solid",
        },
        {
            "points": [
                {"t": _date(df, int(neck_a["idx"])), "p": round(float(neck_a["price"]), 4)},
                {"t": _date(df, confirm), "p": round(_line(confirm, neck_a, neck_b), 4)},
            ],
            "style": "dashed",
        },
    ]


def _event(df: pd.DataFrame, kind: str, name: str, pts: list[dict],
           confirm: int, levels: dict, note: str, score: int,
           neck_a: dict, neck_b: dict) -> dict:
    return {
        "kind": kind,
        "name": name,
        "direction": "bear",
        "start_idx": int(pts[0]["idx"]),
        "end_idx": int(pts[-1]["idx"]),
        "confirm_idx": int(confirm),
        "key_levels": {
            k: round(float(v), 4) if isinstance(v, (int, float, np.floating)) else v
            for k, v in levels.items()
        },
        "score": int(score),
        "star": True,
        "note": note,
        "trace": _trace(df, pts, neck_a, neck_b, confirm),
        "active": confirm >= len(df) - 160,
    }


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
            # 两顶之间若出现更高高点，不能把整段硬凑成M顶。
            highs_between = [p for p in ap[left_i + 1:right_i] if p["kind"] == "H"]
            if any(float(p["price"]) > max(p1, p2) * 1.005 for p in highs_between):
                continue
            depth = (min(p1, p2) - float(valley["price"])) / min(p1, p2)
            if depth < M_MIN_DEPTH:
                continue
            if not _top_position_ok(df, int(left["idx"]), max(p1, p2)):
                continue
            if not _prior_uptrend_ok(df, int(left["idx"]), p1, M_PRIOR_UP_PCT):
                continue
            neckline = float(valley["price"])
            confirm = _find_down_confirm(df, right, lambda _: neckline)
            if confirm is None:
                continue
            target = neckline - (max(p1, p2) - neckline)
            note = (
                f"严格M顶：两顶间隔{gap}根K线，高度差{abs(p2-p1)/avg:.1%}，"
                f"中间回撤{depth:.1%}；{_date(df, confirm)}有效跌破颈线{neckline:.2f}，"
                f"量比{_vol_ratio(df, confirm):.2f}。"
            )
            out.append(_event(
                df, "double_top", "M顶", [left, valley, right], confirm,
                {"neckline": neckline, "high1": p1, "high2": p2,
                 "measure_target": target, "invalidation": max(p1, p2)},
                note, 90, valley, valley,
            ))
            break
    return out


def _head_shoulders_tops(df: pd.DataFrame, ap: list[dict]) -> list[dict]:
    out: list[dict] = []
    for end in range(4, len(ap)):
        s1, n1, head, n2, s2 = ap[end - 4:end + 1]
        if "".join(str(p["kind"]) for p in (s1, n1, head, n2, s2)) != "HLHLH":
            continue
        span = int(s2["idx"] - s1["idx"])
        if not (HS_MIN_SPAN <= span <= HS_MAX_SPAN):
            continue
        left_time = int(head["idx"] - s1["idx"])
        right_time = int(s2["idx"] - head["idx"])
        if left_time <= 0 or right_time <= 0:
            continue
        time_ratio = left_time / right_time
        if not (HS_TIME_RATIO[0] <= time_ratio <= HS_TIME_RATIO[1]):
            continue

        ls, hd, rs = float(s1["price"]), float(head["price"]), float(s2["price"])
        shoulder_avg = (ls + rs) / 2.0
        if shoulder_avg <= 0 or abs(rs - ls) / shoulder_avg > HS_SHOULDER_TOL:
            continue
        prominence = (hd - max(ls, rs)) / hd if hd > 0 else 0.0
        if prominence < HS_HEAD_PROM:
            continue
        if not _top_position_ok(df, int(head["idx"]), hd):
            continue
        if not _prior_uptrend_ok(df, int(s1["idx"]), ls, HS_PRIOR_UP_PCT):
            continue

        neck_at_head = _line(int(head["idx"]), n1, n2)
        depth = (hd - neck_at_head) / hd if hd > 0 else 0.0
        if depth < HS_MIN_DEPTH:
            continue
        atr_ref = float(df["ATR14"].iloc[int(head["idx"])]) if "ATR14" in df.columns else np.nan
        neck_slope = abs((float(n2["price"]) - float(n1["price"])) /
                         max(1, int(n2["idx"] - n1["idx"])))
        if np.isfinite(atr_ref) and atr_ref > 0 and neck_slope > atr_ref * HS_MAX_NECK_SLOPE_ATR:
            continue

        confirm = _find_down_confirm(df, s2, lambda i: _line(i, n1, n2))
        if confirm is None:
            continue
        neckline = _line(confirm, n1, n2)
        target = neckline - (hd - neck_at_head)
        note = (
            f"严格头肩顶：结构跨度{span}根K线，左右肩差{abs(rs-ls)/shoulder_avg:.1%}，"
            f"头部高出双肩{prominence:.1%}，时间比{time_ratio:.2f}；"
            f"{_date(df, confirm)}有效跌破颈线{neckline:.2f}，量比{_vol_ratio(df, confirm):.2f}。"
        )
        out.append(_event(
            df, "head_shoulders_top", "头肩顶", [s1, n1, head, n2, s2], confirm,
            {"neckline": neckline, "neckline_left": float(n1["price"]),
             "neckline_right": float(n2["price"]), "head": hd,
             "shoulder_left": ls, "shoulder_right": rs,
             "measure_target": target, "invalidation": max(hd, rs)},
            note, 94, n1, n2,
        ))
    return out


def find_strict_top_patterns(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    confirmed = piv_mod.pivots_asof(pivots, len(df) - 1)
    ap = piv_mod.alternating(confirmed).to_dict("records")
    candidates = _m_tops(df, ap) + _head_shoulders_tops(df, ap)
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
