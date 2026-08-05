"""Live audit for the fully integrated investment-grade engine using production bars.

The production endpoint is used only as a market-data source. All analysis is
performed by the code checked out in the pull request.
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

from backend.app import analysis_v7
from backend.app import indicators
from backend.app import investment_engine_v14 as engine

BASE_URL = "https://kchart.onrender.com/api/chart"
INDEX_CODES = ["000001.SH", "000300.SH", "000905.SH", "000688.SH", "399001.SZ", "399006.SZ"]
EQUITY_CODES = ["600519.SH", "300750.SZ", "002594.SZ", "601318.SH", "000333.SZ", "688981.SH"]


def fetch_df(code: str) -> pd.DataFrame:
    last = None
    for attempt in range(8):
        try:
            response = requests.get(
                BASE_URL,
                params={"ts_code": code, "timeframe": "1d", "refresh": 0},
                timeout=90,
            )
            response.raise_for_status()
            bars = response.json().get("bars") or []
            if len(bars) < 600:
                raise RuntimeError(f"{code}: only {len(bars)} bars")
            df = pd.DataFrame(bars).rename(columns={
                "time": "trade_date", "o": "open", "h": "high",
                "l": "low", "c": "close", "v": "vol",
            })
            df["trade_date"] = (
                pd.to_datetime(df["trade_date"], unit="s", utc=True)
                .dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            )
            df["amount"] = 0.0
            return indicators.compute_all(
                df.rename(columns={"trade_date": "ts"})
            ).rename(columns={"ts": "trade_date"})
        except Exception as exc:  # pragma: no cover - network audit
            last = exc
            time.sleep(12 + attempt * 4)
    raise RuntimeError(last)


def date_at(df: pd.DataFrame, idx: int):
    return pd.to_datetime(df["trade_date"].iloc[int(idx)]).date()


def event_key(event: dict) -> tuple:
    levels = event.get("key_levels") or {}
    return (
        str(event.get("kind")), int(event.get("start_idx", -1)),
        int(event.get("middle_idx", -1)), int(event.get("end_idx", -1)),
        int(event.get("confirm_idx", -1)), round(float(levels.get("neckline", 0)), 3),
    )


def validate_result(code: str, df: pd.DataFrame, asset_kind: str) -> tuple[int, int]:
    patterns = engine.find_investment_patterns(df)
    result = analysis_v7.analyze(df, asset_kind=asset_kind)
    diagnostics = result["diagnostics"]
    labels = [str(event.get("label") or "") for event in result["annotations"]]

    assert diagnostics["analysis_version"] == analysis_v7.ANALYSIS_VERSION
    assert diagnostics["causal"] is True
    assert len(patterns) <= engine.MAX_PATTERN_EVENTS
    assert diagnostics["indicator_events"] <= engine.MAX_INDICATOR_EVENTS
    assert not {"MACD金叉", "MACD死叉", "结构失效"}.intersection(labels)
    assert all(len(label) <= 8 for label in labels)
    assert all(
        event.get("label") in {"0.5", "0.618"}
        for event in result["annotations"] if event.get("kind") == "fibonacci"
    )
    assert all(engine._large_pattern(df, event) for event in patterns)
    for i, left in enumerate(patterns):
        for right in patterns[i + 1:]:
            assert engine._pattern_overlap(left, right) < engine.PATTERN_OVERLAP_LIMIT

    print(
        code,
        f"bars={len(df)}", f"asof={date_at(df, len(df)-1)}",
        f"patterns={len(patterns)}", f"indicators={diagnostics['indicator_events']}",
        "families=" + ",".join(diagnostics.get("pattern_families") or []),
        "structures=" + ",".join(
            f"{event['kind']}[{date_at(df,event['start_idx'])}->{date_at(df,event['confirm_idx'])}]"
            for event in patterns
        ),
    )
    return len(patterns), int(diagnostics["indicator_events"])


def validate_shanghai_macro_top(df: pd.DataFrame) -> None:
    patterns = engine.find_investment_patterns(df)
    tops = [
        event for event in patterns
        if event.get("kind") == "macro_double_top"
        and date_at(df, event["confirm_idx"]).year == 2026
    ]
    assert tops, patterns
    top = max(tops, key=lambda event: int(event["confirm_idx"]))
    start_date = date_at(df, top["start_idx"])
    end_date = date_at(df, top["end_idx"])
    confirm_date = date_at(df, top["confirm_idx"])
    assert start_date <= pd.Timestamp("2026-03-01").date(), top
    assert end_date > start_date
    assert confirm_date > end_date
    assert int(top["confirm_idx"]) - int(top["start_idx"]) >= engine.MIN_REVERSAL_BARS
    assert float(top["key_levels"]["neckline"]) > 0
    assert int(top.get("touches", 0)) >= 1

    cutoff = min(len(df), int(top["confirm_idx"]) + 6)
    prefix = df.iloc[:cutoff].copy().reset_index(drop=True)
    prefix_patterns = engine.find_investment_patterns(prefix)
    assert event_key(top) in {event_key(event) for event in prefix_patterns}, (
        top, prefix_patterns
    )
    print(
        "Shanghai macro top:", start_date, end_date, confirm_date,
        "neckline=", top["key_levels"]["neckline"], "touches=", top.get("touches"),
        "prefix_causal=OK",
    )


def main() -> None:
    total_patterns = 0
    total_indicators = 0
    shanghai = None
    observed_families: set[str] = set()
    for code in INDEX_CODES:
        df = fetch_df(code)
        if code == "000001.SH":
            shanghai = df
        p, s = validate_result(code, df, "index")
        total_patterns += p
        total_indicators += s
        observed_families.update(
            event.get("kind", "") for event in engine.find_investment_patterns(df)
        )
    for code in EQUITY_CODES:
        df = fetch_df(code)
        p, s = validate_result(code, df, "equity")
        total_patterns += p
        total_indicators += s
        observed_families.update(
            event.get("kind", "") for event in engine.find_investment_patterns(df)
        )

    assert shanghai is not None
    validate_shanghai_macro_top(shanghai)
    assets = len(INDEX_CODES) + len(EQUITY_CODES)
    assert 4 <= total_patterns <= assets * engine.MAX_PATTERN_EVENTS, total_patterns
    assert total_indicators <= assets * engine.MAX_INDICATOR_EVENTS, total_indicators
    assert "macro_double_top" in observed_families
    assert any(kind not in {"macro_double_top", "macro_double_bottom"} for kind in observed_families)
    print(
        "live investment engine v14 validation OK",
        f"patterns={total_patterns}", f"indicators={total_indicators}",
        "families=" + ",".join(sorted(observed_families)),
    )


if __name__ == "__main__":
    main()
