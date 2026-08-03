"""机构级技术信号层：候选可以多，主图只显示已确认的高质量事件。

核心原则：
- RSI 80/20 只定义极端区，不直接等于反转；必须等待离开极端区并出现价格确认。
- EMA20/EMA60 与 MACD 交叉只在趋势、斜率、波动和量能共同确认后上图。
- 背离必须位于区间极值附近，并等待价格破坏短期趋势后才确认。
- 所有事件仅使用当根及历史数据；需要后续确认时，标签落在确认发生的那根K线上。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import divergence as div_mod

RSI_OVERBOUGHT = 80.0
RSI_OVERSOLD = 20.0
RSI_CONFIRM_HIGH = 72.0
RSI_CONFIRM_LOW = 28.0
RSI_MAX_WAIT = 18

EMA_CONFIRM_BARS = 5
EMA_COOLDOWN = 35
MACD_CONFIRM_BARS = 4
MACD_COOLDOWN = 22
DIV_COOLDOWN = 35
DIV_CONFIRM_BARS = 8


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _event(df: pd.DataFrame, idx: int, price: float, kind: str, label: str,
           direction: str, detail: str, score: int, group: str,
           lines: list | None = None) -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(price), 4),
        "kind": kind,
        "label": label,
        "direction": direction,
        "star": False,
        "detail": detail,
        "lines": lines or [],
        "zones": [],
        "polylines": [],
        "active": idx >= len(df) - 120,
        "_score": int(score),
        "_grp": group,
    }


def _range_position(high: np.ndarray, low: np.ndarray, close: np.ndarray, idx: int,
                    lookback: int = 120) -> float:
    start = max(0, idx - lookback + 1)
    hi = float(np.nanmax(high[start:idx + 1]))
    lo = float(np.nanmin(low[start:idx + 1]))
    if not np.isfinite(hi) or not np.isfinite(lo) or hi <= lo:
        return 0.5
    return float((close[idx] - lo) / (hi - lo))


def _vol_ratio(vol: np.ndarray, idx: int, lookback: int = 20) -> float:
    start = max(0, idx - lookback)
    base = float(np.nanmean(vol[start:idx])) if idx > start else np.nan
    if not np.isfinite(base) or base <= 0 or not np.isfinite(vol[idx]):
        return 1.0
    return float(vol[idx] / base)


def _slope(values: np.ndarray, idx: int, span: int) -> float:
    j = max(0, idx - span)
    if not np.isfinite(values[idx]) or not np.isfinite(values[j]):
        return 0.0
    return float(values[idx] - values[j])


def rsi_signals(df: pd.DataFrame) -> list[dict]:
    """RSI极端区必须出现“离开极端区+价格反转确认”才上图。"""
    rsi = df["RSI6"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    ma10 = df["MA10"].to_numpy(dtype=float)
    ema20 = df["EMA20"].to_numpy(dtype=float)
    ema60 = df["EMA60"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)

    out: list[dict] = []
    ob: dict | None = None
    os: dict | None = None
    last = {"ob": -10_000, "os": -10_000}

    for i in range(1, len(df)):
        if not np.isfinite(rsi[i - 1]) or not np.isfinite(rsi[i]):
            continue

        strong_up = (
            np.isfinite(adx[i]) and adx[i] >= 25
            and close[i] > ema20[i] > ema60[i]
        )
        strong_dn = (
            np.isfinite(adx[i]) and adx[i] >= 25
            and close[i] < ema20[i] < ema60[i]
        )

        if ob is None and rsi[i - 1] <= RSI_OVERBOUGHT < rsi[i]:
            ob = {"entry": i, "peak": float(rsi[i]), "high": float(high[i])}
        elif ob is not None:
            ob["peak"] = max(float(ob["peak"]), float(rsi[i]))
            ob["high"] = max(float(ob["high"]), float(high[i]))
            waited = i - int(ob["entry"])
            price_break = (
                np.isfinite(ma10[i]) and close[i] < ma10[i]
                and close[i] < close[i - 1]
            )
            if (
                rsi[i] <= RSI_CONFIRM_HIGH
                and price_break
                and not strong_up
                and i - last["ob"] >= 25
            ):
                out.append(_event(
                    df, i, high[i], "indicator", "RSI超买", "bear",
                    f"{_date(df, i)} RSI6此前升至{ob['peak']:.1f}（>80），"
                    f"现已跌回{rsi[i]:.1f}并失守MA10，过热开始得到价格确认；"
                    f"前高{ob['high']:.2f}重新收复则信号失效。",
                    68, "rsi_ob_confirm",
                ))
                last["ob"] = i
                ob = None
            elif waited > RSI_MAX_WAIT or rsi[i] < 55:
                ob = None

        if os is None and rsi[i - 1] >= RSI_OVERSOLD > rsi[i]:
            os = {"entry": i, "trough": float(rsi[i]), "low": float(low[i])}
        elif os is not None:
            os["trough"] = min(float(os["trough"]), float(rsi[i]))
            os["low"] = min(float(os["low"]), float(low[i]))
            waited = i - int(os["entry"])
            price_reclaim = (
                np.isfinite(ma10[i]) and close[i] > ma10[i]
                and close[i] > close[i - 1]
            )
            if (
                rsi[i] >= RSI_CONFIRM_LOW
                and price_reclaim
                and not strong_dn
                and i - last["os"] >= 25
            ):
                out.append(_event(
                    df, i, low[i], "indicator", "RSI超卖", "bull",
                    f"{_date(df, i)} RSI6此前降至{os['trough']:.1f}（<20），"
                    f"现已回升至{rsi[i]:.1f}并站回MA10，超卖修复得到价格确认；"
                    f"前低{os['low']:.2f}再度失守则信号失效。",
                    68, "rsi_os_confirm",
                ))
                last["os"] = i
                os = None
            elif waited > RSI_MAX_WAIT or rsi[i] > 45:
                os = None
    return out


def _ema_confirmed(df: pd.DataFrame, idx: int, direction: str) -> bool:
    close = df["close"].to_numpy(dtype=float)
    e20 = df["EMA20"].to_numpy(dtype=float)
    e60 = df["EMA60"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)
    vol = df["vol"].to_numpy(dtype=float)

    values = (close[idx], e20[idx], e60[idx], atr[idx], adx[idx])
    if not all(np.isfinite(x) for x in values) or atr[idx] <= 0:
        return False

    spread = abs(e20[idx] - e60[idx])
    spread_ok = spread >= max(atr[idx] * 0.18, close[idx] * 0.0015)
    slope20 = _slope(e20, idx, 5)
    slope60 = _slope(e60, idx, 10)
    adx_rising = idx >= 5 and np.isfinite(adx[idx - 5]) and adx[idx] >= adx[idx - 5] + 1.5
    regime_ok = adx[idx] >= 18 or adx_rising
    liquid_ok = _vol_ratio(vol, idx) >= 0.72

    if direction == "bull":
        trend_ok = close[idx] > e20[idx] > e60[idx]
        slope_ok = slope20 > atr[idx] * 0.08 and slope60 > -atr[idx] * 0.05
    else:
        trend_ok = close[idx] < e20[idx] < e60[idx]
        slope_ok = slope20 < -atr[idx] * 0.08 and slope60 < atr[idx] * 0.05
    return bool(spread_ok and trend_ok and slope_ok and regime_ok and liquid_ok)


def ema_cross_signals(df: pd.DataFrame) -> list[dict]:
    """识别全部原始交叉，但主图只显示通过后续趋势确认的交叉。"""
    e20 = df["EMA20"].to_numpy(dtype=float)
    e60 = df["EMA60"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    out: list[dict] = []
    pending: dict | None = None
    last_confirm = -10_000

    for i in range(1, len(df)):
        if not all(np.isfinite(x) for x in (e20[i - 1], e60[i - 1], e20[i], e60[i])):
            continue
        golden = e20[i - 1] <= e60[i - 1] and e20[i] > e60[i]
        death = e20[i - 1] >= e60[i - 1] and e20[i] < e60[i]
        if golden:
            pending = {"idx": i, "direction": "bull"}
        elif death:
            pending = {"idx": i, "direction": "bear"}

        if pending is None:
            continue
        age = i - int(pending["idx"])
        if age > EMA_CONFIRM_BARS:
            pending = None
            continue
        direction = str(pending["direction"])
        if i - last_confirm < EMA_COOLDOWN:
            continue
        if not _ema_confirmed(df, i, direction):
            continue

        golden_confirm = direction == "bull"
        out.append(_event(
            df, i, close[i], "trend", "EMA金叉" if golden_confirm else "EMA死叉",
            direction,
            f"{_date(df, i)} EMA20/EMA60交叉后完成确认："
            f"价格位于双均线{'上方' if golden_confirm else '下方'}，"
            "均线斜率、波动扩张和趋势强度同步。",
            72, f"ema_confirm:{i}",
        ))
        last_confirm = i
        pending = None
    return out


def _macd_confirmed(df: pd.DataFrame, idx: int, direction: str) -> bool:
    dif = df["DIF"].to_numpy(dtype=float)
    dea = df["DEA"].to_numpy(dtype=float)
    hist = df["MACD_HIST"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ema20 = df["EMA20"].to_numpy(dtype=float)
    ema60 = df["EMA60"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    vol = df["vol"].to_numpy(dtype=float)

    vals = (dif[idx], dea[idx], hist[idx], close[idx], ema20[idx], ema60[idx], atr[idx])
    if not all(np.isfinite(x) for x in vals) or atr[idx] <= 0:
        return False

    impulse_ok = abs(hist[idx]) >= max(atr[idx] * 0.025, close[idx] * 0.00045)
    momentum_ok = (
        hist[idx] > 0 and (idx == 0 or hist[idx] >= hist[idx - 1])
        if direction == "bull"
        else hist[idx] < 0 and (idx == 0 or hist[idx] <= hist[idx - 1])
    )
    pos = _range_position(high, low, close, idx, 60)
    if direction == "bull":
        price_ok = close[idx] > ema20[idx] and (dif[idx] >= 0 or ema20[idx] >= ema60[idx] * 0.995)
        position_ok = pos >= 0.52
    else:
        price_ok = close[idx] < ema20[idx] and (dif[idx] <= 0 or ema20[idx] <= ema60[idx] * 1.005)
        position_ok = pos <= 0.48
    regime_ok = (np.isfinite(adx[idx]) and adx[idx] >= 15) or position_ok
    liquid_ok = _vol_ratio(vol, idx) >= 0.68
    return bool(impulse_ok and momentum_ok and price_ok and regime_ok and liquid_ok)


def macd_cross_signals(df: pd.DataFrame) -> list[dict]:
    """MACD原始交叉需在4根内获得价格与动能确认，否则作废。"""
    dif = df["DIF"].to_numpy(dtype=float)
    dea = df["DEA"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

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
        if i - int(pending["idx"]) > MACD_CONFIRM_BARS:
            pending = None
            continue
        direction = str(pending["direction"])
        if i - last_confirm < MACD_COOLDOWN:
            continue
        if not _macd_confirmed(df, i, direction):
            continue

        bullish = direction == "bull"
        out.append(_event(
            df, i, close[i], "indicator", "MACD金叉" if bullish else "MACD死叉",
            direction,
            f"{_date(df, i)} MACD交叉后，柱体继续向{'上' if bullish else '下'}扩张，"
            f"价格同步运行在EMA20{'上方' if bullish else '下方'}；"
            "震荡区弱交叉已过滤。",
            70, f"macd_confirm:{i}",
        ))
        last_confirm = i
        pending = None
    return out


def _div_quality(df: pd.DataFrame, ev: dict, indicator: str) -> bool:
    idx1, idx2 = int(ev["idx1"]), int(ev["idx2"])
    gap = idx2 - idx1
    if gap < 12 or gap > 130:
        return False

    p1, p2 = float(ev["price1"]), float(ev["price2"])
    if p1 <= 0 or abs(p2 / p1 - 1.0) < 0.05:
        return False

    i1, i2 = float(ev["ind1"]), float(ev["ind2"])
    scale = max(abs(i1), abs(i2), 1e-9)
    if abs(i2 - i1) / scale < 0.15:
        return False
    if indicator in ("DIF", "DEA") and abs(i2 - i1) < max(abs(p2) * 0.0015, 1e-6):
        return False

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    pos = _range_position(high, low, close, idx2, 120)
    if ev["kind"] == "top" and pos < 0.82:
        return False
    if ev["kind"] == "bottom" and pos > 0.18:
        return False

    if indicator == "RSI6":
        if ev["kind"] == "top" and i2 < 65:
            return False
        if ev["kind"] == "bottom" and i2 > 35:
            return False
    return True


def _div_confirmation(df: pd.DataFrame, ev: dict, start: int) -> int | None:
    close = df["close"].to_numpy(dtype=float)
    ma10 = df["MA10"].to_numpy(dtype=float)
    dif = df["DIF"].to_numpy(dtype=float)
    dea = df["DEA"].to_numpy(dtype=float)
    hist = df["MACD_HIST"].to_numpy(dtype=float)

    stop = min(len(df), start + DIV_CONFIRM_BARS + 1)
    for i in range(start, stop):
        vals = (close[i], ma10[i], dif[i], dea[i], hist[i])
        if not all(np.isfinite(x) for x in vals):
            continue
        if ev["kind"] == "top":
            if close[i] < ma10[i] and dif[i] < dea[i] and hist[i] < 0:
                return i
        else:
            if close[i] > ma10[i] and dif[i] > dea[i] and hist[i] > 0:
                return i
    return None


def divergence_signals(df: pd.DataFrame) -> list[dict]:
    """只保留区间极值附近、并被价格和MACD共同确认的背离。"""
    raw: dict[str, list[dict]] = {}
    for col in ("DIF", "DEA", "RSI6"):
        raw[col] = [
            e for e in div_mod.find_divergences(df, col)
            if int(e["confirmed_idx"]) < len(df) and _div_quality(df, e, col)
        ]

    candidates: list[tuple[str, dict]] = []
    used_dea: set[int] = set()

    for dif_ev in raw["DIF"]:
        pairs = [
            (j, e) for j, e in enumerate(raw["DEA"])
            if j not in used_dea and e["kind"] == dif_ev["kind"]
            and abs(int(e["idx2"]) - int(dif_ev["idx2"])) <= 5
        ]
        if pairs:
            pair = min(pairs, key=lambda x: abs(int(x[1]["idx2"]) - int(dif_ev["idx2"])))
            used_dea.add(pair[0])
        candidates.append(("MACD", dif_ev))

    # RSI背离只有在附近没有MACD背离时才单独保留，避免同一拐点重复贴标签。
    for rsi_ev in raw["RSI6"]:
        if any(
            ev["kind"] == rsi_ev["kind"]
            and abs(int(ev["idx2"]) - int(rsi_ev["idx2"])) <= 6
            for _, ev in candidates
        ):
            continue
        candidates.append(("RSI", rsi_ev))

    out: list[dict] = []
    last_by_direction = {"bull": -10_000, "bear": -10_000}
    for family, ev in sorted(candidates, key=lambda x: int(x[1]["confirmed_idx"])):
        direction = "bear" if ev["kind"] == "top" else "bull"
        ci = _div_confirmation(df, ev, int(ev["confirmed_idx"]))
        if ci is None or ci - last_by_direction[direction] < DIV_COOLDOWN:
            continue

        label = (
            f"{family}顶背离" if ev["kind"] == "top"
            else f"{family}底背离"
        )
        price = float(df["high" if direction == "bear" else "low"].iloc[ci])
        out.append(_event(
            df, ci, price, "divergence", label, direction,
            f"{_date(df, ci)} 背离已通过右侧pivot、区间极值位置及"
            f"MA10/MACD反转共同确认；价格从{ev['price1']:.2f}到{ev['price2']:.2f}。",
            78 if family == "MACD" else 74, f"div_confirm:{direction}:{ci}",
            lines=[{
                "t1": _date(df, int(ev["idx1"])), "p1": float(ev["price1"]),
                "t2": _date(df, int(ev["idx2"])), "p2": float(ev["price2"]),
                "style": "solid",
            }],
        ))
        last_by_direction[direction] = ci
    return out


def all_signals(df: pd.DataFrame) -> list[dict]:
    events = (
        rsi_signals(df)
        + macd_cross_signals(df)
        + divergence_signals(df)
        + ema_cross_signals(df)
    )
    events.sort(key=lambda e: (e["bar_idx"], -e.get("_score", 0)))
    return events
