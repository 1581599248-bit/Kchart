"""推背图v5离线验收：不依赖行情API。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import analysis_v5, fibonacci_history, indicators, signals_v5


def market_frame(n: int = 760) -> pd.DataFrame:
    rng = np.random.default_rng(20260803)
    x = np.arange(n)
    trend = 100 + 0.025 * x
    cyc = 9 * np.sin(x / 24) + 4 * np.sin(x / 9)
    close = trend + cyc + rng.normal(0, 0.55, n)
    open_ = close + rng.normal(0, 0.35, n)
    high = np.maximum(open_, close) + rng.uniform(0.25, 1.3, n)
    low = np.minimum(open_, close) - rng.uniform(0.25, 1.3, n)
    return pd.DataFrame({
        "trade_date": pd.date_range("2020-01-01", periods=n, freq="B"),
        "open": open_, "high": high, "low": low, "close": close,
        "vol": rng.integers(100_000, 3_000_000, n).astype(float),
        "amount": rng.uniform(1e6, 1e8, n),
    })


def validate_signal_causality() -> None:
    df = indicators.compute_all(market_frame())
    full = [(e["bar_idx"], e["label"], e["direction"]) for e in signals_v5.all_signals(df)]
    for cut in (260, 380, 520, 680):
        prefix = df.iloc[:cut].copy().reset_index(drop=True)
        got = [(e["bar_idx"], e["label"], e["direction"]) for e in signals_v5.all_signals(prefix)]
        expected = [e for e in full if e[0] < cut]
        assert got == expected, (cut, got[-5:], expected[-5:])


def validate_rsi_thresholds() -> None:
    df = indicators.compute_all(market_frame(140))
    df["RSI6"] = 50.0
    df.loc[39, "RSI6"], df.loc[40, "RSI6"] = 79.0, 81.0
    df.loc[79, "RSI6"], df.loc[80, "RSI6"] = 21.0, 19.0
    df.loc[40, "close"] = df.loc[:40, "high"].max()
    df.loc[80, "close"] = df.loc[:80, "low"].min()
    df["ADX"] = 15.0
    events = signals_v5.rsi_signals(df)
    labels = [e["label"] for e in events]
    assert "RSI超买" in labels, labels
    assert "RSI超卖" in labels, labels


def validate_ema_all_crosses() -> None:
    df = indicators.compute_all(market_frame(220))
    raw = []
    e20 = df["EMA20"].to_numpy(float)
    e60 = df["EMA60"].to_numpy(float)
    for i in range(1, len(df)):
        if e20[i-1] <= e60[i-1] and e20[i] > e60[i]:
            raw.append((i, "EMA金叉"))
        elif e20[i-1] >= e60[i-1] and e20[i] < e60[i]:
            raw.append((i, "EMA死叉"))
    got = [(e["bar_idx"], e["label"]) for e in signals_v5.ema_cross_signals(df)]
    assert got == raw, (got, raw)


def validate_fibonacci_confirmation() -> None:
    n = 80
    close = np.full(n, 100.0)
    close[:11] = np.linspace(110, 100, 11)
    close[10:31] = np.linspace(100, 140, 21)
    close[31:46] = np.linspace(140, 115.3, 15)
    close[46:] = 118
    df = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close, "high": close + 0.6, "low": close - 0.6,
        "close": close, "vol": 1_000_000.0, "amount": 1e7,
    })
    pivots = pd.DataFrame([
        (10, df.loc[10, "trade_date"], 100.0, "L", 15),
        (30, df.loc[30, "trade_date"], 140.0, "H", 35),
    ], columns=["idx", "trade_date", "price", "kind", "confirmed_at_idx"])
    events = fibonacci_history.find_fibonacci_touches(df, pivots)
    fib618 = [e for e in events if e["label"] == "Fib 0.618"]
    assert fib618 and fib618[0]["bar_idx"] >= 35, fib618


def validate_full_analysis() -> None:
    df = indicators.compute_all(market_frame())
    result = analysis_v5.analyze(df, "1d")
    assert result["diagnostics"]["bars_scanned"] == len(df)
    assert result["diagnostics"]["causal"] is True
    for event in result["annotations"]:
        assert 0 <= event["bar_idx"] < len(df), event
        assert len(event["label"]) <= 8, event["label"]
    summary = result["summary"]
    assert "若" in summary["outlook_text"]
    assert "不预设方向" in summary["outlook_text"]


if __name__ == "__main__":
    validate_signal_causality()
    validate_rsi_thresholds()
    validate_ema_all_crosses()
    validate_fibonacci_confirmation()
    validate_full_analysis()
    print("analysis_v5 validation OK")
