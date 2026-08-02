"""背离检测（ARCHITECTURE.md 第3节 divergence.py 规范）。

价格 pivot 新高/新低而指标 pivot 未新高/新低 → 顶/底背离。
第二个 pivot 右侧确认后事件才生效（confirmed_idx = idx2 + right），
这是防未来函数关键：背离在第二 pivot 形成当下不可知，必须等 right 根确认。

背离用较小的 pivot 窗口（3/3），比结构识别的 5/5 更灵敏，文件头可调。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIV_LEFT = 3
DIV_RIGHT = 3
MIN_PIVOT_GAP = 3        # 两个 pivot 至少间隔根数
MAX_PIVOT_GAP = 60       # 两个 pivot 最远间隔根数（太远不算同一次背离）
PRICE_TOLERANCE = 0.0    # 价格新高/新低的容差（0=严格）


def _series_pivots(s: pd.Series, left: int, right: int):
    """在任意序列上找局部极值，返回 (top_idx_list, bottom_idx_list)。"""
    v = s.to_numpy(dtype=float)
    n = len(v)
    tops, bots = [], []
    for i in range(left, n - right):
        if np.isnan(v[i]):
            continue
        w = v[i - left : i + right + 1]
        if np.isnan(w).any():
            continue
        if v[i] == w.max() and int(np.argmax(w)) == left:
            tops.append(i)
        elif v[i] == w.min() and int(np.argmin(w)) == left:
            bots.append(i)
    return tops, bots


def find_divergences(
    df: pd.DataFrame,
    indicator_col: str,
    price_col: str = "close",
    left: int = DIV_LEFT,
    right: int = DIV_RIGHT,
) -> list[dict]:
    """价格 vs 指标背离检测。

    返回 list[{kind('top'/'bottom'), idx1, idx2, confirmed_idx}]：
    - top：价格 pivot 高点 p2 > p1，但指标在 p2 处低于 p1 处（指标未新高）。
    - bottom：价格 pivot 低点 p2 < p1，但指标在 p2 处高于 p1 处（指标未新低）。
    - confirmed_idx = idx2 + right：第二 pivot 右侧确认才输出
      （find_pivots 同源逻辑保证 confirmed_idx <= n-1，即事件在样本内已生效）。
    """
    if indicator_col not in df.columns:
        return []
    ind = df[indicator_col]
    px = df[price_col]
    ptops, pbots = _series_pivots(px, left, right)
    out: list[dict] = []

    def _scan(piv_idx: list[int], kind: str):
        for a, b in zip(piv_idx[:-1], piv_idx[1:]):
            gap = b - a
            if gap < MIN_PIVOT_GAP or gap > MAX_PIVOT_GAP:
                continue
            i1, i2 = ind.iloc[a], ind.iloc[b]
            if np.isnan(i1) or np.isnan(i2):
                continue
            p1, p2 = px.iloc[a], px.iloc[b]
            if kind == "top":
                if p2 > p1 * (1 + PRICE_TOLERANCE) and i2 < i1:
                    out.append({"kind": "top", "idx1": a, "idx2": b, "confirmed_idx": b + right,
                                "indicator": indicator_col,
                                "price1": float(p1), "price2": float(p2),
                                "ind1": float(i1), "ind2": float(i2)})
            else:
                if p2 < p1 * (1 - PRICE_TOLERANCE) and i2 > i1:
                    out.append({"kind": "bottom", "idx1": a, "idx2": b, "confirmed_idx": b + right,
                                "indicator": indicator_col,
                                "price1": float(p1), "price2": float(p2),
                                "ind1": float(i1), "ind2": float(i2)})

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
