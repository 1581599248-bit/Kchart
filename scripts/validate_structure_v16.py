"""结构识别引擎 v16 验证：合成形态 / 真实指数 / 因果截断 / RSI 趋势过滤。"""
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


# ---------- D. RSI 趋势过滤：强趋势行情不标超买 ----------

def test_rsi_trend_filter():
    n = 260
    i = np.arange(n, dtype=float)
    close = 100 + 0.5 * i + 2.0 * np.sin(i / 2.5)
    df = _mk_df(close, seed=11)
    df = indicators.compute_all(df).reset_index(drop=True)
    warm = 120  # ADX 预热期之后的已确立强趋势段
    adx_tail = float(df["ADX"].iloc[-1])
    rsi_tail = float(df["RSI6"].iloc[-1])
    assert adx_tail >= engine.TREND_ADX, f"合成强趋势 ADX={adx_tail:.1f} 未达标"
    assert (df["RSI6"].iloc[warm:] > engine.RSI_OB).any(), "强趋势段未出现 RSI>90"
    out = engine.rsi_extreme_signals(df)
    late = [e for e in out if e["label"] == "RSI超买" and int(e["bar_idx"]) >= warm]
    assert not late, f"已确立强趋势(ADX={adx_tail:.0f})不应标超买: {len(late)}"
    print(f"D RSI趋势过滤 OK（ADX={adx_tail:.0f}, 强趋势段RSI6>90共"
          f"{int((df['RSI6'].iloc[warm:] > engine.RSI_OB).sum())}根 → 0 个超买标注）")


if __name__ == "__main__":
    test_synthetic()
    test_real_indices()
    test_causality()
    test_rsi_trend_filter()
    print("validate_structure_v16 全部通过")
