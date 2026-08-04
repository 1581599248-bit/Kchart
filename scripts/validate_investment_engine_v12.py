"""Deterministic regression checks for investment_engine_v12."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import analysis_v7
from backend.app import indicators
from backend.app import investment_engine_v12 as engine


def base_frame(n: int = 900, seed: int = 20260804) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.02, 0.8, n))
    high = close + rng.uniform(0.2, 1.4, n)
    low = close - rng.uniform(0.2, 1.4, n)
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2022-01-03", periods=n, freq="B"),
        "open": close + rng.normal(0, 0.3, n),
        "high": high,
        "low": low,
        "close": close,
        "vol": rng.integers(100_000, 1_000_000, n).astype(float),
        "amount": rng.uniform(1e7, 1e9, n),
    })
    return indicators.compute_all(frame.rename(columns={"trade_date": "ts"})).rename(
        columns={"ts": "trade_date"}
    )


def test_large_filter(df: pd.DataFrame) -> None:
    small = {
        "kind": "ascending_triangle", "start_idx": 100, "end_idx": 120,
        "confirm_idx": 125, "trace": [], "score": 90,
    }
    assert not engine._large_pattern(df, small)

    # Magnify the tested section so the candidate clears percentage/ATR requirements.
    boosted = df.copy()
    boosted.loc[100:145, ["high", "low", "close"]] *= 0.90
    boosted.loc[146:190, ["high", "low", "close"]] *= 1.10
    large = {
        "kind": "ascending_triangle", "start_idx": 100, "end_idx": 180,
        "confirm_idx": 190,
        "trace": [{"points": [
            {"t": str(boosted["trade_date"].iloc[100].date()), "p": float(boosted["high"].iloc[100])},
            {"t": str(boosted["trade_date"].iloc[180].date()), "p": float(boosted["high"].iloc[180])},
        ], "style": "solid"}],
        "score": 90,
    }
    assert engine._large_pattern(boosted, large)


def test_rsi_thresholds(df: pd.DataFrame) -> None:
    work = df.iloc[:220].copy().reset_index(drop=True)
    work["RSI6"] = 50.0
    # 80/20 must not create events.
    work.loc[80:84, "RSI6"] = [82, 84, 83, 75, 70]
    work.loc[120:124, "RSI6"] = [18, 16, 17, 25, 30]
    assert engine.rsi_extreme_signals(work) == []

    # >90 followed by a confirmed exit may create one overbought event.
    work.loc[150:155, "RSI6"] = [91, 94, 93, 88, 84, 80]
    work.loc[154:156, "close"] = work.loc[154:156, "MA10"].to_numpy() * [0.995, 0.99, 0.985]
    signals = engine.rsi_extreme_signals(work)
    assert any(event["label"] == "RSI超买" for event in signals), signals


def test_ema_entanglement() -> None:
    n = 650
    x = np.arange(n)
    close = 100 + np.sin(x / 4.0) * 0.15
    df = pd.DataFrame({
        "trade_date": pd.date_range("2022-01-03", periods=n, freq="B"),
        "open": close, "high": close + 0.25, "low": close - 0.25,
        "close": close, "vol": np.full(n, 500_000.0), "amount": np.full(n, 1e8),
    })
    computed = indicators.compute_all(df.rename(columns={"trade_date": "ts"})).rename(
        columns={"ts": "trade_date"}
    )
    assert engine.ema_regime_signals(computed) == []


def test_fibonacci_filter() -> None:
    raw = [
        {"bar_idx": 1, "label": "Fib 0.382", "lines": [1], "zones": [1], "polylines": [1]},
        {"bar_idx": 2, "label": "0.5", "lines": [1], "zones": [1], "polylines": [1]},
        {"bar_idx": 3, "label": "Fib 0.618", "lines": [1], "zones": [1], "polylines": [1]},
        {"bar_idx": 4, "label": "0.786", "lines": [1], "zones": [1], "polylines": [1]},
    ]
    out = engine._filter_fibonacci(raw)
    assert [event["label"] for event in out] == ["0.5", "0.618"]
    assert all(not event["lines"] and not event["zones"] and not event["polylines"] for event in out)


def test_full_chain(df: pd.DataFrame) -> None:
    result = analysis_v7.analyze(df, asset_kind="equity")
    labels = [str(event.get("label") or "") for event in result["annotations"]]
    forbidden = {"EMA金叉", "EMA死叉", "MACD金叉", "MACD死叉", "结构失效"}
    assert not forbidden.intersection(labels), labels
    assert all(len(label) <= 8 for label in labels)
    fib = [event for event in result["annotations"] if event.get("kind") == "fibonacci"]
    assert all(event["label"] in {"0.5", "0.618"} for event in fib)
    pattern_names = [event for event in result["annotations"] if event.get("kind") == "pattern" and event.get("history_label")]
    assert len(pattern_names) <= engine.MAX_PATTERN_EVENTS
    assert result["diagnostics"]["causal"] is True
    assert result["diagnostics"]["analysis_version"] == analysis_v7.ANALYSIS_VERSION


def main() -> None:
    df = base_frame()
    test_large_filter(df)
    test_rsi_thresholds(df)
    test_ema_entanglement()
    test_fibonacci_filter()
    test_full_chain(df)
    print("investment engine v12 validation OK")


if __name__ == "__main__":
    main()
