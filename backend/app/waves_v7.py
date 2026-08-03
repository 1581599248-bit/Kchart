"""严格、可解释的波浪结构识别。

只输出完成并右侧确认的结构：
- 标准推动浪：1-2-3-4-5，3浪不得最短，4浪不得明显进入1浪区间；
- 调整浪：A-B-C，B浪回撤和C浪延伸必须落在合理比例区间。

波浪属于主观性较高的结构工具，因此本模块宁缺毋滥，不输出“构筑中”计数。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

WAVE_ZZ_PCT = 0.08
MIN_IMPULSE_BARS = 55
MIN_IMPULSE_PCT = 0.15
MIN_LEG_BARS = 5
W2_RETRACE = (0.236, 0.786)
W4_RETRACE = (0.146, 0.50)
W5_TO_W1 = (0.382, 1.618)
W4_OVERLAP_TOL = 0.01

MIN_ABC_BARS = 24
MIN_A_PCT = 0.07
ABC_B_RETRACE = (0.236, 0.786)
ABC_C_EXTENSION = (0.618, 1.618)
PRIOR_TREND_BARS = 120
PRIOR_TREND_PCT = 0.15
ACTIVE_BARS = 120


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _trace(df: pd.DataFrame, pts: list[dict]) -> list[dict]:
    return [{
        "points": [
            {"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
            for p in pts
        ],
        "style": "solid",
    }]


def _event(df: pd.DataFrame, kind: str, name: str, direction: str,
           pts: list[dict], levels: dict, note: str, score: int) -> dict:
    end = pts[-1]
    end_idx = int(end["idx"])
    confirm_idx = int(end["confirmed_at_idx"])
    close = df["close"].to_numpy(dtype=float)
    invalidation = levels.get("invalidation")
    active = len(df) - 1 - end_idx <= ACTIVE_BARS
    if isinstance(invalidation, (int, float)):
        if direction == "bull" and np.any(close[confirm_idx + 1:] < float(invalidation)):
            active = False
        if direction == "bear" and np.any(close[confirm_idx + 1:] > float(invalidation)):
            active = False
    return {
        "kind": kind,
        "name": name,
        "direction": direction,
        "start_idx": int(pts[0]["idx"]),
        "end_idx": end_idx,
        "confirm_idx": confirm_idx,
        "key_levels": {
            k: round(float(v), 4) if isinstance(v, (int, float, np.floating)) else v
            for k, v in levels.items()
        },
        "score": int(score),
        "star": False,
        "note": note,
        "trace": _trace(df, pts),
        "active": bool(active),
    }


def _ratio(value: float, base: float) -> float:
    return float(value / base) if base > 0 else np.nan


def _duration_ok(pts: list[dict], min_total: int) -> bool:
    gaps = [int(pts[i + 1]["idx"]) - int(pts[i]["idx"]) for i in range(len(pts) - 1)]
    return min(gaps) >= MIN_LEG_BARS and sum(gaps) >= min_total


def _prior_uptrend(df: pd.DataFrame, top: dict) -> bool:
    idx = int(top["idx"])
    start = max(0, idx - PRIOR_TREND_BARS)
    lo = float(df["low"].iloc[start:idx + 1].min())
    return lo > 0 and float(top["price"]) / lo - 1.0 >= PRIOR_TREND_PCT


def _prior_downtrend(df: pd.DataFrame, bottom: dict) -> bool:
    idx = int(bottom["idx"])
    start = max(0, idx - PRIOR_TREND_BARS)
    hi = float(df["high"].iloc[start:idx + 1].max())
    return float(bottom["price"]) > 0 and hi / float(bottom["price"]) - 1.0 >= PRIOR_TREND_PCT


def _bull_impulse(df: pd.DataFrame, pts: list[dict]) -> dict | None:
    if "".join(str(p["kind"]) for p in pts) != "LHLHLH":
        return None
    if not _duration_ok(pts, MIN_IMPULSE_BARS):
        return None
    p0, p1, p2, p3, p4, p5 = [float(p["price"]) for p in pts]
    if not (p1 > p0 and p2 > p0 and p3 > p1 and p4 > p2 and p5 > p3):
        return None
    if p5 / p0 - 1.0 < MIN_IMPULSE_PCT:
        return None

    w1, w3, w5 = p1 - p0, p3 - p2, p5 - p4
    r2 = _ratio(p1 - p2, w1)
    r4 = _ratio(p3 - p4, w3)
    r5 = _ratio(w5, w1)
    if not (W2_RETRACE[0] <= r2 <= W2_RETRACE[1]):
        return None
    if not (W4_RETRACE[0] <= r4 <= W4_RETRACE[1]):
        return None
    if p4 < p1 * (1.0 - W4_OVERLAP_TOL):
        return None
    if w3 < min(w1, w5):
        return None
    if not (W5_TO_W1[0] <= r5 <= W5_TO_W1[1]):
        return None

    note = (
        f"标准上升推动浪完成：1浪{p0:.2f}→{p1:.2f}，"
        f"2浪回撤{r2:.3f}；3浪至{p3:.2f}且不是最短浪；"
        f"4浪回撤{r4:.3f}且未明显进入1浪区间；5浪至{p5:.2f}。"
    )
    return _event(
        df, "wave_impulse_up", "上升五浪", "bull", pts,
        {"wave1_top": p1, "wave2_low": p2, "wave3_top": p3,
         "wave4_low": p4, "wave5_top": p5, "invalidation": p4},
        note, 86,
    )


def _bear_impulse(df: pd.DataFrame, pts: list[dict]) -> dict | None:
    if "".join(str(p["kind"]) for p in pts) != "HLHLHL":
        return None
    if not _duration_ok(pts, MIN_IMPULSE_BARS):
        return None
    p0, p1, p2, p3, p4, p5 = [float(p["price"]) for p in pts]
    if not (p1 < p0 and p2 < p0 and p3 < p1 and p4 < p2 and p5 < p3):
        return None
    if p0 <= 0 or 1.0 - p5 / p0 < MIN_IMPULSE_PCT:
        return None

    w1, w3, w5 = p0 - p1, p2 - p3, p4 - p5
    r2 = _ratio(p2 - p1, w1)
    r4 = _ratio(p4 - p3, w3)
    r5 = _ratio(w5, w1)
    if not (W2_RETRACE[0] <= r2 <= W2_RETRACE[1]):
        return None
    if not (W4_RETRACE[0] <= r4 <= W4_RETRACE[1]):
        return None
    if p4 > p1 * (1.0 + W4_OVERLAP_TOL):
        return None
    if w3 < min(w1, w5):
        return None
    if not (W5_TO_W1[0] <= r5 <= W5_TO_W1[1]):
        return None

    note = (
        f"标准下跌推动浪完成：1浪{p0:.2f}→{p1:.2f}，"
        f"2浪反弹{r2:.3f}；3浪至{p3:.2f}且不是最短浪；"
        f"4浪反弹{r4:.3f}且未明显进入1浪区间；5浪至{p5:.2f}。"
    )
    return _event(
        df, "wave_impulse_down", "下跌五浪", "bear", pts,
        {"wave1_low": p1, "wave2_high": p2, "wave3_low": p3,
         "wave4_high": p4, "wave5_low": p5, "invalidation": p4},
        note, 86,
    )


def _down_abc(df: pd.DataFrame, pts: list[dict]) -> dict | None:
    if "".join(str(p["kind"]) for p in pts) != "HLHL":
        return None
    if not _duration_ok(pts, MIN_ABC_BARS):
        return None
    top, a, b, c = pts
    pt, pa, pb, pc = [float(p["price"]) for p in pts]
    a_len = pt - pa
    if pt <= 0 or a_len <= 0 or a_len / pt < MIN_A_PCT:
        return None
    if not (pa < pb < pt and pc < pa):
        return None
    if not _prior_uptrend(df, top):
        return None
    b_ratio = _ratio(pb - pa, a_len)
    c_ext = _ratio(pb - pc, a_len)
    if not (ABC_B_RETRACE[0] <= b_ratio <= ABC_B_RETRACE[1]):
        return None
    if not (ABC_C_EXTENSION[0] <= c_ext <= ABC_C_EXTENSION[1]):
        return None
    note = (
        f"下跌ABC完成：A浪{pt:.2f}→{pa:.2f}，"
        f"B浪回撤{b_ratio:.3f}至{pb:.2f}，"
        f"C浪为A浪的{c_ext:.3f}倍并跌至{pc:.2f}。"
    )
    return _event(
        df, "wave_abc_down", "下跌ABC", "bear", pts,
        {"top": pt, "a_low": pa, "b_high": pb, "c_low": pc,
         "invalidation": pb}, note, 82,
    )


def _up_abc(df: pd.DataFrame, pts: list[dict]) -> dict | None:
    if "".join(str(p["kind"]) for p in pts) != "LHLH":
        return None
    if not _duration_ok(pts, MIN_ABC_BARS):
        return None
    bottom, a, b, c = pts
    p0, pa, pb, pc = [float(p["price"]) for p in pts]
    a_len = pa - p0
    if p0 <= 0 or a_len <= 0 or a_len / p0 < MIN_A_PCT:
        return None
    if not (p0 < pb < pa and pc > pa):
        return None
    if not _prior_downtrend(df, bottom):
        return None
    b_ratio = _ratio(pa - pb, a_len)
    c_ext = _ratio(pc - pb, a_len)
    if not (ABC_B_RETRACE[0] <= b_ratio <= ABC_B_RETRACE[1]):
        return None
    if not (ABC_C_EXTENSION[0] <= c_ext <= ABC_C_EXTENSION[1]):
        return None
    note = (
        f"上升ABC完成：A浪{p0:.2f}→{pa:.2f}，"
        f"B浪回撤{b_ratio:.3f}至{pb:.2f}，"
        f"C浪为A浪的{c_ext:.3f}倍并升至{pc:.2f}。"
    )
    return _event(
        df, "wave_abc_up", "上升ABC", "bull", pts,
        {"bottom": p0, "a_high": pa, "b_low": pb, "c_high": pc,
         "invalidation": pb}, note, 82,
    )


def find_waves(df: pd.DataFrame) -> list[dict]:
    """返回严格完成的推动浪与ABC结构，去除重叠低分计数。"""
    zz = piv_mod.zigzag(df, min_pct=WAVE_ZZ_PCT)
    if len(zz) < 4:
        return []
    pts = zz.to_dict("records")
    candidates: list[dict] = []

    for end in range(5, len(pts)):
        window = pts[end - 5:end + 1]
        event = _bull_impulse(df, window) or _bear_impulse(df, window)
        if event:
            candidates.append(event)

    for end in range(3, len(pts)):
        window = pts[end - 3:end + 1]
        event = _down_abc(df, window) or _up_abc(df, window)
        if event:
            candidates.append(event)

    candidates.sort(key=lambda e: (int(e["end_idx"]), -int(e["score"])))
    out: list[dict] = []
    for event in candidates:
        overlaps = [
            old for old in out
            if old["kind"] == event["kind"]
            and abs(int(old["end_idx"]) - int(event["end_idx"])) <= 20
        ]
        if overlaps:
            best = max(overlaps + [event], key=lambda e: int(e["score"]))
            if best is event:
                for old in overlaps:
                    out.remove(old)
                out.append(event)
        else:
            out.append(event)
    return sorted(out, key=lambda e: int(e["confirm_idx"]))
