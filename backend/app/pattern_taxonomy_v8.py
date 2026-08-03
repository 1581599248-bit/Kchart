"""方向化形态命名与结构冲突裁决。

原则：
- 旗形、楔形、三角形、矩形直接表达看涨/看跌方向；
- 未突破矩形不进入主图；
- 同一区间内，已确认顶部/底部反转优先于矩形和三角整理，避免M顶被箱体覆盖。
"""
from __future__ import annotations

from typing import Iterable

_DIRECTIONAL = {
    "bull_flag": ("bull_flag_directional", "牛旗形"),
    "bear_flag": ("bear_flag_directional", "熊旗形"),
    # 上升楔形通常为看跌收敛；下降楔形通常为看涨收敛。
    "rising_wedge": ("bear_wedge_directional", "熊楔形"),
    "falling_wedge": ("bull_wedge_directional", "牛楔形"),
    "asc_triangle": ("bullish_triangle_directional", "看涨三角形"),
    "ascending_triangle": ("bullish_triangle_directional", "看涨三角形"),
    "desc_triangle": ("bearish_triangle_directional", "看跌三角形"),
    "descending_triangle": ("bearish_triangle_directional", "看跌三角形"),
    "sym_triangle": ("symmetric_triangle_directional", "对称三角形"),
    "symmetric_triangle": ("symmetric_triangle_directional", "对称三角形"),
}

_REVERSAL_KINDS = {
    "double_top", "head_shoulders_top", "triple_top",
    "double_bottom", "head_shoulders_bottom", "triple_bottom",
    "arc_top", "arc_bottom",
}

# 这些整理形态与已确认反转结构高度重叠时应被覆盖。
_CONSOLIDATION_SOURCES = {
    "box", "range_box", "asc_triangle", "ascending_triangle",
    "desc_triangle", "descending_triangle", "sym_triangle", "symmetric_triangle",
}


def _replace_note(note: str, old_name: str, new_name: str) -> str:
    text = str(note or "")
    if old_name and old_name in text:
        return text.replace(old_name, new_name, 1)
    return f"{new_name}：{text}" if text else new_name


def _normalize_one(event: dict) -> dict | None:
    item = dict(event)
    source_kind = str(item.get("kind") or "")
    old_name = str(item.get("name") or "")
    direction = str(item.get("direction") or "range")
    confirm_idx = item.get("confirm_idx")

    item["source_kind"] = source_kind

    if source_kind in ("box", "range_box"):
        # 没有方向确认的箱体只是候选区间，不作为经典形态贴到K线上。
        if confirm_idx is None or direction not in ("bull", "bear"):
            return None
        if direction == "bull":
            item["kind"], item["name"] = "bull_rectangle", "牛矩形"
        else:
            item["kind"], item["name"] = "bear_rectangle", "熊矩形"
        item["note"] = _replace_note(str(item.get("note") or ""), old_name, item["name"])
        return item

    mapped = _DIRECTIONAL.get(source_kind)
    if mapped:
        item["kind"], item["name"] = mapped
        item["note"] = _replace_note(str(item.get("note") or ""), old_name, item["name"])
    return item


def _span(event: dict) -> tuple[int, int]:
    start = int(event.get("start_idx", 0))
    end = int(event.get("end_idx", start))
    return min(start, end), max(start, end)


def _overlap_ratio(a: dict, b: dict) -> float:
    a0, a1 = _span(a)
    b0, b1 = _span(b)
    intersection = max(0, min(a1, b1) - max(a0, b0) + 1)
    shorter = max(1, min(a1 - a0 + 1, b1 - b0 + 1))
    return intersection / shorter


def _same_structure_story(reversal: dict, consolidation: dict) -> bool:
    if _overlap_ratio(reversal, consolidation) >= 0.50:
        return True
    rc = reversal.get("confirm_idx")
    cc = consolidation.get("confirm_idx")
    if rc is None or cc is None:
        return False
    # 确认时间很近且区间有交集，也视为同一段走势的竞争解释。
    r0, r1 = _span(reversal)
    c0, c1 = _span(consolidation)
    intersects = min(r1, c1) >= max(r0, c0)
    return intersects and abs(int(rc) - int(cc)) <= 30


def apply_pattern_taxonomy(events: Iterable[dict]) -> list[dict]:
    """统一方向化命名，并执行反转优先的冲突裁决。"""
    normalized = [item for event in events if (item := _normalize_one(event)) is not None]
    reversals = [
        event for event in normalized
        if str(event.get("source_kind") or event.get("kind")) in _REVERSAL_KINDS
        and event.get("confirm_idx") is not None
    ]

    out: list[dict] = []
    for event in normalized:
        source_kind = str(event.get("source_kind") or event.get("kind"))
        if source_kind in _CONSOLIDATION_SOURCES:
            if any(_same_structure_story(reversal, event) for reversal in reversals):
                continue
        out.append(event)

    return sorted(
        out,
        key=lambda event: int(event.get("confirm_idx") or event.get("end_idx", 0)),
    )
