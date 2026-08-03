"""推背图v7.2严格结构验收：不依赖外部行情。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import analysis_v7, fibonacci_history, indicators, strict_tops_v8


def _frame(n: int = 360) -> pd.DataFrame:
    close = np.linspace(75.0, 115.0, n)
    return pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=n, freq="B"),
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "vol": np.full(n, 1_000_000.0),
        "amount": np.full(n, 1e8),
        "ATR14": np.full(n, 2.0),
    })


def _pivots(df: pd.DataFrame, rows: list[tuple[int, float, str]]) -> pd.DataFrame:
    return pd.DataFrame([
        (idx, df.loc[idx, "trade_date"], price, kind, idx + 5)
        for idx, price, kind in rows
    ], columns=["idx", "trade_date", "price", "kind", "confirmed_at_idx"])


def validate_m_top_large_only() -> None:
    df = _frame(240)
    df.loc[:70, "close"] = np.linspace(78, 119, 71)
    df.loc[:70, "high"] = df.loc[:70, "close"] + 1
    df.loc[:70, "low"] = df.loc[:70, "close"] - 1
    df.loc[70, ["open", "high", "low", "close"]] = [119, 121, 118, 120]
    df.loc[105, ["open", "high", "low", "close"]] = [105, 106, 103, 104]
    df.loc[140, ["open", "high", "low", "close"]] = [118, 119, 117, 118]
    df.loc[146, ["open", "high", "low", "close", "vol"]] = [104, 105, 101, 102, 1_500_000]

    valid = _pivots(df, [(70, 120, "H"), (105, 104, "L"), (140, 118, "H")])
    events = strict_tops_v8.find_strict_top_patterns(df, valid)
    assert any(e["kind"] == "double_top" for e in events), events

    short = _pivots(df, [(70, 120, "H"), (85, 104, "L"), (100, 118, "H")])
    events = strict_tops_v8.find_strict_top_patterns(df, short)
    assert not any(e["kind"] == "double_top" for e in events), events


def validate_head_shoulders_top_strict() -> None:
    df = _frame(320)
    df.loc[:80, "close"] = np.linspace(70, 119, 81)
    df.loc[:80, "high"] = df.loc[:80, "close"] + 1
    df.loc[:80, "low"] = df.loc[:80, "close"] - 1
    points = {
        80: (119, 121, 118, 120),
        110: (107, 108, 105, 106),
        145: (131, 133, 130, 132),
        180: (105, 106, 103, 104),
        220: (118, 120, 117, 119),
        226: (103, 104, 100, 101),
    }
    for idx, (o, h, l, c) in points.items():
        df.loc[idx, ["open", "high", "low", "close"]] = [o, h, l, c]
    df.loc[226, "vol"] = 1_500_000
    valid = _pivots(df, [
        (80, 120, "H"), (110, 106, "L"), (145, 132, "H"),
        (180, 104, "L"), (220, 119, "H"),
    ])
    events = strict_tops_v8.find_strict_top_patterns(df, valid)
    assert any(e["kind"] == "head_shoulders_top" for e in events), events

    short = _pivots(df, [
        (120, 120, "H"), (135, 106, "L"), (150, 132, "H"),
        (165, 104, "L"), (180, 119, "H"),
    ])
    events = strict_tops_v8.find_strict_top_patterns(df, short)
    assert not any(e["kind"] == "head_shoulders_top" for e in events), events


def validate_fibonacci_large_05_0618_only() -> None:
    df = _frame(220)
    df.loc[10:90, "close"] = np.linspace(100, 130, 81)
    df.loc[10:90, "high"] = df.loc[10:90, "close"] + 0.6
    df.loc[10:90, "low"] = df.loc[10:90, "close"] - 0.6
    level0618 = 130 - 0.618 * 30
    df.loc[110, ["open", "high", "low", "close"]] = [114.8, 115.6, 114.5, 115.2]
    df.loc[130, ["open", "high", "low", "close"]] = [111.4, 112.0, 110.9, level0618 + 0.1]
    zz = pd.DataFrame([
        (10, df.loc[10, "trade_date"], 100.0, "L", 15),
        (90, df.loc[90, "trade_date"], 130.0, "H", 95),
    ], columns=["idx", "trade_date", "price", "kind", "confirmed_at_idx"])
    with patch("backend.app.fibonacci_history.piv_mod.zigzag", return_value=zz):
        events = fibonacci_history.find_fibonacci_touches(df, pd.DataFrame())
    labels = {e["label"] for e in events}
    assert labels <= {"0.5", "0.618"}, labels
    assert "0.5" in labels and "0.618" in labels, events
    assert all(not e.get("lines") and not e.get("zones") and not e.get("polylines") for e in events)

    too_short = pd.DataFrame([
        (10, df.loc[10, "trade_date"], 100.0, "L", 15),
        (55, df.loc[55, "trade_date"], 130.0, "H", 60),
    ], columns=zz.columns)
    with patch("backend.app.fibonacci_history.piv_mod.zigzag", return_value=too_short):
        assert fibonacci_history.find_fibonacci_touches(df, pd.DataFrame()) == []

    too_small = pd.DataFrame([
        (10, df.loc[10, "trade_date"], 100.0, "L", 15),
        (90, df.loc[90, "trade_date"], 118.0, "H", 95),
    ], columns=zz.columns)
    with patch("backend.app.fibonacci_history.piv_mod.zigzag", return_value=too_small):
        assert fibonacci_history.find_fibonacci_touches(df, pd.DataFrame()) == []


def validate_v7_full_chain() -> None:
    rng = np.random.default_rng(20260803)
    n = 760
    close = 100 + np.cumsum(rng.normal(0.03, 1.1, n))
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2020-01-01", periods=n, freq="B"),
        "open": close + rng.normal(0, 0.3, n),
        "high": close + rng.uniform(0.3, 1.5, n),
        "low": close - rng.uniform(0.3, 1.5, n),
        "close": close,
        "vol": rng.integers(100_000, 3_000_000, n).astype(float),
        "amount": rng.uniform(1e6, 1e8, n),
    })
    result = analysis_v7.analyze(indicators.compute_all(frame), "1d")
    diagnostics = result["diagnostics"]
    assert diagnostics["analysis_version"] == "analysis_v7.2"
    assert diagnostics["causal"] is True
    assert diagnostics["patterns_displayed"] <= diagnostics["patterns_detected"]
    for event in result["annotations"]:
        if event.get("kind") == "fibonacci":
            assert event["label"] in {"0.5", "0.618"}, event
        if event.get("trace_only"):
            assert event.get("history_label") is True, event


if __name__ == "__main__":
    validate_m_top_large_only()
    validate_head_shoulders_top_strict()
    validate_fibonacci_large_05_0618_only()
    validate_v7_full_chain()
    print("analysis_v7.2 strict structure validation OK")
