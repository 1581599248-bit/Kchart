"""波段 pivot 高低点识别（ARCHITECTURE.md 第3节 pivots.py 规范）。

结构识别地基：patterns / divergence / harmonics / fibonacci 全部建立在本模块之上。

防未来函数核心约定（质量红线6）：
- 第 i 根K线的 pivot 只有在第 i+right 根K线走完时才"生效"，
  即 confirmed_at_idx = idx + right。
- 一切下游逻辑在 asof_bar 时点只允许使用 confirmed_at_idx <= asof_bar 的 pivot，
  统一通过 pivots_asof() 过滤，禁止直接消费 find_pivots 的原始输出。
- find_pivots 本身只扫描 range(left, n-right)，保证每个输出的 pivot 在当前数据
  末端都已完成右侧确认（confirmed_at_idx <= n-1）。

参数（行业标准值，冻结，可调仅限文件头）：
- LEFT = RIGHT = 5：pivot 左右各 5 根内最高/最低。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LEFT = 5
RIGHT = 5
ZZ_MIN_PCT = 0.05  # zigzag 最小反转幅度


def find_pivots(df: pd.DataFrame, left: int = LEFT, right: int = RIGHT) -> pd.DataFrame:
    """滚动窗口 pivot 高低点。

    返回 DataFrame[idx, trade_date, price, kind('H'/'L'), confirmed_at_idx]，按 idx 升序。
    - 波段高点：high[i] 为左右各 left/right 根窗口内最高（并列取最早）。
    - 波段低点：low[i] 为窗口内最低（并列取最早）。
    - confirmed_at_idx = idx + right：该 pivot 右侧确认生效的 bar 下标。
    """
    n = len(df)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    dates = df["trade_date"].to_numpy()
    rows = []
    # 只扫描 [left, n-right)：保证 confirmed_at_idx = i+right <= n-1，右侧确认在样本内完成
    for i in range(left, n - right):
        hw = highs[i - left : i + right + 1]
        if highs[i] == hw.max() and int(np.argmax(hw)) == left:
            rows.append((i, dates[i], float(highs[i]), "H", i + right))
            continue  # 同一根K线不同时记高低点（极小概率，取更显著的高点）
        lw = lows[i - left : i + right + 1]
        if lows[i] == lw.min() and int(np.argmin(lw)) == left:
            rows.append((i, dates[i], float(lows[i]), "L", i + right))
    out = pd.DataFrame(rows, columns=["idx", "trade_date", "price", "kind", "confirmed_at_idx"])
    if len(out):
        out["idx"] = out["idx"].astype(int)
        out["confirmed_at_idx"] = out["confirmed_at_idx"].astype(int)
    return out


def pivots_asof(pivots: pd.DataFrame, asof_bar: int) -> pd.DataFrame:
    """asof_bar 时点已右侧确认的 pivot 子集（防未来函数的唯一入口）。

    断言：返回值中绝不允许出现 confirmed_at_idx > asof_bar 的 pivot。
    """
    out = pivots[pivots["confirmed_at_idx"] <= asof_bar]
    assert (out["confirmed_at_idx"] <= asof_bar).all(), "未来函数：使用了未右侧确认的 pivot"
    return out


def alternating(pivots: pd.DataFrame) -> pd.DataFrame:
    """把原始 pivot 序列交替化：同kind相邻者保留更极端的一个。

    numpy 向量化实现（find_patterns_history 每个确认时点都调用，iterrows 曾是主热点）。
    语义与原 iterrows 版完全一致：同kind并列时后者取代前者（>=/<= 取新）。
    """
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
        l = keep[-1]
        if kind[j] == kind[l]:
            more_extreme = (kind[j] == "H" and price[j] >= price[l]) or (
                kind[j] == "L" and price[j] <= price[l]
            )
            if more_extreme:
                keep[-1] = j
        else:
            keep.append(j)
    out = pivots.iloc[keep, :][cols].reset_index(drop=True)
    out["idx"] = out["idx"].astype(int)
    out["confirmed_at_idx"] = out["confirmed_at_idx"].astype(int)
    return out


def zigzag(df: pd.DataFrame, min_pct: float = ZZ_MIN_PCT, left: int = LEFT, right: int = RIGHT) -> pd.DataFrame:
    """基于 pivot 的交替 zigzag 序列（最小反转幅度 min_pct）。

    返回与 find_pivots 同构的 DataFrame，kind 严格 H/L 交替。
    注意：zigzag 末端腿为"进行中"状态，最后一个 pivot 之后价格可能继续延伸；
    下游判定主导波段时应把最后一个 pivot 视为最近腿的端点（其本身已右侧确认）。
    """
    piv = find_pivots(df, left=left, right=right)
    if not len(piv):
        return piv
    # numpy 向量化（语义同原 iterrows 版：同向取更极端者，反向幅度达标才开新腿）
    kind = piv["kind"].to_numpy()
    price = piv["price"].to_numpy(dtype=float)
    keep: list[int] = []
    for j in range(len(piv)):
        if not keep:
            keep.append(j)
            continue
        l = keep[-1]
        if kind[j] == kind[l]:
            # 同向延伸：保留更极端者（当前腿仍在发展中）
            if (kind[j] == "H" and price[j] >= price[l]) or (
                kind[j] == "L" and price[j] <= price[l]
            ):
                keep[-1] = j
            continue
        # 反向 pivot：幅度达标才确认一条新腿
        if abs(price[j] / price[l] - 1.0) >= min_pct:
            keep.append(j)
        # 幅度不足：跳过；后续同kind pivot 会在上面分支自然更新末端
    cols = ["idx", "trade_date", "price", "kind", "confirmed_at_idx"]
    out = piv.iloc[keep, :][cols].reset_index(drop=True) if keep else pd.DataFrame(columns=cols)
    if len(out):
        out["idx"] = out["idx"].astype(int)
        out["confirmed_at_idx"] = out["confirmed_at_idx"].astype(int)
    return out


# ---- 多尺度自适应（ARCHITECTURE.md 多尺度自适应分析 §1-2） ----
MS_LEVELS = (0.03, 0.08, 0.15)   # 小/交易/背景三级 zigzag 最小反转幅度（可调仅限此处）


def multi_scale_zigzag(df: pd.DataFrame, levels: tuple = MS_LEVELS) -> dict:
    """三级 zigzag：{'small': zz3%, 'trade': zz8%, 'background': zz15%}。

    每条腿可由 current_leg() 读取幅度与持续K线数；级别语义：
    small=近期信号级，trade=当前交易级别，background=大周期方向与位置。
    """
    names = ("small", "trade", "background")
    return {name: zigzag(df, min_pct=pct) for name, pct in zip(names, levels)}


def current_leg(zz: pd.DataFrame) -> dict | None:
    """某级别当前腿：最后一个已确认 pivot → 至今的方向/幅度/持续K线数/起点。

    返回 {dir('up'/'down'), start_idx, start_price, start_date, pct(至今涨跌幅),
          bars(持续K线数)}；zz 为空返回 None。
    """
    if not len(zz):
        return None
    last = zz.iloc[-1]
    d = "up" if last["kind"] == "L" else "down"
    return {
        "dir": d,
        "start_idx": int(last["idx"]),
        "start_price": float(last["price"]),
        "start_date": str(last["trade_date"])[:10],
        "kind": str(last["kind"]),
    }


def annotate_leg(leg: dict, closes: np.ndarray, asof: int) -> dict:
    """补全当前腿的至今幅度与持续K线数（原地更新并返回）。"""
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
        print(f"{ts}: bars={len(d)} pivots={len(p)} (H={int((p.kind=='H').sum())}, "
              f"L={int((p.kind=='L').sum())}) zigzag={len(z)}")
        print(p.tail(3).to_string(index=False))
    print("pivots 自检通过")
