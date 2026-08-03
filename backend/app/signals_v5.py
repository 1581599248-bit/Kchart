"""低噪声、严格因果的指标信号层。

规则：
- RSI6 > 80 才进入超买，< 20 才进入超卖；强趋势中的顺势极值不贴反转标签。
- EMA20/EMA60 每一次真实金叉、死叉都保留。
- MACD 金叉/死叉须满足价格或零轴确认，并过滤极弱交叉。
- MACD/RSI 背离必须经过右侧 pivot 确认，并满足最小价格与指标差异。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import divergence as div_mod

RSI_OVERBOUGHT = 80.0
RSI_OVERSOLD = 20.0
RSI_COOLDOWN = 25
MACD_CROSS_COOLDOWN = 12
DIV_COOLDOWN = 25


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
        "active": True,
        "_score": int(score),
        "_grp": group,
    }


def _range_position(high: np.ndarray, low: np.ndarray, close: np.ndarray, idx: int,
                    lookback: int = 60) -> float:
    start = max(0, idx - lookback + 1)
    hi = float(np.nanmax(high[start:idx + 1]))
    lo = float(np.nanmin(low[start:idx + 1]))
    if not np.isfinite(hi) or not np.isfinite(lo) or hi <= lo:
        return 0.5
    return float((close[idx] - lo) / (hi - lo))


def rsi_signals(df: pd.DataFrame) -> list[dict]:
    """RSI 极端区进入信号；只用当根及历史数据。"""
    rsi = df["RSI6"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    ema20 = df["EMA20"].to_numpy(dtype=float)
    ema60 = df["EMA60"].to_numpy(dtype=float)
    adx = df["ADX"].to_numpy(dtype=float)
    out: list[dict] = []
    last = {"ob": -10_000, "os": -10_000}

    for i in range(1, len(df)):
        if not np.isfinite(rsi[i - 1]) or not np.isfinite(rsi[i]):
            continue
        pos = _range_position(high, low, close, i)
        strong_up = adx[i] >= 28 and close[i] > ema20[i] > ema60[i]
        strong_dn = adx[i] >= 28 and close[i] < ema20[i] < ema60[i]

        if rsi[i - 1] <= RSI_OVERBOUGHT < rsi[i] and i - last["ob"] >= RSI_COOLDOWN:
            # 强趋势中的超买通常是强度，不把它误标成看跌；高位且非强趋势才提示。
            if not strong_up and pos >= 0.72:
                out.append(_event(
                    df, i, high[i], "indicator", "RSI超买", "bear",
                    f"{_date(df, i)} RSI6={rsi[i]:.1f} 上穿80，价格位于近60根区间上部；"
                    "仅表示短线过热，需等待价格结构确认。",
                    58, "rsi_ob",
                ))
                last["ob"] = i

        if rsi[i - 1] >= RSI_OVERSOLD > rsi[i] and i - last["os"] >= RSI_COOLDOWN:
            if not strong_dn and pos <= 0.28:
                out.append(_event(
                    df, i, low[i], "indicator", "RSI超卖", "bull",
                    f"{_date(df, i)} RSI6={rsi[i]:.1f} 下穿20，价格位于近60根区间下部；"
                    "仅表示短线过度下跌，需等待止跌结构确认。",
                    58, "rsi_os",
                ))
                last["os"] = i
    return out


def ema_cross_signals(df: pd.DataFrame) -> list[dict]:
    """完整保留 EMA20/EMA60 历史金叉死叉。"""
    e20 = df["EMA20"].to_numpy(dtype=float)
    e60 = df["EMA60"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    out: list[dict] = []
    for i in range(1, len(df)):
        if not all(np.isfinite(x) for x in (e20[i - 1], e60[i - 1], e20[i], e60[i])):
            continue
        golden = e20[i - 1] <= e60[i - 1] and e20[i] > e60[i]
        death = e20[i - 1] >= e60[i - 1] and e20[i] < e60[i]
        if not (golden or death):
            continue
        out.append(_event(
            df, i, close[i], "trend", "EMA金叉" if golden else "EMA死叉",
            "bull" if golden else "bear",
            f"{_date(df, i)} EMA20={e20[i]:.2f} {'上穿' if golden else '下穿'}"
            f"EMA60={e60[i]:.2f}，收盘价={close[i]:.2f}。",
            64, f"ema_cross:{i}",
        ))
    return out


def macd_cross_signals(df: pd.DataFrame) -> list[dict]:
    """过滤震荡区极弱 MACD 交叉，但保留有价格/零轴确认的交叉。"""
    dif = df["DIF"].to_numpy(dtype=float)
    dea = df["DEA"].to_numpy(dtype=float)
    hist = df["MACD_HIST"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ema20 = df["EMA20"].to_numpy(dtype=float)
    atr = df["ATR14"].to_numpy(dtype=float)
    out: list[dict] = []
    last_idx = -10_000

    for i in range(1, len(df)):
        vals = (dif[i - 1], dea[i - 1], dif[i], dea[i], hist[i], close[i], ema20[i], atr[i])
        if not all(np.isfinite(x) for x in vals):
            continue
        golden = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        death = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        if not (golden or death) or i - last_idx < MACD_CROSS_COOLDOWN:
            continue

        impulse = abs(hist[i] - hist[i - 1])
        meaningful = impulse >= max(atr[i] * 0.015, close[i] * 0.0003)
        trend_ok = (golden and (close[i] >= ema20[i] or dif[i] >= 0)) or (
            death and (close[i] <= ema20[i] or dif[i] <= 0)
        )
        if not (meaningful and trend_ok):
            continue

        out.append(_event(
            df, i, close[i], "indicator", "MACD金叉" if golden else "MACD死叉",
            "bull" if golden else "bear",
            f"{_date(df, i)} DIF={dif[i]:.3f} {'上穿' if golden else '下穿'}"
            f"DEA={dea[i]:.3f}；价格与趋势位置同步确认。",
            62, "macd_cross",
        ))
        last_idx = i
    return out


def _div_quality(ev: dict, indicator: str) -> bool:
    p1, p2 = float(ev["price1"]), float(ev["price2"])
    if p1 <= 0 or abs(p2 / p1 - 1.0) < 0.04:
        return False
    i1, i2 = float(ev["ind1"]), float(ev["ind2"])
    scale = max(abs(i1), abs(i2), 1e-9)
    if abs(i2 - i1) / scale < 0.12:
        return False
    if indicator in ("DIF", "DEA") and abs(i2 - i1) < max(p2 * 0.0012, 1e-6):
        return False
    return True


def divergence_signals(df: pd.DataFrame) -> list[dict]:
    """明显背离：价格摆幅、指标差异与右侧确认三重过滤。"""
    raw: dict[str, list[dict]] = {}
    for col in ("DIF", "DEA", "RSI6"):
        raw[col] = [
            e for e in div_mod.find_divergences(df, col)
            if int(e["confirmed_idx"]) < len(df) and _div_quality(e, col)
        ]

    out: list[dict] = []
    used_dea: set[int] = set()
    last_by_label: dict[str, int] = {}

    # DIF 与 DEA 在相近价格 pivot 上同向背离时合并为一个 MACD 背离。
    for dif_ev in raw["DIF"]:
        candidates = [
            (j, e) for j, e in enumerate(raw["DEA"])
            if j not in used_dea and e["kind"] == dif_ev["kind"]
            and abs(int(e["idx2"]) - int(dif_ev["idx2"])) <= 5
        ]
        pair = min(candidates, key=lambda x: abs(int(x[1]["idx2"]) - int(dif_ev["idx2"]))) if candidates else None
        ev = dif_ev
        if pair:
            used_dea.add(pair[0])
        label = "MACD顶背离" if ev["kind"] == "top" else "MACD底背离"
        ci = int(ev["confirmed_idx"])
        if ci - last_by_label.get(label, -10_000) < DIV_COOLDOWN:
            continue
        direction = "bear" if ev["kind"] == "top" else "bull"
        price = float(df["high" if direction == "bear" else "low"].iloc[ci])
        out.append(_event(
            df, ci, price, "divergence", label, direction,
            f"{_date(df, ci)} 右侧确认：价格从{ev['price1']:.2f}到{ev['price2']:.2f}"
            f"继续{'抬高' if ev['kind']=='top' else '降低'}，MACD动能未同步。",
            72, label,
            lines=[{
                "t1": _date(df, int(ev["idx1"])), "p1": float(ev["price1"]),
                "t2": _date(df, int(ev["idx2"])), "p2": float(ev["price2"]),
                "style": "solid",
            }],
        ))
        last_by_label[label] = ci

    for ev in raw["RSI6"]:
        label = "RSI顶背离" if ev["kind"] == "top" else "RSI底背离"
        ci = int(ev["confirmed_idx"])
        if ci - last_by_label.get(label, -10_000) < DIV_COOLDOWN:
            continue
        direction = "bear" if ev["kind"] == "top" else "bull"
        price = float(df["high" if direction == "bear" else "low"].iloc[ci])
        out.append(_event(
            df, ci, price, "divergence", label, direction,
            f"{_date(df, ci)} 右侧确认：价格创新{'高' if ev['kind']=='top' else '低'}，"
            f"RSI6由{ev['ind1']:.1f}变为{ev['ind2']:.1f}，动能未同步。",
            70, label,
            lines=[{
                "t1": _date(df, int(ev["idx1"])), "p1": float(ev["price1"]),
                "t2": _date(df, int(ev["idx2"])), "p2": float(ev["price2"]),
                "style": "solid",
            }],
        ))
        last_by_label[label] = ci
    return out


def all_signals(df: pd.DataFrame) -> list[dict]:
    events = (
        rsi_signals(df)
        + macd_cross_signals(df)
        + divergence_signals(df)
        + ema_cross_signals(df)
    )
    events.sort(key=lambda e: (e["bar_idx"], e.get("_score", 0)))
    return events
