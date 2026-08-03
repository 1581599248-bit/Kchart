"""大级别结构显示筛选回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import pattern_display_v8, pattern_taxonomy_v8


def _frame(n: int = 420) -> pd.DataFrame:
    x = np.linspace(0, 12 * np.pi, n)
    close = 100 + 0.08 * np.arange(n) + 8 * np.sin(x)
    return pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "open": close,
        "high": close + 1.5,
        "low": close - 1.5,
        "close": close,
        "vol": np.full(n, 1_000_000.0),
        "ATR14": np.full(n, 1.2),
    })


def _event(kind: str, name: str, direction: str, start: int, end: int,
           confirm: int | None, score: int = 80) -> dict:
    return {
        "kind": kind,
        "name": name,
        "direction": direction,
        "start_idx": start,
        "end_idx": end,
        "confirm_idx": confirm,
        "key_levels": {},
        "score": score,
        "star": False,
        "note": name,
        "trace": [],
        "active": True,
    }


def validate_small_and_unconfirmed_are_hidden() -> None:
    df = _frame()
    small = _event("double_top", "M顶", "bear", 50, 85, 90, 95)
    building = _event("double_top", "M顶", "bear", 50, 150, None, 95)
    trendline = _event("trendline_break", "趋势突破", "bull", 30, 180, 185, 90)
    result = pattern_display_v8.select_display_patterns(df, [small, building, trendline])
    assert result == [], result


def validate_one_shape_per_region() -> None:
    df = _frame()
    m_top = _event("double_top", "M顶", "bear", 40, 150, 160, 96)
    rectangle = _event("bear_rectangle", "熊矩形", "bear", 70, 155, 160, 82)
    triangle = _event("bearish_triangle_directional", "看跌三角形", "bear", 65, 150, 158, 84)
    wedge = _event("bear_wedge_directional", "熊楔形", "bear", 72, 148, 156, 86)
    result = pattern_display_v8.select_display_patterns(df, [rectangle, triangle, wedge, m_top])
    assert len(result) == 1, result
    assert result[0]["kind"] == "double_top", result
    assert result[0]["name"] == "M顶", result


def validate_separate_major_regions_survive() -> None:
    df = _frame()
    first = _event("double_top", "M顶", "bear", 25, 120, 130, 96)
    second = _event("bull_flag_directional", "牛旗形", "bull", 250, 320, 330, 82)
    result = pattern_display_v8.select_display_patterns(df, [first, second])
    assert [event["name"] for event in result] == ["M顶", "牛旗形"], result
    assert all(event.get("display_major") is True for event in result)


def validate_taxonomy_and_rectangle_suppression() -> None:
    events = [
        _event("bull_flag", "上升旗形", "bull", 10, 70, 75),
        _event("bear_flag", "下跌旗形", "bear", 90, 150, 155),
        _event("rising_wedge", "上升楔形", "bear", 170, 230, 235),
        _event("falling_wedge", "下降楔形", "bull", 250, 310, 315),
        _event("asc_triangle", "上升三角形", "bull", 20, 90, 95),
        _event("desc_triangle", "下降三角形", "bear", 110, 180, 185),
        _event("box", "箱体震荡", "range", 200, 280, None),
    ]
    result = pattern_taxonomy_v8.apply_pattern_taxonomy(events)
    names = {event["name"] for event in result}
    assert "牛旗形" in names and "熊旗形" in names, names
    assert "牛楔形" in names and "熊楔形" in names, names
    assert "看涨三角形" in names and "看跌三角形" in names, names
    assert "箱体震荡" not in names, names


if __name__ == "__main__":
    validate_small_and_unconfirmed_are_hidden()
    validate_one_shape_per_region()
    validate_separate_major_regions_survive()
    validate_taxonomy_and_rectangle_suppression()
    print("pattern display v8 validation OK")
