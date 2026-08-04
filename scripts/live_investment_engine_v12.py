"""Live audit for v15 M/W geometry and signal recall."""
from __future__ import annotations
import sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import pandas as pd, requests
from backend.app import analysis_v7, indicators
from backend.app import investment_engine_v15 as engine
BASE_URL="https://kchart.onrender.com/api/chart"
INDEX_CODES=["000001.SH","000300.SH","000905.SH","000688.SH","399001.SZ","399006.SZ"]
EQUITY_CODES=["600519.SH","300750.SZ","002594.SZ","601318.SH","000333.SZ","688981.SH"]


def fetch_df(code):
    last=None
    for attempt in range(8):
        try:
            r=requests.get(BASE_URL,params={"ts_code":code,"timeframe":"1d","refresh":0},timeout=90); r.raise_for_status(); bars=r.json().get("bars") or []
            if len(bars)<600: raise RuntimeError(f"{code}: only {len(bars)} bars")
            df=pd.DataFrame(bars).rename(columns={"time":"trade_date","o":"open","h":"high","l":"low","c":"close","v":"vol"})
            df["trade_date"]=pd.to_datetime(df["trade_date"],unit="s",utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None); df["amount"]=0.
            return indicators.compute_all(df.rename(columns={"trade_date":"ts"})).rename(columns={"ts":"trade_date"})
        except Exception as exc: last=exc; time.sleep(12+attempt*4)
    raise RuntimeError(last)


def date_at(df,idx): return pd.to_datetime(df["trade_date"].iloc[int(idx)]).date()


def event_key(e):
    lv=e.get("key_levels") or {}
    return (str(e.get("kind")),int(e.get("start_idx",-1)),int(e.get("middle_idx",-1)),int(e.get("end_idx",-1)),int(e.get("confirm_idx",-1)),round(float(lv.get("neckline",0)),3))


def validate_macro_geometry(df,e):
    traces=e.get("trace") or []; assert len(traces)>=2 and len(traces[0].get("points") or [])>=3,e
    lv=e.get("key_levels") or {}; neckline=float(lv["neckline"]); assert lv.get("neckline_source")=="principal_intervening_pivot",e
    start,end=int(e["start_idx"]),int(e["end_idx"])
    piv=engine.rules.piv_mod.zigzag(df,min_pct=float(e["scale"])); piv=engine.rules.piv_mod.pivots_asof(piv,len(df)-1)
    pts=engine.rules.piv_mod.alternating(piv).to_dict("records"); mk="L" if e["direction"]=="bear" else "H"
    middle=[p for p in pts if start<int(p["idx"])<end and p["kind"]==mk]; assert middle,e
    expected=min(float(p["price"]) for p in middle) if mk=="L" else max(float(p["price"]) for p in middle)
    assert abs(neckline-expected)<1e-6,(e,expected)
    assert int(e["middle_idx"]) in {int(p["idx"]) for p in middle if abs(float(p["price"])-expected)<1e-6}


def validate_result(code,df,asset_kind):
    patterns=engine.find_investment_patterns(df); result=analysis_v7.analyze(df,asset_kind=asset_kind); d=result["diagnostics"]
    labels=[str(x.get("label") or "") for x in result["annotations"]]
    assert d["analysis_version"]==analysis_v7.ANALYSIS_VERSION and d["causal"] is True
    assert len(patterns)<=d["pattern_budget"]<=engine.MAX_PATTERN_EVENTS
    assert d["indicator_events"]<=d["indicator_budget"]<=engine.MAX_INDICATOR_EVENTS
    assert not {"EMA金叉","EMA死叉","MACD金叉","MACD死叉","结构失效"}.intersection(labels)
    for e in patterns:
        if e.get("kind") in {"macro_double_top","macro_double_bottom"}: validate_macro_geometry(df,e)
    print(code,f"patterns={len(patterns)}/{d['pattern_budget']}",f"indicators={d['indicator_events']}/{d['indicator_budget']}","structures="+",".join(f"{e['kind']}[{date_at(df,e['start_idx'])}->{date_at(df,e['confirm_idx'])}]" for e in patterns))
    return len(patterns),int(d["indicator_events"])


def validate_shanghai(df):
    patterns=engine.find_investment_patterns(df)
    tops=[e for e in patterns if e.get("kind")=="macro_double_top" and date_at(df,e["confirm_idx"]).year==2026]; assert tops,patterns
    top=max(tops,key=lambda e:int(e["confirm_idx"])); validate_macro_geometry(df,top)
    assert date_at(df,top["start_idx"])<=pd.Timestamp("2026-03-01").date()
    ws=[e for e in patterns if e.get("kind")=="macro_double_bottom" and date_at(df,e["start_idx"]).year in {2023,2024} and date_at(df,e["confirm_idx"]).year in {2024,2025}]
    assert ws,patterns
    for e in ws: validate_macro_geometry(df,e)
    prefix=df.iloc[:min(len(df),int(top["confirm_idx"])+6)].copy().reset_index(drop=True)
    assert event_key(top) in {event_key(e) for e in engine.find_investment_patterns(prefix)}
    print("Shanghai 2026 M:",date_at(df,top["start_idx"]),date_at(df,top["middle_idx"]),date_at(df,top["end_idx"]),date_at(df,top["confirm_idx"]),"neckline=",top["key_levels"]["neckline"],"pivots=",top.get("pivot_count"))
    print("Shanghai 2024 W:",[(date_at(df,e["start_idx"]),date_at(df,e["middle_idx"]),date_at(df,e["end_idx"]),date_at(df,e["confirm_idx"]),e["key_levels"]["neckline"]) for e in ws])
    assert len(patterns)>=6,patterns


def main():
    total_p=total_i=0; sh=None
    for code in INDEX_CODES:
        df=fetch_df(code); sh=df if code=="000001.SH" else sh; p,i=validate_result(code,df,"index"); total_p+=p; total_i+=i
    for code in EQUITY_CODES:
        df=fetch_df(code); p,i=validate_result(code,df,"equity"); total_p+=p; total_i+=i
    assert sh is not None; validate_shanghai(sh); assert total_p>=(len(INDEX_CODES)+len(EQUITY_CODES))*5,total_p
    print("live investment engine v15 validation OK",f"patterns={total_p}",f"indicators={total_i}")
if __name__=="__main__": main()
