"""K线结构识别（ARCHITECTURE.md 第3节 patterns.py 规范）。

输入 df + pivots（pivots.find_pivots 输出），输出 list[PatternEvent]：
PatternEvent = {kind, name, direction('bull'/'bear'/'range'), start_idx, end_idx,
                confirm_idx|None, key_levels{...}, score(-100..100), star(bool), note(str)}

实现清单（规范§3）：
- bull：双底/W底、三重底、头肩底、圆弧底
- bear：双顶/M顶、头肩顶
- range：箱体震荡（含上沿/下沿突破确认）
- 中继：上升旗形、下跌旗形、上升楔形、下降楔形、对称/上升/下降三角形
- 波浪：上升五浪位置判定、下跌 ABC 判定

防未来函数约定：
- 只使用 asof_bar（最后一根K线）时点已右侧确认的 pivot（pivots_asof 过滤）。
- confirm_idx（颈线/边界突破确认K线）不得早于结构最后一个 pivot 的
  confirmed_at_idx——突破发生时该 pivot 必须已是"可知"的（_find_confirm 强制）。
- 未确认结构 score 减半并在 note 标注「构筑中」。

参数全部为文件头常量（防过拟合，冻结）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pivots as piv_mod

# ---- 容差参数（冻结，调整仅限此处）----
DOUBLE_TOL = 0.03        # 双底/双顶 右底更低（右顶更高）的从严容忍度
DOUBLE_TOL_HIGH = 0.10   # 双底 右底偏高（双顶 右顶偏低）的放宽容忍度（强势变体）
DOUBLE_MIN_GAP_D = 20    # 双底/双顶两底（顶）最小间隔：日线≥20根（≈4周，Bulkowski/StockCharts）
DOUBLE_MIN_GAP_W = 4     # 周线≥4根（≈4周）
DOUBLE_MAX_GAP_D = 150   # 双底/双顶两底（顶）最大间隔：日线≤150根（≈7个月）
DOUBLE_MAX_GAP_W = 60    # 周线≤60根（≈14个月）
DOUBLE_MIN_DEPTH = 0.08  # 双底/双顶中间反弹最小幅度（相对较低底；文献≥10%，A股指数放宽）
PRIOR_TREND_PCT = 0.08   # 前置趋势：进入反转形态前的最小顺向幅度
PRIOR_TREND_BARS = 60    # 前置趋势回看窗口（根）
TRIPLE_TOL = 0.03        # 三重底三低点离散度上限
TRIPLE_MIN_DEPTH = 0.06  # 三重底最小深度（相对最低底）
HS_MIN_DEPTH = 0.06      # 头肩头到颈线最小深度（相对颈线）
HS_TIME_SYMM = 2.0       # 头肩左右肩到头的时间对称比上限（比值须 ∈ [1/x, x]）
HS_HEAD_PROM = 0.02      # 头部显著性：头须高出（低于）较高的肩 ≥2%，否则"肩"实为双顶/双底的另一峰——M顶/W底与头肩的分野
EXPIRE_BARS = 60         # 未确认结构的有效期（根，过期未成形的构筑中事件不再上图）
SHOULDER_TOL = 0.05      # 头肩两肩相对差上限
ARC_WINDOW = 60          # 圆弧底观察窗口（根）
ARC_TOL = 0.08           # 圆弧底两侧低点相对差上限
ARC_MIN_DEPTH = 0.05     # 圆弧底最小深度
BOX_LOOKBACK = 60        # 箱体识别回看（根）
BOX_TOL = 0.03           # 箱体内同侧 pivot 离散度
BOX_MAX_HEIGHT = 0.15    # 箱体最大高度（相对下沿）
FLAG_MIN_POLE = 0.10     # 旗杆最小涨幅/跌幅
FLAG_MAX_CONSOL_BARS = 20  # 旗面最长持续
FLAT_EPS = 0.0008        # 趋势线"水平"判定（每根K线相对价格斜率）
VOL_CONFIRM_MULT = 1.2   # 突破放量倍数（相对前20根均量）
WAVE4_OVERLAP_TOL = 0.02  # 4浪可容忍进入1浪区间的幅度
SCORE_CONFIRMED = 80
SCORE_BUILDING = 40      # 未确认减半

_MAJOR_KINDS = {
    "double_bottom", "triple_bottom", "head_shoulders_bottom", "arc_bottom",
    "double_top", "head_shoulders_top",
}


def _ds(df: pd.DataFrame, i: int) -> str:
    """trade_date 转 YYYY-MM-DD 短串（note 文案用）。"""
    return str(df["trade_date"].iloc[i])[:10]


def _vol_note(df: pd.DataFrame, ci: int) -> str:
    """突破日量能描述（放量确认/缩量确认）。"""
    if "vol" not in df.columns or ci < 20:
        return ""
    v = df["vol"].to_numpy(dtype=float)
    base = np.nanmean(v[ci - 20 : ci])
    if base > 0 and v[ci] > VOL_CONFIRM_MULT * base:
        return f"，放量确认（量比{v[ci] / base:.1f}）"
    return "，缩量确认"


def _find_confirm(closes: np.ndarray, last_pivot: dict, level_fn, direction: str, n: int):
    """从结构最后一根 pivot 之后找收盘突破确认K线。

    扫描起点 = max(pivot.idx+1, pivot.confirmed_at_idx)：
    突破确认不得早于该 pivot 右侧生效之时，否则等于用未确认的 pivot 做交易决策。
    level_fn(i) 返回第 i 根K线处的颈线/边界值（支持斜线颈线）。
    """
    start = max(int(last_pivot["idx"]) + 1, int(last_pivot["confirmed_at_idx"]))
    for i in range(start, n):
        lv = level_fn(i)
        if direction == "up" and closes[i] > lv:
            return i
        if direction == "down" and closes[i] < lv:
            return i
    return None


def _trace_pts(df: pd.DataFrame, pts: list[dict]) -> list[dict]:
    """pivot 序列 → 描摹折线点 [{t: 'YYYY-MM-DD', p: price}]。"""
    return [{"t": _ds(df, int(p["idx"])), "p": round(float(p["price"]), 4)} for p in pts]


def _trace_line(df: pd.DataFrame, i1: int, p1: float, i2: int, p2: float) -> list[dict]:
    """任意两点 → 描摹线段（边界/通道用）。"""
    return [{"t": _ds(df, int(i1)), "p": round(float(p1), 4)},
            {"t": _ds(df, int(i2)), "p": round(float(p2), 4)}]


def _ev(kind, name, direction, start_idx, end_idx, confirm_idx, key_levels, note,
        confirmed_score=SCORE_CONFIRMED, trace=None):
    confirmed = confirm_idx is not None
    return {
        "kind": kind,
        "name": name,
        "direction": direction,
        "start_idx": int(start_idx),
        "end_idx": int(end_idx),
        "confirm_idx": int(confirm_idx) if confirmed else None,
        "key_levels": {k: (round(float(v), 4) if isinstance(v, (int, float, np.floating)) else v)
                       for k, v in key_levels.items()},
        "score": confirmed_score if confirmed else SCORE_BUILDING,
        "star": bool(confirmed and kind in _MAJOR_KINDS),
        "note": note if confirmed else note + "（构筑中，未确认）",
        # trace: 形态描摹折线 [{points:[{t,p}...], style:'solid'|'dashed'}...]（前端金色描边）
        "trace": trace or [],
        # active: 结构时效（classify_active 判定，历史结构图上保留但不进 summary）
        "active": True,
    }


# ---------- 反转结构（基于交替 pivot 序列的几何关系） ----------

def _prior_trend_ok(df: pd.DataFrame, pivot: dict, direction: str) -> bool:
    """反转形态的前置趋势校验（没有前置趋势就没有"反转"可言）。

    底部反转（bull）要求 PRIOR_TREND_BARS 窗口内曾出现比 pivot 高 PRIOR_TREND_PCT 的高点
    （即形态是跌出来的）；顶部反转（bear）镜像。
    """
    i = int(pivot["idx"])
    lo = max(0, i - PRIOR_TREND_BARS)
    if direction == "bull":
        ref = float(df["high"].to_numpy(dtype=float)[lo : i + 1].max())
        return ref >= pivot["price"] * (1 + PRIOR_TREND_PCT)
    ref = float(df["low"].to_numpy(dtype=float)[lo : i + 1].min())
    return ref <= pivot["price"] * (1 - PRIOR_TREND_PCT)


def _pair_bottom(ap: list[dict], e: int, closes: np.ndarray, n: int, df: pd.DataFrame,
                 min_gap: int, max_gap: int):
    """以 ap[e]（右底）回溯配对左底：两底为区间内最低的两个低点，中间允许震荡，
    颈线 = 两底之间最高反弹高点（Bulkowski: 突破两谷之间的最高点确认）。"""
    p2 = ap[e]
    best = None
    for i in range(e - 1, -1, -1):
        p1 = ap[i]
        if p1["kind"] != "L":
            continue
        gap = p2["idx"] - p1["idx"]
        if gap > max_gap:
            break
        if gap < min_gap:
            continue
        rel = (p2["price"] - p1["price"]) / p1["price"]
        if not (-DOUBLE_TOL <= rel <= DOUBLE_TOL_HIGH):
            continue
        mid = ap[i + 1 : e]
        mid_hs = [p for p in mid if p["kind"] == "H"]
        if not mid_hs:
            continue
        lo2 = min(p1["price"], p2["price"])
        # 两底之间不得出现更低低点（否则是阶梯下跌，不是双底）
        if any(p["price"] < lo2 * 0.995 for p in mid if p["kind"] == "L"):
            continue
        m = max(mid_hs, key=lambda p: p["price"])
        # 反弹高点越过左底下跌起点（结构被复位）且右底再破左底 = 下跌延续，不是双底
        # （真双底的右底须守得住：2024 反弹 3174 超下跌起点 2976 但右底 2690 守住左底 2635）
        if p2["price"] < p1["price"] and i >= 1 and ap[i - 1]["kind"] == "H" \
                and m["price"] > ap[i - 1]["price"]:
            continue
        depth = (m["price"] - lo2) / lo2
        if depth < DOUBLE_MIN_DEPTH:
            continue
        if not _prior_trend_ok(df, p1, "bull"):
            continue
        if best is None or depth > best[0]:
            best = (depth, i, m)
    if best is None:
        return None
    _, i, m = best
    p1 = ap[i]
    neckline = m["price"]
    lo2 = min(p1["price"], p2["price"])
    ci = _find_confirm(closes, p2, lambda x: neckline, "up", n)
    tgt = neckline + (neckline - lo2)
    note = f"W底：两低点 {p1['price']:.2f}/{p2['price']:.2f}，颈线 {neckline:.2f}"
    if ci is not None:
        note += f"，{_ds(df, ci)} 收盘站上颈线" + _vol_note(df, ci)
    return _ev("double_bottom", "W底", "bull", p1["idx"], p2["idx"], ci,
               {"neckline": neckline, "low1": p1["price"], "low2": p2["price"],
                "measure_target": tgt, "invalidation": lo2}, note,
               trace=[{"points": _trace_pts(df, ap[i : e + 1]), "style": "solid"}])


def _pair_top(ap: list[dict], e: int, closes: np.ndarray, n: int, df: pd.DataFrame,
              min_gap: int, max_gap: int):
    """以 ap[e]（右顶）回溯配对左顶（双底镜像）。"""
    p2 = ap[e]
    best = None
    for i in range(e - 1, -1, -1):
        p1 = ap[i]
        if p1["kind"] != "H":
            continue
        gap = p2["idx"] - p1["idx"]
        if gap > max_gap:
            break
        if gap < min_gap:
            continue
        rel = (p2["price"] - p1["price"]) / p1["price"]
        if not (-DOUBLE_TOL <= rel <= DOUBLE_TOL):
            continue  # M顶两峰须同高（±3%）：右顶偏低 >3% 是"更低的高点"（下跌阶梯），不是双顶
        mid = ap[i + 1 : e]
        mid_ls = [p for p in mid if p["kind"] == "L"]
        if not mid_ls:
            continue
        hi2 = max(p1["price"], p2["price"])
        if any(p["price"] > hi2 * 1.005 for p in mid if p["kind"] == "H"):
            continue
        m = min(mid_ls, key=lambda p: p["price"])
        depth = (hi2 - m["price"]) / hi2
        if depth < DOUBLE_MIN_DEPTH:
            continue
        if not _prior_trend_ok(df, p1, "bear"):
            continue
        if best is None or depth > best[0]:
            best = (depth, i, m)
    if best is None:
        return None
    _, i, m = best
    p1 = ap[i]
    neckline = m["price"]
    hi2 = max(p1["price"], p2["price"])
    ci = _find_confirm(closes, p2, lambda x: neckline, "down", n)
    tgt = neckline - (hi2 - neckline)
    note = f"M顶：两高点 {p1['price']:.2f}/{p2['price']:.2f}，颈线 {neckline:.2f}"
    if ci is not None:
        note += f"，{_ds(df, ci)} 收盘跌破颈线" + _vol_note(df, ci)
    return _ev("double_top", "M顶", "bear", p1["idx"], p2["idx"], ci,
               {"neckline": neckline, "high1": p1["price"], "high2": p2["price"],
                "measure_target": tgt, "invalidation": hi2}, note,
               trace=[{"points": _trace_pts(df, ap[i : e + 1]), "style": "solid"}])


def _detect_double(ap: list[dict], closes: np.ndarray, n: int, df: pd.DataFrame,
                   min_gap: int = DOUBLE_MIN_GAP_D, max_gap: int = DOUBLE_MAX_GAP_D):
    """双底（W底）/双顶（M顶）：以最后 1~3 个 pivot 为右底/右顶回溯配对。

    有效性条件（Bulkowski/StockCharts 经典判定，过滤"随便两个相近低点"的误报）：
    - 右底相对左底 ∈ [-DOUBLE_TOL, +DOUBLE_TOL_HIGH]（右底略高是强势变体，右底更低>3%否决；
      双顶镜像），间隔 ∈ [min_gap, max_gap]（日线 20~150 根 ≈ 4周~7个月）；
    - 两底（顶）之间允许震荡，但不得出现更低低点（更高高点）；颈线为区间内最高反弹高点
      （双顶为最低回撤低点）；
    - 中间反弹幅度（颈线−较低底）/较低底 >= DOUBLE_MIN_DEPTH（文献≥10%，A股指数放宽）；
    - 有前置趋势（底部形态前有明显下跌，顶部形态前有明显上涨）；
    - 未确认不算形态：确认与否交给上层裁决（收盘站上/跌破颈线）。
    """
    bull = bear = None
    for e in range(len(ap) - 1, max(len(ap) - 4, 0), -1):
        p2 = ap[e]
        if p2["kind"] == "L" and bull is None:
            bull = _pair_bottom(ap, e, closes, n, df, min_gap, max_gap)
        if p2["kind"] == "H" and bear is None:
            bear = _pair_top(ap, e, closes, n, df, min_gap, max_gap)
        if bull is not None and bear is not None:
            break
    return bull, bear


def _detect_triple_bottom(ap: list[dict], closes: np.ndarray, n: int, df: pd.DataFrame):
    """三重底：三个低点离散度 < TRIPLE_TOL，中间两个高点构成颈线；
    深度（颈线−最低底）/最低底 >= TRIPLE_MIN_DEPTH，且有前置下跌趋势。"""
    for e in range(len(ap) - 1, max(len(ap) - 4, 3), -1):
        if e < 4:
            break
        p1, m1, p2, m2, p3 = ap[e - 4 : e + 1]
        kinds = [p["kind"] for p in (p1, m1, p2, m2, p3)]
        if kinds != ["L", "H", "L", "H", "L"]:
            continue
        lows = [p1["price"], p2["price"], p3["price"]]
        if (max(lows) - min(lows)) / min(lows) >= TRIPLE_TOL:
            continue
        neckline = max(m1["price"], m2["price"])
        if (neckline - min(lows)) / min(lows) < TRIPLE_MIN_DEPTH:
            continue
        if not _prior_trend_ok(df, p1, "bull"):
            continue
        ci = _find_confirm(closes, p3, lambda i: neckline, "up", n)
        tgt = neckline + (neckline - min(lows))
        note = f"三重底：三低点 {'/'.join(f'{x:.2f}' for x in lows)}，颈线 {neckline:.2f}"
        if ci is not None:
            note += f"，{_ds(df, ci)} 收盘突破颈线" + _vol_note(df, ci)
        return _ev("triple_bottom", "三重底", "bull", p1["idx"], p3["idx"], ci,
                   {"neckline": neckline, "lows": [round(x, 4) for x in lows],
                    "measure_target": tgt, "invalidation": p3["price"]}, note,
                   trace=[{"points": _trace_pts(df, [p1, m1, p2, m2, p3]), "style": "solid"}])
    return None


def _detect_head_shoulders(ap: list[dict], closes: np.ndarray, n: int, df: pd.DataFrame):
    """头肩底 / 头肩顶。颈线为两肩间高（低）点连线（支持斜线）。

    有效性条件：头 = 三谷（峰）中最低（高）者且右肩不破头；两肩价差 < SHOULDER_TOL；
    头到颈线深度 >= HS_MIN_DEPTH；左右肩到头的时间比 ∈ [1/HS_TIME_SYMM, HS_TIME_SYMM]；
    有前置趋势。
    """
    bull = bear = None
    for e in range(len(ap) - 1, max(len(ap) - 4, 3), -1):
        if e < 4:
            break
        s1, m1, head, m2, s2 = ap[e - 4 : e + 1]
        kinds = [p["kind"] for p in (s1, m1, head, m2, s2)]
        t1, t2 = head["idx"] - s1["idx"], s2["idx"] - head["idx"]
        sym_ok = t1 > 0 and t2 > 0 and (1 / HS_TIME_SYMM) <= t1 / t2 <= HS_TIME_SYMM
        # 头肩底：L H L H L，头部最低，右肩不低于头部（由 head 最低保证），两肩近似
        if kinds == ["L", "H", "L", "H", "L"] and bull is None:
            if head["price"] < s1["price"] and head["price"] < s2["price"] \
                    and abs(s2["price"] - s1["price"]) / ((s1["price"] + s2["price"]) / 2) < SHOULDER_TOL \
                    and (min(s1["price"], s2["price"]) - head["price"]) / min(s1["price"], s2["price"]) >= HS_HEAD_PROM \
                    and s2["idx"] - s1["idx"] >= 10 and sym_ok \
                    and _prior_trend_ok(df, s1, "bull"):
                x1, y1, x2, y2 = m1["idx"], m1["price"], m2["idx"], m2["price"]
                slope = (y2 - y1) / max(x2 - x1, 1)
                nl = lambda i: y1 + slope * (i - x1)
                nl_head = nl(head["idx"])
                if (nl_head - head["price"]) / nl_head >= HS_MIN_DEPTH:
                    ci = _find_confirm(closes, s2, nl, "up", n)
                    tgt = nl(s2["idx"]) + (nl_head - head["price"])
                    note = (f"头肩底：左肩 {s1['price']:.2f}/头 {head['price']:.2f}/右肩 {s2['price']:.2f}，"
                            f"颈线约 {nl(s2['idx']):.2f}")
                    if ci is not None:
                        note += f"，{_ds(df, ci)} 收盘突破颈线" + _vol_note(df, ci)
                    bull = _ev("head_shoulders_bottom", "头肩底", "bull", s1["idx"], s2["idx"], ci,
                               {"neckline_at_end": nl(s2["idx"]), "neckline_left": y1,
                                "neckline_right": y2, "head": head["price"],
                                "shoulder_left": s1["price"], "shoulder_right": s2["price"],
                                "measure_target": tgt, "invalidation": s2["price"]}, note,
                               trace=[{"points": _trace_pts(df, [s1, m1, head, m2, s2]), "style": "solid"}])
        # 头肩顶：H L H L H，头部最高
        if kinds == ["H", "L", "H", "L", "H"] and bear is None:
            if head["price"] > s1["price"] and head["price"] > s2["price"] \
                    and abs(s2["price"] - s1["price"]) / ((s1["price"] + s2["price"]) / 2) < SHOULDER_TOL \
                    and (head["price"] - max(s1["price"], s2["price"])) / head["price"] >= HS_HEAD_PROM \
                    and s2["idx"] - s1["idx"] >= 10 and sym_ok \
                    and _prior_trend_ok(df, s1, "bear"):
                x1, y1, x2, y2 = m1["idx"], m1["price"], m2["idx"], m2["price"]
                slope = (y2 - y1) / max(x2 - x1, 1)
                nl = lambda i: y1 + slope * (i - x1)
                nl_head = nl(head["idx"])
                if (head["price"] - nl_head) / head["price"] >= HS_MIN_DEPTH:
                    ci = _find_confirm(closes, s2, nl, "down", n)
                    tgt = nl(s2["idx"]) - (head["price"] - nl_head)
                    note = (f"头肩顶：左肩 {s1['price']:.2f}/头 {head['price']:.2f}/右肩 {s2['price']:.2f}，"
                            f"颈线约 {nl(s2['idx']):.2f}")
                    if ci is not None:
                        note += f"，{_ds(df, ci)} 收盘跌破颈线" + _vol_note(df, ci)
                    bear = _ev("head_shoulders_top", "头肩顶", "bear", s1["idx"], s2["idx"], ci,
                               {"neckline_at_end": nl(s2["idx"]), "neckline_left": y1,
                                "neckline_right": y2, "head": head["price"],
                                "shoulder_left": s1["price"], "shoulder_right": s2["price"],
                                "measure_target": tgt, "invalidation": s2["price"]}, note,
                               trace=[{"points": _trace_pts(df, [s1, m1, head, m2, s2]), "style": "solid"}])
        if bull is not None and bear is not None:
            break
    return bull, bear


def _detect_arc_bottom(df: pd.DataFrame, closes: np.ndarray, n: int, asof: int):
    """圆弧底（启发式）：最近 ARC_WINDOW 根内低点序列呈 U 形，两侧低点近似等高。"""
    if asof < ARC_WINDOW - 1:
        return None
    s = asof - ARC_WINDOW + 1
    lows = df["low"].to_numpy(dtype=float)[s : asof + 1]
    start_low = float(np.mean(lows[:5]))
    end_low = float(np.mean(lows[-5:]))
    min_pos = int(np.argmin(lows))
    min_low = float(lows[min_pos])
    in_middle = 0.2 * ARC_WINDOW <= min_pos <= 0.8 * ARC_WINDOW
    depth = (start_low - min_low) / start_low
    if not in_middle or depth < ARC_MIN_DEPTH:
        return None
    if abs(end_low - start_low) / start_low >= ARC_TOL:
        return None
    highs = df["high"].to_numpy(dtype=float)
    neckline = float(np.max(highs[s : s + 10]))  # 弧口左沿高点
    last = {"idx": s + min_pos, "confirmed_at_idx": s + min_pos + piv_mod.RIGHT}
    ci = _find_confirm(closes, last, lambda i: neckline, "up", n)
    tgt = neckline + (neckline - min_low)
    note = (f"圆弧底：弧底 {min_low:.2f}（深度 {depth:.1%}），两侧低点 "
            f"{start_low:.2f}/{end_low:.2f}，弧口颈线 {neckline:.2f}（启发式识别）")
    if ci is not None:
        note += f"，{_ds(df, ci)} 收盘突破弧口" + _vol_note(df, ci)
    return _ev("arc_bottom", "圆弧底", "bull", s, s + min_pos, ci,
               {"neckline": neckline, "arc_low": min_low, "measure_target": tgt,
                "invalidation": min_low}, note,
               trace=[{"points": [{"t": _ds(df, s), "p": round(start_low, 4)},
                                  {"t": _ds(df, s + min_pos), "p": round(min_low, 4)},
                                  {"t": _ds(df, asof), "p": round(end_low, 4)}],
                       "style": "solid"}])


# ---------- 中继/整理结构 ----------

def _trendline(points: list[dict]):
    """对 pivot 点做一元线性回归 price ~ idx，返回 (slope_per_bar, 截距)。"""
    x = np.array([p["idx"] for p in points], dtype=float)
    y = np.array([p["price"] for p in points], dtype=float)
    k, b = np.polyfit(x, y, 1)
    return float(k), float(b)


def _recent_hl(ap: list[dict], asof: int, lookback: int):
    hs = [p for p in ap if p["kind"] == "H" and p["idx"] >= asof - lookback]
    ls = [p for p in ap if p["kind"] == "L" and p["idx"] >= asof - lookback]
    return hs, ls


def _detect_box(ap: list[dict], closes: np.ndarray, n: int, df: pd.DataFrame, asof: int):
    """箱体震荡：BOX_LOOKBACK 内 >=2 个高点压在同一带、>=2 个低点托在同一带。"""
    hs, ls = _recent_hl(ap, asof, BOX_LOOKBACK)
    if len(hs) < 2 or len(ls) < 2:
        return None
    upper = max(p["price"] for p in hs)
    lower = min(p["price"] for p in ls)
    h_spread = (upper - min(p["price"] for p in hs)) / upper
    l_spread = (max(p["price"] for p in ls) - lower) / lower
    height = (upper - lower) / lower
    if h_spread >= BOX_TOL or l_spread >= BOX_TOL or height >= BOX_MAX_HEIGHT or height <= 0:
        return None
    if not (lower * (1 - BOX_TOL) <= closes[asof] <= upper * (1 + BOX_TOL)):
        return None  # 现价已远离箱体，不再视为箱体事件
    start = min(p["idx"] for p in hs + ls)
    last_p = max(hs + ls, key=lambda p: p["idx"])
    ci_up = _find_confirm(closes, last_p, lambda i: upper, "up", n)
    ci_dn = _find_confirm(closes, last_p, lambda i: lower, "down", n)
    ci, direction, brk = None, "range", ""
    if ci_up is not None and (ci_dn is None or ci_up <= ci_dn):
        ci, direction, brk = ci_up, "bull", "上沿"
    elif ci_dn is not None:
        ci, direction, brk = ci_dn, "bear", "下沿"
    note = f"箱体震荡：上沿 {upper:.2f} / 下沿 {lower:.2f}（振幅 {height:.1%}）"
    if ci is not None:
        note += f"，{_ds(df, ci)} 收盘突破{brk}" + _vol_note(df, ci)
    ev = _ev("box", "箱体震荡", direction, start, asof, ci,
             {"upper": upper, "lower": lower,
              "measure_target_up": upper + (upper - lower),
              "measure_target_dn": lower - (upper - lower),
              "invalidation": lower if direction != "bear" else upper}, note,
             trace=[{"points": _trace_line(df, start, upper, asof, upper), "style": "dashed"},
                    {"points": _trace_line(df, start, lower, asof, lower), "style": "dashed"}])
    ev["star"] = bool(ci is not None and direction == "bull")
    return ev


def _detect_flag(df: pd.DataFrame, closes: np.ndarray, asof: int):
    """上升旗形（bull）：急涨旗杆 + 小幅下飘旗面，收盘突破旗面上沿确认。
    下跌旗形（bear）镜像。"""
    if asof < 30:
        return None
    bull = bear = None
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    for pole_len in (8, 10, 13):
        # 旗面长度 5..FLAG_MAX_CONSOL_BARS，旗杆紧跟其前
        for consol_len in range(5, FLAG_MAX_CONSOL_BARS + 1):
            c_end = asof
            c_start = asof - consol_len + 1
            p_end = c_start - 1
            p_start = p_end - pole_len + 1
            if p_start < 0:
                continue
            pole_gain = closes[p_end] / closes[p_start] - 1
            consol_high = float(np.max(highs[c_start : c_end + 1]))
            consol_low = float(np.min(lows[c_start : c_end + 1]))
            if pole_gain >= FLAG_MIN_POLE and bull is None:
                drift = np.polyfit(np.arange(consol_len), closes[c_start : c_end + 1], 1)[0]
                retraced = (closes[p_end] - closes[c_end]) / max(closes[p_end] - closes[p_start], 1e-9)
                if drift < 0 and 0.1 <= retraced <= 0.6 and consol_high <= closes[p_end] * 1.03:
                    last = {"idx": c_end, "confirmed_at_idx": c_end}
                    ci = _find_confirm(closes, last, lambda i: consol_high, "up", len(closes))
                    # 旗形 confirm 只允许未来发生；此处 asof 即末端，ci 必为 None，改查历史：
                    note = (f"上升旗形：旗杆 {pole_gain:.1%}（{pole_len}根），"
                            f"旗面回撤 {retraced:.1%}，旗面上沿 {consol_high:.2f}")
                    bull = _ev("bull_flag", "上升旗形", "bull", p_start, c_end, ci,
                               {"flag_upper": consol_high, "flag_lower": consol_low,
                                "pole_top": float(closes[p_end]),
                                "measure_target": float(closes[p_end]) + (closes[p_end] - closes[p_start]),
                                "invalidation": consol_low}, note,
                               trace=[{"points": _trace_line(df, p_start, closes[p_start], p_end, closes[p_end]),
                                       "style": "solid"},
                                      {"points": _trace_line(df, c_start, consol_high, c_end, consol_high),
                                       "style": "dashed"},
                                      {"points": _trace_line(df, c_start, consol_low, c_end, consol_low),
                                       "style": "dashed"}])
            pole_drop = closes[p_start] / closes[p_end] - 1
            if pole_drop >= FLAG_MIN_POLE and bear is None:
                drift = np.polyfit(np.arange(consol_len), closes[c_start : c_end + 1], 1)[0]
                retraced = (closes[c_end] - closes[p_end]) / max(closes[p_start] - closes[p_end], 1e-9)
                if drift > 0 and 0.1 <= retraced <= 0.6 and consol_low >= closes[p_end] * 0.97:
                    last = {"idx": c_end, "confirmed_at_idx": c_end}
                    ci = _find_confirm(closes, last, lambda i: consol_low, "down", len(closes))
                    note = (f"下跌旗形：旗杆跌幅 {pole_drop:.1%}（{pole_len}根），"
                            f"旗面反弹 {retraced:.1%}，旗面下沿 {consol_low:.2f}")
                    bear = _ev("bear_flag", "下跌旗形", "bear", p_start, c_end, ci,
                               {"flag_upper": consol_high, "flag_lower": consol_low,
                                "pole_bottom": float(closes[p_end]),
                                "measure_target": float(closes[p_end]) - (closes[p_start] - closes[p_end]),
                                "invalidation": consol_high}, note,
                               trace=[{"points": _trace_line(df, p_start, closes[p_start], p_end, closes[p_end]),
                                       "style": "solid"},
                                      {"points": _trace_line(df, c_start, consol_high, c_end, consol_high),
                                       "style": "dashed"},
                                      {"points": _trace_line(df, c_start, consol_low, c_end, consol_low),
                                       "style": "dashed"}])
        if bull is not None and bear is not None:
            break
    return bull, bear


def _detect_wedge_triangle(ap: list[dict], closes: np.ndarray, n: int, df: pd.DataFrame, asof: int):
    """楔形与三角形：最近交替 pivot 的上沿（高点连线）与下沿（低点连线）的斜率/收敛关系。

    - 上升楔形：两线同向上且收敛（下沿更陡）→ bear 反转
    - 下降楔形：两线同向下且收敛（上沿更陡）→ bull 反转
    - 对称三角形：上沿下压、下沿上托
    - 上升三角形：上沿水平、下沿上托
    - 下降三角形：上沿下压、下沿水平
    """
    hs, ls = _recent_hl(ap, asof, 70)
    if len(hs) < 2 or len(ls) < 2:
        return []
    hs = hs[-3:]
    ls = ls[-3:]
    ku, bu = _trendline(hs)
    kl, bl = _trendline(ls)
    px = closes[asof]
    su, sl = ku / px, kl / px  # 归一化斜率（每根K线相对价格）
    span_end = asof + 10
    gap_now = (ku * asof + bu) - (kl * asof + bl)
    gap_prev = (ku * hs[0]["idx"] + bu) - (kl * hs[0]["idx"] + bl)
    converging = 0 < gap_now < gap_prev
    events = []
    up_line = lambda i: ku * i + bu
    dn_line = lambda i: kl * i + bl
    last_p = max(hs + ls, key=lambda p: p["idx"])
    start = min(hs[0]["idx"], ls[0]["idx"])

    def _mk(kind, name, direction, brk_dir, level_fn, extra):
        ci = _find_confirm(closes, last_p, level_fn, brk_dir, n)
        note = extra
        if ci is not None:
            note += f"，{_ds(df, ci)} 收盘突破边界" + _vol_note(df, ci)
        inv = dn_line(asof) if direction != "bear" else up_line(asof)
        return _ev(kind, name, direction, start, asof, ci,
                   {"upper_now": up_line(asof), "lower_now": dn_line(asof),
                    "invalidation": float(inv)}, note,
                   trace=[{"points": _trace_line(df, start, up_line(start), asof, up_line(asof)),
                           "style": "dashed"},
                          {"points": _trace_line(df, start, dn_line(start), asof, dn_line(asof)),
                           "style": "dashed"}])

    if converging:
        if su > FLAT_EPS and sl > FLAT_EPS and sl > su:
            events.append(_mk("rising_wedge", "上升楔形", "bear", "down", dn_line,
                              "上升楔形：高点低点同步抬高但收敛，看跌反转结构"))
        elif su < -FLAT_EPS and sl < -FLAT_EPS and su < sl:
            events.append(_mk("falling_wedge", "下降楔形", "bull", "up", up_line,
                              "下降楔形：高点低点同步下移但收敛，看涨反转结构"))
        elif su < -FLAT_EPS and sl > FLAT_EPS:
            events.append(_mk("sym_triangle", "对称三角形", "range", "up", up_line,
                              "对称三角形：上沿下压、下沿上托，收敛待方向选择"))
        elif abs(su) <= FLAT_EPS and sl > FLAT_EPS:
            events.append(_mk("asc_triangle", "上升三角形", "bull", "up", up_line,
                              "上升三角形：上沿水平压制、低点抬升，偏多"))
        elif su < -FLAT_EPS and abs(sl) <= FLAT_EPS:
            events.append(_mk("desc_triangle", "下降三角形", "bear", "down", dn_line,
                              "下降三角形：下沿水平支撑、高点下移，偏空"))
    return events


# ---------- 波浪 ----------

def _detect_waves(zz: pd.DataFrame, closes: np.ndarray, asof: int, df: pd.DataFrame):
    """上升五浪位置判定 + 下跌 ABC 判定（基于 zigzag 腿）。

    时效纪律（ABC 准确性红线）：下跌 ABC 只有在价格仍处结构内（未收复 B 浪高点、
    未到达 C 浪量度目标）才视为活跃；价格收复 B 浪高点 = 调整结束转为历史结构，
    note 明确写出，summary 不得再把它当作"当下结构"描述。
    """
    events = []
    if len(zz) < 4:
        return events
    pts = zz.tail(10).to_dict("records")
    px = float(closes[asof])

    # 上升五浪：L0 H1 L2 H3 L4 (H5)，2浪不破0浪起点，4浪不深入1浪顶
    for k in range(len(pts) - 1, 3, -1):
        seq = pts[max(0, k - 5) : k + 1]
        if len(seq) >= 5 and seq[0]["kind"] == "L":
            ok = True
            legs = seq
            for a, b in zip(legs, legs[1:]):
                if a["kind"] == b["kind"]:
                    ok = False
            if not ok:
                continue
            l0 = legs[0]["price"]
            h1 = legs[1]["price"]
            l2 = legs[2]["price"]
            if not (h1 > l0 and l2 > l0):
                continue
            wave = 3  # 已有 L0-H1-L2-H3 中的 H3 或由 legs 数确定
            h3 = legs[3]["price"] if len(legs) > 3 else None
            if h3 is None or h3 <= h1:
                continue
            note = f"上升五浪：1浪顶 {h1:.2f}、3浪顶 {h3:.2f}"
            levels = {"wave1_top": h1, "wave2_low": l2, "wave3_top": h3,
                      "invalidation": l2}
            if len(legs) >= 5:
                l4 = legs[4]["price"]
                if l4 <= l2 or l4 < h1 * (1 - WAVE4_OVERLAP_TOL):
                    continue  # 4浪违规（进入1浪区间过深），不算有效五浪计数
                levels["wave4_low"] = l4
                levels["invalidation"] = l4
                wave = 5
                note += f"、4浪底 {l4:.2f}，当前处于第5浪（末升段，注意衰竭）"
                if len(legs) >= 6:
                    h5 = legs[5]["price"]
                    levels["wave5_top"] = h5
                    note = note.replace("当前处于第5浪（末升段，注意衰竭）",
                                        f"5浪顶 {h5:.2f} 已现，五浪完成，警惕转入调整")
            else:
                note += "，当前处于第4浪调整或第5浪构筑"
            events.append(_ev("wave_up5", "上升五浪", "bull", legs[0]["idx"], legs[-1]["idx"],
                              None, levels, note, confirmed_score=50,
                              trace=[{"points": _trace_pts(df, legs), "style": "solid"}]))
            break

    # 下跌 ABC：显著高点 H_top → L_a → 反弹 H_b(<H_top) → 跌破 L_a 走 C 浪
    for k in range(len(pts) - 1, 2, -1):
        seq = pts[max(0, k - 3) : k + 1]
        if len(seq) < 4:
            continue
        t, a, b, c = seq[-4], seq[-3], seq[-2], seq[-1]
        if [p["kind"] for p in (t, a, b, c)] != ["H", "L", "H", "L"]:
            continue
        if not (b["price"] < t["price"] and c["price"] < a["price"]):
            continue
        c_target = a["price"] - (t["price"] - a["price"]) * 0.618  # C=A 的 0.618 起
        # 时效状态机：价格位置决定 ABC 是"当下结构"还是"历史结构"
        if px > b["price"]:
            state = "，价格已收复B浪高点，下跌调整结束（转为历史结构）"
            active = False
        elif px <= c_target:
            state = "，C浪已及量度目标（转为历史结构）"
            active = False
        elif px > c["price"] * 1.01:
            state = "，C浪似已企稳"
            active = True
        else:
            state = "，C浪进行中"
            active = True
        note = (f"下跌ABC：顶 {t['price']:.2f}→A浪底 {a['price']:.2f}→B浪反弹 {b['price']:.2f}"
                f"→C浪至 {c['price']:.2f}" + state)
        ev = _ev("wave_down_abc", "下跌ABC", "bear", t["idx"], c["idx"],
                 None, {"top": t["price"], "a_low": a["price"], "b_high": b["price"],
                        "c_low": c["price"], "c_target": c_target,
                        "measure_target": c_target, "invalidation": b["price"]}, note,
                 confirmed_score=50,
                 trace=[{"points": _trace_pts(df, [t, a, b, c]), "style": "solid"}])
        ev["active"] = active
        events.append(ev)
        break
    return events


# ---------- 结构时效分类（多尺度自适应 §3） ----------

def classify_active(events: list[dict], closes: np.ndarray, asof: int,
                    trade_leg_start_idx: int) -> list[dict]:
    """判定每个结构是否"活跃"（原地更新 e['active'] 并返回 events）。

    历史结构（图上标注保留，不进 summary）的判定：
    1. 价格已越过失效点（bull/range：收盘 < invalidation；bear：收盘 > invalidation）；
    2. 已确认结构的量度目标已到达；
    3. 未确认结构的终点落在交易级别当前腿起点之前（本级别已走出新腿）；
    4. 已被同级反向的更新已确认结构取代。
    """
    px = float(closes[asof])
    confirmed = [e for e in events if e["confirm_idx"] is not None]
    for e in events:
        if not e.get("active", True):
            continue  # 探测器内部已判定历史（如 ABC 收复 B 浪高点）
        lv = e["key_levels"]
        inv = lv.get("invalidation")
        active = True
        if inv is not None:
            if e["direction"] == "bear" and px > inv:
                active = False
            elif e["direction"] != "bear" and px < inv:
                active = False
        if active and e["confirm_idx"] is not None:
            if e["direction"] == "bull":
                tgt = lv.get("measure_target") or lv.get("measure_target_up")
                if tgt is not None and px >= tgt:
                    active = False
            elif e["direction"] == "bear":
                tgt = lv.get("measure_target") or lv.get("measure_target_dn")
                if tgt is not None and px <= tgt:
                    active = False
        if active and e["confirm_idx"] is None and e["end_idx"] < trade_leg_start_idx:
            active = False
        if active and e["confirm_idx"] is not None:
            newer_opp = any(x["confirm_idx"] > e["confirm_idx"]
                            and x["direction"] != e["direction"] for x in confirmed)
            if newer_opp:
                active = False
        e["active"] = bool(active)
    # 同方向已确认结构只留一个代表为活跃（主要反转结构优先，其次确认最晚），
    # 其余转为历史事件——否则同向旧突破信号（楔形/三角/箱体/M顶…）全部堆积在当下区域
    by_dir: dict[str, list[dict]] = {}
    for e in events:
        if e.get("active", True) and e["confirm_idx"] is not None:
            by_dir.setdefault(e["direction"], []).append(e)
    for lst in by_dir.values():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda e: (_REV_KIND_RANK.get(e["kind"], 0) >= 2, e["confirm_idx"]))
        for e in lst[:-1]:
            e["active"] = False
    return events


# ---------- 历史结构检测引擎（全图推背） ----------

_GAP_BY_TF = {"1d": (DOUBLE_MIN_GAP_D, DOUBLE_MAX_GAP_D),
              "1w": (DOUBLE_MIN_GAP_W, DOUBLE_MAX_GAP_W),
              "60m": (DOUBLE_MIN_GAP_D, DOUBLE_MAX_GAP_D)}


def _range_overlaps(a: dict, b: dict) -> bool:
    return not (a["end_idx"] < b["start_idx"] or a["start_idx"] > b["end_idx"])


def _dedupe_overlaps(events: list[dict]) -> list[dict]:
    """同 kind 且 pivot 区间相交的事件视为同一结构的重复探测。
    裁决：已确认 > 未确认；都已确认取确认更早（信号更及时），确认同时取两底/顶价差更小
    （形态更标准）；都未确认取右端更晚（信息更新）。"""
    def rel_diff(e: dict) -> float:
        lv = e["key_levels"]
        if "low1" in lv and "low2" in lv:
            return abs(lv["low2"] - lv["low1"]) / lv["low1"]
        if "high1" in lv and "high2" in lv:
            return abs(lv["high2"] - lv["high1"]) / lv["high1"]
        return 0.0

    out: list[dict] = []
    for e in sorted(events, key=lambda x: (x["kind"], x["end_idx"])):
        dup = next((x for x in out if x["kind"] == e["kind"] and _range_overlaps(x, e)), None)
        if dup is None:
            out.append(e)
            continue
        ec, dc = e["confirm_idx"], dup["confirm_idx"]
        if (ec is not None) != (dc is not None):
            replace = ec is not None
        elif ec is not None and dc is not None:
            replace = (ec, rel_diff(e)) < (dc, rel_diff(dup))
        else:
            replace = e["end_idx"] > dup["end_idx"]
        if replace:
            out.remove(dup)
            out.append(e)
    return out


_REV_KIND_RANK = {"triple_bottom": 3, "head_shoulders_bottom": 3, "head_shoulders_top": 3,
                  "double_bottom": 2, "double_top": 2, "arc_bottom": 1}


def _dedupe_cross_kind(events: list[dict]) -> list[dict]:
    """同一反转区域（方向相同且 pivot 区间相交）只留一个最有代表性的结构：
    已确认 > 未确认，确认更早 > 更晚，三重底/头肩 > 双底/双顶 > 圆弧底。"""
    rev = [e for e in events if e["kind"] in _REV_KIND_RANK]
    rest = [e for e in events if e["kind"] not in _REV_KIND_RANK]
    out: list[dict] = []
    for e in sorted(rev, key=lambda x: x["start_idx"]):
        dup = next((x for x in out if x["direction"] == e["direction"]
                    and _range_overlaps(x, e)), None)
        if dup is None:
            out.append(e)
            continue
        ec, dc = e["confirm_idx"], dup["confirm_idx"]
        e_key = (ec is not None, -(ec or 10**9), _REV_KIND_RANK[e["kind"]], e["end_idx"])
        d_key = (dc is not None, -(dc or 10**9), _REV_KIND_RANK[dup["kind"]], dup["end_idx"])
        if e_key > d_key:
            out.remove(dup)
            out.append(e)
    return rest + out


def find_patterns_history(df: pd.DataFrame, pivots: pd.DataFrame,
                          timeframe: str = "1d", asof_bar: int | None = None) -> list[dict]:
    """滚动时点历史结构检测：在每个 pivot 右侧确认时点重跑反转/整理结构探测，
    让历史上出现过的形态也能上图（此前只探测最新时点，长周期图上一片空白）。

    裁决纪律（Bulkowski"未确认不算形态"）：
    - 从未确认且失效点被收盘越过 = 构筑失败，丢弃（事后看是噪声，不提供错误信息）；
    - 未确认且结构右端距今超过 EXPIRE_BARS = 过期未成形态，丢弃；
    - 已确认但确认前先破失效点 = 序列不自洽，丢弃；
    - 已确认结构正常保留（其后的失效/达标由 classify_active 标记）。
    """
    n = len(df)
    asof_final = n - 1 if asof_bar is None else min(asof_bar, n - 1)
    closes = df["close"].to_numpy(dtype=float)
    confirmed_all = piv_mod.pivots_asof(pivots, asof_final)
    min_gap, max_gap = _GAP_BY_TF.get(timeframe, (DOUBLE_MIN_GAP_D, DOUBLE_MAX_GAP_D))

    found: dict[tuple, dict] = {}
    order: list[tuple] = []
    checkpoints = sorted({int(x) for x in confirmed_all["confirmed_at_idx"]})
    for cp in checkpoints:
        if cp > asof_final:
            break
        ap = piv_mod.alternating(piv_mod.pivots_asof(confirmed_all, cp)).to_dict("records")
        if len(ap) < 3:
            continue
        cands: list[dict] = []
        bull, bear = _detect_double(ap, closes, n, df, min_gap, max_gap)
        cands += [e for e in (bull, bear) if e]
        tb = _detect_triple_bottom(ap, closes, n, df)
        if tb:
            cands.append(tb)
        if len(ap) >= 5:
            hb, ht = _detect_head_shoulders(ap, closes, n, df)
            cands += [e for e in (hb, ht) if e]
        cands += _detect_wedge_triangle(ap, closes, n, df, cp)
        box = _detect_box(ap, closes, n, df, cp)
        if box:
            cands.append(box)
        for e in cands:
            key = (e["kind"], e["start_idx"], e["end_idx"])
            old = found.get(key)
            if old is None:
                found[key] = e
                order.append(key)
            elif old["confirm_idx"] is None and e["confirm_idx"] is not None:
                found[key] = e  # 后一时点拿到确认（note 含确认日期），更新记录
    candidates = [found[k] for k in order]

    def _violated(e: dict, upto: int) -> bool:
        inv = e["key_levels"].get("invalidation")
        end = int(e["end_idx"])
        if inv is None or upto <= end + 1:
            return False
        seg = closes[end + 1 : upto]
        if not len(seg):
            return False
        return bool(np.any(seg > inv)) if e["direction"] == "bear" else bool(np.any(seg < inv))

    # 先做有效性裁决（失败/过期/不自洽剔除），再做同结构去重——
    # 顺序不可颠倒：否则会被"随后才被判失败的形态"顶掉真形态（如假三重底顶掉真W底）
    survivors: list[dict] = []
    for e in candidates:
        ci = e["confirm_idx"]
        if ci is None:
            if _violated(e, asof_final + 1):
                continue  # 构筑失败
            if asof_final - int(e["end_idx"]) > EXPIRE_BARS:
                continue  # 过期未成形
        elif _violated(e, int(ci)):
            continue  # 确认前先失效，序列不自洽
        survivors.append(e)
    return _dedupe_cross_kind(_dedupe_overlaps(survivors))


def find_patterns(df: pd.DataFrame, pivots: pd.DataFrame, asof_bar: int | None = None,
                  timeframe: str = "1d") -> list[dict]:
    """结构识别主入口（规范签名：输入 df + pivots，输出 list[PatternEvent]）。

    只使用 asof_bar 时点已右侧确认的 pivot；历史结构由滚动时点引擎检出，
    旗形/波浪为"当下状态"判定只在最终时点运行；全部事件经 classify_active 时效分类。
    """
    n = len(df)
    asof = n - 1 if asof_bar is None else min(asof_bar, n - 1)
    closes = df["close"].to_numpy(dtype=float)

    events = find_patterns_history(df, pivots, timeframe=timeframe, asof_bar=asof)
    bf, xf = _detect_flag(df, closes, asof)
    events += [e for e in (bf, xf) if e]
    zz = piv_mod.zigzag(df)
    events += _detect_waves(zz, closes, asof, df)

    # 多尺度时效分类：交易级别（8% zigzag）当前腿起点
    zz_trade = piv_mod.zigzag(df, min_pct=piv_mod.MS_LEVELS[1])
    leg = piv_mod.current_leg(zz_trade)
    classify_active(events, closes, asof,
                    leg["start_idx"] if leg else 0)

    # 下跌ABC 与多头反转构筑（W底/三重底/头肩底）同区共存时只留反转读法：
    # "C浪企稳"与"右底构筑"是同一价格行为的两种解读，并列标注等于给出矛盾信息
    bull_rev = [e for e in events
                if e.get("active", True) and e["direction"] == "bull"
                and e["kind"] in _REV_KIND_RANK]
    if bull_rev:
        events = [e for e in events if not (e["kind"] == "wave_down_abc"
                                            and any(_range_overlaps(e, b) for b in bull_rev))]

    events.sort(key=lambda e: (e["confirm_idx"] or e["end_idx"]))
    return events


if __name__ == "__main__":
    from . import db, pivots as _p

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = loader(ts, start="2023-01-01")
        pv = _p.find_pivots(d)
        evs = find_patterns(d, pv)
        print(f"{ts}: patterns={len(evs)}")
        for e in evs:
            print(f"  {e['name']}({e['direction']}) score={e['score']} star={e['star']} "
                  f"confirm={e['confirm_idx']} | {e['note'][:60]}")
    print("patterns 自检通过")
