"""谐波形态 XABCD（ARCHITECTURE.md 第3节 harmonics.py 规范）。

**仅展示用途，零打分权重**（无同行评审证据，见 MODEL_DESIGN.md §2）。
本模块输出只许进入 /api/analysis 的图上标注与文字分析，禁止被 scoring.py 引用。

取最近 5 个交替 pivot 组成 X-A-B-C-D，按各形态经典比率校验（容差 ±5%）：
- Gartley: B=0.618XA, D=0.786XA, BC=0.382~0.886AB
- Bat:     B=0.382~0.5XA, D=0.886XA
- Butterfly: D=1.272XA 扩展（B 约 0.786XA）
- Crab:    D=1.618XA（B=0.382~0.618XA）
- Shark:   D=0.886~1.13XC 区
D 点必须已右侧确认才算 completed；未完成（当前仅 XABC 四点）时给出潜在 D 目标区（PRZ）。

方向约定：X 为低点 → 看多谐波（D 点为潜在反转买入区）；X 为高点 → 看空。
"""
from __future__ import annotations

import pandas as pd

from . import pivots as piv_mod

RATIO_TOL = 0.05       # 比率容差 ±5%（相对）
MAX_WINDOWS = 8        # 只回看最近 8 个交替 pivot，控制事件数量
SCORE_COMPLETED = 50
SCORE_POTENTIAL = 30


def _close(a: float, target: float, tol: float = RATIO_TOL) -> bool:
    return abs(a - target) <= target * tol


def _within(a: float, lo: float, hi: float, tol: float = RATIO_TOL) -> bool:
    return lo * (1 - tol) <= a <= hi * (1 + tol)


def _ratios(pts: list[dict]):
    """5 个交替 pivot 的谐波比率。X 低为看多腿（XA 向上）。"""
    x, a, b, c, d = (p["price"] for p in pts)
    bullish = pts[0]["kind"] == "L"
    xa = abs(a - x)
    if xa <= 0:
        return None
    ab = abs(b - a)
    bc = abs(c - b)
    b_xa = ab / xa
    bc_ab = bc / ab if ab > 0 else 0.0
    if bullish:  # X低 A高：D 对 XA 的回撤/扩展以 A 为锚向下量
        d_xa = (a - d) / xa
        shark = (c - d) / abs(c - x) if c != x else 0.0
    else:        # X高 A低：D 以 A 为锚向上量
        d_xa = (d - a) / xa
        shark = (d - c) / abs(c - x) if c != x else 0.0
    return {"bullish": bullish, "b_xa": b_xa, "d_xa": d_xa, "bc_ab": bc_ab,
            "shark": shark, "xa": xa}


def _match(r: dict):
    """按比率匹配形态，返回 (name, d_ratio_for_prz, prz_ref) 或 None。优先级按稀有度。"""
    b, d, bc, sh = r["b_xa"], r["d_xa"], r["bc_ab"], r["shark"]
    if _close(d, 1.618) and _within(b, 0.382, 0.618):
        return ("Crab", 1.618, "XA")
    if _close(d, 1.272) and _within(b, 0.618, 0.886):
        return ("Butterfly", 1.272, "XA")
    if _within(sh, 0.886, 1.13) and not _close(d, 0.786) and not _close(d, 0.886):
        return ("Shark", None, "XC")
    if _close(d, 0.886) and _within(b, 0.382, 0.5):
        return ("Bat", 0.886, "XA")
    if _close(d, 0.786) and _close(b, 0.618) and _within(bc, 0.382, 0.886):
        return ("Gartley", 0.786, "XA")
    return None


def _prz_from_ratio(pts: list[dict], d_ratio: float, ref: str, bullish: bool):
    """由 D 目标比率（±5%）推 PRZ 价格区间（用于 completed 的校验带与未完成的投影）。"""
    x, a, c = pts[0]["price"], pts[1]["price"], pts[3]["price"]
    lo_r, hi_r = d_ratio * (1 - RATIO_TOL), d_ratio * (1 + RATIO_TOL)
    if bullish:
        base = a if ref == "XA" else c
        rng = abs(a - x) if ref == "XA" else abs(c - x)
        return base - hi_r * rng, base - lo_r * rng
    base = a if ref == "XA" else c
    rng = abs(a - x) if ref == "XA" else abs(c - x)
    return base + lo_r * rng, base + hi_r * rng


def _event(name, direction, pts5, d_info, prz_lo, prz_hi, completed, score, note):
    def _pt(p):
        return {"idx": int(p["idx"]), "price": round(float(p["price"]), 4)}
    x, a, b, c, d = pts5
    return {
        "name": name,
        "direction": direction,
        "x": _pt(x), "a": _pt(a), "b": _pt(b), "c": _pt(c),
        "d": _pt(d) if completed else None,
        "d_projected": d_info,
        "prz_low": round(float(prz_lo), 4),
        "prz_high": round(float(prz_hi), 4),
        "completed": bool(completed),
        "score": score,
        "note": note,
    }


def find_xabcd(pivots: pd.DataFrame, asof_idx: int | None = None) -> list[dict]:
    """谐波形态检测主入口（规范签名：find_xabcd(pivots) -> list[HarmonicEvent]）。

    - 输入 pivot 先交替化；逐 5 点滑动窗口校验形态比率（±5%）。
    - completed 判定：D pivot 的 confirmed_at_idx <= asof_idx（右侧确认），
      未确认/未形成的 D 一律 completed=False 并给出潜在 D 目标区（PRZ）。
    - 对当前进行中的 XABC（最近 4 点），若 B/BC 比率满足某形态前提，投影潜在 D 区。
    """
    ap = piv_mod.alternating(pivots).to_dict("records")
    if len(ap) < 4:
        return []
    if asof_idx is None:
        asof_idx = int(max(p["confirmed_at_idx"] for p in ap))
    events: list[dict] = []
    seen_names: set[str] = set()

    # ---- 已完成 5 点窗口（从最近往远扫，同名形态只保留最近一个）----
    tail = ap[-MAX_WINDOWS:]
    for e in range(len(tail) - 1, 3, -1):
        pts = tail[e - 4 : e + 1]
        r = _ratios(pts)
        if r is None:
            continue
        m = _match(r)
        if m is None or m[0] in seen_names:
            continue
        name, d_ratio, ref = m
        if name == "Shark":
            # Shark 的 D 目标是 0.886~1.13XC 整段区间（含 ±5% 容差），无单一比率
            x_p, c_p = pts[0]["price"], pts[3]["price"]
            rng = abs(c_p - x_p)
            lo_r, hi_r = 0.886 * (1 - RATIO_TOL), 1.13 * (1 + RATIO_TOL)
            if r["bullish"]:
                prz_lo, prz_hi = c_p - hi_r * rng, c_p - lo_r * rng
            else:
                prz_lo, prz_hi = c_p + lo_r * rng, c_p + hi_r * rng
        else:
            prz_lo, prz_hi = _prz_from_ratio(pts, d_ratio, ref, r["bullish"])
        d_pivot = pts[4]
        completed = int(d_pivot["confirmed_at_idx"]) <= asof_idx
        direction = "bull" if r["bullish"] else "bear"
        note = (f"{name} 形态{'已完成' if completed else 'D点待右侧确认'}："
                f"B={r['b_xa']:.3f}XA，D={r['d_xa']:.3f}XA，BC={r['bc_ab']:.3f}AB；"
                f"PRZ {min(prz_lo, prz_hi):.2f}~{max(prz_lo, prz_hi):.2f}")
        events.append(_event(name, direction, pts, None, prz_lo, prz_hi, completed,
                             SCORE_COMPLETED if completed else SCORE_POTENTIAL, note))
        seen_names.add(name)

    # ---- 进行中 XABC：投影潜在 D 区 ----
    if len(ap) >= 4 and "potential" not in seen_names:
        pts4 = ap[-4:]
        bullish = pts4[0]["kind"] == "L"
        x, a, b, c = (p["price"] for p in pts4)
        xa = abs(a - x)
        ab = abs(b - a)
        bc = abs(c - b)
        if xa > 0 and ab > 0:
            b_xa = ab / xa
            bc_ab = bc / ab
            cand = None
            if _close(b_xa, 0.618) and _within(bc_ab, 0.382, 0.886):
                cand = ("Gartley", 0.786)
            elif _within(b_xa, 0.382, 0.5) and _within(bc_ab, 0.382, 0.886):
                cand = ("Bat", 0.886)
            if cand and cand[0] not in seen_names:
                name, d_ratio = cand
                lo_r, hi_r = d_ratio * (1 - RATIO_TOL), d_ratio * (1 + RATIO_TOL)
                if bullish:
                    prz_lo, prz_hi = a - hi_r * xa, a - lo_r * xa
                else:
                    prz_lo, prz_hi = a + lo_r * xa, a + hi_r * xa
                note = (f"潜在 {name} 构筑中：XABC 已就位（B={b_xa:.3f}XA，BC={bc_ab:.3f}AB），"
                        f"潜在 D 目标区（PRZ）{min(prz_lo, prz_hi):.2f}~{max(prz_lo, prz_hi):.2f}，"
                        f"D 点右侧确认前不构成已完成形态")
                pts5 = pts4 + [pts4[3]]  # d 占位（completed=False 时不输出 d）
                events.append(_event(name, "bull" if bullish else "bear", pts5,
                                     {"ratio_of_XA": d_ratio,
                                      "zone": [round(min(prz_lo, prz_hi), 4), round(max(prz_lo, prz_hi), 4)]},
                                     prz_lo, prz_hi, False, SCORE_POTENTIAL, note))
    events.sort(key=lambda e: e["x"]["idx"])
    return events


if __name__ == "__main__":
    from . import db, pivots as _p

    for ts, loader in (("600519.SH", db.load_daily_qfq), ("000300.SH", db.load_index_daily)):
        d = loader(ts, start="2022-01-01")
        pv = _p.find_pivots(d)
        evs = find_xabcd(pv, asof_idx=len(d) - 1)
        print(f"{ts}: harmonics={len(evs)}")
        for e in evs:
            print(f"  {e['name']}({e['direction']}) completed={e['completed']} "
                  f"PRZ=[{e['prz_low']:.2f},{e['prz_high']:.2f}]")
    print("harmonics 自检通过")
