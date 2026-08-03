"""推背图 v11：指数仅恢复经真实盲测的M顶/W底，其他信号保持安全模式。

生产主图规则：
- 指数：M顶/W底描摹 + 颈线确认、原版谐波、主波段0.5/0.618；
- 个股：原版谐波、主波段0.5/0.618；
- 不输出结构失效、EMA/MACD/RSI买卖标签及其他未验证形态。
"""
from __future__ import annotations

from . import analysis_v5 as base
from . import fibonacci_history
from . import harmonics_history
from . import index_reversals_v17
from . import indicators
from . import pivots as piv_mod

ANALYSIS_VERSION = "analysis_v11.0-index-reversal"
_BLOCKED_LABELS = {
    "结构失效", "EMA金叉", "EMA死叉", "MACD金叉", "MACD死叉",
    "RSI超买", "RSI超卖",
}


def _date(df, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _replace_structure_text(summary: dict, structure: str) -> dict:
    summary["structure"] = structure
    old = str(summary.get("outlook_text") or "")
    marker = "结构："
    if marker in old:
        before, rest = old.split(marker, 1)
        if "。" in rest:
            _, after = rest.split("。", 1)
            summary["outlook_text"] = before + marker + structure + "。" + after
    return summary


def _summary(df, pivots, reversals: list[dict], asset_kind: str) -> dict:
    summary = base._summary(df, pivots, [])
    if asset_kind != "index":
        return _replace_structure_text(summary, "自动形态识别重构中，暂不输出")
    if not reversals:
        return _replace_structure_text(summary, "当前无已确认的大级别M顶/W底")
    latest = max(reversals, key=lambda event: int(event["confirm_idx"]))
    action = "跌破颈线" if latest["direction"] == "bear" else "突破颈线"
    structure = f"{latest['name']}，{_date(df, latest['confirm_idx'])}{action}确认"
    return _replace_structure_text(summary, structure)


def _reversal_annotations(df, reversals: list[dict]) -> list[dict]:
    """只输出形态描摹和颈线确认；明确禁止结构失效事件。"""
    annotations: list[dict] = []
    for event in reversals:
        direction = str(event["direction"])
        end_idx = int(event["end_idx"])
        confirm_idx = int(event["confirm_idx"])
        formation_price = float(
            df["high" if direction == "bear" else "low"].iloc[end_idx]
        )
        confirm_price = float(
            df["high" if direction == "bear" else "low"].iloc[confirm_idx]
        )
        detail = str(event.get("note") or "")

        # 折线末端显示一次“M顶/W底”，不生成箭头，避免遮挡K线。
        annotations.append({
            "bar_idx": end_idx,
            "price": formation_price,
            "kind": "pattern",
            "label": str(event["name"]),
            "direction": direction,
            "star": False,
            "detail": detail,
            "lines": [],
            "zones": [],
            "polylines": event.get("trace") or [],
            "trace_only": True,
            "history_label": True,
            "active": True,
            "_score": int(event.get("score", 0)),
            "_grp": f"index_reversal_trace:{event['kind']}:{end_idx}",
        })

        break_label = "跌破颈线" if direction == "bear" else "突破颈线"
        annotations.append({
            "bar_idx": confirm_idx,
            "price": confirm_price,
            "kind": "pattern",
            "label": break_label,
            "direction": direction,
            "star": True,
            "detail": detail,
            "lines": [],
            "zones": [],
            "polylines": [],
            "active": True,
            "_score": int(event.get("score", 0)) + 5,
            "_grp": f"index_reversal_confirm:{event['kind']}:{confirm_idx}",
        })
    return annotations


def analyze(df, timeframe: str = "1d", asset_kind: str = "equity") -> dict:
    del timeframe
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 60:
        return {"annotations": [], "summary": {}}

    kind = "index" if str(asset_kind).lower() == "index" else "equity"
    pivots = piv_mod.find_pivots(d)
    reversals = index_reversals_v17.find_index_reversals(d) if kind == "index" else []

    annotations = (
        _reversal_annotations(d, reversals)
        + fibonacci_history.find_fibonacci_touches(d, pivots)
        + harmonics_history.find_harmonic_annotations(d, pivots)
    )
    annotations = base._density(annotations)

    clean: list[dict] = []
    allowed_kinds = {"fibonacci", "harmonic", "pattern"}
    for raw in annotations:
        event = dict(raw)
        if event.get("kind") not in allowed_kinds:
            continue
        if kind != "index" and event.get("kind") == "pattern":
            continue
        label = str(event.get("label") or "")
        if label in _BLOCKED_LABELS:
            continue
        event["label"] = label if event.get("kind") == "fibonacci" else label[:8]
        event.pop("_score", None)
        event.pop("_grp", None)
        clean.append(event)

    return {
        "annotations": clean,
        "summary": _summary(d, pivots, reversals, kind),
        "diagnostics": {
            "analysis_version": ANALYSIS_VERSION,
            "asset_kind": kind,
            "bars_scanned": len(d),
            "index_reversals": len(reversals),
            "patterns_displayed": len(reversals),
            "annotations": len(clean),
            "mode": "index_reversal_only" if kind == "index" else "safe_equity",
            "causal": True,
        },
    }
