"""全历史谐波XABCD与PRZ生命周期。

完成D点必须右侧确认；进入PRZ只表示潜在反转区。随后只有价格按预期离开PRZ
才标“谐波确认”，向反方向穿透PRZ则标“谐波失效”。
"""
from __future__ import annotations

import pandas as pd

from . import harmonics as base
from . import pivots as piv_mod

REVERSAL_CONFIRM = 0.025
FAIL_BUFFER = 0.02
FOLLOW_BARS = 15


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _zone(df: pd.DataFrame, start: int, end: int, lo: float, hi: float,
          direction: str) -> dict:
    return {
        "t1": _date(df, start),
        "t2": _date(df, min(end, len(df) - 1)),
        "top": round(float(max(lo, hi)), 4),
        "bottom": round(float(min(lo, hi)), 4),
        "color": "rgba(239,83,80,.10)" if direction == "bear" else "rgba(38,166,154,.10)",
    }


def _annotation(df: pd.DataFrame, idx: int, price: float, label: str,
                direction: str, detail: str, score: int,
                zones: list | None = None, polylines: list | None = None,
                star: bool = False) -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(price), 4),
        "kind": "harmonic",
        "label": label,
        "direction": direction,
        "star": bool(star),
        "detail": detail,
        "lines": [],
        "zones": zones or [],
        "polylines": polylines or [],
        "active": idx >= len(df) - 120,
        "_score": int(score),
        "_grp": f"harmonic:{label}:{idx}",
    }


def _polyline(df: pd.DataFrame, pts: list[dict]) -> list[dict]:
    return [{
        "points": [
            {"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
            for p in pts
        ],
        "style": "solid",
    }]


def _completed_windows(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    ap = piv_mod.alternating(pivots).to_dict("records")
    if len(ap) < 5:
        return []
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    out: list[dict] = []
    used: list[tuple[int, str]] = []

    for end in range(4, len(ap)):
        pts = ap[end - 4:end + 1]
        ratios = base._ratios(pts)
        if ratios is None:
            continue
        match = base._match(ratios)
        if match is None:
            continue
        name, d_ratio, ref = match
        d = pts[4]
        d_confirm = int(d["confirmed_at_idx"])
        if d_confirm >= len(df):
            continue
        # 避免同一区域同方向重复命名多个近似谐波。
        direction = "bull" if ratios["bullish"] else "bear"
        if any(abs(d_confirm - old_idx) <= 8 and direction == old_dir for old_idx, old_dir in used):
            continue

        if name == "Shark":
            x_p, c_p = float(pts[0]["price"]), float(pts[3]["price"])
            rng = abs(c_p - x_p)
            lo_r = 0.886 * (1 - base.RATIO_TOL)
            hi_r = 1.13 * (1 + base.RATIO_TOL)
            if ratios["bullish"]:
                prz_lo, prz_hi = c_p - hi_r * rng, c_p - lo_r * rng
            else:
                prz_lo, prz_hi = c_p + lo_r * rng, c_p + hi_r * rng
        else:
            prz_lo, prz_hi = base._prz_from_ratio(pts, d_ratio, ref, ratios["bullish"])
        prz_lo, prz_hi = min(prz_lo, prz_hi), max(prz_lo, prz_hi)

        zone_end = min(len(df) - 1, d_confirm + FOLLOW_BARS)
        detail = (
            f"{_date(df, d_confirm)} {name} D点右侧确认，PRZ {prz_lo:.2f}~{prz_hi:.2f}；"
            "进入PRZ不等于反转，需等待价格离开区域确认。"
        )
        out.append(_annotation(
            df, d_confirm, float(d["price"]), f"{name} PRZ", direction,
            detail, 66, zones=[_zone(df, d_confirm, zone_end, prz_lo, prz_hi, direction)],
            polylines=_polyline(df, pts), star=False,
        ))

        confirmation = None
        failure = None
        for i in range(d_confirm + 1, zone_end + 1):
            if direction == "bull":
                if close[i] >= prz_hi * (1 + REVERSAL_CONFIRM):
                    confirmation = i
                    break
                if close[i] <= prz_lo * (1 - FAIL_BUFFER):
                    failure = i
                    break
            else:
                if close[i] <= prz_lo * (1 - REVERSAL_CONFIRM):
                    confirmation = i
                    break
                if close[i] >= prz_hi * (1 + FAIL_BUFFER):
                    failure = i
                    break

        if confirmation is not None:
            out.append(_annotation(
                df, confirmation,
                low[confirmation] if direction == "bear" else high[confirmation],
                "谐波确认", direction,
                f"{_date(df, confirmation)} 价格按预期方向离开{name} PRZ，反转得到价格确认。",
                76, star=True,
            ))
        elif failure is not None:
            out.append(_annotation(
                df, failure,
                high[failure] if direction == "bear" else low[failure],
                "谐波失效", "bear" if direction == "bull" else "bull",
                f"{_date(df, failure)} 价格反向穿透{name} PRZ，原谐波假设失效。",
                70, star=False,
            ))
        used.append((d_confirm, direction))
    return out


def _current_potential(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    """只在现价已经进入潜在PRZ时提示进行中谐波，避免远端投影噪声。"""
    candidates = base.find_xabcd(pivots, asof_idx=len(df) - 1)
    close = float(df["close"].iloc[-1])
    out: list[dict] = []
    for ev in candidates:
        if ev.get("completed"):
            continue
        lo, hi = sorted((float(ev["prz_low"]), float(ev["prz_high"])))
        if not (lo <= close <= hi):
            continue
        out.append(_annotation(
            df, len(df) - 1, close, "潜在PRZ", ev["direction"],
            f"现价进入潜在{ev['name']} PRZ {lo:.2f}~{hi:.2f}，D点尚未右侧确认。",
            52, zones=[_zone(df, len(df)-1, len(df)-1, lo, hi, ev["direction"])],
        ))
    return out


def find_harmonic_annotations(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    out = _completed_windows(df, pivots) + _current_potential(df, pivots)
    out.sort(key=lambda e: (e["bar_idx"], e["label"]))
    return out
