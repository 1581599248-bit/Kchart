"""Deterministic regression checks for v15 M/W geometry and signal recall."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import numpy as np, pandas as pd
from backend.app import analysis_v7, indicators
from backend.app import investment_engine_v12 as rules
from backend.app import investment_engine_v15 as engine
from backend.app import pattern_taxonomy_v8

def compute(f): return indicators.compute_all(f.rename(columns={"trade_date":"ts"})).rename(columns={"ts":"trade_date"})
def base_frame(n=900,seed=20260804):
 r=np.random.default_rng(seed); c=100+np.cumsum(r.normal(.02,.8,n)); return compute(pd.DataFrame({"trade_date":pd.date_range("2022-01-03",periods=n,freq="B"),"open":c+r.normal(0,.3,n),"high":c+r.uniform(.2,1.4,n),"low":c-r.uniform(.2,1.4,n),"close":c,"vol":r.integers(100000,1000000,n).astype(float),"amount":r.uniform(1e7,1e9,n)}))
def shaped_frame(k):
 n=360; a=([(0,80),(70,122),(145,96),(225,119),(285,94),(359,88)] if k=="M" else [(0,130),(70,88),(145,112),(225,91),(285,116),(359,124)]); x=np.arange(n); c=np.interp(x,[i for i,_ in a],[v for _,v in a])+np.sin(x/5)*.18
 return compute(pd.DataFrame({"trade_date":pd.date_range("2024-01-02",periods=n,freq="B"),"open":c,"high":c+.6,"low":c-.6,"close":c,"vol":np.full(n,1e6),"amount":np.full(n,1e8)}))
def assert_principal(df,e):
 p=rules.piv_mod.zigzag(df,min_pct=float(e["scale"])); p=rules.piv_mod.pivots_asof(p,len(df)-1); pts=rules.piv_mod.alternating(p).to_dict("records"); k="L" if e["direction"]=="bear" else "H"; m=[x for x in pts if int(e["start_idx"])<int(x["idx"])<int(e["end_idx"]) and x["kind"]==k]; assert m
 exp=min(float(x["price"]) for x in m) if k=="L" else max(float(x["price"]) for x in m); assert abs(float(e["key_levels"]["neckline"])-exp)<1e-3,(e,exp); assert int(e["middle_idx"]) in {int(x["idx"]) for x in m if abs(float(x["price"])-exp)<1e-9}; assert e["key_levels"]["neckline_source"]=="principal_intervening_pivot"; assert len(e["trace"][0]["points"])>=3
def test_macro():
 m=shaped_frame("M"); t=[e for e in engine._macro_candidates(m) if e["kind"]=="macro_double_top"]; assert t; assert_principal(m,max(t,key=lambda e:e["score"]))
 w=shaped_frame("W"); b=[e for e in engine._macro_candidates(w) if e["kind"]=="macro_double_bottom"]; assert b; assert_principal(w,max(b,key=lambda e:e["score"]))
def test_misc(df):
 assert engine._dynamic_pattern_budget(df)>=6 and engine._dynamic_pattern_budget(pd.concat([df,df],ignore_index=True))>engine._dynamic_pattern_budget(df); assert engine.MAX_PATTERN_EVENTS>=10 and engine.MAX_INDICATOR_EVENTS>=8
 raw=[{"kind":"bull_flag","name":"上升旗形","direction":"bull","confirm_idx":100},{"kind":"bear_flag","name":"下降旗形","direction":"bear","confirm_idx":110},{"kind":"rising_wedge","name":"上升楔形","direction":"bear","confirm_idx":120},{"kind":"falling_wedge","name":"下降楔形","direction":"bull","confirm_idx":130},{"kind":"asc_triangle","name":"上升三角形","direction":"bull","confirm_idx":140},{"kind":"desc_triangle","name":"下降三角形","direction":"bear","confirm_idx":150}]; assert {e["name"] for e in pattern_taxonomy_v8.apply_pattern_taxonomy(raw)}=={"牛旗形","熊旗形","熊楔形","牛楔形","看涨三角形","看跌三角形"}
 work=df.iloc[:220].copy().reset_index(drop=True); work["RSI6"]=50.; work.loc[80:84,"RSI6"]=[82,84,83,75,70]; work.loc[120:124,"RSI6"]=[18,16,17,25,30]; assert rules.rsi_extreme_signals(work)==[]; work.loc[150:155,"RSI6"]=[91,94,93,88,84,80]; work.loc[154:156,"close"]=work.loc[154:156,"MA10"].to_numpy()*[.995,.99,.985]; assert any(e["label"]=="RSI超买" for e in rules.rsi_extreme_signals(work))
 n=650; x=np.arange(n); c=100+np.sin(x/4)*.15; flat=compute(pd.DataFrame({"trade_date":pd.date_range("2022-01-03",periods=n,freq="B"),"open":c,"high":c+.25,"low":c-.25,"close":c,"vol":np.full(n,5e5),"amount":np.full(n,1e8)})); assert rules.ema_regime_signals(flat)==[]
def test_full(df):
 r=analysis_v7.analyze(df,asset_kind="equity"); labels=[str(e.get("label") or "") for e in r["annotations"]]; assert not {"EMA金叉","EMA死叉","MACD金叉","MACD死叉","结构失效"}.intersection(labels); assert all(len(x)<=8 for x in labels); d=r["diagnostics"]; assert d["patterns_displayed"]<=d["pattern_budget"]<=engine.MAX_PATTERN_EVENTS; assert d["indicator_events"]<=d["indicator_budget"]<=engine.MAX_INDICATOR_EVENTS and d["causal"] is True and d["analysis_version"]==analysis_v7.ANALYSIS_VERSION
def main():
 df=base_frame(); test_macro(); test_misc(df); test_full(df); print("investment engine v15 validation OK")
if __name__=="__main__": main()
