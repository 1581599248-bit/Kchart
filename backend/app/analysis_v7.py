"""推背图 v7.4 safe mode：撤下未经真实样本验证的形态与交叉信号。

主图暂时只保留：
- 原版谐波完成D点与进入PRZ；
- 大结构回撤的0.5/0.618触达标签。

自动经典形态、结构失效以及EMA/MACD/RSI买卖标签在重建和真实样本外
验证完成前不进入主图。指标原始序列仍由接口正常返回，用户可手动开关查看。
"""
from __future__ import annotations

from . import analysis_v5 as base
from . import fibonacci_history
from . import harmonics_history
from . import indicators
from . import pivots as piv_mod

ANALYSIS_VERSION = "analysis_v7.4-safe"


def _safe_summary(df, pivots) -> dict:
    summary = base._summary(df, pivots, [])
    summary["structure"] = "自动形态识别重构中，暂不输出"
    old = str(summary.get("outlook_text") or "")
    marker = "结构："
    if marker in old:
        before, rest = old.split(marker, 1)
        if "。" in rest:
            _, after = rest.split("。", 1)
            summary["outlook_text"] = before + marker + summary["structure"] + "。" + after
    return summary


def analyze(df, timeframe: str = "1d") -> dict:
    del timeframe
    d = df if "DIF" in df.columns else indicators.compute_all(df)
    d = d.reset_index(drop=True)
    if len(d) < 60:
        return {"annotations": [], "summary": {}}

    pivots = piv_mod.find_pivots(d)
    annotations = (
        fibonacci_history.find_fibonacci_touches(d, pivots)
        + harmonics_history.find_harmonic_annotations(d, pivots)
    )
    annotations = base._density(annotations)

    clean: list[dict] = []
    for raw in annotations:
        event = dict(raw)
        # Safe mode硬门：禁止任何形态、结构失效或指标买卖标签混入主图。
        if event.get("kind") not in {"fibonacci", "harmonic"}:
            continue
        label = str(event.get("label") or "")
        if label in {"结构失效", "EMA金叉", "EMA死叉", "MACD金叉", "MACD死叉"}:
            continue
        event["label"] = label if event.get("kind") == "fibonacci" else label[:8]
        event.pop("_score", None)
        event.pop("_grp", None)
        clean.append(event)

    return {
        "annotations": clean,
        "summary": _safe_summary(d, pivots),
        "diagnostics": {
            "analysis_version": ANALYSIS_VERSION,
            "bars_scanned": len(d),
            "patterns_detected": 0,
            "patterns_displayed": 0,
            "historical_traces": 0,
            "annotations": len(clean),
            "safe_mode": True,
            "causal": True,
        },
    }
