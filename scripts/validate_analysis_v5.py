"""推背图v5基础指标信号验收：不依赖行情API。

大级别结构、Fib、波浪和顶部形态由validate_analysis_v7.py单独验收。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import analysis_v5, indicators, signals_v5


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


def validate_rsi_requires_price_confirmation() -> None:
    df = indicators.compute_all(market_frame(150))
    df["RSI6"] = 50.0
    df["ADX"] = 15.0

    df.loc[39, "RSI6"], df.loc[40, "RSI6"] = 79.0, 82.0
    df.loc[41, "RSI6"], df.loc[42, "RSI6"] = 86.0, 78.0
    df.loc[43, "RSI6"] = 70.0
    df.loc[42, "close"] = float(df.loc[42, "MA10"]) + 1.0
    df.loc[43, "close"] = float(df.loc[43, "MA10"]) - 1.0

    df.loc[79, "RSI6"], df.loc[80, "RSI6"] = 21.0, 18.0
    df.loc[81, "RSI6"], df.loc[82, "RSI6"] = 14.0, 23.0
    df.loc[83, "RSI6"] = 30.0
    df.loc[82, "close"] = float(df.loc[82, "MA10"]) - 1.0
    df.loc[83, "close"] = float(df.loc[83, "MA10"]) + 1.0

    events = signals_v5.rsi_signals(df)
    found = {(e["bar_idx"], e["label"]) for e in events}
    assert (43, "RSI超买") in found, found
    assert (83, "RSI超卖") in found, found
    assert all(i not in (40, 80) for i, _ in found), found


def _manual_signal_frame(n: int = 100) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": np.full(n, 100.0),
        "high": np.full(n, 101.0),
        "low": np.full(n, 99.0),
        "close": np.full(n, 100.0),
        "vol": np.full(n, 1_000_000.0),
        "EMA20": np.full(n, 99.8),
        "EMA60": np.full(n, 100.0),
        "ATR14": np.full(n, 1.0),
        "ADX": np.full(n, 10.0),
        "DIF": np.zeros(n),
        "DEA": np.zeros(n),
        "MACD_HIST": np.zeros(n),
        "MA10": np.full(n, 100.0),
    })


def validate_ema_filters_chop_and_confirms_trend() -> None:
    chop = _manual_signal_frame()
    chop["EMA20"] = 100 + 0.01 * np.sin(np.arange(len(chop)) * 1.8)
    assert signals_v5.ema_cross_signals(chop) == []

    trend = _manual_signal_frame()
    for i in range(30, len(trend)):
        trend.loc[i, "EMA20"] = 100.0 + 0.06 * (i - 30)
        trend.loc[i, "EMA60"] = 100.0 + 0.005 * (i - 30)
        trend.loc[i, "close"] = trend.loc[i, "EMA20"] + 0.6
        trend.loc[i, "high"] = trend.loc[i, "close"] + 0.5
        trend.loc[i, "low"] = trend.loc[i, "close"] - 0.5
        trend.loc[i, "ADX"] = 24.0
    events = signals_v5.ema_cross_signals(trend)
    assert any(e["label"] == "EMA金叉" for e in events), events


def validate_noise_suppression() -> None:
    n = 600
    x = np.arange(n)
    close = 100 + 0.22 * np.sin(x * 1.7)
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2021-01-01", periods=n, freq="B"),
        "open": close,
        "high": close + 0.25,
        "low": close - 0.25,
        "close": close,
        "vol": np.full(n, 1_000_000.0),
        "amount": np.full(n, 1e7),
    })
    df = indicators.compute_all(frame)
    events = signals_v5.all_signals(df)
    assert len(events) <= 4, [(e["bar_idx"], e["label"]) for e in events]


def validate_full_analysis() -> None:
    df = indicators.compute_all(market_frame())
    result = analysis_v5.analyze(df, "1d")
    assert result["diagnostics"]["bars_scanned"] == len(df)
    assert result["diagnostics"]["causal"] is True
    for event in result["annotations"]:
        assert 0 <= event["bar_idx"] < len(df), event
        assert len(event["label"]) <= 8, event["label"]
        if event.get("kind") == "fibonacci":
            assert not event.get("lines"), event
            assert not event.get("zones"), event
            assert not event.get("polylines"), event
    summary = result["summary"]
    assert "若" in summary["outlook_text"]
    assert "不预设方向" in summary["outlook_text"]


if __name__ == "__main__":
    validate_signal_causality()
    validate_rsi_requires_price_confirmation()
    validate_ema_filters_chop_and_confirms_trend()
    validate_noise_suppression()
    validate_full_analysis()
    print("analysis_v5 base signal validation OK")
