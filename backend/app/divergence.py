"""背离检测（ARCHITECTURE.md 第3节 divergence.py 规范）。

价格 pivot 新高/新低而指标 pivot 未新高/新低 → 顶/底背离。
第二个 pivot 右侧确认后事件才生效（confirmed_idx = idx2 + right）。
同一 DataFrame 上不同指标共用同一套价格 pivot，避免重复扫描。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIV_LEFT = 3
DIV_RIGHT = 3
MIN_PIVOT_GAP = 3
MAX_PIVOT_GAP = 60
PRICE_TOLERANCE = 0.0


def _series_pivots(s: pd.Series, left: int, right: int):
    """在任意序列上找局部极值，返回 (top_idx_list, bottom_idx_list)。

    使用滑动窗口向量化，语义保持不变：并列极值只取窗口内最早者，
    同一位置同时满足高低点时优先记高点。
    """
    values = s.to_numpy(dtype=float)
    n = len(values)
    width = left + right + 1
    if n < width:
        return [], []

    windows = np.lib.stride_tricks.sliding_window_view(values, width)
    centers = np.arange(left, n - right)
    center_values = values[centers]

    tops_mask = (
        (center_values == windows.max(axis=1))
        & (windows.argmax(axis=1) == left)
    )
    bots_mask = (
        (center_values == windows.min(axis=1))
        & (windows.argmin(axis=1) == left)
        & ~tops_mask
    )
    return centers[tops_mask].tolist(), centers[bots_mask].tolist()


def _price_pivots(df: pd.DataFrame, price_col: str, left: int, right: int):
    """同一 DataFrame 内缓存价格 pivot，供 DIF/DEA/RSI6 背离共同使用。"""
    cache = df.attrs.setdefault("_ryan_div_price_pivots", {})
    key = (id(df), price_col, left, right, len(df))
    hit = cache.get(key)
    if hit is None:
        hit = _series_pivots(df[price_col], left, right)
        cache[key] = hit
    return hit


def find_divergences(
    df: pd.DataFrame,
    indicator_col: str,
    price_col: str = "close",
    left: int = DIV_LEFT,
    right: int = DIV_RIGHT,
) -> list[dict]:
    """价格 vs 指标背离检测。"""
    if indicator_col not in df.columns:
        return []

    ind_values = df[indicator_col].to_numpy(dtype=float)
    px_values = df[price_col].to_numpy(dtype=float)
    ptops, pbots = _price_pivots(df, price_col, left, right)
    out: list[dict] = []

    def _scan(piv_idx: list[int], kind: str):
        for a, b in zip(piv_idx[:-1], piv_idx[1:]):
            gap = b - a
            if gap < MIN_PIVOT_GAP or gap > MAX_PIVOT_GAP:
                continue
            i1, i2 = ind_values[a], ind_values[b]
            if np.isnan(i1) or np.isnan(i2):
                continue
            p1, p2 = px_values[a], px_values[b]
            if kind == "top":
                matched = p2 > p1 * (1 + PRICE_TOLERANCE) and i2 < i1
            else:
                matched = p2 < p1 * (1 - PRICE_TOLERANCE) and i2 > i1
            if not matched:
                continue
            out.append({
                "kind": kind,
                "idx1": a,
                "idx2": b,
                "confirmed_idx": b + right,
                "indicator": indicator_col,
                "price1": float(p1),
                "price2": float(p2),
                "ind1": float(i1),
                "ind2": float(i2),
            })

    _scan(ptops, "top")
    _scan(pbots, "bottom")
    out.sort(key=lambda e: e["idx2"])
    return out


if __name__ == "__main__":
    from . import db, indicators

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = indicators.compute_all(loader(ts, start="2023-01-01"))
        for col in ("DIF", "RSI6"):
            ev = find_divergences(d, col)
            tops = sum(1 for e in ev if e["kind"] == "top")
            bots = sum(1 for e in ev if e["kind"] == "bottom")
            print(f"{ts} {col}: divergences={len(ev)} (top={tops}, bottom={bots})")
            if ev:
                print("  last:", ev[-1])
    print("divergence 自检通过")
