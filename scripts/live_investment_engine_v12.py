"""Live audit for v15 M/W geometry and signal recall.

Production API supplies bars only. Analysis runs from the checked-out branch.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from backend.app import analysis_v7, indicators
from backend.app import investment_engine_v15 as engine

BASE_URL = "https://kchart.onrender.com/api/chart"
INDEX_CODES = ["000001.SH", "000300.SH", "000905.SH", "000688.SH", "399001.SZ", "399006.SZ"]
EQUITY_CODES = ["600519.SH", "300750.SZ", "002594.SZ", "601318.SH", "000333.SZ", "688981.SH"]


def fetch_df(code: str) -> pd.DataFrame:
    last = None
    for attempt in range(8):
        try:
            response = requests.get(BASE_URL, params={"ts_code": code, "timeframe": "1d", "refresh": 0}, timeout=90)
            response.raise_for_status()
            bars = response.json().get("bars") or []
            if len(bars) < 600:
                raise RuntimeError(f"{code}: only {len(bars)} bars")
            df = pd.DataFrame(bars).rename(columns={
                "time": "trade_date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "vol",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"], unit="s", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            df["amount"] = 0.0
            return indicators.compute_all(df.rename(columns={"trade_date": "ts"})).rename(columns={"ts": "trade_date"})
        except Exception as exc:
            last = exc
            time.sleep(12 + attempt * 4)
    raise RuntimeError(last)


def date_at(df: pd.DataFrame, idx: int):
    return pd.to_datetime(df["trade_date"].iloc[int(idx)]).date()


def event_key(event: dict) -> tuple:
    levels = event.get("key_levels") or {}
    return (
        str(event.get("kind")), int(event.get("start_idx", -1)), int(event.get("middle_idx", -1)),
        int(event.get("end_idx", -1)), int(event.get("confirm_idx", -1)),
        round(float(levels.get("neckline", 0)), 3),
    )


def validate_macro_geometry(df: pd.DataFrame, event: dict) -> None:
    traces = event.get("trace") or []
    assert len(traces) >= 2, event
    letter = traces[0].get("points") or []
    assert len(letter) >= 3, event
    levels = event.get("key_levels") or {}
    neckline = float(levels["neckline"])
    assert levels.get("neckline_source") == "principal_intervening_pivot", event
    middle_idx = int(event["middle_idx"])
    assert abs(neckline - float(df["low" if event["direction"] == "bear" else "high"].iloc[middle_idx])) < 1e-6

    start, end = int(event["start_idx"]), int(event["end_idx"])
    candidates = engine.rules.piv_mod.zigzag(df, min_pct=float(event["scale"]))
    candidates = engine.rules.piv_mod.pivots_asof(candidates, len(df) - 1)
    points = engine.rules.piv_mod.alternating(candidates).to_dict("records")
    middle_kind = "L" if event["direction"] == "bear" else "H"
    middle = [p for p in points if start < int(p["idx"]) < end and p["kind"] == middle_kind]
    assert middle, event
    expected = min(float(p["price"]) for p in middle) if event["direction"] == "bear" else max(float(p["price"]) for p in middle)
    assert abs(neckline - expected) < 1e-6, (event, expected)


def validate_result(code: str, df: pd.DataFrame, asset_kind: str) -> tuple[int, int]:
    patterns = engine.find_investment_patterns(df)
    result = analysis_v7.analyze(df, asset_kind=asset_kind)
    diagnostics = result["diagnostics"]
    labels = [str(event.get("label") or "") for event in result["annotations"]]
    assert diagnostics["analysis_version"] == analysis_v7.ANALYSIS_VERSION
    assert diagnostics["causal"] is True
    assert len(patterns) <= diagnostics["pattern_budget"] <= engine.MAX_PATTERN_EVENTS
    assert diagnostics["indicator_events"] <= diagnostics["indicator_budget"] <= engine.MAX_INDICATOR_EVENTS
    assert not {"EMA金叉", "EMA死叉", "MACD金叉", "MACD死叉", "结构失效"}.intersection(labels)
    assert all(len(label) <= 8 for label in labels)
    assert all(e.get("label") in {"0.5", "0.618"} for e in result["annotations"] if e.get("kind") == "fibonacci")
    for event in patterns:
        if event.get("kind") in {"macro_double_top", "macro_double_bottom"}:
            validate_macro_geometry(df, event)
    print(
        code, f"bars={len(df)}", f"asof={date_at(df, len(df)-1)}",
        f"patterns={len(patterns)}/{diagnostics['pattern_budget']}",
        f"indicators={diagnostics['indicator_events']}/{diagnostics['indicator_budget']}",
        "structures=" + ",".join(
            f"{e['kind']}[{date_at(df,e['start_idx'])}->{date_at(df,e['confirm_idx'])}]"
            for e in patterns
        ),
    )
    return len(patterns), int(diagnostics["indicator_events"])


def validate_shanghai(df: pd.DataFrame) -> None:
    patterns = engine.find_investment_patterns(df)
    tops_2026 = [e for e in patterns if e.get("kind") == "macro_double_top" and date_at(df, e["confirm_idx"]).year == 2026]
    assert tops_2026, patterns
    top = max(tops_2026, key=lambda e: int(e["confirm_idx"]))
    validate_macro_geometry(df, top)
    assert date_at(df, top["start_idx"]) <= pd.Timestamp("2026-03-01").date(), top
    assert len((top.get("trace") or [])[0].get("points") or []) >= 3, top

    # The 2024 W-shaped reversal must remain visible instead of being displaced by
    # the four-event cap. Accept construction starting in 2023 or 2024 and a
    # breakout/confirmation in 2024 or 2025.
    w_events = [
        e for e in patterns if e.get("kind") == "macro_double_bottom"
        and date_at(df, e["start_idx"]).year in {2023, 2024}
        and date_at(df, e["confirm_idx"]).year in {2024, 2025}
    ]
    assert w_events, patterns
    for event in w_events:
        validate_macro_geometry(df, event)

    cutoff = min(len(df), int(top["confirm_idx"]) + 6)
    prefix = df.iloc[:cutoff].copy().reset_index(drop=True)
    prefix_patterns = engine.find_investment_patterns(prefix)
    assert event_key(top) in {event_key(e) for e in prefix_patterns}, (top, prefix_patterns)
    print(
        "Shanghai 2026 M:", date_at(df, top["start_idx"]), date_at(df, top["middle_idx"]),
        date_at(df, top["end_idx"]), date_at(df, top["confirm_idx"]),
        "neckline=", top["key_levels"]["neckline"], "pivot_count=", top.get("pivot_count"),
        "prefix_causal=OK",
    )
    print("Shanghai 2024 W:", [
        (date_at(df,e["start_idx"]), date_at(df,e["middle_idx"]), date_at(df,e["end_idx"]),
         date_at(df,e["confirm_idx"]), e["key_levels"]["neckline"])
        for e in w_events
    ])
    assert len(patterns) >= 6, patterns


def main() -> None:
    total_patterns = total_indicators = 0
    shanghai = None
    for code in INDEX_CODES:
        df = fetch_df(code)
        if code == "000001.SH":
            shanghai = df
        p, s = validate_result(code, df, "index")
        total_patterns += p; total_indicators += s
    for code in EQUITY_CODES:
        df = fetch_df(code)
        p, s = validate_result(code, df, "equity")
        total_patterns += p; total_indicators += s
    assert shanghai is not None
    validate_shanghai(shanghai)
    assets = len(INDEX_CODES) + len(EQUITY_CODES)
    assert total_patterns >= assets * 5, total_patterns
    print("live investment engine v15 validation OK", f"patterns={total_patterns}", f"indicators={total_indicators}")


if __name__ == "__main__":
    main()
