"""从生产 /api/chart 拉取真实K线，审计M顶候选和现有规则拒绝原因。

仅用于重建分支CI，不进入生产接口。
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import requests

from backend.app import indicators, pivots as piv_mod, strict_tops_v9

URL = "https://kchart.onrender.com/api/chart?ts_code=000001.SH&timeframe=1d&refresh=0"


def fetch_bars() -> pd.DataFrame:
    last = None
    for attempt in range(8):
        try:
            r = requests.get(URL, timeout=90)
            r.raise_for_status()
            payload = r.json()
            bars = payload.get("bars") or []
            if len(bars) >= 200:
                df = pd.DataFrame(bars).rename(columns={
                    "time": "trade_date", "o": "open", "h": "high",
                    "l": "low", "c": "close", "v": "vol",
                })
                df["trade_date"] = pd.to_datetime(df["trade_date"], unit="s", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
                df["amount"] = 0.0
                return df.sort_values("trade_date").reset_index(drop=True)
        except Exception as exc:  # pragma: no cover - live diagnostic
            last = exc
        time.sleep(15 + attempt * 5)
    raise RuntimeError(f"production chart fetch failed: {last}")


def atr_at(df: pd.DataFrame, idx: int) -> float:
    x = float(df["ATR14"].iloc[idx])
    return x if math.isfinite(x) and x > 0 else float("nan")


def top_position(df: pd.DataFrame, idx: int, price: float) -> float:
    start = max(0, idx - 251)
    hi = float(df["high"].iloc[start:idx + 1].max())
    lo = float(df["low"].iloc[start:idx + 1].min())
    return (price - lo) / (hi - lo) if hi > lo else float("nan")


def prior_advance(df: pd.DataFrame, idx: int, price: float) -> tuple[float, float]:
    start = max(0, idx - 252)
    lo = float(df["low"].iloc[start:idx + 1].min())
    atr = atr_at(df, idx)
    advance = price - lo
    return advance / lo if lo > 0 else float("nan"), advance / atr if math.isfinite(atr) else float("nan")


def find_confirm(df: pd.DataFrame, right: dict, neckline: float) -> int | None:
    close = df["close"].to_numpy(float)
    atr = df["ATR14"].to_numpy(float)
    start = max(int(right["idx"]) + 1, int(right["confirmed_at_idx"]))
    for i in range(start, len(df)):
        buffer = max(neckline * 0.01, (atr[i] * 0.5 if math.isfinite(atr[i]) else 0.0))
        decisive = close[i] <= neckline - buffer
        two_closes = i > start and close[i] < neckline and close[i - 1] < neckline
        if decisive and two_closes:
            return i
    return None


def audit_m_candidates(df: pd.DataFrame, ap: list[dict]) -> None:
    rows = []
    for right_i in range(2, len(ap)):
        right = ap[right_i]
        if right["kind"] != "H":
            continue
        for left_i in range(right_i - 2, -1, -2):
            left = ap[left_i]
            if left["kind"] != "H":
                continue
            gap = int(right["idx"] - left["idx"])
            if gap > 150:
                break
            if gap < 10:
                continue
            lows = [p for p in ap[left_i + 1:right_i] if p["kind"] == "L"]
            if not lows:
                continue
            valley = min(lows, key=lambda p: float(p["price"]))
            p1, p2, pv = map(float, (left["price"], right["price"], valley["price"]))
            rel = p2 / p1 - 1.0
            depth = (min(p1, p2) - pv) / min(p1, p2)
            atr = atr_at(df, int(left["idx"]))
            depth_atr = (min(p1, p2) - pv) / atr if math.isfinite(atr) else float("nan")
            pos = top_position(df, int(left["idx"]), max(p1, p2))
            prior_pct, prior_atr = prior_advance(df, int(left["idx"]), p1)
            confirm = find_confirm(df, right, pv)
            total_span = None if confirm is None else confirm - int(left["idx"])
            reasons = []
            if gap < strict_tops_v9.M_MIN_PEAK_GAP:
                reasons.append("peak_gap")
            if not (-strict_tops_v9.M_RIGHT_TOP_DOWN_TOL <= rel <= strict_tops_v9.M_RIGHT_TOP_UP_TOL):
                reasons.append("top_similarity")
            if depth < strict_tops_v9.M_MIN_DEPTH_PCT:
                reasons.append("depth_pct")
            if math.isfinite(depth_atr) and depth_atr < strict_tops_v9.M_MIN_DEPTH_ATR:
                reasons.append("depth_atr")
            if pos < strict_tops_v9.M_TOP_POSITION_MIN:
                reasons.append("top_position")
            if prior_pct < strict_tops_v9.M_PRIOR_UP_PCT:
                reasons.append("prior_pct")
            if math.isfinite(prior_atr) and prior_atr < strict_tops_v9.M_PRIOR_UP_ATR:
                reasons.append("prior_atr")
            if confirm is None:
                reasons.append("no_confirm")
            elif total_span is not None and total_span < strict_tops_v9.M_MIN_TOTAL_SPAN:
                reasons.append("total_span")
            rows.append({
                "left": str(df["trade_date"].iloc[int(left["idx"])].date()),
                "valley": str(df["trade_date"].iloc[int(valley["idx"])].date()),
                "right": str(df["trade_date"].iloc[int(right["idx"])].date()),
                "p1": round(p1, 2), "pv": round(pv, 2), "p2": round(p2, 2),
                "gap": gap, "right_rel": round(rel, 4), "depth": round(depth, 4),
                "depth_atr": round(depth_atr, 2) if math.isfinite(depth_atr) else None,
                "position": round(pos, 3), "prior_pct": round(prior_pct, 3),
                "prior_atr": round(prior_atr, 2) if math.isfinite(prior_atr) else None,
                "confirm": None if confirm is None else str(df["trade_date"].iloc[confirm].date()),
                "total_span": total_span,
                "reasons": ",".join(reasons) or "PASS",
            })
    out = pd.DataFrame(rows)
    if out.empty:
        print("NO M CANDIDATES")
        return
    out = out[out["left"] >= "2024-01-01"].sort_values(["right", "depth"], ascending=[False, False]).head(40)
    print("\n=== M CANDIDATES SINCE 2024 ===")
    print(out.to_string(index=False))


def main() -> None:
    raw = fetch_bars()
    df = indicators.compute_all(raw.rename(columns={"trade_date": "ts"})).rename(columns={"ts": "trade_date"})
    piv = piv_mod.find_pivots(df)
    ap = piv_mod.alternating(piv_mod.pivots_asof(piv, len(df) - 1)).to_dict("records")
    print(f"bars={len(df)} asof={df['trade_date'].iloc[-1].date()} pivots={len(ap)}")
    print("\n=== LAST 35 CONFIRMED PIVOTS ===")
    for p in ap[-35:]:
        print(str(df["trade_date"].iloc[int(p["idx"])].date()), p["kind"], round(float(p["price"]), 2), "known", int(p["confirmed_at_idx"]))
    print("\n=== CURRENT V9 EVENTS ===")
    for event in strict_tops_v9.find_strict_top_patterns(df, piv):
        print(event["name"], df["trade_date"].iloc[event["start_idx"]].date(), df["trade_date"].iloc[event["end_idx"]].date(), df["trade_date"].iloc[event["confirm_idx"]].date(), event["note"])
    audit_m_candidates(df, ap)


if __name__ == "__main__":
    main()
