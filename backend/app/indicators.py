"""技术指标（ARCHITECTURE.md 第3节 indicators.py 规范）。

全部函数输入 DataFrame（含 open/high/low/close/vol），在副本上追加等长列返回。
标准参数固定写死（防过拟合），所有计算只用当前及历史行（rolling/ewm 方向向后，无未来函数）。

口径说明：
- MACD_HIST = 2×(DIF−DEA)（国内口径）。
- RSI 采用 Wilder 平滑口径（ewm alpha=1/n, adjust=False）。
- WR 输出 0 ~ -100。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_ma(df: pd.DataFrame, periods=(5, 10, 20, 60, 120, 250)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"MA{p}"] = out["close"].rolling(p, min_periods=p).mean()
    return out


def add_ema(df: pd.DataFrame, periods=(12, 20, 26, 50, 60)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"EMA{p}"] = out["close"].ewm(span=p, adjust=False).mean()
    return out


def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    out = df.copy()
    dif = out["close"].ewm(span=fast, adjust=False).mean() - out["close"].ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    out["DIF"] = dif
    out["DEA"] = dea
    out["MACD_HIST"] = 2 * (dif - dea)  # 国内口径
    return out


def add_kdj(df: pd.DataFrame, n=9, k_period=3, d_period=3) -> pd.DataFrame:
    out = df.copy()
    low_n = out["low"].rolling(n, min_periods=1).min()
    high_n = out["high"].rolling(n, min_periods=1).max()
    rsv = (out["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    # 国内通用递推口径：K = (2/3)K_prev + (1/3)RSV，初值 50
    k = rsv.ewm(alpha=1 / k_period, adjust=False).mean()
    d = k.ewm(alpha=1 / d_period, adjust=False).mean()
    out["K"] = k
    out["D"] = d
    out["J"] = 3 * k - 2 * d
    return out


def add_rsi(df: pd.DataFrame, periods=(6, 12, 24)) -> pd.DataFrame:
    """RSI，Wilder 平滑口径。"""
    out = df.copy()
    diff = out["close"].diff()
    up = diff.clip(lower=0)
    dn = (-diff).clip(lower=0)
    for p in periods:
        au = up.ewm(alpha=1 / p, adjust=False).mean()
        ad = dn.ewm(alpha=1 / p, adjust=False).mean()
        rs = au / ad.replace(0, np.nan)
        out[f"RSI{p}"] = 100 - 100 / (1 + rs)
    return out


def add_wr(df: pd.DataFrame, periods=(6, 10)) -> pd.DataFrame:
    """威廉指标，输出 0 ~ -100。"""
    out = df.copy()
    for p in periods:
        hh = out["high"].rolling(p, min_periods=p).max()
        ll = out["low"].rolling(p, min_periods=p).min()
        out[f"WR{p}"] = (hh - out["close"]) / (hh - ll).replace(0, np.nan) * -100
    return out


def add_boll(df: pd.DataFrame, n=20, k=2) -> pd.DataFrame:
    out = df.copy()
    mid = out["close"].rolling(n, min_periods=n).mean()
    std = out["close"].rolling(n, min_periods=n).std(ddof=0)
    out["BOLL_MID"] = mid
    out["BOLL_UP"] = mid + k * std
    out["BOLL_DN"] = mid - k * std
    return out


def add_atr(df: pd.DataFrame, n=14) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out[f"ATR{n}"] = tr.ewm(alpha=1 / n, adjust=False).mean()  # Wilder 平滑
    return out


def add_obv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    direction = np.sign(out["close"].diff()).fillna(0)
    out["OBV"] = (direction * out["vol"]).cumsum()
    return out


def add_adx(df: pd.DataFrame, n=14) -> pd.DataFrame:
    out = df.copy()
    up_move = out["high"].diff()
    down_move = -out["low"].diff()
    pdm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    mdm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=out.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(mdm, index=out.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    out["PDI"] = pdi
    out["MDI"] = mdi
    out["ADX"] = dx.ewm(alpha=1 / n, adjust=False).mean()
    return out


def add_roc(df: pd.DataFrame, periods=(20, 60)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"ROC{p}"] = out["close"].pct_change(p) * 100
    return out


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """一次算全所有指标，返回追加指标列后的副本。"""
    out = add_ma(df)
    out = add_ema(out)
    out = add_macd(out)
    out = add_kdj(out)
    out = add_rsi(out)
    out = add_wr(out)
    out = add_boll(out)
    out = add_atr(out)
    out = add_obv(out)
    out = add_adx(out)
    out = add_roc(out)
    return out


if __name__ == "__main__":
    from . import db

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = loader(ts, start="2024-01-01")
        r = compute_all(d)
        print(f"{ts}: rows={len(r)}, cols={len(r.columns)}")
        print(r.tail(1).T.to_string())
        # 等长校验 + 无整列全空
        assert len(r) == len(d)
        assert not r["MA250"].isna().all()
    print("indicators 自检通过")
