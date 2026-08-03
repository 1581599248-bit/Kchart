"""原版谐波展示适配器。

恢复此前简洁口径：
- 已完成谐波只在 D 点标注；
- 现价进入 PRZ 时单独提示并显示原版金色区域；
- 不再额外输出“谐波确认 / 谐波失效 / 潜在PRZ”生命周期标签。

底层识别仍直接使用 harmonics.py 的原始 XABCD 比率与右侧确认规则。
"""
from __future__ import annotations

import pandas as pd

from . import harmonics as base

RECENT_D_STAR_BARS = 40

_SHORT_NAME = {
    "Gartley": "加特利",
    "Bat": "蝙蝠",
    "Butterfly": "蝴蝶",
    "Crab": "螃蟹",
    "Shark": "鲨鱼",
}


def _date(df: pd.DataFrame, idx: int) -> str:
    return str(df["trade_date"].iloc[int(idx)])[:10]


def _name(name: str) -> str:
    return _SHORT_NAME.get(str(name), str(name))


def _zone(df: pd.DataFrame, event: dict, lo: float, hi: float) -> list[dict]:
    return [{
        "t1": _date(df, int(event["x"]["idx"])),
        "t2": _date(df, len(df) - 1),
        "top": float(hi),
        "bottom": float(lo),
        "color": "rgba(240,185,11,0.08)",
    }]


def _annotation(df: pd.DataFrame, idx: int, price: float, label: str,
                direction: str, detail: str, score: int,
                zones: list[dict], star: bool) -> dict:
    return {
        "bar_idx": int(idx),
        "price": round(float(price), 4),
        "kind": "harmonic",
        "label": label,
        "direction": direction,
        "star": bool(star),
        "detail": detail,
        "lines": [],
        "zones": zones,
        "polylines": [],
        "active": True,
        "_score": int(score),
        "_grp": f"harmonic:{label}:{idx}",
    }


def find_harmonic_annotations(df: pd.DataFrame, pivots: pd.DataFrame) -> list[dict]:
    """按原版口径输出 D 点和当前 PRZ 两类标注。"""
    asof = len(df) - 1
    close = float(df["close"].iloc[-1])
    events = base.find_xabcd(pivots, asof_idx=asof)
    out: list[dict] = []

    for event in events:
        lo = min(float(event["prz_low"]), float(event["prz_high"]))
        hi = max(float(event["prz_low"]), float(event["prz_high"]))
        in_prz = lo <= close <= hi
        zones = _zone(df, event, lo, hi)
        short = _name(str(event["name"]))

        if event.get("completed") and event.get("d"):
            d_idx = int(event["d"]["idx"])
            out.append(_annotation(
                df=df,
                idx=d_idx,
                price=float(event["d"]["price"]),
                label=f"{short}D点",
                direction=str(event["direction"]),
                detail=str(event["note"]),
                score=65,
                zones=zones,
                star=bool(in_prz or d_idx >= asof - RECENT_D_STAR_BARS),
            ))

        # 原版逻辑：构筑中的远端投影不上图；只有现价真正进入 PRZ 才提示。
        if in_prz:
            direction = str(event["direction"])
            out.append(_annotation(
                df=df,
                idx=asof,
                price=close,
                label=f"进入{short}PRZ",
                direction=direction,
                detail=(
                    f"现价 {close:.2f} 落入 {event['name']} 谐波反转区（PRZ）"
                    f"{lo:.2f}~{hi:.2f}，"
                    f"{'潜在反转买入区' if direction == 'bull' else '潜在反转卖出区'}"
                ),
                score=85,
                zones=zones,
                star=True,
            ))

    out.sort(key=lambda item: (int(item["bar_idx"]), str(item["label"])))
    return out
