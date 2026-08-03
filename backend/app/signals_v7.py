"""v7技术信号入口。

沿用v6已经确认过的RSI、背离和EMA规则；MACD采用更严格的趋势延续确认：
原始交叉只是候选，只有价格、均线、柱体、ADX和区间突破共同确认后才上图。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals_v5 as base

MACD_CONFIRM_BARS = 6
MACD_COOLDOWN = 30


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _event(df: pd.DataFrame, idx: int, label: str, direction: str,
           detail: str) -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(df["close"].iloc[idx]), 4),
        "kind": "indicator",
        "label": label,
        "direction": direction,
        "star": False,
        "detail": detail,
        "lines": [],
        "zones": [],
        "polylines": [],
        "active": idx >= len(df) - 120,
        "_score": 76,
        "_grp": f"macd_v7:{direction}:{idx}",
    }


def _slope(values: np.ndarray, idx: int, span: int) -> float:
    j = max(0, idx - span)
    if not np.isfinite(values[idx]) or not np.isfinite(values[j]):
        return 0.0
    return float(values[idx] - values[j])


def _volume_ratio(vol: np.ndarray, idx: int) -> float:
    start = max(0, idx - 20)
    base_vol = float(np.nanmean(vol[start:idx])) if idx > start else np.nan
    if not np.isfinite(base_vol) or base_vol <= 0:
        return 1.0
    return float(vol[idx] / base_vol)


def _confirmed(df: pd.DataFrame, idx: int, cross_idx: int, direction: str) -> bool:
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    dif = df["DIF"].to_numpy(dtype=float)
    dea = df["DEA"].to_numpy(dtype=float)
    hist = df["MACD_HIST"].to_numpy(dtype=float)
    ema20 = df["EMA20"].to_numpy(dtype=float)
    ema60 = df["EMA60"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)
    vol = df["vol"].to_numpy(dtype=float)

    vals = (close[idx], dif[idx], dea[idx], hist[idx], ema20[idx], ema60[idx], atr[idx], adx[idx])
    if not all(np.isfinite(x) for x in vals) or atr[idx] <= 0:
        return False
    if idx < 2:
        return False

    start = max(0, cross_idx - 20)
    prior_high = float(np.nanmax(high[start:cross_idx])) if cross_idx > start else float(high[cross_idx])
    prior_low = float(np.nanmin(low[start:cross_idx])) if cross_idx > start else float(low[cross_idx])
    hist_floor = max(float(atr[idx]) * 0.03, float(close[idx]) * 0.0006)
    adx_rising = idx >= 5 and np.isfinite(adx[idx - 5]) and adx[idx] >= adx[idx - 5] + 1.0
    liquid = _volume_ratio(vol, idx) >= 0.85

    if direction == "bull":
        histogram = hist[idx] > hist[idx - 1] > 0 and hist[idx - 1] >= hist[idx - 2]
        trend = close[idx] > ema20[idx] > ema60[idx]
        slopes = _slope(ema20, idx, 5) > atr[idx] * 0.10 and _slope(ema60, idx, 10) >= 0
        breakout = close[idx] >= prior_high * 0.995
        macd_side = dif[idx] > dea[idx] and (dif[idx] >= 0 or ema20[idx] > ema60[idx] * 1.002)
    else:
        histogram = hist[idx] < hist[idx - 1] < 0 and hist[idx - 1] <= hist[idx - 2]
        trend = close[idx] < ema20[idx] < ema60[idx]
        slopes = _slope(ema20, idx, 5) < -atr[idx] * 0.10 and _slope(ema60, idx, 10) <= 0
        breakout = close[idx] <= prior_low * 1.005
        macd_side = dif[idx] < dea[idx] and (dif[idx] <= 0 or ema20[idx] < ema60[idx] * 0.998)

    strength = abs(float(hist[idx])) >= hist_floor
    regime = adx[idx] >= 20 and (adx_rising or adx[idx] >= 25)
    return bool(histogram and trend and slopes and breakout and macd_side and strength and regime and liquid)


def macd_cross_signals(df: pd.DataFrame) -> list[dict]:
    dif = df["DIF"].to_numpy(dtype=float)
    dea = df["DEA"].to_numpy(dtype=float)
    out: list[dict] = []
    pending: dict | None = None
    last_confirm = -10_000

    for i in range(1, len(df)):
        if not all(np.isfinite(x) for x in (dif[i - 1], dea[i - 1], dif[i], dea[i])):
            continue
        golden = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        death = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        if golden:
            pending = {"idx": i, "direction": "bull"}
        elif death:
            pending = {"idx": i, "direction": "bear"}

        if pending is None:
            continue
        cross_idx = int(pending["idx"])
        direction = str(pending["direction"])
        if i - cross_idx > MACD_CONFIRM_BARS:
            pending = None
            continue
        if i - last_confirm < MACD_COOLDOWN:
            continue
        if not _confirmed(df, i, cross_idx, direction):
            continue

        bullish = direction == "bull"
        out.append(_event(
            df, i,
            "MACD金叉" if bullish else "MACD死叉",
            direction,
            f"{_date(df, cross_idx)}出现原始{'金叉' if bullish else '死叉'}，"
            f"{_date(df, i)}才完成趋势确认：价格{'上破' if bullish else '下破'}近20日区间，"
            "EMA20/EMA60同向排列、MACD柱连续扩张且ADX转强。",
        ))
        last_confirm = i
        pending = None
    return out


def all_signals(df: pd.DataFrame) -> list[dict]:
    events = (
        base.rsi_signals(df)
        + macd_cross_signals(df)
        + base.divergence_signals(df)
        + base.ema_cross_signals(df)
    )
    events.sort(key=lambda e: (int(e["bar_idx"]), -int(e.get("_score", 0))))
    return events
