"""打印真实上证3% ZigZag和v13原始候选，定位近期M顶被哪一步删除。"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from backend.app import indicators, pivots as piv_mod
from backend.app import reversal_patterns_v11 as eng
from backend.app import reversal_patterns_v13 as v13

URL = "https://kchart.onrender.com/api/chart?ts_code=000001.SH&timeframe=1d&refresh=0"


def fetch_df() -> pd.DataFrame:
    last = None
    for attempt in range(8):
        try:
            r = requests.get(URL, timeout=90)
            r.raise_for_status()
            bars = r.json().get("bars") or []
            if len(bars) < 200:
                raise RuntimeError(len(bars))
            df = pd.DataFrame(bars).rename(columns={
                "time": "trade_date", "o": "open", "h": "high",
                "l": "low", "c": "close", "v": "vol",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"], unit="s", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            df["amount"] = 0.0
            return indicators.compute_all(df.rename(columns={"trade_date": "ts"})).rename(columns={"ts": "trade_date"})
        except Exception as exc:
            last = exc
            time.sleep(15 + attempt * 5)
    raise RuntimeError(last)


def describe(df: pd.DataFrame, a: dict, m: dict, b: dict) -> None:
    print("TRY", eng._date(df, a["idx"]), a["kind"], round(a["price"], 2),
          eng._date(df, m["idx"]), m["kind"], round(m["price"], 2),
          eng._date(df, b["idx"]), b["kind"], round(b["price"], 2))
    old = eng.MIN_SCORE
    eng.MIN_SCORE = -1
    try:
        event = eng._candidate(df, a, m, b, top=True)
    finally:
        eng.MIN_SCORE = old
    print("RESULT", None if event is None else {
        "confirm": eng._date(df, event["confirm_idx"]),
        "neckline": event["key_levels"]["neckline"],
        "score": event["score"],
        "start": event["start_idx"], "end": event["end_idx"],
    })


def main() -> None:
    df = fetch_df()
    zz = piv_mod.zigzag(df, min_pct=eng.INDEX_ZIGZAG_PCT)
    zz = piv_mod.pivots_asof(zz, len(df) - 1)
    pts = zz.to_dict("records")
    print("=== 3% ZIGZAG SINCE 2025-10 ===")
    for p in pts:
        if eng._date(df, p["idx"]) >= "2025-10-01":
            print(eng._date(df, p["idx"]), p["kind"], round(float(p["price"]), 2), "known", p["confirmed_at_idx"])
    print("=== HLH TRIPLES SINCE 2026 ===")
    for i in range(2, len(pts)):
        a, m, b = pts[i-2:i+1]
        if f"{a['kind']}{m['kind']}{b['kind']}" == "HLH" and eng._date(df, a["idx"]) >= "2026-01-01":
            describe(df, a, m, b)
    print("=== RAW V13 ===")
    for e in v13._raw_candidates(df):
        if eng._date(df, e["start_idx"]) >= "2026-01-01":
            print(e["name"], eng._date(df, e["start_idx"]), eng._date(df, e["end_idx"]), eng._date(df, e["confirm_idx"]), e["key_levels"], e["score"])


if __name__ == "__main__":
    main()
