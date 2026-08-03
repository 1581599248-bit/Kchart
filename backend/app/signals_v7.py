"""v9技术信号入口。

MACD继续使用趋势确认；EMA不再把机械交叉当信号，而使用状态机：
缠绕区→候选方向→持续张口→区间突破确认→趋势锁定→失效冷却。
同一趋势阶段最多输出一次EMA信号。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals_v5 as base

MACD_CONFIRM_BARS = 6
MACD_COOLDOWN = 30

EMA_CONFIRM_WINDOW = 12
EMA_MIN_HOLD = 4
EMA_TANGLE_LOOKBACK = 20
EMA_TANGLE_CROSSES = 2
EMA_TANGLE_NARROW_BARS = 8
EMA_REARM_BARS = 20
EMA_INVALID_BARS = 3


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _event(df: pd.DataFrame, idx: int, label: str, direction: str,
           detail: str, kind: str = "indicator", score: int = 76) -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(df["close"].iloc[idx]), 4),
        "kind": kind,
        "label": label,
        "direction": direction,
        "star": False,
        "detail": detail,
        "lines": [],
        "zones": [],
        "polylines": [],
        "active": idx >= len(df) - 120,
        "_score": score,
        "_grp": f"{label}:{direction}:{idx}",
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
    if not all(np.isfinite(x) for x in vals) or atr[idx] <= 0 or idx < 2:
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
        if i - last_confirm < MACD_COOLDOWN or not _confirmed(df, i, cross_idx, direction):
            continue

        bullish = direction == "bull"
        out.append(_event(
            df, i, "MACD金叉" if bullish else "MACD死叉", direction,
            f"{_date(df, cross_idx)}出现原始{'金叉' if bullish else '死叉'}，"
            f"{_date(df, i)}才完成趋势确认：价格{'上破' if bullish else '下破'}近20日区间，"
            "EMA20/EMA60同向排列、MACD柱连续扩张且ADX转强。",
        ))
        last_confirm = i
        pending = None
    return out


def _ema_cross_count(e20: np.ndarray, e60: np.ndarray, start: int, end: int) -> int:
    count = 0
    for i in range(max(1, start), end + 1):
        if not all(np.isfinite(x) for x in (e20[i - 1], e60[i - 1], e20[i], e60[i])):
            continue
        if (e20[i - 1] <= e60[i - 1] and e20[i] > e60[i]) or \
                (e20[i - 1] >= e60[i - 1] and e20[i] < e60[i]):
            count += 1
    return count


def _ema_is_narrow(close: np.ndarray, e20: np.ndarray, e60: np.ndarray,
                   atr: np.ndarray, idx: int) -> bool:
    if not all(np.isfinite(x) for x in (close[idx], e20[idx], e60[idx], atr[idx])) or atr[idx] <= 0:
        return True
    return abs(e20[idx] - e60[idx]) <= max(atr[idx] * 0.22, close[idx] * 0.0015)


def _ema_entangled(close: np.ndarray, e20: np.ndarray, e60: np.ndarray,
                    atr: np.ndarray, idx: int) -> bool:
    start = max(1, idx - EMA_TANGLE_LOOKBACK + 1)
    crosses = _ema_cross_count(e20, e60, start, idx)
    narrow = sum(_ema_is_narrow(close, e20, e60, atr, j) for j in range(start, idx + 1))
    return crosses >= EMA_TANGLE_CROSSES or narrow >= EMA_TANGLE_NARROW_BARS


def _ema_regime_confirmed(df: pd.DataFrame, idx: int, cross_idx: int,
                          direction: str) -> bool:
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    e20 = df["EMA20"].to_numpy(dtype=float)
    e60 = df["EMA60"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)
    vol = df["vol"].to_numpy(dtype=float)

    if idx - cross_idx < EMA_MIN_HOLD or idx < EMA_MIN_HOLD:
        return False
    vals = (close[idx], e20[idx], e60[idx], atr[idx], adx[idx])
    if not all(np.isfinite(x) for x in vals) or atr[idx] <= 0:
        return False

    hold_start = idx - EMA_MIN_HOLD + 1
    if direction == "bull":
        held = np.all(e20[hold_start:idx + 1] > e60[hold_start:idx + 1])
    else:
        held = np.all(e20[hold_start:idx + 1] < e60[hold_start:idx + 1])
    if not held:
        return False

    gap = np.abs(e20[hold_start:idx + 1] - e60[hold_start:idx + 1])
    widening = gap[-1] > gap[-2] and gap[-1] >= max(gap[0] * 1.35, atr[idx] * 0.32,
                                                      close[idx] * 0.002)
    if not widening:
        return False

    prior_start = max(0, cross_idx - 20)
    prior_high = float(np.nanmax(high[prior_start:cross_idx])) if cross_idx > prior_start else high[cross_idx]
    prior_low = float(np.nanmin(low[prior_start:cross_idx])) if cross_idx > prior_start else low[cross_idx]
    slope20 = _slope(e20, idx, 5)
    slope60 = _slope(e60, idx, 10)

    if direction == "bull":
        price_side = close[idx] > e20[idx] > e60[idx]
        slope_ok = slope20 >= atr[idx] * 0.18 and slope60 >= atr[idx] * 0.03
        breakout = close[idx] >= prior_high + atr[idx] * 0.10
    else:
        price_side = close[idx] < e20[idx] < e60[idx]
        slope_ok = slope20 <= -atr[idx] * 0.18 and slope60 <= -atr[idx] * 0.03
        breakout = close[idx] <= prior_low - atr[idx] * 0.10
    if not (price_side and slope_ok and breakout):
        return False

    adx_vote = adx[idx] >= 22 and (idx < 5 or not np.isfinite(adx[idx - 5]) or adx[idx] >= adx[idx - 5] + 1.5)
    atr_base = float(np.nanmedian(atr[max(0, idx - 20):idx]))
    atr_vote = np.isfinite(atr_base) and atr_base > 0 and atr[idx] >= atr_base * 1.02
    volume_vote = _volume_ratio(vol, idx) >= 1.0
    strength_votes = int(adx_vote) + int(atr_vote) + int(volume_vote)

    # 确认日附近仍有多次交叉或大量窄幅缠绕，说明尚未完成方向选择。
    recent_start = max(1, idx - 7)
    recent_crosses = _ema_cross_count(e20, e60, recent_start, idx)
    recent_narrow = sum(_ema_is_narrow(close, e20, e60, atr, j)
                        for j in range(recent_start, idx + 1))
    clean_exit = recent_crosses <= 1 and recent_narrow <= 2
    return bool(strength_votes >= 2 and clean_exit)


def _ema_trend_still_valid(close: np.ndarray, e20: np.ndarray, e60: np.ndarray,
                           idx: int, direction: int) -> bool:
    if not all(np.isfinite(x) for x in (close[idx], e20[idx], e60[idx])):
        return False
    if direction > 0:
        return close[idx] >= min(e20[idx], e60[idx]) and e20[idx] >= e60[idx]
    return close[idx] <= max(e20[idx], e60[idx]) and e20[idx] <= e60[idx]


def ema_regime_signals(df: pd.DataFrame) -> list[dict]:
    """每个趋势阶段最多输出一次EMA方向确认；缠绕区所有机械交叉均屏蔽。"""
    e20 = df["EMA20"].to_numpy(dtype=float)
    e60 = df["EMA60"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)

    out: list[dict] = []
    pending: dict | None = None
    trend_state = 0  # 1多头趋势锁定，-1空头趋势锁定，0未选择方向
    invalid_count = 0
    lock_until = -1

    for i in range(1, len(df)):
        if not all(np.isfinite(x) for x in (e20[i - 1], e60[i - 1], e20[i], e60[i])):
            continue

        if trend_state != 0:
            if _ema_trend_still_valid(close, e20, e60, i, trend_state):
                invalid_count = 0
            else:
                invalid_count += 1
            if invalid_count >= EMA_INVALID_BARS:
                trend_state = 0
                invalid_count = 0
                pending = None
                lock_until = i + EMA_REARM_BARS
            continue

        if i < lock_until:
            continue

        golden = e20[i - 1] <= e60[i - 1] and e20[i] > e60[i]
        death = e20[i - 1] >= e60[i - 1] and e20[i] < e60[i]
        if golden:
            pending = {"idx": i, "direction": "bull",
                       "started_entangled": _ema_entangled(close, e20, e60, atr, i)}
        elif death:
            pending = {"idx": i, "direction": "bear",
                       "started_entangled": _ema_entangled(close, e20, e60, atr, i)}

        if pending is None:
            continue
        cross_idx = int(pending["idx"])
        direction = str(pending["direction"])
        if i - cross_idx > EMA_CONFIRM_WINDOW:
            pending = None
            continue
        if not _ema_regime_confirmed(df, i, cross_idx, direction):
            continue

        bullish = direction == "bull"
        out.append(_event(
            df, i, "EMA金叉" if bullish else "EMA死叉", direction,
            f"{_date(df, cross_idx)}出现机械{'金叉' if bullish else '死叉'}，"
            f"直到{_date(df, i)}连续{EMA_MIN_HOLD}根保持同向、均线持续张口并"
            f"{'突破前高' if bullish else '跌破前低'}，且ADX/ATR/量能至少两项转强，"
            "才确认为一次新的趋势方向；此后同一趋势阶段不再重复标注。",
            kind="trend", score=82,
        ))
        trend_state = 1 if bullish else -1
        pending = None
        invalid_count = 0
    return out


def all_signals(df: pd.DataFrame) -> list[dict]:
    events = (
        base.rsi_signals(df)
        + macd_cross_signals(df)
        + base.divergence_signals(df)
        + ema_regime_signals(df)
    )
    events.sort(key=lambda e: (int(e["bar_idx"]), -int(e.get("_score", 0))))
    return events
