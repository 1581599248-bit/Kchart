"""推背图 v5：全历史结构、低噪声指标、条件式前瞻。

从2020年展示起点开始完整扫描。所有结构、背离、谐波只在右侧确认后生效；
图上使用短标签，完整解释放hover与分析卡。结论只描述触发条件，不预设涨跌。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fibonacci as fib_mod
from . import fibonacci_history
from . import harmonics_history
from . import indicators
from . import patterns as base_patterns
from . import patterns_ext
from . import pivots as piv_mod
from . import signals_v5

ANALYSIS_VERSION = "analysis_v5.0"
PATTERN_RIGHT_CONFIRM = piv_mod.RIGHT

_SHORT_NAMES = {
    "double_bottom": "W底",
    "double_top": "M顶",
    "triple_bottom": "三重底",
    "triple_top": "三重顶",
    "head_shoulders_bottom": "头肩底",
    "head_shoulders_top": "头肩顶",
    "arc_bottom": "圆弧底",
    "arc_top": "圆弧顶",
    "bull_flag": "上升旗形",
    "bear_flag": "下降旗形",
    "rising_wedge": "上升楔形",
    "falling_wedge": "下降楔形",
    "symmetric_triangle": "对称三角",
    "ascending_triangle": "上升三角",
    "descending_triangle": "下降三角",
    "broadening_triangle": "扩散三角",
    "box": "箱体",
    "range_box": "箱体",
    "trendline_break": "趋势突破",
}
_MAJOR = {
    "double_bottom", "double_top", "triple_bottom", "triple_top",
    "head_shoulders_bottom", "head_shoulders_top", "arc_bottom", "arc_top",
}


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _safe_name(event: dict) -> str:
    name = _SHORT_NAMES.get(str(event.get("kind")), str(event.get("name") or "结构"))
    return name[:8]


def _dedupe_patterns(events: list[dict]) -> list[dict]:
    """同种结构、相近末端只保留确认更充分的一项。"""
    events = sorted(events, key=lambda e: (
        str(e.get("kind")), int(e.get("end_idx", 0)),
        e.get("confirm_idx") is not None, int(e.get("score", 0)),
    ))
    out: list[dict] = []
    for event in events:
        dup_idx = None
        for i in range(len(out) - 1, -1, -1):
            old = out[i]
            if old.get("kind") != event.get("kind"):
                continue
            if abs(int(old.get("end_idx", 0)) - int(event.get("end_idx", 0))) <= 12:
                dup_idx = i
                break
        if dup_idx is None:
            out.append(event)
            continue
        old = out[dup_idx]
        old_rank = (old.get("confirm_idx") is not None, int(old.get("score", 0)))
        new_rank = (event.get("confirm_idx") is not None, int(event.get("score", 0)))
        if new_rank >= old_rank:
            out[dup_idx] = event
    return sorted(out, key=lambda e: int(e.get("confirm_idx") or e.get("end_idx", 0)))


def _invalidation(df: pd.DataFrame, event: dict) -> int | None:
    ci = event.get("confirm_idx")
    if ci is None:
        return None
    levels = event.get("key_levels") or {}
    inv = levels.get("invalidation")
    if not isinstance(inv, (int, float)):
        return None
    close = df["close"].to_numpy(dtype=float)
    for i in range(int(ci) + 1, len(df)):
        if event.get("direction") == "bull" and close[i] < float(inv):
            return i
        if event.get("direction") == "bear" and close[i] > float(inv):
            return i
    return None


def _pattern_lines(df: pd.DataFrame, event: dict, until: int) -> list[dict]:
    levels = event.get("key_levels") or {}
    start = int(event.get("start_idx", 0))
    lines: list[dict] = []
    neck = levels.get("neckline")
    if isinstance(neck, (int, float)):
        lines.append({
            "t1": _date(df, start), "p1": float(neck),
            "t2": _date(df, until), "p2": float(neck),
            "style": "dashed",
        })
    for key in ("upper", "lower", "trendline"):
        value = levels.get(key)
        if isinstance(value, (int, float)):
            lines.append({
                "t1": _date(df, int(event.get("end_idx", start))), "p1": float(value),
                "t2": _date(df, until), "p2": float(value),
                "style": "dashed",
            })
    return lines


def _pattern_annotations(df: pd.DataFrame, pivots: pd.DataFrame,
                         patterns: list[dict]) -> tuple[list[dict], list[dict]]:
    pivot_confirm = {
        int(row.idx): int(row.confirmed_at_idx)
        for row in pivots.itertuples()
    }
    annotations: list[dict] = []
    enriched: list[dict] = []

    for event in patterns:
        event = dict(event)
        end_idx = int(event.get("end_idx", 0))
        available_idx = pivot_confirm.get(end_idx, min(len(df) - 1, end_idx + PATTERN_RIGHT_CONFIRM))
        confirm_idx = event.get("confirm_idx")
        fail_idx = _invalidation(df, event)
        confirmed = confirm_idx is not None
        active = bool(event.get("active", True)) and fail_idx is None
        if not confirmed and len(df) - 1 - available_idx > 80:
            active = False
        status = "失效" if fail_idx is not None else "已确认" if confirmed else "构筑中"
        event.update({"status": status, "active": active, "fail_idx": fail_idx})
        enriched.append(event)

        name = _safe_name(event)
        direction = str(event.get("direction") or "range")
        if direction not in ("bull", "bear"):
            direction = "range"
        note = str(event.get("note") or "")
        detail = f"{name}｜{status}。{note}"
        trace = event.get("trace") or []
        formation_price = float(df["low" if direction == "bull" else "high"].iloc[available_idx])
        annotations.append({
            "bar_idx": available_idx,
            "price": formation_price,
            "kind": "pattern",
            "label": name,
            "direction": direction,
            "star": False,
            "detail": detail,
            "lines": _pattern_lines(df, event, int(confirm_idx or len(df)-1)),
            "zones": [],
            "polylines": trace,
            "active": active,
            "_score": int(event.get("score", 50)),
            "_grp": f"pattern:{event.get('kind')}:{end_idx}",
        })

        if confirmed:
            has_neckline = isinstance((event.get("key_levels") or {}).get("neckline"), (int, float))
            if event.get("kind") == "trendline_break":
                break_label = str(event.get("name") or ("突破趋势" if direction == "bull" else "跌破趋势"))[:8]
            elif has_neckline:
                break_label = "突破颈线" if direction == "bull" else "跌破颈线"
            else:
                break_label = "向上突破" if direction == "bull" else "向下跌破"
            ci = int(confirm_idx)
            annotations.append({
                "bar_idx": ci,
                "price": float(df["low" if direction == "bull" else "high"].iloc[ci]),
                "kind": "pattern",
                "label": break_label,
                "direction": direction,
                "star": event.get("kind") in _MAJOR,
                "detail": f"{_date(df, ci)} {name}{break_label}，结构由构筑转为确认。",
                "lines": _pattern_lines(df, event, ci),
                "zones": [],
                "polylines": [],
                "active": active,
                "_score": int(event.get("score", 70)) + 5,
                "_grp": f"pattern_confirm:{event.get('kind')}:{ci}",
            })

        if fail_idx is not None:
            fi = int(fail_idx)
            annotations.append({
                "bar_idx": fi,
                "price": float(df["close"].iloc[fi]),
                "kind": "pattern",
                "label": "结构失效",
                "direction": "bear" if direction == "bull" else "bull",
                "star": False,
                "detail": f"{_date(df, fi)} {name}越过失效位，原结构假设终止。",
                "lines": [], "zones": [], "polylines": [],
                "active": False, "_score": 62,
                "_grp": f"pattern_fail:{event.get('kind')}:{fi}",
            })
    return annotations, enriched


def _density(events: list[dict]) -> list[dict]:
    """结构与EMA完整保留；其他同类近邻信号只留更重要的一项。"""
    events = sorted(events, key=lambda e: (int(e.get("bar_idx", 0)), -int(e.get("_score", 0))))
    kept: list[dict] = []
    for event in events:
        label = str(event.get("label") or "")
        if label in ("EMA金叉", "EMA死叉") or event.get("kind") == "pattern":
            kept.append(event)
            continue
        window = 8 if event.get("kind") == "fibonacci" else 12 if event.get("kind") == "harmonic" else 10
        conflict = [
            old for old in kept
            if old.get("kind") == event.get("kind")
            and abs(int(old.get("bar_idx", 0)) - int(event.get("bar_idx", 0))) <= window
            and old.get("direction") == event.get("direction")
        ]
        if not conflict:
            kept.append(event)
            continue
        best = max(conflict + [event], key=lambda x: (bool(x.get("star")), int(x.get("_score", 0))))
        if best is event:
            for old in conflict:
                kept.remove(old)
            kept.append(event)
    return sorted(kept, key=lambda e: int(e.get("bar_idx", 0)))


def _unique_levels(values: list[float], close: float, side: str) -> list[float]:
    vals = [float(x) for x in values if np.isfinite(x) and (x < close if side == "support" else x > close)]
    vals.sort(reverse=side == "support")
    out: list[float] = []
    for value in vals:
        if any(abs(value / old - 1.0) < 0.008 for old in out if old != 0):
            continue
        out.append(value)
        if len(out) >= 3:
            break
    return [round(x, 2) for x in out]


def _summary(df: pd.DataFrame, pivots: pd.DataFrame, patterns: list[dict]) -> dict:
    close = float(df["close"].iloc[-1])
    ema20 = float(df["EMA20"].iloc[-1])
    ema60 = float(df["EMA60"].iloc[-1])
    adx = float(df["ADX"].iloc[-1]) if np.isfinite(df["ADX"].iloc[-1]) else 0.0
    rsi = float(df["RSI6"].iloc[-1])
    dif = float(df["DIF"].iloc[-1])
    dea = float(df["DEA"].iloc[-1])

    if close > ema20 > ema60:
        trend = f"多头排列，ADX={adx:.0f}"
        trend_dir = 1
    elif close < ema20 < ema60:
        trend = f"空头排列，ADX={adx:.0f}"
        trend_dir = -1
    else:
        trend = f"均线交错，ADX={adx:.0f}"
        trend_dir = 0

    recent = [
        p for p in patterns
        if p.get("active") and len(df) - 1 - int(p.get("confirm_idx") or p.get("end_idx", 0)) <= 180
    ]
    latest = max(recent, key=lambda p: int(p.get("confirm_idx") or p.get("end_idx", 0))) if recent else None
    structure = (
        f"{_safe_name(latest)}，{latest.get('status')}"
        if latest else "当前无清晰已确认经典结构"
    )

    rsi_state = "超买" if rsi > 80 else "超卖" if rsi < 20 else "中性"
    macd_state = "金叉侧" if dif > dea else "死叉侧"
    momentum = f"RSI6={rsi:.1f}（{rsi_state}）；MACD处于{macd_state}"

    v = df["vol"].to_numpy(dtype=float)
    v5 = float(np.nanmean(v[-5:]))
    v20 = float(np.nanmean(v[-20:]))
    vr = v5 / v20 if v20 > 0 else 1.0
    volume = f"5日/20日均量={vr:.2f}（{'放量' if vr>1.2 else '缩量' if vr<0.8 else '平稳'}）"

    lows = pivots[pivots["kind"] == "L"].tail(8)["price"].astype(float).tolist()
    highs = pivots[pivots["kind"] == "H"].tail(8)["price"].astype(float).tolist()
    supports = lows + [ema20, ema60]
    resistances = highs + [ema20, ema60]
    if latest:
        for key, value in (latest.get("key_levels") or {}).items():
            if not isinstance(value, (int, float)):
                continue
            if key in ("invalidation", "lower", "neckline"):
                supports.append(float(value))
            if key in ("upper", "neckline", "measure_target", "measure_target_up"):
                resistances.append(float(value))

    fib = fib_mod.fib_analysis(df, pivots)
    if fib:
        for value in fib["levels"].values():
            supports.append(float(value)); resistances.append(float(value))
    key_supports = _unique_levels(supports, close, "support")
    key_resistances = _unique_levels(resistances, close, "resistance")
    nearest_s = key_supports[0] if key_supports else None
    nearest_r = key_resistances[0] if key_resistances else None

    if trend_dir >= 0 and nearest_r is not None:
        up_text = f"若收盘有效突破{nearest_r:.2f}，再观察更高阻力；"
    else:
        up_text = f"若重新站上EMA20（{ema20:.2f}）并突破最近压力，结构才改善；"
    if nearest_s is not None:
        down_text = f"若跌破{nearest_s:.2f}，当前结构转弱或失效。"
    else:
        down_text = f"若持续运行在EMA60（{ema60:.2f}）下方，弱势延续。"

    outlook = (
        f"截至{_date(df, len(df)-1)}，{trend}。"
        f"结构：{structure}。"
        f"{up_text}{down_text}"
        f"动量：{momentum}；量能：{volume}。所有结论均为条件推演，不预设方向。"
    )
    rr = None
    if nearest_s is not None and nearest_r is not None and close > nearest_s:
        rr = round((nearest_r - close) / (close - nearest_s), 2)
        if rr < 0 or rr > 10:
            rr = None
    return {
        "trend": trend,
        "structure": structure,
        "momentum": momentum,
        "volume": volume,
        "key_supports": key_supports,
        "key_resistances": key_resistances,
        "target_price": nearest_r,
        "target_source": "突破后观察位" if nearest_r is not None else "",
        "stop_loss": nearest_s,
        "stop_source": "结构失效参考位" if nearest_s is not None else "",
        "risk_reward": rr,
        "outlook_text": outlook,
    }


def analyze(df: pd.DataFrame, timeframe: str = "1d") -> dict:
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 60:
        return {"annotations": [], "summary": {}}

    pivots = piv_mod.find_pivots(d)
    base = base_patterns.find_patterns(d, pivots, asof_bar=len(d)-1, timeframe=timeframe)
    extra = patterns_ext.find_patterns_ext(d, pivots, timeframe=timeframe)
    patterns = _dedupe_patterns(base + extra)
    pattern_annotations, patterns_enriched = _pattern_annotations(d, pivots, patterns)

    annotations = (
        pattern_annotations
        + signals_v5.all_signals(d)
        + fibonacci_history.find_fibonacci_touches(d, pivots)
        + harmonics_history.find_harmonic_annotations(d, pivots)
    )
    annotations = _density(annotations)
    # 清理内部排序字段；图上标签一律保持短文本。
    for event in annotations:
        event["label"] = str(event.get("label") or "")[:8]
        event.pop("_score", None)
        event.pop("_grp", None)

    return {
        "annotations": annotations,
        "summary": _summary(d, pivots, patterns_enriched),
        "diagnostics": {
            "analysis_version": ANALYSIS_VERSION,
            "bars_scanned": len(d),
            "patterns": len(patterns_enriched),
            "annotations": len(annotations),
            "causal": True,
        },
    }
