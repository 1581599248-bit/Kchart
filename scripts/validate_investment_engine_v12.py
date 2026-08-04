"""Deterministic regression checks for v15 M/W geometry and signal recall."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from backend.app import analysis_v7, indicators
from backend.app import investment_engine_v12 as rules
from backend.app import investment_engine_v15 as engine
from backend.app import pattern_taxonomy_v8


def compute(frame: pd.DataFrame) -> pd.DataFrame:
    return indicators.compute_all(frame.rename(columns={"trade_date": "ts"})).rename(columns={"ts": "trade_date"})


def base_frame(n: int = 900, seed: int = 20260804) -> pd.DataFrame:
    rng = np.random.default_rng(seed); close = 100.0 + np.cumsum(rng.normal(0.02, 0.8, n))
    return compute(pd.DataFrame({"trade_date": pd.date_range("2022-01-03", periods=n, freq="B"),
        "open": close + rng.normal(0, .3), "high": close + rng.uniform(.2, 1.4, n),
        "low": close - rng.uniform(.2, 1.4, n), "close": close,
        "vol": rng.integers(100_000, 1_000_000, n).astype(float), "amount": rng.uniform(1e7, 1e9, n)}))


def shaped_frame(kind: str) -> pd.DataFrame:
    n = 360
    anchors = ([(0,80),(70,122),(145,96),(225,119),(285,94),(359,88)] if kind == "M"
               else [(0,130),(70,88),(145,112),(225,91),(285,116),(359,124)])
    x = np.arange(n); close = np.interp(x, [a for a,_ in anchors], [b for _,b in anchors]) + np.sin(x/5)*.18
    return compute(pd.DataFrame({"trade_date": pd.date_range("2024-01-02", periods=n, freq="B"),
        "open": close, "high": close+.6, "low": close-.6, "close": close,
        "vol": np.full(n,1_000_000.), "amount": np.full(n,1e8)}))


def assert_principal_pivot(df: pd.DataFrame, event: dict) -> None:
    scale = float(event["scale"]); start = int(event["start_idx"]); end = int(event["end_idx"])
    piv = rules.piv_mod.zigzag(df, min_pct=scale)
    piv = rules.piv_mod.pivots_asof(piv, len(df)-1)
    pts = rules.piv_mod.alternating(piv).to_dict("records")
    kind = "L" if event["direction"] == "bear" else "H"
    middle = [p for p in pts if start < int(p["idx"]) < end and p["kind"] == kind]
    assert middle, event
    expected = min(float(p["price"]) for p in middle) if kind == "L" else max(float(p["price"]) for p in middle)
    assert abs(float(event["key_levels"]["neckline"]) - expected) < 1e-6, (event, expected)
    assert int(event["middle_idx"]) in {int(p["idx"]) for p in middle if abs(float(p["price"])-expected)<1e-6}
    assert event["key_levels"]["neckline_source"] == "principal_intervening_pivot"
    assert len(event["trace"][0]["points"]) >= 3


def test_macro_geometry() -> None:
    tops = [e for e in engine._macro_candidates(shaped_frame("M")) if e["kind"] == "macro_double_top"]
    assert tops; m = shaped_frame("M"); assert_principal_pivot(m, max(tops,key=lambda e:e["score"]))
    bottoms = [e for e in engine._macro_candidates(shaped_frame("W")) if e["kind"] == "macro_double_bottom"]
    assert bottoms; w = shaped_frame("W"); assert_principal_pivot(w, max(bottoms,key=lambda e:e["score"]))


def test_dynamic_recall(df: pd.DataFrame) -> None:
    assert engine._dynamic_pattern_budget(df) >= 6
    assert engine._dynamic_pattern_budget(pd.concat([df,df],ignore_index=True)) > engine._dynamic_pattern_budget(df)
    assert engine.MAX_PATTERN_EVENTS >= 10 and engine.MAX_INDICATOR_EVENTS >= 8


def test_directional_taxonomy() -> None:
    raw=[{"kind":"bull_flag","name":"上升旗形","direction":"bull","confirm_idx":100},
         {"kind":"bear_flag","name":"下降旗形","direction":"bear","confirm_idx":110},
         {"kind":"rising_wedge","name":"上升楔形","direction":"bear","confirm_idx":120},
         {"kind":"falling_wedge","name":"下降楔形","direction":"bull","confirm_idx":130},
         {"kind":"asc_triangle","name":"上升三角形","direction":"bull","confirm_idx":140},
         {"kind":"desc_triangle","name":"下降三角形","direction":"bear","confirm_idx":150}]
    assert {e["name"] for e in pattern_taxonomy_v8.apply_pattern_taxonomy(raw)} == {"牛旗形","熊旗形","熊楔形","牛楔形","看涨三角形","看跌三角形"}


def test_rsi_and_ema(df: pd.DataFrame) -> None:
    work=df.iloc[:220].copy().reset_index(drop=True); work["RSI6"]=50.; work.loc[80:84,"RSI6"]=[82,84,83,75,70]; work.loc[120:124,"RSI6"]=[18,16,17,25,30]
    assert rules.rsi_extreme_signals(work)==[]
    work.loc[150:155,"RSI6"]=[91,94,93,88,84,80]; work.loc[154:156,"close"]=work.loc[154:156,"MA10"].to_numpy()*[.995,.99,.985]
    assert any(e["label"]=="RSI超买" for e in rules.rsi_extreme_signals(work))
    n=650; x=np.arange(n); close=100+np.sin(x/4)*.15
    flat=compute(pd.DataFrame({"trade_date":pd.date_range("2022-01-03",periods=n,freq="B"),"open":close,"high":close+.25,"low":close-.25,"close":close,"vol":np.full(n,500_000.),"amount":np.full(n,1e8)}))
    assert rules.ema_regime_signals(flat)==[]


def test_full_chain(df: pd.DataFrame) -> None:
    result=analysis_v7.analyze(df,asset_kind="equity"); labels=[str(e.get("label") or "") for e in result["annotations"]]
    assert not {"EMA金叉","EMA死叉","MACD金叉","MACD死叉","结构失效"}.intersection(labels)
    assert all(len(x)<=8 for x in labels)
    assert all(e["label"] in {"0.5","0.618"} for e in result["annotations"] if e.get("kind")=="fibonacci")
    d=result["diagnostics"]; assert d["patterns_displayed"]<=d["pattern_budget"]<=engine.MAX_PATTERN_EVENTS
    assert d["indicator_events"]<=d["indicator_budget"]<=engine.MAX_INDICATOR_EVENTS and d["causal"] is True
    assert d["analysis_version"]==analysis_v7.ANALYSIS_VERSION


def main() -> None:
    df=base_frame(); test_macro_geometry(); test_dynamic_recall(df); test_directional_taxonomy(); test_rsi_and_ema(df); test_full_chain(df)
    print("investment engine v15 validation OK")
if __name__=="__main__": main()
