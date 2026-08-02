"""推背图聚合（ARCHITECTURE.md 第3节 analysis.py 规范）。

analyze(df, timeframe) -> AnalysisResult：
- annotations：图上标注（pattern/divergence/fib/harmonic/indicator），含 lines/zones。
  结构信号为主体：形态构筑里程碑（右底/右肩确认）、颈线/边界突破、回踩确认；
  指标信号收敛为「特别明显的 RSI6 超买超卖」，背离单独成类。
- summary：趋势/结构/动量/量能/支撑阻力/目标位/止损/盈亏比/分段中文客观展望。

口径与纪律：
- 全部信号只依赖当前及历史行；结构/背离/谐波只消费右侧已确认事件
  （各子模块内部已强制，本层不再引入任何未来数据）。
- star=True 仅用于：已确认的主要结构突破、回踩颈线确认、已确认背离、
  进入谐波 PRZ、关键斐波那契位（golden pocket）企稳。
- 密度控制：同一 10 根K线窗口内同类（kind）信号只保留最重要的一个
  （star 优先，其次 score，再次更近者）。
- fibonacci / harmonics 仅展示（零打分权重，见 MODEL_DESIGN.md），
  本模块输出不得被 scoring.py 引用。
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

from . import divergence as div_mod
from . import fibonacci as fib_mod
from . import harmonics as har_mod
from . import indicators
from . import patterns as pat_mod
from . import pivots as piv_mod

ANALYSIS_VERSION = "analysis_v4.3"   # 分析算法版本：改动识别/标注/结论逻辑时递增，缓存键引用

DIV_STAR_AGE = 40         # 背离确认后多少根内仍给 star（已废弃，保留兼容）
DENSITY_WINDOW = 10       # 密度控制窗口（根）
ATR_STOP_MULT = 2.0       # ATR 止损倍数
RETEST_TOL = 0.01         # 回踩颈线容差（最低价触及颈线的相对偏差）
FIB_NEAR_TOL = 0.015      # 现价贴近斐波那契重要位的相对偏差上限
FIB_KEY_RATIOS = (0.382, 0.5, 0.618, 0.786)   # 值得上图的斐波那契重要位
DIV_MIN_PRICE_SWING = 0.03    # 明显背离：两 pivot 最小价格波幅
DIV_MIN_IND_REL = 0.15        # 明显背离：指标反差（相对较大值）
DIV_MIN_IND_ABS_PCT = 0.002   # 明显背离：DIF/DEA 反差绝对下限（占价格比例）
_MAJOR_PAT_KINDS = {"double_bottom", "double_top", "triple_bottom",
                    "head_shoulders_bottom", "head_shoulders_top", "arc_bottom"}  # 主要反转结构
STAR_RECENT_BARS = 250    # 历史结构星标保鲜期（根）：过期的已确认结构降为小号标记，避免满屏金星


# ---------------- 指标/趋势信号 ----------------

def _indicator_signals(d: pd.DataFrame) -> list[dict]:
    """RSI6 超买/超卖（全历史，严格因果、无未来函数）：
    - 进入信号：RSI6 当根上穿 90 = 超买 / 下穿 10 = 超卖（当根收盘即知，绝不后置到转折点）；
    - 衰竭/修复确认：RSI6 跌回 88 下方（超买衰竭）/ 升回 12 上方（超卖修复），
      仅当本轮区间 RSI 极值足够极端（≥93 / ≤7）才标注；88/12 回差防阈值抖动；
    - 星标：进入信号当根 RSI ≥95 / ≤5；衰竭/修复信号区间极值 ≥96 / ≤4。
    每个超买/超卖波段只标一进一出；不使用 zigzag 转折点定位（那是未来函数）。
    MACD/KDJ 交叉等高频弱信号不上图；背离由 divergence.py 单独输出。"""
    n = len(d)
    rsi6 = d["RSI6"].to_numpy(dtype=float)
    high = d["high"].to_numpy(dtype=float)
    low = d["low"].to_numpy(dtype=float)
    ma20 = d["MA20"].to_numpy(dtype=float)
    ev: list[dict] = []
    ob_peak = ob_hi = None        # 当前超买波段：RSI 峰值 / 期间最高价
    os_trough = os_lo = None      # 当前超卖波段：RSI 谷值 / 期间最低价
    for i in range(1, n):
        r0, r1 = rsi6[i - 1], rsi6[i]
        if np.isnan(r0) or np.isnan(r1):
            continue
        m = ma20[i]
        m_txt = "" if np.isnan(m) else f"（{m:.2f}）"
        # ---- 超买波段 ----
        if ob_peak is None and r0 < 90 <= r1:            # 新进超买区
            ob_peak, ob_hi = r1, float(high[i])
            ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(high[i]),
                       "kind": "indicator", "label": "RSI6超买",
                       "direction": "bear", "star": bool(r1 >= 95),
                       "detail": (f"RSI6={r1:.0f} 上穿 90 进入超买区，短线过热、追高需谨慎；"
                                  f"RSI 跌回 90 下方为衰竭确认，回踩 MA20{m_txt} 不破则趋势未坏"),
                       "_score": 58, "_grp": "rsi_ob_in"})
        elif ob_peak is not None and r1 >= 90:           # 波段延续
            ob_peak, ob_hi = max(ob_peak, r1), max(ob_hi, float(high[i]))
        elif ob_peak is not None and r1 < 88:            # 衰竭确认（跌出超买区）
            if ob_peak >= 93:
                ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(high[i]),
                           "kind": "indicator", "label": "RSI6超买衰竭",
                           "direction": "bear", "star": bool(ob_peak >= 96),
                           "detail": (f"超买衰竭确认：RSI6 自峰值 {ob_peak:.0f} 跌回 90 下方，短线防回调；"
                                      f"区间高点 {ob_hi:.2f} 不能放量收复则回调延续"),
                           "_score": 62, "_grp": "rsi_ob_out"})
            ob_peak = ob_hi = None
        elif ob_peak is not None:                        # 88~90 徘徊，波段未结束
            ob_hi = max(ob_hi, float(high[i]))
        # ---- 超卖波段 ----
        if os_trough is None and r0 > 10 >= r1:          # 新进超卖区
            os_trough, os_lo = r1, float(low[i])
            ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(low[i]),
                       "kind": "indicator", "label": "RSI6超卖",
                       "direction": "bull", "star": bool(r1 <= 5),
                       "detail": (f"RSI6={r1:.0f} 下穿 10 进入超卖区，杀跌过度；"
                                  f"RSI 升回 10 上方为修复确认，反抽 MA20{m_txt} 不过则弱势未改"),
                       "_score": 58, "_grp": "rsi_os_in"})
        elif os_trough is not None and r1 <= 10:         # 波段延续
            os_trough, os_lo = min(os_trough, r1), min(os_lo, float(low[i]))
        elif os_trough is not None and r1 > 12:          # 修复确认（升出超卖区）
            if os_trough <= 7:
                ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(low[i]),
                           "kind": "indicator", "label": "RSI6超卖修复",
                           "direction": "bull", "star": bool(os_trough <= 4),
                           "detail": (f"超卖修复确认：RSI6 自谷值 {os_trough:.0f} 升回 10 上方，短线有修复反弹；"
                                      f"区间低点 {os_lo:.2f} 失守则修复失败"),
                           "_score": 62, "_grp": "rsi_os_out"})
            os_trough = os_lo = None
        elif os_trough is not None:                      # 10~12 徘徊，波段未结束
            os_lo = min(os_lo, float(low[i]))
    return ev


def _trend_cross_signals(d: pd.DataFrame) -> list[dict]:
    """趋势切换层（全历史）：EMA20×EMA60 金叉/死叉=中期趋势方向确认，
    ADX≥25 给星标（趋势有强度）。文案给动态支撑/压力与失效条件。"""
    n = len(d)
    close = d["close"].to_numpy(dtype=float)
    ema20, ema60 = d["EMA20"].to_numpy(dtype=float), d["EMA60"].to_numpy(dtype=float)
    adx = d["ADX"].to_numpy(dtype=float)
    ev: list[dict] = []
    for i in range(1, n):
        if np.isnan(ema60[i]) or np.isnan(ema20[i - 1]):
            continue
        golden = ema20[i - 1] <= ema60[i - 1] and ema20[i] > ema60[i]
        death = ema20[i - 1] >= ema60[i - 1] and ema20[i] < ema60[i]
        if not (golden or death):
            continue
        a = adx[i] if not np.isnan(adx[i]) else 0.0
        ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(close[i]),
                   "kind": "trend",
                   "label": "EMA20金叉EMA60" if golden else "EMA20死叉EMA60",
                   "direction": "bull" if golden else "bear",
                   "star": bool(a >= 25),
                   "detail": (f"EMA20({'%.2f' % ema20[i]}){'上穿' if golden else '下穿'}"
                              f"EMA60({'%.2f' % ema60[i]})，中期趋势{'转多' if golden else '转空'}"
                              f"（ADX={a:.0f}）；"
                              + (f"回调看 EMA60 动态支撑，死叉则转多失败" if golden
                                 else f"反弹看 EMA60 动态压力，金叉则转空失败")),
                   "_score": 62, "_grp": "trend_cross"})
    return ev


def _pullback_signals(d: pd.DataFrame) -> list[dict]:
    """趋势延续层（全历史）：多头排列（MA20>MA60 且价在 MA60 上）内回踩 MA20/MA60 后
    收盘重新站上 MA20 = 回踩企稳；空头排列内反抽 MA20/MA60 后收盘重新跌破 MA20 = 反抽受阻。
    每波回调/反抽只标一次（20 根去重）。这是趋势跟踪方法论的核心进场/持有信号。"""
    n = len(d)
    close = d["close"].to_numpy(dtype=float)
    high, low = d["high"].to_numpy(dtype=float), d["low"].to_numpy(dtype=float)
    ma20, ma60 = d["MA20"].to_numpy(dtype=float), d["MA60"].to_numpy(dtype=float)
    ev: list[dict] = []
    last_bull = last_bear = -10**9
    for i in range(2, n):
        if np.isnan(ma60[i]) or np.isnan(ma20[i - 1]):
            continue
        bull_regime = ma20[i] > ma60[i] and close[i] > ma60[i]
        bear_regime = ma20[i] < ma60[i] and close[i] < ma60[i]
        if bull_regime and i - last_bull > 20:
            dipped = float(np.min(low[max(0, i - 6):i])) <= ma20[i] * 1.005
            if dipped and close[i - 1] <= ma20[i - 1] and close[i] > ma20[i]:
                last_bull = i
                ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(close[i]),
                           "kind": "trend", "label": "回踩企稳", "direction": "bull",
                           "star": False,
                           "detail": (f"多头趋势内回踩企稳：低点触及 MA20 区域后收回 {close[i]:.2f}，"
                                      f"趋势延续看高一线；收盘跌破 MA60（{ma60[i]:.2f}）则破坏"),
                           "_score": 56, "_grp": "trend_pb_bull"})
        elif bear_regime and i - last_bear > 20:
            rallied = float(np.max(high[max(0, i - 6):i])) >= ma20[i] * 0.995
            if rallied and close[i - 1] >= ma20[i - 1] and close[i] < ma20[i]:
                last_bear = i
                ev.append({"bar_idx": i, "time": _date_str(d, i), "price": float(close[i]),
                           "kind": "trend", "label": "反抽受阻", "direction": "bear",
                           "star": False,
                           "detail": (f"空头趋势内反抽受阻：反弹触及 MA20 区域后回落 {close[i]:.2f}，"
                                      f"下降趋势延续；收盘收复 MA60（{ma60[i]:.2f}）则破坏"),
                           "_score": 56, "_grp": "trend_pb_bear"})
    return ev


# ---------------- 明显背离过滤 ----------------

def _notable_divergences(divs: list[dict]) -> list[dict]:
    """只保留"特别明显"的背离：价格波幅 ≥3%，且指标反差显著
    （相对较大值 ≥15%，且有绝对下限——DIF/DEA 为价格的 0.2%，RSI 为 5 点，
    防止指标在零轴附近的毛刺被当成背离）。"""
    out = []
    for e in divs:
        swing = abs(e["price2"] - e["price1"]) / e["price1"]
        if swing < DIV_MIN_PRICE_SWING:
            continue
        diff = abs(e["ind1"] - e["ind2"])
        base = max(abs(e["ind1"]), abs(e["ind2"]))
        abs_floor = 5.0 if e["indicator"].startswith("RSI") else DIV_MIN_IND_ABS_PCT * e["price2"]
        if diff < max(DIV_MIN_IND_REL * base, abs_floor):
            continue
        out.append(e)
    return out


# ---------------- 重要背离裁决（精选口径：只有高价值背离才上图） ----------------

DIV_GAP_BY_TF = {"1d": 10, "1w": 4, "60m": 10}   # 两峰/谷最小间隔（根）
DIV_KEEP_SCORE = 3     # 重要性得分下限（≥3 才上图）
DIV_STAR_SCORE = 4     # 重要背离星标下限
DIV_CROSS_BARS = 10    # 背离后 MACD 交叉确认的窗口（根）
DIV_CLUSTER_BARS = 60  # 连续背离（二次背离）计次窗口（根）


def _important_divergences(d: pd.DataFrame, divs: list[dict], timeframe: str) -> list[dict]:
    """在"明显背离"基础上裁决"重要背离"（文献支持的高胜率过滤条件）：

    打分项（每项 +1）：
    - 两峰/谷间隔达标（日线≥10根，周线≥4根）：间隔太近是噪声；
    - 第二峰/谷缩量（5日均量低于第一峰/谷）：量价配合；
    - 第二 pivot 后 DIV_CROSS_BARS 根内出现 MACD 死叉（顶背离）/金叉（底背离）确认；
    - 背离时 RSI6 处于极值区（顶>70 / 底<30）：位置配合；
    - DIV_CLUSTER_BARS 根内有同向另一次背离（连续背离更危险/更可贵）。
    得分 ≥DIV_KEEP_SCORE 才上图，≥DIV_STAR_SCORE 星标（写入 e["_imp"]/e["star"]）。
    DIF 与 DEA 在同一对 pivot 上重复时只保留 DIF（MACD 背离以 DIF 为准）。
    """
    base = _notable_divergences(divs)
    if not base:
        return []
    # 同 (kind, idx1, idx2) 去重：DIF 优先，其次 DEA，最后 RSI
    pref = {"DIF": 0, "DEA": 1}
    best: dict[tuple, dict] = {}
    for e in base:
        key = (e["kind"], e["idx1"], e["idx2"])
        cur = best.get(key)
        if cur is None or pref.get(e["indicator"], 2) < pref.get(cur["indicator"], 2):
            best[key] = e
    cands = list(best.values())

    n = len(d)
    vol = d["vol"].to_numpy(dtype=float) if "vol" in d.columns else None
    rsi6 = d["RSI6"].to_numpy(dtype=float)
    dif = d["DIF"].to_numpy(dtype=float)
    dea = d["DEA"].to_numpy(dtype=float)
    min_gap = DIV_GAP_BY_TF.get(timeframe, 10)

    def _crossed(idx2: int, kind: str) -> bool:
        for i in range(idx2 + 1, min(idx2 + 1 + DIV_CROSS_BARS, n)):
            if np.isnan(dif[i]) or np.isnan(dea[i]):
                continue
            if kind == "top" and dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
                return True
            if kind == "bottom" and dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
                return True
        return False

    def _vol_shrink(idx1: int, idx2: int) -> bool:
        if vol is None or idx1 < 5 or idx2 < 5:
            return False
        v1 = float(np.nanmean(vol[idx1 - 4 : idx1 + 1]))
        v2 = float(np.nanmean(vol[idx2 - 4 : idx2 + 1]))
        return v1 > 0 and v2 < v1

    scored = []
    for e in cands:
        s = 0
        if e["idx2"] - e["idx1"] >= min_gap:
            s += 1
        if _vol_shrink(e["idx1"], e["idx2"]):
            s += 1
        if _crossed(e["idx2"], e["kind"]):
            s += 1
        r = rsi6[e["idx2"]]
        if not np.isnan(r) and ((e["kind"] == "top" and r > 70) or (e["kind"] == "bottom" and r < 30)):
            s += 1
        scored.append([e, s])
    # 连续背离计次（在通过显著性过滤的集合内）
    for item in scored:
        e = item[0]
        if any(x is not e and x["kind"] == e["kind"]
               and abs(x["idx2"] - e["idx2"]) <= DIV_CLUSTER_BARS for x, _ in scored):
            item[1] += 1
        e["_imp"] = item[1]
    out = [e for e, s in scored if s >= DIV_KEEP_SCORE]
    for e in out:
        e["star"] = e["_imp"] >= DIV_STAR_SCORE
    return out


# ---------------- 斐波那契 / 谐波 / 结构里程碑标注 ----------------

def _fib_annotations(d: pd.DataFrame, fib) -> list[dict]:
    """现价到达斐波那契重要位时上图：golden pocket 星标，其余重要位贴近时普通标注。"""
    if fib is None:
        return []
    asof = len(d) - 1
    close = float(d["close"].iloc[-1])
    sw = fib["swing"]
    direction = "bull" if sw["dir"] == "up" else "bear"   # 上升腿回撤位=支撑；下跌腿反弹位=压力
    leg_txt = "上升" if sw["dir"] == "up" else "下跌"
    anchor = f"主导{leg_txt}腿 {sw['start_price']:.2f}→{sw['end_price']:.2f}"
    if fib["golden_pocket"]:
        lo, hi = fib["golden_pocket_zone"]
        return [{"bar_idx": asof, "time": _date_str(d, asof), "price": close,
                 "kind": "fib", "label": "黄金口袋", "direction": direction, "star": True,
                 "detail": (f"现价 {close:.2f} 落入{anchor}的 0.618~0.65 回撤区"
                            f"（{min(lo, hi):.2f}~{max(lo, hi):.2f}），黄金口袋"
                            f"{'企稳位，重点关注止跌信号' if direction == 'bull' else '承压位，反弹至此易遇阻'}"),
                 "_score": 80,
                 "zones": [{"t1": _date_str(d, sw["end_idx"]), "t2": _date_str(d, asof),
                            "top": float(max(lo, hi)), "bottom": float(min(lo, hi)),
                            "color": "rgba(240,185,11,0.10)"}]}]
    r = fib["nearest_level"]
    ratio = float(r["ratio"])
    if ratio in FIB_KEY_RATIOS and abs(r["price"] - close) / close <= FIB_NEAR_TOL:
        return [{"bar_idx": asof, "time": _date_str(d, asof), "price": float(r["price"]),
                 "kind": "fib", "label": f"斐波那契{ratio:g}位", "direction": direction,
                 "star": False,
                 "detail": (f"现价 {close:.2f} 贴近{anchor}的 {ratio:g} "
                            f"{'回撤' if sw['dir'] == 'up' else '反弹'}位 {r['price']:.2f}"),
                 "_score": 55}]
    return []


def _harmonic_annotations(d: pd.DataFrame, harms) -> list[dict]:
    """谐波形态标注：已完成形态标 D 点；现价进入 PRZ 反转区星标；构筑中的投影 PRZ。"""
    asof = len(d) - 1
    close = float(d["close"].iloc[-1])
    out: list[dict] = []
    for e in harms:
        lo, hi = min(e["prz_low"], e["prz_high"]), max(e["prz_low"], e["prz_high"])
        in_prz = lo <= close <= hi
        zone = [{"t1": _date_str(d, e["x"]["idx"]), "t2": _date_str(d, asof),
                 "top": float(hi), "bottom": float(lo), "color": "rgba(240,185,11,0.08)"}]
        if e["completed"] and e.get("d"):
            di = e["d"]["idx"]
            out.append({"bar_idx": di, "time": _date_str(d, di),
                        "price": float(e["d"]["price"]), "kind": "harmonic",
                        "label": f"谐波{e['name']}·D点反转", "direction": e["direction"],
                        "star": bool(in_prz or di >= asof - DIV_STAR_AGE),
                        "detail": e["note"], "_score": 65, "zones": zone})
        # 「潜在形态构筑中」不再上图（精选口径：只保留已完成 D 点与进入 PRZ 两类）
        if in_prz:
            out.append({"bar_idx": asof, "time": _date_str(d, asof), "price": close,
                        "kind": "harmonic", "label": f"进入{e['name']} PRZ",
                        "direction": e["direction"], "star": True,
                        "detail": (f"现价 {close:.2f} 落入 {e['name']} 谐波反转区（PRZ）"
                                   f"{lo:.2f}~{hi:.2f}，"
                                   f"{'潜在反转买入区' if e['direction'] == 'bull' else '潜在反转卖出区'}"),
                        "_score": 85, "zones": zone})
    return out


# 形态标注的前瞻推演：量度目标 / 失效位 / 构筑中的条件推演——全部是事件时点
# 即可推导的点位（不用任何未来数据），这是"方法论标注"与"事后复盘"的分水岭
def _pat_forward(e: dict, conditional: bool = False) -> str:
    lv = e.get("key_levels") or {}
    bear = e["direction"] == "bear"
    tgt = lv.get("measure_target")
    if tgt is None:
        tgt = lv.get("measure_target_dn") if bear else lv.get("measure_target_up")
    inv = lv.get("invalidation")
    nl = lv.get("neckline")
    parts: list[str] = []
    if e.get("confirm_idx") is not None and not conditional:
        if tgt is not None:
            parts.append(f"{'下行' if bear else '上行'}量度目标 {float(tgt):.2f}")
        if inv is not None:
            parts.append(f"失效位 {float(inv):.2f}（收盘{'收复' if bear else '失守'}则结构破坏，重新评估）")
    else:
        if nl is not None:
            parts.append(f"收盘{'跌破' if bear else '突破'}颈线 {float(nl):.2f} 方可确认")
        if tgt is not None:
            parts.append(f"确认后量度目标 {float(tgt):.2f}")
        if inv is not None:
            parts.append(f"{'收复' if bear else '失守'} {float(inv):.2f} 则构筑失败")
    return "；".join(parts)


# 形态"右半部分就位"里程碑：标注在结构最后一个 pivot（右底/右肩/第三底）上
_MILESTONE_LABEL = {
    "double_bottom": "右底确认", "double_top": "右顶确认",
    "triple_bottom": "第三底确认",
    "head_shoulders_bottom": "右肩确认", "head_shoulders_top": "右肩确认",
}


def _neckline_fn(e):
    """由 key_levels 还原颈线取值函数 level_fn(bar_idx)（支持斜线颈线）；无颈线返回 None。"""
    lv = e["key_levels"]
    if "neckline" in lv:
        v = float(lv["neckline"])
        return lambda i: v
    if "neckline_left" in lv and "neckline_right" in lv:
        x1, x2 = e["start_idx"], e["end_idx"]
        y1, y2 = float(lv["neckline_left"]), float(lv["neckline_right"])
        slope = (y2 - y1) / max(x2 - x1, 1)
        return lambda i: y1 + slope * (i - x1)
    if e["kind"] == "box" and "upper" in lv and "lower" in lv:
        v = float(lv["upper"] if e["direction"] != "bear" else lv["lower"])
        return lambda i: v
    return None


def _structure_milestones(d: pd.DataFrame, pats) -> list[dict]:
    """结构里程碑标注：
    1) 右底/右肩/第三底就位（形态构筑关键节点）；
    2) 突破后回踩颈线企稳确认（bear 形态为反抽颈线受阻确认）。
    """
    n = len(d)
    close = d["close"].to_numpy(dtype=float)
    high = d["high"].to_numpy(dtype=float)
    low = d["low"].to_numpy(dtype=float)
    out: list[dict] = []
    for e in pats:
        if not e.get("active", True):
            continue  # 里程碑/回踩标注只服务当下活跃结构（历史结构仅保留突破标注本身）
        bar = int(e["end_idx"])
        # 1) 构筑里程碑（未确认形态的右端已由"·构筑中"主标注占据同一 bar，不重复标）
        suffix = _MILESTONE_LABEL.get(e["kind"])
        if suffix is not None and e["confirm_idx"] is not None:
            # 里程碑站在确认之前：文案必须是该时点的因果推演——剥离 note 中的
            # 突破日期从句（"…，YYYY-MM-DD 收盘突破/跌破颈线…"），改用条件推演
            base_note = re.split(r"，\d{4}-\d{2}-\d{2}", e["note"], maxsplit=1)[0]
            fwd = _pat_forward(e, conditional=True)
            out.append({"bar_idx": bar, "time": _date_str(d, bar),
                        "price": float(low[bar] if e["direction"] == "bull" else high[bar]),
                        "kind": "pattern", "label": f"{e['name']}·{suffix}",
                        "direction": e["direction"], "star": False,
                        "detail": (base_note + f"。{fwd}" if fwd else base_note),
                        "_score": 60, "_grp": f"pattern:ms:{e['kind']}",
                        "active": bool(e.get("active", True))})
        # 2) 回踩/反抽确认（仅限已突破且带颈线的形态，每形态只标首次回踩）
        ci = e["confirm_idx"]
        if ci is None:
            continue
        nlf = _neckline_fn(e)
        if nlf is None:
            continue
        inv = e["key_levels"].get("invalidation")
        bull = e["direction"] != "bear"
        for i in range(int(ci) + 1, n):
            if inv is not None and ((bull and close[i] < inv) or (not bull and close[i] > inv)):
                break  # 结构已失效，之后的触碰不算回踩确认
            nl = nlf(i)
            if bull and low[i] <= nl * (1 + RETEST_TOL) and close[i] > nl \
                    and close[i - 1] > nlf(i - 1):
                tgt = e["key_levels"].get("measure_target") or e["key_levels"].get("measure_target_up")
                out.append({"bar_idx": i, "time": _date_str(d, i), "price": float(nl),
                            "kind": "pattern", "label": f"{e['name']}·回踩颈线确认",
                            "direction": "bull", "star": True,
                            "detail": (f"突破后回踩颈线 {nl:.2f} 企稳（最低 {low[i]:.2f} 触及后收回），"
                                       f"{e['name']}突破有效性获二次确认"
                                       + (f"；上行量度目标 {float(tgt):.2f} 维持有效" if tgt else "")),
                            "_score": 75, "_grp": f"pattern:retest:{e['kind']}",
                            "active": bool(e.get("active", True))})
                break
            if not bull and high[i] >= nl * (1 - RETEST_TOL) and close[i] < nl \
                    and close[i - 1] < nlf(i - 1):
                tgt = e["key_levels"].get("measure_target") or e["key_levels"].get("measure_target_dn")
                out.append({"bar_idx": i, "time": _date_str(d, i), "price": float(nl),
                            "kind": "pattern", "label": f"{e['name']}·反抽颈线确认",
                            "direction": "bear", "star": True,
                            "detail": (f"跌破后反抽颈线 {nl:.2f} 受阻（最高 {high[i]:.2f} 触及后回落），"
                                       f"{e['name']}跌破有效性获二次确认"
                                       + (f"；下行量度目标 {float(tgt):.2f} 维持有效" if tgt else "")),
                            "_score": 75, "_grp": f"pattern:retest:{e['kind']}",
                            "active": bool(e.get("active", True))})
                break
    return out


# ---------------- 标注构建与密度控制 ----------------

def _date_str(d: pd.DataFrame, i: int) -> str:
    return str(d["trade_date"].iloc[i])[:10]


def _density_filter(annotations: list[dict]) -> list[dict]:
    """同一 DENSITY_WINDOW 根窗口内同密度组（_grp，缺省=kind）只保留最重要的一个。

    重要性排序：star > _score > 更近的 bar_idx。全局贪心：按重要性降序逐个收编。
    pattern 按「阶段（突破/构筑里程碑/回踩）× 形态种类」分组，
    避免不同形态的结构标注互相挤掉（同类形态本身只会有最近一个事件）。
    """
    def prio(a):
        return (1 if a["star"] else 0, a.get("_score", 0), a["bar_idx"])

    def _clash(a, b):
        if abs(a["bar_idx"] - b["bar_idx"]) > DENSITY_WINDOW:
            return False
        return a.get("_grp", a["kind"]) == b.get("_grp", b["kind"])

    kept: list[dict] = []
    # 先裁指标信号：与同方向形态标注同窗（同一底部/顶部区域）时让位给形态标注——
    # 结构信息优先，避免"RSI6超卖+W底构筑中"叠字；孤立区域的 RSI6 超买超卖照常保留
    pat_anns = [a for a in annotations if a["kind"] == "pattern"]
    annotations = [
        a for a in annotations
        if not (a["kind"] == "indicator"
                and any(p.get("direction") == a.get("direction")
                        and abs(p["bar_idx"] - a["bar_idx"]) <= DENSITY_WINDOW
                        for p in pat_anns))]
    for a in sorted(annotations, key=prio, reverse=True):
        # 密度分组：默认按 kind；pattern 细分 突破/构筑里程碑/回踩，互不挤占
        clash = next((k for k in kept if _clash(k, a)), None)
        if clash is None:
            kept.append(a)
        elif prio(a) > prio(clash):
            kept.remove(clash)
            kept.append(a)
    for a in kept:
        a.pop("_score", None)
        a.pop("_grp", None)
    kept.sort(key=lambda a: a["bar_idx"])
    return kept


def _build_annotations(d: pd.DataFrame, pats, divs, fib, harms, ind_sigs) -> list[dict]:
    n = len(d)
    asof = n - 1
    close = float(d["close"].iloc[-1])
    ann: list[dict] = []

    # 背离的"确认位"参考：次级 zigzag 提供两峰/两谷之间的反向 pivot
    zz_ref = piv_mod.zigzag(d, min_pct=piv_mod.MS_LEVELS[0])
    ref_rows = zz_ref.to_dict("records") if len(zz_ref) else []

    def _div_confirm_level(e):
        """顶背离：两峰间的低点 = 跌破确认位；底背离：两谷间的高点 = 收复确认位。"""
        kind = "L" if e["kind"] == "top" else "H"
        mids = [p for p in ref_rows
                if e["idx1"] < int(p["idx"]) < e["idx2"] and p["kind"] == kind]
        if not mids:
            return None
        pick = min if kind == "L" else max
        return float(pick(mids, key=lambda p: p["price"])["price"])

    # 已确认形态 = 颈线/边界突破事件：标注直接写明突破，且全部星标（重要结构信号）
    _BRK_SUFFIX = {
        "double_bottom": "颈线突破", "triple_bottom": "颈线突破",
        "head_shoulders_bottom": "颈线突破", "arc_bottom": "弧口突破",
        "double_top": "颈线跌破", "head_shoulders_top": "颈线跌破",
        "rising_wedge": "下沿跌破", "falling_wedge": "上沿突破",
        "asc_triangle": "上沿突破", "desc_triangle": "下沿跌破",
    }

    def _brk_label(e):
        if e["confirm_idx"] is None:
            return e["name"] + "·构筑中"
        sfx = _BRK_SUFFIX.get(e["kind"])
        if sfx is None:
            sfx = {"bull": "上沿突破", "bear": "下沿跌破"}.get(e["direction"], "边界突破")
        return e["name"] + sfx

    for e in pats:
        # 精选口径：非主要形态（楔形/三角形/箱体/旗形/波浪）只在活跃（当下）时上图；
        # 历史区段只保留 W底/M顶/头肩/三重底/圆弧底 等主要反转结构的确认事件
        if e["kind"] not in _MAJOR_PAT_KINDS and not e.get("active", True):
            continue
        bar = e["confirm_idx"] if e["confirm_idx"] is not None else e["end_idx"]
        lv = e["key_levels"]
        price = lv.get("neckline") or lv.get("neckline_at_end") or lv.get("upper") \
            or lv.get("upper_now") or lv.get("lower") or lv.get("lower_now") \
            or lv.get("c_low") or lv.get("arc_low") or lv.get("flag_upper") \
            or lv.get("wave3_top") or close
        star_pat = (bool(e["star"]) or (e["confirm_idx"] is not None
                                        and e["kind"] in _MAJOR_PAT_KINDS)) \
            and (bool(e.get("active", True)) or int(bar) >= asof - STAR_RECENT_BARS)
        fwd = _pat_forward(e)
        a = {"bar_idx": int(bar), "time": _date_str(d, bar), "price": float(price),
             "kind": "pattern", "label": _brk_label(e),
             "direction": ("bull" if e["direction"] == "range" and e["confirm_idx"] is not None
                           else e["direction"]),
             "star": star_pat,
             "detail": (e["note"] + f"。{fwd}" if fwd else e["note"]),
             "_score": e["score"], "_grp": f"pattern:brk:{e['kind']}",
             "active": bool(e.get("active", True))}
        if e.get("trace"):
            a["polylines"] = e["trace"]
        lines = []
        if "neckline" in lv:
            lines.append({"t1": _date_str(d, e["start_idx"]), "p1": float(lv["neckline"]),
                          "t2": _date_str(d, bar), "p2": float(lv["neckline"]),
                          "style": "dashed"})
        elif "neckline_left" in lv and "neckline_right" in lv:
            mids = [p for p in (e["start_idx"], e["end_idx"])]
            lines.append({"t1": _date_str(d, e["start_idx"]), "p1": float(lv["neckline_left"]),
                          "t2": _date_str(d, e["end_idx"]), "p2": float(lv["neckline_right"]),
                          "style": "dashed"})
        if "upper" in lv and "lower" in lv:
            a["zones"] = [{"t1": _date_str(d, e["start_idx"]), "t2": _date_str(d, asof),
                           "top": float(lv["upper"]), "bottom": float(lv["lower"]),
                           "color": "rgba(240,185,11,0.08)"}]
        if lines:
            a["lines"] = lines
        ann.append(a)

    for e in divs:
        # 箭头放在右侧确认根（事件真正可知的时点，防未来函数）；
        # 两 pivot 之间的描摹连线保留，便于看清背离结构
        bar = int(e["confirmed_idx"]) if e.get("confirmed_idx") is not None else int(e["idx2"])
        top = e["kind"] == "top"
        anchor = float(d["high"].iloc[bar]) if top else float(d["low"].iloc[bar])
        label = ("顶背离" if top else "底背离") + f"({e['indicator']})"
        detail = (f"价格 {'新高' if top else '新低'} "
                  f"{e['price1']:.2f}→{e['price2']:.2f}，{e['indicator']} "
                  f"未同步（{e['ind1']:.2f}→{e['ind2']:.2f}），"
                  f"{_date_str(d, e['confirmed_idx'])} 右侧确认，重要性 {e.get('_imp', 0)}/5")
        cfm = _div_confirm_level(e)
        if cfm is not None:
            detail += (f"；确认位 {cfm:.2f}（收盘{'跌破' if top else '收复'}"
                       f"则背离兑现、{'下行' if top else '反弹'}打开）")
        detail += f"；{'新高' if top else '新低'} {e['price2']:.2f} 则背离解除"
        ann.append({
            "bar_idx": bar, "time": _date_str(d, bar), "price": anchor,
            "kind": "divergence", "label": label,
            "direction": "bear" if top else "bull",
            "star": bool(e.get("star", False)),
            "detail": detail,
            "_score": 60 + 10 * e.get("_imp", 0),
            "lines": [{"t1": _date_str(d, e["idx1"]), "p1": float(e["price1"]),
                       "t2": _date_str(d, e["idx2"]), "p2": float(e["price2"]),
                       "style": "solid"}],
        })

    ann.extend(_structure_milestones(d, pats))
    ann.extend(_fib_annotations(d, fib))
    ann.extend(_harmonic_annotations(d, harms))
    ann.extend(ind_sigs)
    return _density_filter(ann)


# ---------------- summary ----------------

def _trend_text(d: pd.DataFrame) -> tuple[str, int]:
    """返回 (趋势描述, 方向分: 1多/-1空/0震荡)。"""
    close = float(d["close"].iloc[-1])
    ma20, ma60, ma250 = (d[c].iloc[-1] for c in ("MA20", "MA60", "MA250"))
    adx, pdi, mdi = (float(d[c].iloc[-1]) for c in ("ADX", "PDI", "MDI"))
    above = sum(1 for m in (ma20, ma60, ma250) if not np.isnan(m) and close > m)
    ma20_slope = np.nan if len(d) < 25 else (d["MA20"].iloc[-1] / d["MA20"].iloc[-6] - 1)
    if above == 3 and (np.isnan(ma20_slope) or ma20_slope > 0):
        return (f"多头排列（收盘价站上 MA20/60/250，ADX={adx:.0f}，"
                f"{'PDI>MDI 多方主导' if pdi > mdi else '但 PDI<MDI 动能转弱'}）", 1)
    if above == 0 and (np.isnan(ma20_slope) or ma20_slope < 0):
        return (f"空头排列（收盘价位于 MA20/60/250 之下，ADX={adx:.0f}，"
                f"{'MDI>PDI 空方主导' if mdi > pdi else '但 MDI<PDI 下行动能减弱'}）", -1)
    return f"震荡/转换期（收盘价站上 {above}/3 条主均线，ADX={adx:.0f}）", 0


def _key_levels(d: pd.DataFrame, pv: pd.DataFrame, fib, close: float):
    """关键支撑/阻力：最近 pivot 低/高点 + 斐波那契位 + BOLL 轨。"""
    supports, resistances = [], []
    lows = pv[pv["kind"] == "L"].tail(6)["price"].tolist()
    highs = pv[pv["kind"] == "H"].tail(6)["price"].tolist()
    cands_s = [p for p in lows if p < close]
    cands_r = [p for p in highs if p > close]
    if fib is not None:
        cands_s += [p for p in fib["levels"].values() if p < close]
        cands_r += [p for p in fib["levels"].values() if p > close]
    bup, bdn = d["BOLL_UP"].iloc[-1], d["BOLL_DN"].iloc[-1]
    if not np.isnan(bdn) and bdn < close:
        cands_s.append(float(bdn))
    if not np.isnan(bup) and bup > close:
        cands_r.append(float(bup))
    # 去重聚类（2% 内合并），支撑取最接近现价的 3 个，阻力同理
    def cluster(vals, reverse):
        out = []
        for v in sorted(vals, reverse=reverse):
            if all(abs(v - o) / o > 0.02 for o in out):
                out.append(v)
            if len(out) >= 3:
                break
        return [round(v, 2) for v in out]
    supports = cluster(cands_s, reverse=True)
    resistances = cluster(cands_r, reverse=False)
    return supports, resistances


def _target_stop(d: pd.DataFrame, pv: pd.DataFrame, pats, fib, close: float, trend_dir: int,
                 zz_trade: pd.DataFrame | None, supports: list[float] | None = None):
    """多尺度自适应目标/止损（ARCHITECTURE.md 多尺度自适应 §5-6）。

    目标：活跃已确认结构量度目标（交易级别）→ 背景级构筑中结构潜在目标 →
    斐波那契扩展位（只做多口径，下跌趋势不产出目标）。
    止损：当前活跃结构失效点（深度不超结构自身深度，防引用古早深坑）→
    交易级别最近已确认 pivot 低点 → 近 40 根 pivot 低点 → 最近关键支撑 →
    兜底 close−2×ATR；硬约束深度 ≤ 2.5×ATR。
    """
    atr = float(d["ATR14"].iloc[-1]) if not np.isnan(d["ATR14"].iloc[-1]) else close * 0.02
    active = [e for e in pats if e.get("active")]

    # ---- 目标（只做多口径：只有多头结构的量度目标才作为上行目标）----
    target = None
    target_src = ""
    confirmed = sorted((e for e in active if e["confirm_idx"] is not None
                        and e["direction"] != "bear"),
                       key=lambda e: e["confirm_idx"])
    for e in reversed(confirmed):
        lv = e["key_levels"]
        t = lv.get("measure_target") or lv.get("measure_target_up")
        if t is None:
            continue
        if t > close:
            target, target_src = float(t), f"{e['name']}量度目标"
            break
    if target is None:
        # 背景级构筑中结构 → 潜在目标区
        building = [e for e in active if e["confirm_idx"] is None and e["direction"] == "bull"]
        for e in reversed(building):
            lv = e["key_levels"]
            t = lv.get("measure_target") or lv.get("measure_target_up")
            if t is not None and t > close:
                target, target_src = float(t), f"{e['name']}潜在目标（构筑中）"
                break
    if target is None and fib is not None and trend_dir >= 0:
        above = sorted(p for p in fib["extensions"].values() if p > close)
        if above:
            target, target_src = float(above[0]), f"斐波那契扩展位（{fib['swing']['dir']}腿）"
    if trend_dir < 0:
        # 只做多口径：下跌趋势不给目标位（回避/观望）
        target, target_src = None, ""

    # ---- 止损 ----
    stop = None
    stop_src = ""
    struct_cands = []
    for e in active:
        inv = e["key_levels"].get("invalidation")
        if inv is None or inv >= close:
            continue
        vals = [v for v in e["key_levels"].values()
                if isinstance(v, (int, float)) and v > 0]
        if not vals:
            continue
        depth = max(vals) - min(vals)
        if close - inv <= depth + 1e-9:  # 止损深度不得超过该结构自身深度
            struct_cands.append((e["confirm_idx"] or e["end_idx"], float(inv), e["name"]))
    if struct_cands:
        struct_cands.sort()
        _, inv, nm = struct_cands[-1]
        stop, stop_src = inv, f"{nm}失效点"
    if stop is None:
        leg = piv_mod.current_leg(zz_trade) if zz_trade is not None and len(zz_trade) else None
        cands = []
        if leg and leg["kind"] == "L" and leg["start_price"] < close:
            cands.append((leg["start_price"], "交易级别最近 pivot 低点"))
        # 近 40 根内 pivot 低点（用户口径），取最靠近现价者
        pv_l = pv[(pv["kind"] == "L") & (pv["idx"] >= len(d) - 40)]
        pivot_lows = [p for p in pv_l["price"].tolist() if p < close]
        if pivot_lows:
            cands.append((max(pivot_lows), "近40根 pivot 低点"))
        # 直线下跌中 pivot 低点缺位时，退到最近关键支撑（BOLL/斐波/pivot 聚类）
        sup_below = [s for s in (supports or []) if s < close]
        if sup_below:
            cands.append((max(sup_below), "最近关键支撑"))
        cands.append((close - ATR_STOP_MULT * atr, f"{ATR_STOP_MULT:.0f}×ATR"))
        stop, stop_src = max(cands, key=lambda x: x[0])  # 保守 = 最靠近现价
    # 硬约束：止损深度 ≤ 2.5×ATR（规则层面封死"现价350止损263"式荒谬输出）
    min_stop = close - 2.5 * atr
    if stop < min_stop:
        stop, stop_src = float(min_stop), "2.5×ATR 硬约束"
    stop = round(float(stop), 2)

    rr = None
    if target is not None and abs(close - stop) > 1e-9:
        rr = round((target - close) / (close - stop), 2)
        if rr is not None and (rr < 0 or rr > 10):
            rr = None  # 负值或远目标放大的虚高盈亏比不可信，不展示
    return target, target_src, stop, stop_src, rr


def _scale_text(ms: dict, closes: np.ndarray, asof: int) -> dict:
    """三级 zigzag → 分层描述（背景级别定方向与位置 / 交易级别当前腿 / 小级别近期信号）。

    描述采用"上一段已完成腿 + 当前发展腿"两段式：只说最后一个 pivot 之后的
    微型发展腿会丢失大级别信息（如从 750 跌至 351 后只显示"自 351 起 -0.3%"）。
    """
    out = {}
    for key, label in (("background", "背景"), ("trade", "交易"), ("small", "小")):
        zz = ms[key]
        leg = piv_mod.current_leg(zz)
        if not leg:
            out[key] = None
            out[key + "_leg"] = None
            continue
        piv_mod.annotate_leg(leg, closes, asof)
        cur_dir = "反弹" if leg["dir"] == "up" else "回落"
        cur = f"{cur_dir}至今 {leg['pct']:+.1%}（{leg['bars']}根）"
        if len(zz) >= 2:
            prev = zz.iloc[-2]
            prev_chg = leg["start_price"] / float(prev["price"]) - 1.0
            out[key] = (f"{label}级别：{str(prev['trade_date'])[:10]} {float(prev['price']):.2f}"
                        f" → {leg['start_date']} {leg['start_price']:.2f}（{prev_chg:+.1%}）后，{cur}")
        else:
            dir_cn = "上升" if leg["dir"] == "up" else "下跌"
            out[key] = f"{label}级别{dir_cn}腿：自 {leg['start_date']} {leg['start_price']:.2f} 起，{cur}"
        out[key + "_leg"] = leg
    return out


def analyze(df: pd.DataFrame, timeframe: str = "1d") -> dict:
    """推背图聚合主入口（规范签名：analyze(df, timeframe) -> AnalysisResult）。"""
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    n = len(d)
    asof = n - 1
    close = float(d["close"].iloc[-1])

    pv = piv_mod.find_pivots(d)
    pats = pat_mod.find_patterns(d, pv, timeframe=timeframe)
    divs = _important_divergences(d, div_mod.find_divergences(d, "DIF")
                                  + div_mod.find_divergences(d, "DEA")
                                  + div_mod.find_divergences(d, "RSI6"), timeframe)
    fib = fib_mod.fib_analysis(d, pv)
    harms = har_mod.find_xabcd(pv, asof_idx=asof)
    ind_sigs = _indicator_signals(d) + _trend_cross_signals(d) + _pullback_signals(d)

    annotations = _build_annotations(d, pats, divs, fib, harms, ind_sigs)

    # ---- summary（多尺度分层：背景级定方向 / 交易级当前结构 / 小级近期信号） ----
    closes = d["close"].to_numpy(dtype=float)
    ms = piv_mod.multi_scale_zigzag(d)
    scale = _scale_text(ms, closes, asof)
    trend_txt, trend_dir = _trend_text(d)
    active_pats = [e for e in pats if e.get("active")]
    if active_pats:
        latest = max(active_pats, key=lambda e: (e["confirm_idx"] or e["end_idx"]))
        struct_txt = latest["note"]
    elif scale["trade"]:
        struct_txt = "无活跃经典结构；" + scale["trade"]
    else:
        struct_txt = "近期无显著经典结构"
    hist = float(d["MACD_HIST"].iloc[-1])
    hist_prev = float(d["MACD_HIST"].iloc[-4]) if n >= 4 else hist
    rsi6 = float(d["RSI6"].iloc[-1])
    if rsi6 > 90:
        rsi_state = "超买区，短线追高风险大"
    elif rsi6 < 10:
        rsi_state = "超卖区，存在技术性修复需求"
    else:
        rsi_state = "中性区"
    momentum_txt = (f"MACD柱 {hist:.3f}，动能{'扩张' if abs(hist) > abs(hist_prev) else '收敛'}；"
                    f"RSI6={rsi6:.1f}（{rsi_state}）")
    v5 = float(np.nanmean(d["vol"].to_numpy(dtype=float)[-5:]))
    v20 = float(np.nanmean(d["vol"].to_numpy(dtype=float)[-20:]))
    if v20 > 0:
        vr = v5 / v20
        v_state = "明显放量" if vr > 1.2 else "明显缩量" if vr < 0.8 else "量能平稳"
        volume_txt = f"5日均量/20日均量={vr:.2f}（{v_state}）"
    else:
        volume_txt = "量能数据不足"
    supports, resistances = _key_levels(d, pv, fib, close)
    target, target_src, stop, stop_src, rr = _target_stop(
        d, pv, pats, fib, close, trend_dir, ms["trade"], supports)

    # 展望：每句独立成段（前端按「。」分段渲染），当下状态 + 前瞻条件
    date_s = _date_str(d, asof)
    s1 = f"截至{date_s}，收盘价 {close:.2f}，{trend_txt}。"
    s2 = f"结构：{struct_txt}。"
    s3 = f"动量：{momentum_txt}。量能：{volume_txt}。"
    s4 = (f"关键位：支撑 {'/'.join(f'{x:.2f}' for x in supports) or '暂缺'}，"
          f"阻力 {'/'.join(f'{x:.2f}' for x in resistances) or '暂缺'}。")
    # 斐波那契/谐波位置提示（仅现价到达重要位时补充一句）
    if fib is not None and fib["golden_pocket"]:
        glo, ghi = fib["golden_pocket_zone"]
        s4 += (f"现价处于主导{('上升' if fib['swing']['dir'] == 'up' else '下跌')}腿的"
               f"黄金口袋回撤区（{min(glo, ghi):.2f}~{max(glo, ghi):.2f}）。")
    elif fib is not None:
        r = fib["nearest_level"]
        if float(r["ratio"]) in FIB_KEY_RATIOS and abs(r["price"] - close) / close <= FIB_NEAR_TOL:
            s4 += f"现价贴近斐波那契{float(r['ratio']):g}位 {r['price']:.2f}。"
    prz_names = [e["name"] for e in harms
                 if min(e["prz_low"], e["prz_high"]) <= close <= max(e["prz_low"], e["prz_high"])]
    if prz_names:
        s4 += f"现价进入 {'、'.join(prz_names)} 谐波反转区（PRZ）。"

    # 前瞻：情景式推演（上行触发条件→目标；下行触发条件→失效/支撑；背离警示）
    nearest_r = resistances[0] if resistances else None
    next_r = resistances[1] if len(resistances) > 1 else None
    below_stop = [s for s in supports if stop is not None and s < stop]
    next_s = max(below_stop) if below_stop else None
    active_name = None
    bear_pat = None     # 最新已确认的活跃空头结构（下行量度目标/失效点）
    if active_pats:
        active_name = max(active_pats, key=lambda e: (e["confirm_idx"] or e["end_idx"]))["name"]
        bears = [e for e in active_pats if e["direction"] == "bear" and e["confirm_idx"] is not None]
        if bears:
            bear_pat = max(bears, key=lambda e: e["confirm_idx"])

    # 最近一次重要背离（60 根内）作为动能警示/提示
    div_note = ""
    if divs:
        last_div = max(divs, key=lambda e: e["idx2"])
        if asof - last_div["idx2"] <= 60:
            div_date = _date_str(d, last_div["confirmed_idx"])
            if last_div["kind"] == "top":
                div_note = (f"警示：{div_date} 确认的顶背离仍在影响期内，上行动能衰减，"
                            f"冲高宜减不宜追。")
            else:
                div_note = (f"提示：{div_date} 确认的底背离仍在影响期内，下跌动能衰竭，"
                            f"关注缩量企稳后的右侧机会。")

    bear_note = ""
    dn_t = None
    if bear_pat is not None:
        blv = bear_pat["key_levels"]
        dn_t = blv.get("measure_target") or blv.get("measure_target_dn")
        b_inv = blv.get("invalidation")
        if dn_t is not None and dn_t < close:
            bear_note = f"{bear_pat['name']}已确认跌破，下行量度目标 {dn_t:.2f}。"
        if b_inv is not None and b_inv > close:
            bear_note += f"若收盘收复 {b_inv:.2f}（{bear_pat['name']}失效点），空头结构破坏，方可重新评估多头。"

    # 构筑中的多头反转结构（未确认）：给出确认触发位与失败位，构成双向情景
    bull_note = ""
    if active_pats:
        builds = [e for e in active_pats
                  if e["direction"] != "bear" and e["confirm_idx"] is None]
        if builds:
            b = max(builds, key=lambda e: e["end_idx"])
            blv2 = b["key_levels"]
            nl = blv2.get("neckline") or blv2.get("neckline_at_end") or blv2.get("upper")
            tgt = blv2.get("measure_target") or blv2.get("measure_target_up")
            b_inv2 = blv2.get("invalidation")
            if nl is not None and nl > close:
                bull_note = f"同时{b['name']}仍在构筑：若收盘突破颈线 {nl:.2f}，底部反转确认"
                if tgt is not None and tgt > close:
                    bull_note += f"，量度目标 {tgt:.2f}"
                bull_note += "；"
                if b_inv2 is not None and b_inv2 < close:
                    bull_note += f"若收盘跌破 {b_inv2:.2f} 则构筑失败"
                    below_inv = [s for s in supports if s < b_inv2]
                    if below_inv:
                        bull_note += f"，下看 {max(below_inv):.2f}"
                    bull_note += "。"

    if trend_dir < 0:
        s5 = "前瞻：趋势偏空，本模型为只做多口径，建议回避/观望。"
        if bear_note:
            s5 += bear_note
        elif nearest_r:
            s5 += (f"若收盘重新站上 {nearest_r:.2f}，下行压力才会缓解"
                   f"{f'，进一步突破 {next_r:.2f} 才谈得上趋势修复' if next_r else ''}；"
                   f"在此之前不评估做多机会。")
        else:
            s5 += "等待趋势转多确认，在此之前不评估做多机会。"
        s5 += bull_note
    else:
        up = ""
        if target is not None:
            up = (f"上行路径：若收盘有效突破 {nearest_r:.2f}（最近阻力），看至{target_src} {target:.2f}"
                  if nearest_r else f"上行路径：目标看至{target_src} {target:.2f}")
            if next_r and next_r > target:
                up += f"，突破后进一步看 {next_r:.2f}"
            if rr is not None:
                up += f"（盈亏比约 {rr:.1f}）"
            up += "。"
        elif nearest_r:
            up = f"上行路径：当前缺乏已确认多头结构的量度目标，若收盘突破 {nearest_r:.2f}，视为转强确认。"
        dn = ""
        if stop is not None:
            # 最新活跃结构为空头结构时，跌破支撑失效的是"反弹/企稳"而非该空头结构本身
            latest_dir = max(active_pats, key=lambda e: (e["confirm_idx"] or e["end_idx"]))["direction"] \
                if active_pats else None
            stop_name = (f"{active_name}失效" if active_name and latest_dir != "bear"
                         else "反弹企稳失效")
            dn = (f"下行风险：若收盘跌破 {stop:.2f}（{stop_src}），{stop_name}"
                  f"{f'，下看 {next_s:.2f}' if next_s else ''}，做多逻辑退出。")
        s5 = "前瞻：" + (bear_note or up)
        if bear_note and up:
            s5 += up
        s5 += dn
    if div_note:
        s5 += div_note

    # ---- 下周推演：以最近交易日后首个周一为起点，给出本周关键位与操作纪律 ----
    asof_dt = pd.to_datetime(d["trade_date"].iloc[-1])
    days_ahead = (7 - asof_dt.weekday()) % 7 or 7
    next_mon = asof_dt + dt.timedelta(days=days_ahead)
    s6 = f"下周推演（{next_mon.month}月{next_mon.day}日起）："
    up_chain = [x for x in resistances if x > close][:2]
    # 最近 pivot 低点必入下方链条（2% 聚类可能把它并进相邻支撑，但它是最近攻防线）
    last_l = pv[pv["kind"] == "L"]["price"].iloc[-1] if len(pv[pv["kind"] == "L"]) else None
    dn_chain: list[float] = []
    for x in [max((s for s in supports if s < close), default=None),
              float(last_l) if last_l is not None else None, stop]:
        if x is not None and x < close and x not in dn_chain:
            dn_chain.append(x)
    dn_chain.sort(reverse=True)
    deeper = dn_t if (bear_pat is not None and dn_t is not None and dn_t < close) else next_s
    if up_chain:
        s6 += f"上方首压 {up_chain[0]:.2f}"
        if len(up_chain) > 1:
            s6 += f"，放量站上则反抽看 {up_chain[1]:.2f}"
        s6 += "；"
    if dn_chain:
        s6 += f"下方首撑 {dn_chain[0]:.2f}"
        if len(dn_chain) > 1:
            s6 += f"，失守看 {dn_chain[-1]:.2f}"
            if deeper is not None and deeper < dn_chain[-1]:
                s6 += f"，收盘跌破 {dn_chain[-1]:.2f} 则下看 {deeper:.2f}"
        elif deeper is not None and deeper < dn_chain[0]:
            s6 += f"，收盘跌破则下看 {deeper:.2f}"
        s6 += "。"
    if trend_dir < 0:
        s6 += "空头排列未变，反弹以减压看待，缩量冲高勿追；出现周线级止跌信号（放量长下影/MACD底背离金叉）前，仓位以观望为主。"
    elif trend_dir > 0:
        s6 += "多头排列未破，回踩支撑区以机会看待，缩量企稳可逢低布局，放量跌破支撑则纪律退出。"
    else:
        s6 += "方向未明，以区间思路应对，突破/跌破上述关键位再顺势跟进。"

    return {
        "annotations": annotations,
        "summary": {
            "trend": trend_txt,
            "structure": struct_txt,
            "momentum": momentum_txt,
            "volume": volume_txt,
            "scale_background": scale["background"],
            "scale_trade": scale["trade"],
            "scale_small": scale["small"],
            "key_supports": supports,
            "key_resistances": resistances,
            "target_price": round(target, 2) if target is not None else None,
            "target_source": target_src or None,
            "stop_loss": stop,
            "stop_source": stop_src or None,
            "risk_reward": rr,
            "outlook_text": s1 + s2 + s3 + s4 + s5 + s6,
        },
    }


if __name__ == "__main__":
    from . import db

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = loader(ts, start="2023-01-01")
        r = analyze(d)
        stars = sum(1 for a in r["annotations"] if a["star"])
        print(f"{ts}: annotations={len(r['annotations'])} star={stars}")
        print("  outlook:", r["summary"]["outlook_text"][:120], "...")
    print("analysis 自检通过")
