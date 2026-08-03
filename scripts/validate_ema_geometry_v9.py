"""v9 EMA状态机与结构边界回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import pattern_geometry_v9, signals_v7


def _signal_frame(n: int) -> pd.DataFrame:
    close = np.full(n, 100.0)
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close.copy(),
        "high": close + 0.3,
        "low": close - 0.3,
        "close": close,
        "vol": np.full(n, 1_000_000.0),
        "EMA20": np.full(n, 100.0),
        "EMA60": np.full(n, 100.0),
        "ATR14": np.full(n, 1.0),
        "ADX": np.full(n, 12.0),
    })


def validate_ema_entanglement_suppressed() -> None:
    df = _signal_frame(600)
    x = np.arange(len(df), dtype=float)
    df["close"] = 100.0 + 0.25 * np.sin(x / 3.0)
    df["open"] = df["close"]
    df["high"] = df["close"] + 0.25
    df["low"] = df["close"] - 0.25
    df["EMA20"] = 100.0 + 0.08 * np.sin(x / 3.5)
    df["EMA60"] = 100.0 + 0.04 * np.sin(x / 5.0 + 0.8)
    events = signals_v7.ema_regime_signals(df)
    assert events == [], events


def validate_ema_one_signal_per_trend() -> None:
    df = _signal_frame(180)
    # 101附近发生机械金叉，随后均线持续张口、价格突破、ADX/ATR/量能转强。
    for i in range(80, len(df)):
        df.loc[i, "EMA60"] = 100.0 + 0.01 * (i - 80)
        df.loc[i, "EMA20"] = 98.2 + 0.10 * (i - 80)
    for i in range(101, len(df)):
        price = 100.5 + 0.45 * (i - 101)
        df.loc[i, ["open", "close"]] = price
        df.loc[i, "high"] = price + 0.35
        df.loc[i, "low"] = price - 0.35
        df.loc[i, "ATR14"] = 1.10
        df.loc[i, "ADX"] = min(35.0, 20.0 + (i - 101))
        df.loc[i, "vol"] = 1_350_000.0

    events = signals_v7.ema_regime_signals(df)
    assert len(events) == 1, events
    assert events[0]["label"] == "EMA金叉", events
    assert events[0]["bar_idx"] > 101, events


def _geometry_frame() -> pd.DataFrame:
    n = 120
    close = np.full(n, 100.0)
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close,
        "high": np.full(n, 111.0),
        "low": np.full(n, 94.0),
        "close": close,
        "ATR14": np.full(n, 1.0),
    })


def _pivots(df: pd.DataFrame, rows: list[tuple[int, float, str]]) -> pd.DataFrame:
    return pd.DataFrame([
        (idx, df.loc[idx, "trade_date"], price, kind, idx + 5)
        for idx, price, kind in rows
    ], columns=["idx", "trade_date", "price", "kind", "confirmed_at_idx"])


def _triangle_event() -> dict:
    return {
        "kind": "bullish_triangle_directional",
        "name": "看涨三角形",
        "direction": "bull",
        "start_idx": 20,
        "end_idx": 80,
        "confirm_idx": 90,
        "key_levels": {},
        "score": 80,
        "trace": [],
        "active": True,
    }


def validate_boundaries_touch_real_pivots() -> None:
    df = _geometry_frame()
    valid = _pivots(df, [
        (25, 110.0, "H"), (30, 95.0, "L"),
        (50, 110.2, "H"), (55, 100.0, "L"),
        (75, 109.9, "H"), (80, 105.0, "L"),
    ])
    events = pattern_geometry_v9.apply_geometry(df, valid, [_triangle_event()])
    assert len(events) == 1, events
    event = events[0]
    assert event.get("geometry_validated") is True, event
    assert event["key_levels"]["upper_touches"] >= 2
    assert event["key_levels"]["lower_touches"] >= 2
    dashed = [line for line in event["trace"] if line.get("style") == "dashed"]
    assert len(dashed) == 2, event["trace"]
    pivot_prices = set(valid["price"].astype(float).tolist())
    for line in dashed:
        assert float(line["points"][0]["p"]) in pivot_prices
        assert float(line["points"][1]["p"]) in pivot_prices

    # 上沿触点不共线，任何候选线都偏离实际结构，应整个删除。
    invalid = _pivots(df, [
        (25, 110.0, "H"), (30, 95.0, "L"),
        (50, 115.0, "H"), (55, 100.0, "L"),
        (75, 109.8, "H"), (80, 105.0, "L"),
    ])
    assert pattern_geometry_v9.apply_geometry(df, invalid, [_triangle_event()]) == []


if __name__ == "__main__":
    validate_ema_entanglement_suppressed()
    validate_ema_one_signal_per_trend()
    validate_boundaries_touch_real_pivots()
    print("EMA regime and pivot geometry v9 validation OK")
