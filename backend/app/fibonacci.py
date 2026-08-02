"""斐波那契位分析（ARCHITECTURE.md 第3节 fibonacci.py 规范）。

**仅展示用途，零打分权重**（统计检验显示斐波那契位与随机水平无差异，见 MODEL_DESIGN.md §2）。
本模块输出只许进入 /api/analysis 的图上标注与文字分析，禁止被 scoring.py 引用。

主导波段 = 最近一个完整 zigzag 腿（dominant_swing）；
fib_analysis 给出回撤位、扩展位、现价位置与 golden pocket。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

LOOKBACK = 120            # dominant_swing 默认回看（根）
RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786, 0.886)
EXTENSIONS = (1.272, 1.618, 2.618)
GOLDEN_POCKET = (0.618, 0.65)   # 黄金口袋区间（回撤比例）


def dominant_swing(df: pd.DataFrame, pivots: pd.DataFrame, lookback: int = LOOKBACK) -> dict | None:
    """当前主导波段：最近一个完整 zigzag 腿。

    返回 {start_idx, end_idx, dir('up'/'down'), start_price, end_price}：
    - dir='up'：低点→高点的上升腿；dir='down'：高点→低点的下跌腿。
    - 以 zigzag 最后两个 pivot 为腿端点（末端 pivot 已右侧确认；
      腿在末端之后可能仍在延伸，属"进行中"性质，由调用方知悉）。
    """
    zz = piv_mod.zigzag(df)
    if len(zz) < 2:
        return None
    n = len(df)
    a, b = zz.iloc[-2], zz.iloc[-1]
    if b["idx"] - a["idx"] > lookback and a["idx"] < n - 1 - lookback:
        return None  # 腿太老，视作无有效主导波段
    direction = "up" if b["kind"] == "H" else "down"
    return {
        "start_idx": int(a["idx"]),
        "end_idx": int(b["idx"]),
        "dir": direction,
        "start_price": float(a["price"]),
        "end_price": float(b["price"]),
    }


def fib_analysis(df: pd.DataFrame, pivots: pd.DataFrame) -> dict | None:
    """斐波那契回撤/扩展分析（规范签名与返回结构）。

    返回 {swing, levels{ratio:price}, position_ratio, nearest_level,
          golden_pocket(bool), extensions{1.272,1.618,2.618}}：
    - 上涨波段：levels 为自高点向下回撤位（高−r×振幅），extensions 为向上突破位（低+r×振幅）；
    - 下跌波段：levels 为自低点向上反弹位（低+r×振幅），extensions 为向下扩展位（高−r×振幅）；
    - position_ratio = 现价在波段振幅中的位置（0=起点，1=终点，可越界）；
    - golden_pocket：现价落在 0.618~0.65 回撤/反弹位区间内。
    """
    sw = dominant_swing(df, pivots)
    if sw is None:
        return None
    close = float(df["close"].iloc[-1])
    hi = max(sw["start_price"], sw["end_price"])
    lo = min(sw["start_price"], sw["end_price"])
    rng = hi - lo
    if rng <= 0:
        return None

    if sw["dir"] == "up":
        levels = {r: hi - r * rng for r in RETRACEMENTS}
        extensions = {r: lo + r * rng for r in EXTENSIONS}
        position_ratio = (close - lo) / rng
        gp_lo, gp_hi = hi - GOLDEN_POCKET[1] * rng, hi - GOLDEN_POCKET[0] * rng
    else:
        levels = {r: lo + r * rng for r in RETRACEMENTS}
        extensions = {r: hi - r * rng for r in EXTENSIONS}
        position_ratio = (hi - close) / rng
        gp_lo, gp_hi = lo + GOLDEN_POCKET[0] * rng, lo + GOLDEN_POCKET[1] * rng

    nearest_ratio, nearest_price = min(levels.items(), key=lambda kv: abs(kv[1] - close))
    return {
        "swing": sw,
        "levels": {r: round(float(p), 4) for r, p in levels.items()},
        "position_ratio": round(float(position_ratio), 4),
        "nearest_level": {"ratio": nearest_ratio, "price": round(float(nearest_price), 4)},
        "golden_pocket": bool(gp_lo <= close <= gp_hi),
        "golden_pocket_zone": [round(float(gp_lo), 4), round(float(gp_hi), 4)],
        "extensions": {r: round(float(p), 4) for r, p in extensions.items()},
    }


if __name__ == "__main__":
    from . import db, pivots as _p

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = loader(ts, start="2023-01-01")
        pv = _p.find_pivots(d)
        r = fib_analysis(d, pv)
        if r:
            print(f"{ts}: swing {r['swing']['dir']} "
                  f"[{r['swing']['start_price']:.2f}→{r['swing']['end_price']:.2f}] "
                  f"pos={r['position_ratio']:.2f} nearest={r['nearest_level']} "
                  f"golden_pocket={r['golden_pocket']}")
        else:
            print(f"{ts}: 无主导波段")
    print("fibonacci 自检通过")
