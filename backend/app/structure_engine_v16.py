"""RYAN 结构识别引擎 v16（大级别优先 · 严格几何 · 全因果）。

设计原则（取代 v12/v13/v14 三层补丁栈，唯一生产引擎）：

1. 大级别优先：背景级 zigzag（大波段）先识别，其覆盖区间内同方向的交易级
   结构一律抑制——2024/2026 这种大 M顶/W底 永远压过小区间噪声结构。
2. 严格几何：只识别四族反转结构（M顶/W底/头肩顶/头肩底）。
   双顶/双底 = zigzag 交替序列上【连续三点】H-L-H / L-H-L，两峰/谷间隔
   20-130 根（约 1-6 个月）；超过 130 根的隔季高点不命名双顶，直接跳过。
   头肩 = 连续五点，头显著高于双肩、双肩价差与时间对称均有硬约束。
3. 全因果防未来函数：只用右侧已确认 pivot（confirmed_at_idx <= 当前根）；
   颈线确认 = 连续 2 根收盘有效突破；未确认 = 构筑中（虚线、无星、分数减半）。
   任何截断 df[:k] 重跑，confirm_idx <= k 的结论必须逐位一致（CI 强制）。
4. 指标信号收敛：RSI6>90/<10 只标"衰竭/修复确认"，ADX>=25 且价格在 MA60
   同侧的强趋势行情一律过滤；MACD 只标与形态同源 pivot 的严重背离。
5. 斐波那契/谐波仅展示，零打分权重，不进入结构判断。

所有参数集中在文件头，调参只改这里。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import fibonacci_history, harmonics_history, indicators, pivots as piv_mod

ENGINE_VERSION = "structure_engine_v16.0"

# ---------------- 尺度 ----------------
BG_ZZ_LO, BG_ZZ_HI = 0.07, 0.14      # 背景级 zigzag = clip(ATR%*4.5)
TR_ZZ_LO, TR_ZZ_HI = 0.03, 0.06      # 交易级 zigzag = clip(ATR%*2.2)
BG_ZZ_MULT, TR_ZZ_MULT = 4.5, 2.2

# ---------------- 双顶/双底几何 ----------------
DUAL_TOL_IDX = 0.03        # 两峰/谷价差容忍（低波：指数等）
DUAL_TOL_VOL = 0.05        # 两峰/谷价差容忍（高波个股）
DUAL_GAP_MIN, DUAL_GAP_MAX = 20, 130   # 交易级：1-6 个月；>130 不命名
DUAL_GAP_BG = (60, 340)                # 背景级：跨季大双顶/底（如 2024 双底）
DUAL_DEPTH_PCT = 0.05      # 中间谷/峰深度（相对峰/谷价）
DUAL_DEPTH_ATR = 3.0       # 或 >= 3 倍 ATR
PRIOR_TREND_PCT = 0.08     # 前置趋势幅度
POS_WINDOW = 250           # 位置判定回看窗口
POS_TOP, POS_BOT = 0.60, 0.40   # 顶部反转须在高位区 / 底部反转须在低位区

# ---------------- 头肩几何 ----------------
HS_HEAD_MIN = 0.015        # 头超出较高肩的最小幅度
HS_SHOULDER_TOL = 0.04     # 双肩价差容忍
HS_DEPTH_PCT = 0.04        # 头到颈线最小距离
HS_DEPTH_ATR = 2.5
HS_TIME_SYM_LO, HS_TIME_SYM_HI = 0.4, 2.5   # 左右肩时间对称比
HS_GAP_MIN, HS_GAP_MAX = 40, 200            # 交易级：左肩到右肩总跨度
HS_GAP_BG = (80, 420)                       # 背景级：跨季大头肩

# ---------------- 颈线确认 ----------------
CONFIRM_CLOSES = 2         # 连续收盘根数
CONFIRM_BUF_PCT = 0.01     # 确认缓冲（相对颈线）
CONFIRM_BUF_ATR = 0.5
INVALID_BUF_ATR = 0.25     # 失效判定缓冲

# ---------------- 密度 ----------------
MAX_PATTERN_EVENTS = 4
MAX_INDICATOR_EVENTS = 5
MIN_REVERSAL_BARS = 75     # 兼容旧 CI 断言：反转结构全程最小根数
PATTERN_OVERLAP_LIMIT = 0.5

# ---------------- RSI 极值 ----------------
RSI_OB, RSI_OS = 90.0, 10.0
RSI_OB_EXIT, RSI_OS_EXIT = 85.0, 15.0
RSI_COOLDOWN = 60
TREND_ADX = 25.0           # ADX>=25 且价在 MA60 同侧 → 趋势行情，过滤超买超卖

# ---------------- MACD 背离 ----------------
DIV_GAP_MIN, DIV_GAP_MAX = 20, 180
DIV_PRICE_SWING = 0.03     # 两 pivot 价格波幅下限
DIV_DIF_REL = 0.15         # DIF 反向差相对下限
DIV_DIF_ABS_PCT = 0.001    # DIF 反向差绝对下限（占价格）
DIV_CONFIRM_BARS = 15
DIV_COOLDOWN = 80

_NAMES = {
    "double_top": "M顶", "double_bottom": "W底",
    "hs_top": "头肩顶", "hs_bottom": "头肩底",
}


# ================= 基础工具 =================

def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _atr(df: pd.DataFrame, idx: int) -> float:
    v = float(df["ATR14"].iloc[int(idx)])
    return v if math.isfinite(v) and v > 0 else float("nan")


def _median_atr_pct(df: pd.DataFrame) -> float:
    a = df["ATR14"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    r = np.where(c > 0, a / np.maximum(c, 1e-9), np.nan)
    r = r[np.isfinite(r)]
    return float(np.median(r)) if len(r) else 0.01


def _zz(df: pd.DataFrame, lo: float, hi: float, mult: float) -> pd.DataFrame:
    """指定级别的交替 zigzag，仅保留已右侧确认的 pivot。"""
    pct = float(np.clip(_median_atr_pct(df) * mult, lo, hi))
    z = piv_mod.zigzag(df, min_pct=pct)
    return piv_mod.pivots_asof(z, len(df) - 1).reset_index(drop=True)


def _position(df: pd.DataFrame, idx: int, price: float, direction: str) -> float:
    """价格在 trailing POS_WINDOW 根区间中的分位；bear 方向返回高位程度。"""
    start = max(0, int(idx) - POS_WINDOW - 1)
    hi = float(df["high"].iloc[start:int(idx) + 1].max())
    lo = float(df["low"].iloc[start:int(idx) + 1].min())
    if hi <= lo:
        return 0.5
    pct = (price - lo) / (hi - lo)
    return pct if direction == "bear" else 1.0 - pct


def _prior_trend(df: pd.DataFrame, idx: int, price: float, direction: str) -> float:
    """形态前的同向趋势幅度（双顶前须有明显升势，双底前须有明显跌势）。"""
    start = max(0, int(idx) - 180)
    if direction == "bear":
        ref = float(df["low"].iloc[start:int(idx) + 1].min())
        return (price - ref) / max(ref, 1e-9)
    ref = float(df["high"].iloc[start:int(idx) + 1].max())
    return (ref - price) / max(price, 1e-9)


def _confirm_break(df: pd.DataFrame, start: int, direction: str,
                   level_at, invalidation: float) -> int | None:
    """连续 CONFIRM_CLOSES 根收盘有效突破颈线 → 返回确认根；先破失效点 → None。

    level_at(idx) 返回该根的颈线值（头肩允许斜颈线）。全程只用 idx 及以前数据。
    """
    close = df["close"].to_numpy(dtype=float)
    outside = 0
    for idx in range(max(1, int(start)), len(df)):
        atr = _atr(df, idx)
        level = float(level_at(idx))
        buf = max(level * CONFIRM_BUF_PCT,
                  atr * CONFIRM_BUF_ATR if math.isfinite(atr) else 0.0)
        inv_buf = atr * INVALID_BUF_ATR if math.isfinite(atr) else invalidation * 0.004
        if direction == "bear":
            if close[idx] > invalidation + inv_buf:
                return None
            outside = outside + 1 if close[idx] < level else 0
            decisive = close[idx] <= level - buf
        else:
            if close[idx] < invalidation - inv_buf:
                return None
            outside = outside + 1 if close[idx] > level else 0
            decisive = close[idx] >= level + buf
        if outside >= CONFIRM_CLOSES and decisive:
            return idx
    return None


# ================= 结构识别 =================

def _mk_event(df, kind, direction, scale, pts, neckline, target, invalidation,
              confirm_idx, status, note) -> dict:
    """统一构造结构事件；trace = pivot 折线 + 颈线（虚线）。"""
    names = _NAMES
    start_idx = int(pts[0]["idx"])
    end_idx = int(pts[-1]["idx"])
    trace = [{
        "points": [{"t": _date(df, int(p["idx"])), "p": round(float(p["price"]), 4)}
                   for p in pts],
        "style": "solid",
    }]
    if neckline is not None and _finite(neckline[1]):
        (t1, p1), (t2, p2) = neckline
        trace.append({
            "points": [{"t": _date(df, t1), "p": round(float(p1), 4)},
                       {"t": _date(df, t2), "p": round(float(p2), 4)}],
            "style": "dashed",
        })
    confirmed = status == "confirmed"
    score = 80 if scale == "background" else 68
    if confirmed:
        score += 8
    else:
        score = int(score * 0.5)
    return {
        "kind": kind, "name": names[kind], "direction": direction, "scale": scale,
        "start_idx": start_idx, "end_idx": end_idx,
        "confirm_idx": int(confirm_idx) if confirm_idx is not None else None,
        "status": status, "key_levels": {
            "neckline": round(float(neckline[0][1]), 4) if neckline else None,
            "measure_target": round(float(target), 4) if _finite(target) else None,
            "invalidation": round(float(invalidation), 4),
        },
        "score": score, "star": confirmed, "note": note, "trace": trace,
        "active": True, "causal": True,
    }


def _dual(df: pd.DataFrame, zz: pd.DataFrame, direction: str, scale: str) -> list[dict]:
    """M顶/W底：zigzag 连续三点 H-L-H / L-H-L（严格相邻，杜绝跨多谷乱配）。"""
    kind_extreme = "H" if direction == "bear" else "L"
    kind_middle = "L" if direction == "bear" else "H"
    names = {"bear": "M顶", "bull": "W底"}
    tol = DUAL_TOL_VOL if _median_atr_pct(df) > 0.02 else DUAL_TOL_IDX
    gap_lo, gap_hi = DUAL_GAP_BG if scale == "background" else (DUAL_GAP_MIN, DUAL_GAP_MAX)
    events: list[dict] = []
    pts = zz.to_dict("records")
    for i in range(len(pts) - 2):
        a, m, b = pts[i], pts[i + 1], pts[i + 2]
        if not (a["kind"] == kind_extreme and m["kind"] == kind_middle
                and b["kind"] == kind_extreme):
            continue
        i1, im, i2 = int(a["idx"]), int(m["idx"]), int(b["idx"])
        gap = i2 - i1
        if not gap_lo <= gap <= gap_hi:
            continue  # 交易级隔季高点不命名双顶/双底（用户规则：只叫箱体压力/lower high）
        p1, pm, p2 = float(a["price"]), float(m["price"]), float(b["price"])
        mean_p = (p1 + p2) / 2.0
        if mean_p <= 0 or abs(p2 - p1) / mean_p > tol:
            continue
        depth = (min(p1, p2) - pm) if direction == "bear" else (pm - max(p1, p2))
        if depth <= 0:
            continue
        atr = _atr(df, i1)
        if depth / mean_p < DUAL_DEPTH_PCT and (
                not math.isfinite(atr) or depth / atr < DUAL_DEPTH_ATR):
            continue
        if _prior_trend(df, i1, p1, direction) < PRIOR_TREND_PCT:
            continue
        pos = _position(df, i1, p1, direction)
        if pos < (POS_TOP if direction == "bear" else POS_BOT):
            continue

        level = pm
        invalidation = max(p1, p2) if direction == "bear" else min(p1, p2)
        target = (level - depth) if direction == "bear" else (level + depth)
        start_confirm = max(i2 + 1, int(b["confirmed_at_idx"]))
        confirm = _confirm_break(df, start_confirm, direction,
                                 lambda _i, lv=level: lv, invalidation)
        status = "confirmed" if confirm is not None else "forming"
        if status == "forming":
            # 构筑中但价格已反向收复失效点 → 形态流产，不标注
            last_close = float(df["close"].iloc[-1])
            if direction == "bear" and last_close > invalidation:
                continue
            if direction == "bull" and last_close < invalidation:
                continue
        kind = "double_top" if direction == "bear" else "double_bottom"
        note = (
            f"{names[direction]}（{scale_cn(scale)}）：{_date(df, i1)} 与 {_date(df, i2)} "
            f"两{'峰' if direction == 'bear' else '谷'}价差 {abs(p2-p1)/mean_p*100:.1f}%，"
            f"间隔 {gap} 根；颈线 {level:.2f}。"
            + (f"{_date(df, confirm)} 连续{CONFIRM_CLOSES}根收盘确认"
               f"{'跌破' if direction == 'bear' else '突破'}，量度目标 {target:.2f}，"
               f"收复 {invalidation:.2f} 失效。" if confirm is not None else
               f"构筑中：{'跌破' if direction == 'bear' else '突破'} {level:.2f} 确认，"
               f"确认后量度目标 {target:.2f}；收复 {invalidation:.2f} 形态失效。")
        )
        events.append(_mk_event(
            df, kind, direction, scale, [a, m, b],
            ((im, level), (confirm if confirm is not None else len(df) - 1, level)),
            target, invalidation, confirm, status, note))
    return events


def scale_cn(scale: str) -> str:
    return "大级别" if scale == "background" else "交易级"


def _hs(df: pd.DataFrame, zz: pd.DataFrame, direction: str, scale: str) -> list[dict]:
    """头肩顶/底：zigzag 连续五点 H-L-H-L-H / L-H-L-H-L。"""
    kx = "H" if direction == "bear" else "L"   # 肩/头的 kind
    km = "L" if direction == "bear" else "H"   # 两谷/峰的 kind
    kind = "hs_top" if direction == "bear" else "hs_bottom"
    events: list[dict] = []
    pts = zz.to_dict("records")
    for i in range(len(pts) - 4):
        ls, t1, hd, t2, rs = pts[i:i + 5]
        kinds = [p["kind"] for p in (ls, t1, hd, t2, rs)]
        if kinds != [kx, km, kx, km, kx]:
            continue
        i_ls, i_t1, i_hd, i_t2, i_rs = (int(p["idx"]) for p in (ls, t1, hd, t2, rs))
        hs_lo, hs_hi = HS_GAP_BG if scale == "background" else (HS_GAP_MIN, HS_GAP_MAX)
        if not hs_lo <= i_rs - i_ls <= hs_hi:
            continue
        p_ls, p_t1, p_hd, p_t2, p_rs = (float(p["price"]) for p in (ls, t1, hd, t2, rs))
        # 头必须显著超出双肩
        if direction == "bear":
            if p_hd < max(p_ls, p_rs) * (1 + HS_HEAD_MIN):
                continue
            shoulder_mean = (p_ls + p_rs) / 2.0
            if abs(p_rs - p_ls) / shoulder_mean > HS_SHOULDER_TOL:
                continue
            neck1, neck2 = p_t1, p_t2
            depth = p_hd - max(neck1, neck2)
        else:
            if p_hd > min(p_ls, p_rs) * (1 - HS_HEAD_MIN):
                continue
            shoulder_mean = (p_ls + p_rs) / 2.0
            if abs(p_rs - p_ls) / shoulder_mean > HS_SHOULDER_TOL:
                continue
            neck1, neck2 = p_t1, p_t2
            depth = min(neck1, neck2) - p_hd
        if depth <= 0:
            continue
        atr = _atr(df, i_hd)
        if depth / p_hd < HS_DEPTH_PCT and (
                not math.isfinite(atr) or depth / atr < HS_DEPTH_ATR):
            continue
        # 时间对称
        left_span, right_span = i_hd - i_ls, i_rs - i_hd
        ratio = left_span / max(right_span, 1)
        if not HS_TIME_SYM_LO <= ratio <= HS_TIME_SYM_HI:
            continue
        if _prior_trend(df, i_ls, p_ls, direction) < PRIOR_TREND_PCT:
            continue
        if _position(df, i_hd, p_hd, direction) < (POS_TOP if direction == "bear" else POS_BOT):
            continue

        # 颈线 = 两谷/峰连线（允许斜率），level_at 线性外推
        slope = (neck2 - neck1) / max(i_t2 - i_t1, 1)

        def level_at(idx, _b=neck1, _s=slope, _i=i_t1):
            return _b + _s * (int(idx) - _i)

        invalidation = p_hd  # 头肩失效点 = 头
        target = (level_at(i_rs) - depth) if direction == "bear" else (level_at(i_rs) + depth)
        start_confirm = max(i_rs + 1, int(rs["confirmed_at_idx"]))
        confirm = _confirm_break(df, start_confirm, direction, level_at, invalidation)
        status = "confirmed" if confirm is not None else "forming"
        if status == "forming":
            last_close = float(df["close"].iloc[-1])
            if direction == "bear" and last_close > invalidation:
                continue
            if direction == "bull" and last_close < invalidation:
                continue
        name = _NAMES[kind]
        neck_now = level_at(confirm if confirm is not None else len(df) - 1)
        note = (
            f"{name}（{scale_cn(scale)}）：左肩 {_date(df, i_ls)}、头 {_date(df, i_hd)}、"
            f"右肩 {_date(df, i_rs)}，双肩价差 {abs(p_rs-p_ls)/shoulder_mean*100:.1f}%；"
            f"颈线 {neck_now:.2f}（斜率 {slope:+.3f}/根）。"
            + (f"{_date(df, confirm)} 确认{'跌破' if direction == 'bear' else '突破'}，"
               f"量度目标 {target:.2f}，收复头部 {invalidation:.2f} 失效。"
               if confirm is not None else
               f"构筑中：{'跌破' if direction == 'bear' else '突破'}颈线确认，"
               f"确认后量度目标约 {target:.2f}；收复头部 {invalidation:.2f} 形态失效。")
        )
        events.append(_mk_event(
            df, kind, direction, scale, [ls, t1, hd, t2, rs],
            ((i_t1, neck1), (confirm if confirm is not None else len(df) - 1, neck_now)),
            target, invalidation, confirm, status, note))
    return events


def _lifecycle(df: pd.DataFrame, e: dict) -> dict | None:
    """结构生命周期：构筑中过期流产（返回 None）；已确认达标/失效 → 历史(active=False)。"""
    last = len(df) - 1
    span = int(e["end_idx"]) - int(e["start_idx"])
    if e["status"] == "forming":
        if last - int(e["end_idx"]) > max(2 * span, 120):
            return None  # 右峰/右肩后迟迟不确认 → 流产，不再占用图面
        return e
    closes = df["close"].to_numpy(dtype=float)[int(e["confirm_idx"]):]
    tgt = e["key_levels"]["measure_target"]
    inv = e["key_levels"]["invalidation"]
    if e["direction"] == "bear":
        if (_finite(tgt) and (closes <= float(tgt)).any()) or (closes > float(inv)).any():
            e["active"] = False
    else:
        if (_finite(tgt) and (closes >= float(tgt)).any()) or (closes < float(inv)).any():
            e["active"] = False
    return e


def find_structures(df: pd.DataFrame) -> list[dict]:
    """两级识别 + 大级别优先合并。返回按 confirm/status 排序的最终结构。"""
    bg, tr = _zz(df, BG_ZZ_LO, BG_ZZ_HI, BG_ZZ_MULT), _zz(df, TR_ZZ_LO, TR_ZZ_HI, TR_ZZ_MULT)
    bg_events, tr_events = [], []
    for direction in ("bear", "bull"):
        bg_events += _dual(df, bg, direction, "background")
        bg_events += _hs(df, bg, direction, "background")
        tr_events += _dual(df, tr, direction, "trade")
        tr_events += _hs(df, tr, direction, "trade")

    def overlap(a: dict, b: dict) -> float:
        a0, a1 = a["start_idx"], (a["confirm_idx"] or len(df) - 1)
        b0, b1 = b["start_idx"], (b["confirm_idx"] or len(df) - 1)
        inter = max(0, min(a1, b1) - max(a0, b0))
        return inter / max(min(a1 - a0, b1 - b0), 1)

    # 大级别优先：与背景级结构区间重叠且同方向的交易级结构全部抑制
    kept_tr = [
        e for e in tr_events
        if not any(e["direction"] == g["direction"]
                   and overlap(e, g) >= PATTERN_OVERLAP_LIMIT for g in bg_events)
    ]
    # 生命周期：流产构筑剔除；活跃结构（构筑中+未达标已确认）优先于历史结构
    pool = [x for x in (_lifecycle(df, e) for e in bg_events + kept_tr) if x is not None]
    # 展示排序：活跃优先，其次按确认/结束时间新近度，大级别与分数作 tie-break。
    # 大级别优先体现在识别与抑制（上文 kept_tr），展示层则让 2026 年的近期结构
    # 优先于 2021/2022 年的远古历史结构——近期结构对前瞻与复盘都更有价值。
    pool.sort(key=lambda e: (e["active"], e["confirm_idx"] or e["end_idx"],
                             e["scale"] == "background", e["score"]), reverse=True)
    # 同方向同区域去重：一个区域只讲一个故事，保留更优者（活跃/近期/大级别优先）
    selected: list[dict] = []
    for e in pool:
        if any(e["direction"] == s["direction"] and overlap(e, s) >= PATTERN_OVERLAP_LIMIT
               for s in selected):
            continue
        selected.append(e)
        if len(selected) >= MAX_PATTERN_EVENTS:
            break
    return sorted(selected, key=lambda e: (e["confirm_idx"] or 10 ** 9))


# ================= 指标信号 =================

def _event(df, idx, label, direction, detail, score, grp) -> dict:
    return {
        "bar_idx": int(idx), "price": round(float(df["close"].iloc[int(idx)]), 4),
        "kind": "indicator", "label": label, "direction": direction, "star": False,
        "detail": detail, "lines": [], "zones": [], "polylines": [],
        "active": int(idx) >= len(df) - 180, "_score": int(score), "_grp": grp,
    }


def _trend_regime(df: pd.DataFrame, idx: int, direction: str) -> bool:
    """强趋势行情判定：ADX>=TREND_ADX 且价格位于 MA60 同侧。

    超买过滤升势（close>MA60），超卖过滤跌势（close<MA60）——此时极端 RSI
    是趋势强度而非反转信号。
    """
    adx = float(df["ADX"].iloc[int(idx)])
    ma60 = float(df["MA60"].iloc[int(idx)])
    close = float(df["close"].iloc[int(idx)])
    if not (_finite(adx) and _finite(ma60)) or adx < TREND_ADX:
        return False
    return close > ma60 if direction == "bear" else close < ma60


def rsi_extreme_signals(df: pd.DataFrame) -> list[dict]:
    """RSI6>90/<10 的衰竭/修复确认；强趋势行情一律过滤。"""
    rsi = df["RSI6"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ma10 = df["MA10"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    out: list[dict] = []
    state = None
    last = {"bear": -10_000, "bull": -10_000}
    for idx in range(1, len(df)):
        if not np.isfinite(rsi[idx]):
            continue
        if state is None:
            if rsi[idx] > RSI_OB and not _trend_regime(df, idx, "bear"):
                state = {"direction": "bear", "entry": idx,
                         "extreme": float(rsi[idx]), "price": float(high[idx])}
            elif rsi[idx] < RSI_OS and not _trend_regime(df, idx, "bull"):
                state = {"direction": "bull", "entry": idx,
                         "extreme": float(rsi[idx]), "price": float(low[idx])}
            continue
        d = str(state["direction"])
        # 进入状态后若演变为强趋势行情，放弃本次跟踪（趋势吃掉极值）
        if _trend_regime(df, idx, d):
            state = None
            continue
        if d == "bear":
            state["extreme"] = max(state["extreme"], float(rsi[idx]))
            state["price"] = max(state["price"], float(high[idx]))
            confirmed = rsi[idx] < RSI_OB_EXIT and close[idx] < ma10[idx] and close[idx] < close[idx - 1]
            expired = idx - state["entry"] > 24 or rsi[idx] < 60
            if confirmed and idx - last[d] >= RSI_COOLDOWN:
                out.append(_event(
                    df, idx, "RSI超买", "bear",
                    f"RSI6最高{state['extreme']:.1f}（>90）后跌回85下方并失守MA10；"
                    f"前高{state['price']:.2f}收复则风险信号失效。", 62, f"rsi90:{idx}"))
                last[d] = idx
                state = None
            elif expired:
                state = None
        else:
            state["extreme"] = min(state["extreme"], float(rsi[idx]))
            state["price"] = min(state["price"], float(low[idx]))
            confirmed = rsi[idx] > RSI_OS_EXIT and close[idx] > ma10[idx] and close[idx] > close[idx - 1]
            expired = idx - state["entry"] > 24 or rsi[idx] > 40
            if confirmed and idx - last[d] >= RSI_COOLDOWN:
                out.append(_event(
                    df, idx, "RSI超卖", "bull",
                    f"RSI6最低{state['extreme']:.1f}（<10）后升回15上方并站回MA10；"
                    f"前低{state['price']:.2f}失守则修复信号失效。", 62, f"rsi10:{idx}"))
                last[d] = idx
                state = None
            elif expired:
                state = None
    return out


def macd_divergence_signals(df: pd.DataFrame) -> list[dict]:
    """严重 MACD 顶/底背离：与形态同源的交易级 zigzag pivot + MA20/柱线确认。"""
    zz = _zz(df, TR_ZZ_LO, TR_ZZ_HI, TR_ZZ_MULT).to_dict("records")
    dif = df["DIF"].to_numpy(dtype=float)
    hist = df["MACD_HIST"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ma20 = df["MA20"].to_numpy(dtype=float)
    out: list[dict] = []
    last = {"bear": -10_000, "bull": -10_000}
    for kind, direction in (("H", "bear"), ("L", "bull")):
        pts = [p for p in zz if p["kind"] == kind]
        for pos in range(1, len(pts)):
            first, second = pts[pos - 1], pts[pos]
            gap = int(second["idx"]) - int(first["idx"])
            if not DIV_GAP_MIN <= gap <= DIV_GAP_MAX:
                continue
            i1, i2 = int(first["idx"]), int(second["idx"])
            p1, p2 = float(first["price"]), float(second["price"])
            if not all(np.isfinite(x) for x in (dif[i1], dif[i2])) or p1 <= 0:
                continue
            swing = abs(p2 - p1) / p1
            if direction == "bear":
                price_ok = p2 >= p1 * 0.985  # 允许持平/微新高
                dif_ok = dif[i2] <= dif[i1] - max(abs(dif[i1]) * DIV_DIF_REL,
                                                  close[i2] * DIV_DIF_ABS_PCT)
            else:
                price_ok = p2 <= p1 * 1.015
                dif_ok = dif[i2] >= dif[i1] + max(abs(dif[i1]) * DIV_DIF_REL,
                                                  close[i2] * DIV_DIF_ABS_PCT)
            if not (price_ok and dif_ok and swing >= DIV_PRICE_SWING):
                continue
            start = max(i2 + 1, int(second["confirmed_at_idx"]))
            for idx in range(start, min(len(df), start + DIV_CONFIRM_BARS)):
                hit = (close[idx] < ma20[idx] and hist[idx] < 0) if direction == "bear" \
                    else (close[idx] > ma20[idx] and hist[idx] > 0)
                if hit and idx - last[direction] >= DIV_COOLDOWN:
                    label = "MACD顶背离" if direction == "bear" else "MACD底背离"
                    out.append(_event(
                        df, idx, label, direction,
                        f"{_date(df, i1)}与{_date(df, i2)}价格{'新高区' if direction == 'bear' else '新低区'}"
                        f"而DIF明显走弱（{dif[i1]:.3f}→{dif[i2]:.3f}），"
                        f"价格已{'跌破' if direction == 'bear' else '站回'}MA20确认。",
                        72, f"macd_div:{direction}:{idx}"))
                    last[direction] = idx
                    break
    return out


def _select_indicator_events(events: list[dict]) -> list[dict]:
    """密度控制：同方向 15 根内留优先者，相邻 <35 根不连发，总量封顶。"""
    priority = {"MACD顶背离": 3, "MACD底背离": 3, "RSI超买": 2, "RSI超卖": 2}
    kept: list[dict] = []
    for e in sorted(events, key=lambda x: int(x.get("bar_idx", 0))):
        conflicts = [o for o in kept if o.get("direction") == e.get("direction")
                     and abs(int(o.get("bar_idx", 0)) - int(e.get("bar_idx", 0))) <= 15]
        if conflicts:
            best = max(conflicts + [e],
                       key=lambda x: (priority.get(str(x.get("label")), 1),
                                      int(x.get("_score", 0))))
            if best is e:
                for o in conflicts:
                    kept.remove(o)
                kept.append(e)
            continue
        if kept and int(e.get("bar_idx", 0)) - int(kept[-1].get("bar_idx", 0)) < 35:
            continue
        kept.append(e)
    return kept[-MAX_INDICATOR_EVENTS:]


def _filter_fibonacci(events: list[dict]) -> list[dict]:
    """斐波那契只保留 0.5 / 0.618 两个最关键位（展示用，零权重）。"""
    out: list[dict] = []
    for raw in events:
        e = dict(raw)
        label = str(e.get("label") or "").replace("Fib", "").strip()
        if label not in {"0.5", "0.500", "0.618"}:
            continue
        e["label"] = "0.5" if label in {"0.5", "0.500"} else "0.618"
        e["lines"], e["zones"], e["polylines"] = [], [], []
        e["_score"], e["_grp"] = 55, f"fib:{e['label']}:{e.get('bar_idx')}"
        out.append(e)
    return out


# ================= 标注与摘要 =================

def pattern_annotations(df: pd.DataFrame, structures: list[dict]) -> list[dict]:
    """结构 → 前端标注：描摹（trace_only，虚线=构筑中）+ 确认星标。"""
    out: list[dict] = []
    for e in structures:
        direction = str(e.get("direction") or "range")
        end_idx = int(e.get("end_idx", 0))
        name = str(e.get("name") or "大结构")
        forming = e.get("status") != "confirmed"
        label = (name + "·构筑")[:8] if forming else name[:8]
        sid = f"{e.get('kind')}:{e.get('start_idx')}:{e.get('confirm_idx')}"
        out.append({
            "bar_idx": end_idx,
            "price": float(df["low" if direction == "bull" else "high"].iloc[end_idx]),
            "kind": "pattern", "label": label, "direction": direction,
            "star": False, "detail": str(e.get("note") or ""),
            "lines": [], "zones": [], "polylines": e.get("trace") or [],
            "trace_only": True, "history_label": not forming,
            "active": bool(e.get("active", True)),
            "_score": int(e.get("score", 70)), "_grp": f"structure:{sid}",
            "structure_id": sid,
        })
        if not forming:
            c = int(e["confirm_idx"])
            out.append({
                "bar_idx": c,
                "price": float(df["low" if direction == "bull" else "high"].iloc[c]),
                "kind": "pattern",
                "label": ("突破颈线" if direction == "bull" else "跌破颈线"),
                "direction": direction, "star": True,
                "detail": str(e.get("note") or ""),
                "lines": [], "zones": [], "polylines": [],
                "active": bool(e.get("active", True)),
                "_score": int(e.get("score", 70)) + 5,
                "_grp": f"structure_confirm:{sid}", "structure_id": sid,
            })
    return out


def _clean(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for raw in sorted(events, key=lambda e: int(e.get("bar_idx", 0))):
        e = dict(raw)
        e["label"] = str(e.get("label") or "")[:8]
        e.pop("_score", None)
        e.pop("_grp", None)
        out.append(e)
    return out


def _summary(df: pd.DataFrame, structures: list[dict]) -> dict:
    close = float(df["close"].iloc[-1])
    e20, e60 = float(df["EMA20"].iloc[-1]), float(df["EMA60"].iloc[-1])
    adx = float(df["ADX"].iloc[-1]) if _finite(df["ADX"].iloc[-1]) else 0.0
    rsi = float(df["RSI6"].iloc[-1])
    dif, dea = float(df["DIF"].iloc[-1]), float(df["DEA"].iloc[-1])
    ma60 = float(df["MA60"].iloc[-1])

    if close > e20 > e60 and e20 > float(df["EMA20"].iloc[-9]):
        trend = f"中期多头，ADX={adx:.0f}"
    elif close < e20 < e60 and e20 < float(df["EMA20"].iloc[-9]):
        trend = f"中期空头，ADX={adx:.0f}"
    else:
        trend = f"趋势未确认，ADX={adx:.0f}"
    regime = "（强趋势，极端RSI视为趋势强度）" if adx >= TREND_ADX else ""

    active = [e for e in structures if e.get("active")]
    latest = max(active, key=lambda e: (e["confirm_idx"] or e["end_idx"])) if active else None
    if latest:
        status_cn = "已确认" if latest["status"] == "confirmed" else "构筑中"
        structure = (f"{latest['name']}（{scale_cn(latest['scale'])}，{status_cn}）"
                     f"颈线 {latest['key_levels']['neckline']:.2f}")
    else:
        structure = "当前无达到投资级门槛的大结构"

    rsi_state = "极端超买" if rsi > RSI_OB else "极端超卖" if rsi < RSI_OS else "非极端"
    momentum = f"RSI6={rsi:.1f}（{rsi_state}）；MACD动能{'偏多' if dif > dea else '偏空'}{regime}"

    v = df["vol"].to_numpy(dtype=float)
    v5, v20 = float(np.nanmean(v[-5:])), float(np.nanmean(v[-20:]))
    vr = v5 / v20 if v20 > 0 else 1.0
    volume = f"5日/20日均量={vr:.2f}（{'放量' if vr > 1.2 else '缩量' if vr < 0.8 else '平稳'}）"

    supports, resistances = [], []
    if latest:
        neck = latest["key_levels"]["neckline"]
        if latest["direction"] == "bull":
            supports.append(neck)
        else:
            resistances.append(neck)
        inv = latest["key_levels"]["invalidation"]
        (resistances if latest["direction"] == "bear" else supports).append(inv)
    zz = _zz(df, TR_ZZ_LO, TR_ZZ_HI, TR_ZZ_MULT)
    lows = zz[zz["kind"] == "L"]["price"].astype(float).tail(3).tolist()
    highs = zz[zz["kind"] == "H"]["price"].astype(float).tail(3).tolist()
    supports += [round(x, 2) for x in lows if x < close][:2]
    resistances += [round(x, 2) for x in highs if x > close][:2]
    supports, resistances = supports[:3], resistances[:3]

    target = stop = None
    rr = None
    if latest and latest["status"] == "confirmed":
        target = latest["key_levels"]["measure_target"]
        stop = latest["key_levels"]["invalidation"]
        if _finite(target) and _finite(stop) and abs(close - stop) > 1e-9:
            rr = round(abs(target - close) / abs(close - stop), 2)

    if latest:
        neck = latest["key_levels"]["neckline"]
        tgt = latest["key_levels"]["measure_target"]
        inv = latest["key_levels"]["invalidation"]
        if latest["status"] == "confirmed":
            outlook = (
                f"截至{_date(df, len(df)-1)}，{trend}。{structure}。"
                f"动量：{momentum}。"
                f"前瞻：{latest['name']}已确认，量度目标 {tgt:.2f}；"
                f"{'收复' if latest['direction'] == 'bear' else '失守'} {inv:.2f} 则判断失效，"
                "在此之前按结构方向看待回调/反弹。"
            )
        else:
            outlook = (
                f"截至{_date(df, len(df)-1)}，{trend}。{structure}。"
                f"动量：{momentum}。"
                f"前瞻：{'跌破' if latest['direction'] == 'bear' else '突破'}颈线 {neck:.2f} "
                f"则{latest['name']}确认，量度目标约 {tgt:.2f}；"
                f"若先{'收复' if latest['direction'] == 'bear' else '失守'} {inv:.2f} 则形态流产，"
                "放弃该结构判断。"
            )
    else:
        outlook = (
            f"截至{_date(df, len(df)-1)}，{trend}。{structure}。动量：{momentum}。"
            "无活跃大结构时以关键支撑/阻力位观察为主，等待结构成型。"
        )

    return {
        "trend": trend, "structure": structure, "momentum": momentum, "volume": volume,
        "key_supports": supports, "key_resistances": resistances,
        "target_price": target, "stop_loss": stop, "risk_reward": rr,
        "outlook_text": outlook,
    }


# ================= 主入口 =================

def analyze(df: pd.DataFrame, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 120:
        return {"annotations": [], "summary": {},
                "diagnostics": {"analysis_version": ENGINE_VERSION}}

    structures = find_structures(d)
    pattern_events = pattern_annotations(d, structures)
    indicator_events = _select_indicator_events(
        rsi_extreme_signals(d) + macd_divergence_signals(d))
    pivots = piv_mod.find_pivots(d)
    fib_events = _filter_fibonacci(fibonacci_history.find_fibonacci_touches(d, pivots))
    harmonic_events = harmonics_history.find_harmonic_annotations(d, pivots)[-2:]
    annotations = _clean(pattern_events + indicator_events + fib_events + harmonic_events)

    return {
        "annotations": annotations,
        "summary": _summary(d, structures),
        "diagnostics": {
            "analysis_version": ENGINE_VERSION,
            "asset_kind": "index" if str(asset_kind).lower() == "index" else "equity",
            "bars_scanned": len(d),
            "structures_displayed": len(structures),
            "structure_families": sorted({e["kind"] for e in structures}),
            "structure_scales": sorted({e["scale"] for e in structures}),
            "indicator_events": len(indicator_events),
            "fibonacci_events": len(fib_events),
            "harmonic_events": len(harmonic_events),
            "causal": True,
            "selection": "background_first_strict_geometry_right_confirmed",
        },
    }


if __name__ == "__main__":
    # 合成教科书 M顶 冒烟：两个等高峰 + 中间谷 + 颈线破位
    n = 300
    t = np.arange(n, dtype=float)
    close = np.full(n, 100.0)
    close[:80] = np.linspace(90, 118, 80)            # 前置升势
    close[80:110] = np.linspace(118, 104, 30)        # 左峰→谷
    close[110:140] = np.linspace(104, 117.5, 30)     # 谷→右峰
    close[140:170] = np.linspace(117.5, 96, 30)      # 右峰→破颈线
    close[170:] = np.linspace(96, 88, 130)
    rng = np.random.default_rng(7)
    demo = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close + rng.normal(0, 0.2, n),
        "high": close + np.abs(rng.normal(0, 0.5, n)) + 0.3,
        "low": close - np.abs(rng.normal(0, 0.5, n)) - 0.3,
        "close": close, "vol": np.full(n, 1e6), "amount": np.full(n, 1e7),
    })
    demo = indicators.compute_all(demo)
    res = analyze(demo)
    kinds = [a["label"] for a in res["annotations"] if a["kind"] == "pattern"]
    assert any("M顶" in k for k in kinds), kinds
    assert any(a.get("star") for a in res["annotations"]), "颈线破位应有确认星标"
    print("structure_engine_v16 自检通过：合成M顶 识别+确认 OK")
    print("summary structure:", res["summary"]["structure"])
