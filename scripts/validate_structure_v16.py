"""结构识别引擎 v16 验证：合成形态 / 真实指数 / 因果截断 / 纯结构输出 / 大级别M顶。"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from backend.app import analysis_v7, indicators  # noqa: E402
from backend.app import structure_engine_v16 as engine  # noqa: E402

assert analysis_v7.ANALYSIS_VERSION == "analysis_v16.0-structure-first"
assert engine.ENGINE_VERSION == "structure_engine_v16.0"
assert engine.MIN_REVERSAL_BARS == 75
assert engine.MAX_PATTERN_EVENTS == 4
assert engine.MAX_INDICATOR_EVENTS == 5


def _mk_df(close: np.ndarray, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(close)
    return pd.DataFrame({
        "trade_date": pd.date_range("2022-01-03", periods=n, freq="B"),
        "open": close + rng.normal(0, 0.2, n),
        "high": close + np.abs(rng.normal(0, 0.4, n)) + 0.3,
        "low": close - np.abs(rng.normal(0, 0.4, n)) - 0.3,
        "close": close,
        "vol": np.full(n, 1e6),
        "amount": np.full(n, 1e7),
    })


def _ramp(n: int, a: float, b: float) -> np.ndarray:
    return np.linspace(a, b, n)


def _baked_df(symbol: str) -> pd.DataFrame:
    with open(os.path.join(REPO, "data", "baked_charts.json"), encoding="utf-8") as fh:
        bars = json.load(fh)["symbols"][symbol]["bars"]
    df = pd.DataFrame({
        "trade_date": pd.to_datetime([b["time"] for b in bars], unit="s").strftime("%Y-%m-%d"),
        "open": [b["o"] for b in bars],
        "high": [b["h"] for b in bars],
        "low": [b["l"] for b in bars],
        "close": [b["c"] for b in bars],
        "vol": [b["v"] for b in bars],
        "amount": [b["amount"] for b in bars],
    })
    return indicators.compute_all(df).reset_index(drop=True)


def _structures(df: pd.DataFrame) -> list[dict]:
    return engine.find_structures(indicators.compute_all(df).reset_index(drop=True)) \
        if "DIF" not in df.columns else engine.find_structures(df)


# ---------- A. 合成教科书形态：必须识别且颈线破位给确认星标 ----------

def test_synthetic():
    n = 300
    m = np.concatenate([_ramp(80, 90, 118), _ramp(30, 118, 104),
                        _ramp(30, 118 - 0, 117.5)[::-1], _ramp(30, 117.5, 96),
                        _ramp(130, 96, 88)])
    res = analysis_v7.analyze(_mk_df(m))
    labels = [a["label"] for a in res["annotations"] if a["kind"] == "pattern"]
    assert any("M顶" in x for x in labels), labels
    assert any(a.get("star") for a in res["annotations"]), "M顶颈线破位应有星标"

    w = np.concatenate([_ramp(100, 108, 92), _ramp(25, 92, 97.5),
                        _ramp(25, 97.5, 92.4), _ramp(65, 92.4, 107),
                        _ramp(85, 107, 109)])
    res = analysis_v7.analyze(_mk_df(w))
    labels = [a["label"] for a in res["annotations"] if a["kind"] == "pattern"]
    assert any("W底" in x for x in labels), labels
    assert any(a.get("star") for a in res["annotations"]), "W底颈线突破应有星标"

    hs = np.concatenate([_ramp(120, 120, 95), _ramp(20, 95, 100),
                         _ramp(20, 100, 90), _ramp(20, 90, 100),
                         _ramp(20, 100, 95.5), _ramp(30, 95.5, 106),
                         _ramp(70, 106, 108)])
    res = analysis_v7.analyze(_mk_df(hs))
    labels = [a["label"] for a in res["annotations"] if a["kind"] == "pattern"]
    assert any("头肩底" in x for x in labels), labels
    assert any(a.get("star") for a in res["annotations"]), "头肩底颈线突破应有星标"
    print("A 合成 M顶/W底/头肩底 识别+确认 OK")


# ---------- B. 真实指数：密度封顶 + 大级别优先抑制 + 2024 双底 ----------

def _overlap(a: dict, b: dict, n: int) -> float:
    a0, a1 = a["start_idx"], (a["confirm_idx"] or n - 1)
    b0, b1 = b["start_idx"], (b["confirm_idx"] or n - 1)
    inter = max(0, min(a1, b1) - max(a0, b0))
    return inter / max(min(a1 - a0, b1 - b0), 1)


def test_real_indices():
    symbols = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH",
               "000300.SH", "000905.SH", "000852.SH", "932000.CSI"]
    for sym in symbols:
        df = _baked_df(sym)
        structs = engine.find_structures(df)
        assert len(structs) <= engine.MAX_PATTERN_EVENTS, (sym, len(structs))
        bg = [e for e in structs if e["scale"] == "background"]
        tr = [e for e in structs if e["scale"] == "trade"]
        for t in tr:
            for g in bg:
                if t["direction"] == g["direction"]:
                    assert _overlap(t, g, len(df)) < engine.PATTERN_OVERLAP_LIMIT, \
                        (sym, t["name"], g["name"], "大级别未压制交易级")
        # 同方向同区域只保留一个故事
        for i, e1 in enumerate(structs):
            for e2 in structs[i + 1:]:
                if e1["direction"] == e2["direction"]:
                    assert _overlap(e1, e2, len(df)) < engine.PATTERN_OVERLAP_LIMIT, \
                        (sym, e1["name"], e2["name"], "同向结构重复")
        print(f"  {sym}: {[(e['name'], e['scale'], e['status'], e['active']) for e in structs]}")

    # 上证指数在 2024-12-31 截断：必须看到 2024 年双底（W底，锚点在 2024）
    df = _baked_df("000001.SH")
    cut = df.index[df["trade_date"] <= "2024-12-31"].max()
    sub = df.loc[:cut].reset_index(drop=True)
    structs = engine.find_structures(sub)
    wb = [e for e in structs if e["kind"] == "double_bottom"
          and str(sub["trade_date"].iloc[e["start_idx"]]).startswith("2024")]
    assert wb, [ (e["kind"], e["start_idx"]) for e in structs ]
    print(f"B 8大指数密度/抑制 OK；上证2024双底锚点 "
          f"{sub['trade_date'].iloc[wb[0]['start_idx']]}→{sub['trade_date'].iloc[wb[0]['end_idx']]}")


# ---------- C. 因果性：任意截断不得出现未来标注 + 确定性 ----------

def test_causality():
    df = _baked_df("000001.SH")
    n = len(df)
    for frac in (0.5, 0.7, 0.85):
        k = int(n * frac)
        sub = df.iloc[:k].reset_index(drop=True)
        last_date = str(sub["trade_date"].iloc[-1])
        r1 = analysis_v7.analyze(sub)
        r2 = analysis_v7.analyze(sub)
        assert json.dumps(r1["annotations"], sort_keys=True, default=str) == \
               json.dumps(r2["annotations"], sort_keys=True, default=str), "结果不确定"
        for a in r1["annotations"]:
            assert int(a["bar_idx"]) < k, (frac, a["label"], a["bar_idx"], k)
            for pl in a.get("polylines") or []:
                for pt in pl.get("points", []):
                    assert str(pt["t"]) <= last_date, (frac, pt["t"], last_date)
    print("C 截断因果(50%/70%/85%)+确定性 OK")


# ---------- D. 纯结构输出：其余信号一律屏蔽 + 注释从 2020-03 开始 ----------

def test_structure_only_output():
    banned = {"RSI超买", "RSI超卖", "MACD顶背离", "MACD底背离", "EMA金叉", "EMA死叉",
              "小波段", "0.5", "0.618", "鲨鱼D点"}
    df = _baked_df("000001.SH")
    res = analysis_v7.analyze(df)
    labels = [a["label"] for a in res["annotations"]]
    assert not banned.intersection(labels), banned.intersection(labels)
    assert res["diagnostics"]["indicator_events"] == 0
    # 数据从 2020-01 起，但所有注释须从 2020-03 开始
    for a in res["annotations"]:
        dt = str(df["trade_date"].iloc[int(a["bar_idx"])])[:10]
        assert dt >= engine.MIN_ANNOTATION_DATE, (a["label"], dt)
    print(f"D 纯结构输出+注释≥{engine.MIN_ANNOTATION_DATE} OK（{len(res['annotations'])} 条标注）")


# ---------- E. 回测颈线 / 完整描摹（起手腿+形态+突破腿）/ 级别配色 ----------

def test_new_annotations():
    # M顶 + 确认后回抽：必须标注“回测颈线”，且描摹画完整（起手腿 + 颈线从左峰画起）
    m = np.concatenate([_ramp(80, 90, 118), _ramp(30, 118, 104),
                        _ramp(30, 104, 117.5), _ramp(30, 117.5, 96),
                        _ramp(20, 96, 104.5), _ramp(110, 104.5, 88)])
    res = analysis_v7.analyze(_mk_df(m))
    labels = [a["label"] for a in res["annotations"]]
    assert any("M顶" in x for x in labels), labels
    assert "回测颈线" in labels, labels
    tr = [a for a in res["annotations"]
          if a.get("trace_only") and "M顶" in str(a["label"])][0]
    solid = [pl for pl in tr["polylines"] if pl["style"] == "solid"][0]
    assert len(solid["points"]) >= 5, "折线应含起手腿+形态+突破腿（起手拐点+两峰一谷+确认根）"
    assert solid.get("color"), "折线必须带颜色（大级别金/交易级紫）"
    sp = solid["points"]
    assert sp[0]["t"] < sp[1]["t"] and float(sp[0]["p"]) < float(sp[1]["p"]), \
        "M顶须从起手低点画起，形成完整字母"
    dashed = [pl for pl in tr["polylines"] if pl["style"] == "dashed"]
    assert dashed and dashed[0]["points"][0]["t"] <= solid["points"][1]["t"], \
        "颈线须从左峰画起"
    print("E 回测颈线/完整描摹(含起手腿)/级别配色 OK")


# ---------- F. 大级别 M顶：两峰价差小、间隔约 50 根也必须识别（2026 实盘案例） ----------

def test_big_scale_dual_top():
    # 前置升势 → 峰1 → 深谷 → 峰2（仅高 0.2%）→ 破颈线：峰间隔 50 根，属背景级
    m = np.concatenate([_ramp(80, 100, 130), _ramp(25, 130, 117),
                        _ramp(25, 117, 132), _ramp(25, 132, 115),
                        _ramp(25, 115, 131.8), _ramp(70, 131.8, 100)])
    df = _mk_df(m)
    structs = engine.find_structures(indicators.compute_all(df).reset_index(drop=True))
    bg = [e for e in structs if e["kind"] == "double_top" and e["scale"] == "background"]
    assert bg, [(e["name"], e["scale"], e["start_idx"], e["end_idx"]) for e in structs]
    top = bg[0]
    assert top["status"] == "confirmed" and top["star"], "破颈线应确认并给星标"
    gap = int(top["end_idx"]) - int(top["start_idx"])
    assert 40 <= gap <= 80, gap
    # 同区域交易级小 M顶 必须被大级别抑制（只讲一个故事）
    tr = [e for e in structs if e["kind"] == "double_top" and e["scale"] == "trade"]
    for t in tr:
        inter = max(0, min(t["confirm_idx"] or len(df) - 1, top["confirm_idx"] or len(df) - 1)
                    - max(t["start_idx"], top["start_idx"]))
        span = max(min((t["confirm_idx"] or len(df) - 1) - t["start_idx"],
                       (top["confirm_idx"] or len(df) - 1) - top["start_idx"]), 1)
        assert inter / span < engine.PATTERN_OVERLAP_LIMIT, "大M顶应压制同区小M顶"
    print(f"F 大级别M顶(间隔{gap}根)识别+确认+抑制小结构 OK")


if __name__ == "__main__":
    test_synthetic()
    test_real_indices()
    test_causality()
    test_structure_only_output()
    test_new_annotations()
    test_big_scale_dual_top()
    print("validate_structure_v16 全部通过")
