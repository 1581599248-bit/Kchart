"""Fast chart bundle service.

将K线、指标、推背图组合成单次读取对象。
后续由main.py接入，避免用户请求时重复计算。
"""
from __future__ import annotations

from . import chart_cache


def get_chart(code: str):
    """返回已经预计算的完整图表数据。"""
    return chart_cache.get(code)


def save_chart(code: str, kline, indicators, analysis, meta=None):
    payload = {
        "code": code,
        "kline": kline,
        "indicators": indicators,
        "analysis": analysis,
        "meta": meta or {},
    }
    return chart_cache.save(code, payload)
