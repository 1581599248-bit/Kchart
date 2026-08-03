"""补充结构识别：三重顶、扩散三角与趋势线突破。

该模块只消费已经右侧确认的 pivot。结构确认必须发生在最后一个组成 pivot
confirmed_at_idx 之后，避免未来函数。与 patterns.py 合并使用，不替代原有成熟规则。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

TRIPLE_TOL = 0.045
TRIPLE_MIN_DEPTH = 0.07
TRIPLE_MIN_GAP = 12
TRIPLE_MAX_SPAN = 220
BROADEN_MIN_EXPANSION = 1.25
TREND_BREAK_MIN_SPAN = 25
TREND_BREAK_COOLDOWN = 35


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _line_value(i: int, i1: int, p1: float, i2: int, p2: float) -> float:
    if i2 == i1:
        return float(p2)
    return float(p1 + (p2 - p1) * (i - i1) / (i2 - i1))


def _find_break(close: np.ndarray, start: int, direction: str, level_fn) -> int | None:
    for i in range(max(0, int(start)), len(close)):
        lv = float(level_fn(i))
        if direction == "up" and close[i] > lv:
            return i
        if direction == "down" and close[i] < lv:
            return i
    return None


def _event(kind: str, name: str, direction: str, pts: list[dict], confirm_idx: int | None,
           levels: dict, note: str, trace: list[dict], score: int = 82) -> dict:
    return {
        "kind": kind,
        "name": name,
        "direction": direction,
        "start_idx": int(pts[0]["idx"]),
        "end_idx": int(pts[-1]["idx"]),
        "confirm_idx": None if confirm_idx is None else int(confirm_idx),
        "key_levels": {
            k: round(float(v), 4) if isinstance(v, (int, float, np.floating)) else v
            for k, v in levels.items()
        },
        "score": int(score if confirm_idx is not None else score // 2),
        "star": bool(confirm_idx is not None),
        "note": note if confirm_idx is not None else note + "（构筑中）",
        "trace": trace,
        "active": True,
    }


def _trace(df: pd.DataFrame, pts: list[dict]) -> list[dict]:
    return [{
        "points": [{"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)} for p in pts],
        "style": "solid",
    }]


def _position_ok(df: pd.DataFrame, idx: int, price: float, direction: str) -> bool:
    start = max(0, int(idx) - 250)
    hi = float(df["high"].iloc[start:idx + 1].max())
    lo = float(df["low"].iloc[start:idx + 1].min())
    if hi <= lo:
        return True
    pos = (price - lo) / (hi - lo)
    return pos >= 0.62 if direction == "bear" else pos <= 0.38


def _triple_patterns(df: pd.DataFrame, ap: list[dict]) -> list[dict]:
    close = df["close"].to_numpy(dtype=float)
    out: list[dict] = []
    for j in range(4, len(ap)):
        pts = ap[j - 4:j + 1]
        kinds = "".join(str(p["kind"]) for p in pts)
        if kinds not in ("HLHLH", "LHLHL"):
            continue
        span = int(pts[-1]["idx"] - pts[0]["idx"])
        gaps = [int(pts[k + 1]["idx"] - pts[k]["idx"]) for k in range(4)]
        if span > TRIPLE_MAX_SPAN or min(gaps) < TRIPLE_MIN_GAP:
            continue

        is_top = kinds == "HLHLH"
        extremes = [float(pts[k]["price"]) for k in (0, 2, 4)]
        mean_ext = float(np.mean(extremes))
        if mean_ext <= 0 or (max(extremes) - min(extremes)) / mean_ext > TRIPLE_TOL:
            continue

        mids = [pts[1], pts[3]]
        if is_top:
            neckline_ref = max(float(mids[0]["price"]), float(mids[1]["price"]))
            depth = (mean_ext - neckline_ref) / mean_ext
            direction, kind, name = "bear", "triple_top", "三重顶"
            if depth < TRIPLE_MIN_DEPTH or not _position_ok(df, pts[2]["idx"], mean_ext, direction):
                continue
            level_fn = lambda i, a=mids[0], b=mids[1]: _line_value(
                i, int(a["idx"]), float(a["price"]), int(b["idx"]), float(b["price"])
            )
            confirm = _find_break(
                close, max(int(pts[-1]["confirmed_at_idx"]), int(pts[-1]["idx"]) + 1),
                "down", level_fn,
            )
            neck_at_end = level_fn(int(pts[-1]["idx"]))
            target = neck_at_end - (mean_ext - neck_at_end)
            invalidation = max(extremes) * 1.015
        else:
            neckline_ref = min(float(mids[0]["price"]), float(mids[1]["price"]))
            depth = (neckline_ref - mean_ext) / max(mean_ext, 1e-9)
            direction, kind, name = "bull", "triple_bottom", "三重底"
            if depth < TRIPLE_MIN_DEPTH or not _position_ok(df, pts[2]["idx"], mean_ext, direction):
                continue
            level_fn = lambda i, a=mids[0], b=mids[1]: _line_value(
                i, int(a["idx"]), float(a["price"]), int(b["idx"]), float(b["price"])
            )
            confirm = _find_break(
                close, max(int(pts[-1]["confirmed_at_idx"]), int(pts[-1]["idx"]) + 1),
                "up", level_fn,
            )
            neck_at_end = level_fn(int(pts[-1]["idx"]))
            target = neck_at_end + (neck_at_end - mean_ext)
            invalidation = min(extremes) * 0.985

        note = (
            f"{name}：三个{'高点' if is_top else '低点'}离散度"
            f"{(max(extremes)-min(extremes))/mean_ext:.1%}，颈线约{neck_at_end:.2f}；"
            + (f"{_date(df, confirm)} 收盘确认。" if confirm is not None else "等待颈线确认。")
        )
        tr = _trace(df, pts)
        tr.append({
            "points": [
                {"t": _date(df, int(mids[0]["idx"])), "p": round(float(mids[0]["price"]), 4)},
                {"t": _date(df, int(confirm if confirm is not None else len(df)-1)),
                 "p": round(float(level_fn(confirm if confirm is not None else len(df)-1)), 4)},
            ],
            "style": "dashed",
        })
        out.append(_event(
            kind, name, direction, pts, confirm,
            {"neckline": neck_at_end, "measure_target": target, "invalidation": invalidation},
            note, tr, 84,
        ))
    return out


def _broadening(df: pd.DataFrame, ap: list[dict]) -> list[dict]:
    """五点扩散三角：高点抬高、低点降低、区间宽度明显扩大。"""
    close = df["close"].to_numpy(dtype=float)
    out: list[dict] = []
    for j in range(4, len(ap)):
        pts = ap[j - 4:j + 1]
        kinds = "".join(str(p["kind"]) for p in pts)
        if kinds not in ("HLHLH", "LHLHL"):
            continue
        highs = [p for p in pts if p["kind"] == "H"]
        lows = [p for p in pts if p["kind"] == "L"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        if not all(highs[k + 1]["price"] > highs[k]["price"] for k in range(len(highs)-1)):
            continue
        if not all(lows[k + 1]["price"] < lows[k]["price"] for k in range(len(lows)-1)):
            continue
        first_width = float(highs[0]["price"] - lows[0]["price"])
        last_width = float(highs[-1]["price"] - lows[-1]["price"])
        if first_width <= 0 or last_width / first_width < BROADEN_MIN_EXPANSION:
            continue

        upper = lambda i, a=highs[-2], b=highs[-1]: _line_value(
            i, int(a["idx"]), float(a["price"]), int(b["idx"]), float(b["price"])
        )
        lower = lambda i, a=lows[-2], b=lows[-1]: _line_value(
            i, int(a["idx"]), float(a["price"]), int(b["idx"]), float(b["price"])
        )
        start = max(int(pts[-1]["confirmed_at_idx"]), int(pts[-1]["idx"]) + 1)
        up_break = _find_break(close, start, "up", upper)
        dn_break = _find_break(close, start, "down", lower)
        candidates = [(x, d) for x, d in ((up_break, "bull"), (dn_break, "bear")) if x is not None]
        confirm, direction = min(candidates, key=lambda x: x[0]) if candidates else (None, "range")
        name = "扩散三角"
        note = (
            f"扩散三角：区间宽度扩大至原来的{last_width/first_width:.1f}倍；"
            + (f"{_date(df, confirm)} {'向上突破' if direction=='bull' else '向下跌破'}。"
               if confirm is not None else "仍在边界内震荡。")
        )
        tr = _trace(df, pts)
        tr += [
            {"points": [
                {"t": _date(df, int(highs[-2]["idx"])), "p": float(highs[-2]["price"])},
                {"t": _date(df, int(confirm if confirm is not None else len(df)-1)),
                 "p": upper(confirm if confirm is not None else len(df)-1)},
            ], "style": "dashed"},
            {"points": [
                {"t": _date(df, int(lows[-2]["idx"])), "p": float(lows[-2]["price"])},
                {"t": _date(df, int(confirm if confirm is not None else len(df)-1)),
                 "p": lower(confirm if confirm is not None else len(df)-1)},
            ], "style": "dashed"},
        ]
        out.append(_event(
            "broadening_triangle", name, direction, pts, confirm,
            {"upper": upper(int(pts[-1]["idx"])), "lower": lower(int(pts[-1]["idx"]))},
            note, tr, 70,
        ))
    return out


def _trendline_breaks(df: pd.DataFrame, ap: list[dict]) -> list[dict]:
    """三点趋势线突破：下降高点线被上破、上升低点线被下破。"""
    close = df["close"].to_numpy(dtype=float)
    out: list[dict] = []
    last_confirm = {"bull": -10_000, "bear": -10_000}

    for kind, direction, name in (("H", "bull", "突破趋势"), ("L", "bear", "跌破趋势")):
        pts_kind = [p for p in ap if p["kind"] == kind]
        for j in range(2, len(pts_kind)):
            pts = pts_kind[j-2:j+1]
            span = int(pts[-1]["idx"] - pts[0]["idx"])
            if span < TREND_BREAK_MIN_SPAN:
                continue
            prices = [float(p["price"]) for p in pts]
            if direction == "bull" and not (prices[0] > prices[1] > prices[2]):
                continue
            if direction == "bear" and not (prices[0] < prices[1] < prices[2]):
                continue
            line = lambda i, a=pts[0], b=pts[2]: _line_value(
                i, int(a["idx"]), float(a["price"]), int(b["idx"]), float(b["price"])
            )
            start = max(int(pts[-1]["confirmed_at_idx"]), int(pts[-1]["idx"]) + 1)
            confirm = _find_break(close, start, "up" if direction == "bull" else "down", line)
            if confirm is None or confirm - last_confirm[direction] < TREND_BREAK_COOLDOWN:
                continue
            # 必须有至少1%的有效穿越，过滤刚好蹭线。
            lv = line(confirm)
            if abs(close[confirm] / lv - 1.0) < 0.01:
                continue
            last_confirm[direction] = confirm
            note = (
                f"{_date(df, confirm)} 收盘价{close[confirm]:.2f}"
                f"{'上破下降趋势线' if direction=='bull' else '下破上升趋势线'}{lv:.2f}。"
            )
            trace = [{
                "points": [
                    {"t": _date(df, int(pts[0]["idx"])), "p": float(pts[0]["price"])},
                    {"t": _date(df, confirm), "p": float(lv)},
                ],
                "style": "dashed",
            }]
            out.append(_event(
                "trendline_break", name, direction, pts, confirm,
                {"trendline": lv, "invalidation": lv}, note, trace, 68,
            ))
    return out


def find_patterns_ext(df: pd.DataFrame, pivots: pd.DataFrame,
                      timeframe: str = "1d") -> list[dict]:
    del timeframe  # 当前补充结构只用于日线统一接口，保留参数以兼容未来扩展。
    confirmed = piv_mod.pivots_asof(pivots, len(df) - 1)
    ap = piv_mod.alternating(confirmed).to_dict("records")
    events = _triple_patterns(df, ap) + _broadening(df, ap) + _trendline_breaks(df, ap)
    events.sort(key=lambda e: (e["confirm_idx"] if e["confirm_idx"] is not None else e["end_idx"], e["kind"]))
    return events
