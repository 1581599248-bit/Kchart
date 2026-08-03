"""波段 pivot 高低点识别（ARCHITECTURE.md 第3节 pivots.py 规范）。

结构识别地基：patterns / divergence / harmonics / fibonacci 全部建立在本模块之上。
同一 DataFrame 的 pivot 只识别一次，多尺度 zigzag 共用该结果。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LEFT = 5
RIGHT = 5
ZZ_MIN_PCT = 0.05


def _empty_pivots() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["idx", "trade_date", "price", "kind", "confirmed_at_idx"]
    )


def find_pivots(df: pd.DataFrame, left: int = LEFT, right: int = RIGHT) -> pd.DataFrame:
    """滚动窗口 pivot 高低点。

    返回 DataFrame[idx, trade_date, price, kind('H'/'L'), confirmed_at_idx]。
    使用 numpy 滑动窗口向量化，语义保持原实现不变：
    并列极值取窗口内最早者，同一根同时满足高低点时优先记高点。
    """
    cache = df.attrs.setdefault("_ryan_pivot_cache", {})
    cache_key = (id(df), left, right, len(df))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached.copy(deep=False)

    n = len(df)
    width = left + right + 1
    if n < width:
        out = _empty_pivots()
        cache[cache_key] = out
        return out.copy(deep=False)

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    dates = df["trade_date"].to_numpy()
    centers = np.arange(left, n - right)

    high_windows = np.lib.stride_tricks.sliding_window_view(highs, width)
    low_windows = np.lib.stride_tricks.sliding_window_view(lows, width)
    high_center = highs[centers]
    low_center = lows[centers]

    is_high = (
        (high_center == high_windows.max(axis=1))
        & (high_windows.argmax(axis=1) == left)
    )
    is_low = (
        (low_center == low_windows.min(axis=1))
        & (low_windows.argmin(axis=1) == left)
        & ~is_high
    )
    selected = is_high | is_low
    idx = centers[selected]

    if not len(idx):
        out = _empty_pivots()
    else:
        high_selected = is_high[selected]
        prices = np.where(high_selected, highs[idx], lows[idx])
        kinds = np.where(high_selected, "H", "L")
        out = pd.DataFrame({
            "idx": idx.astype(int),
            "trade_date": dates[idx],
            "price": prices.astype(float),
            "kind": kinds,
            "confirmed_at_idx": (idx + right).astype(int),
        })

    cache[cache_key] = out
    return out.copy(deep=False)


def pivots_asof(pivots: pd.DataFrame, asof_bar: int) -> pd.DataFrame:
    """asof_bar 时点已右侧确认的 pivot 子集。"""
    out = pivots[pivots["confirmed_at_idx"] <= asof_bar]
    assert (out["confirmed_at_idx"] <= asof_bar).all(), "未来函数：使用了未右侧确认的 pivot"
    return out


def alternating(pivots: pd.DataFrame) -> pd.DataFrame:
    """把原始 pivot 序列交替化：同kind相邻者保留更极端的一个。"""
    cols = ["idx", "trade_date", "price", "kind", "confirmed_at_idx"]
    if not len(pivots):
        return pd.DataFrame(columns=cols)
    kind = pivots["kind"].to_numpy()
    price = pivots["price"].to_numpy(dtype=float)
    keep: list[int] = []
    for j in range(len(pivots)):
        if not keep:
            keep.append(j)
            continue
        last = keep[-1]
        if kind[j] == kind[last]:
            more_extreme = (
                (kind[j] == "H" and price[j] >= price[last])
                or (kind[j] == "L" and price[j] <= price[last])
            )
            if more_extreme:
                keep[-1] = j
        else:
            keep.append(j)
    out = pivots.iloc[keep, :][cols].reset_index(drop=True)
    out["idx"] = out["idx"].astype(int)
    out["confirmed_at_idx"] = out["confirmed_at_idx"].astype(int)
    return out


def _zigzag_from_pivots(piv: pd.DataFrame, min_pct: float) -> pd.DataFrame:
    """基于已经识别好的 pivot 构造指定阈值 zigzag。"""
    if not len(piv):
        return piv.copy(deep=False)
    kind = piv["kind"].to_numpy()
    price = piv["price"].to_numpy(dtype=float)
    keep: list[int] = []
    for j in range(len(piv)):
        if not keep:
            keep.append(j)
            continue
        last = keep[-1]
        if kind[j] == kind[last]:
            if (
                (kind[j] == "H" and price[j] >= price[last])
                or (kind[j] == "L" and price[j] <= price[last])
            ):
                keep[-1] = j
            continue
        if abs(price[j] / price[last] - 1.0) >= min_pct:
            keep.append(j)

    cols = ["idx", "trade_date", "price", "kind", "confirmed_at_idx"]
    out = piv.iloc[keep, :][cols].reset_index(drop=True) if keep else pd.DataFrame(columns=cols)
    if len(out):
        out["idx"] = out["idx"].astype(int)
        out["confirmed_at_idx"] = out["confirmed_at_idx"].astype(int)
    return out


def zigzag(
    df: pd.DataFrame,
    min_pct: float = ZZ_MIN_PCT,
    left: int = LEFT,
    right: int = RIGHT,
) -> pd.DataFrame:
    """基于 pivot 的交替 zigzag 序列。"""
    return _zigzag_from_pivots(find_pivots(df, left=left, right=right), min_pct)


MS_LEVELS = (0.03, 0.08, 0.15)


def multi_scale_zigzag(df: pd.DataFrame, levels: tuple = MS_LEVELS) -> dict:
    """三级 zigzag，共用一次 pivot 识别。"""
    names = ("small", "trade", "background")
    piv = find_pivots(df)
    return {
        name: _zigzag_from_pivots(piv, pct)
        for name, pct in zip(names, levels)
    }


def current_leg(zz: pd.DataFrame) -> dict | None:
    """某级别当前腿。"""
    if not len(zz):
        return None
    last = zz.iloc[-1]
    direction = "up" if last["kind"] == "L" else "down"
    return {
        "dir": direction,
        "start_idx": int(last["idx"]),
        "start_price": float(last["price"]),
        "start_date": str(last["trade_date"])[:10],
        "kind": str(last["kind"]),
    }


def annotate_leg(leg: dict, closes: np.ndarray, asof: int) -> dict:
    """补全当前腿的至今幅度与持续K线数。"""
    px = float(closes[asof])
    leg["pct"] = px / leg["start_price"] - 1.0
    leg["bars"] = int(asof - leg["start_idx"])
    leg["price"] = px
    return leg


if __name__ == "__main__":
    from . import db

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = loader(ts, start="2024-01-01")
        p = find_pivots(d)
        z = zigzag(d)
        last = len(d) - 1
        pa = pivots_asof(p, last)
        assert len(pa) == len(p), "末端时点应全部已确认"
        print(
            f"{ts}: bars={len(d)} pivots={len(p)} "
            f"(H={int((p.kind=='H').sum())}, L={int((p.kind=='L').sum())}) zigzag={len(z)}"
        )
        print(p.tail(3).to_string(index=False))
    print("pivots 自检通过")
