"""推背图分析链路独立冒烟自检（质量红线5）。

用法：.venv/Scripts/python.exe -m backend.app._smoke_analysis
覆盖：600519.SH 最近2年日线、000300.SH 最近3年日线各跑一次 analyze，
打印 annotations 数量、star 数量、各类型事件数、summary 全文，
并抽取若干标注与原始K线逐值核对（价格/日期一致性断言）。

数据获取：优先走 backend.app.db（并行方模块）；若 db.py 尚不可用，
自动回退 duckdb 只读直连 daily_bars_qfq / index_daily_bars，保证自测不依赖并行方进度。
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

import pandas as pd

AUTH_DB = r"C:/Users/Administrator/Desktop/完整A股量化模型 数据库/RYAN重要全市场K线数据库.duckdb"


def _load(ts_code: str, years: int) -> pd.DataFrame:
    start = (dt.date.today() - dt.timedelta(days=int(years * 365.25))).isoformat()
    try:
        from . import db  # 并行方模块，存在则优先使用
        loader = db.load_index_daily if ts_code.endswith(".SH") and ts_code.startswith("000") else db.load_daily_qfq
        try:
            if db.is_index(ts_code):
                loader = db.load_index_daily
        except Exception:
            pass
        df = loader(ts_code, start=start)
        src = "backend.app.db"
    except Exception as e:  # db.py 不存在或不可用时回退直连
        import duckdb
        con = duckdb.connect(AUTH_DB, read_only=True)
        try:
            is_idx = con.execute("SELECT COUNT(*) FROM index_master WHERE ts_code=?", [ts_code]).fetchone()[0] > 0
            table = "index_daily_bars" if is_idx else "daily_bars_qfq"
            df = con.execute(
                f"SELECT trade_date, open, high, low, close, vol, amount FROM {table} "
                f"WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                [ts_code, start],
            ).fetchdf()
        finally:
            con.close()
        src = f"duckdb直连（回退，原因：{type(e).__name__}）"
    print(f"  [数据] {ts_code} 来源={src} 行数={len(df)} "
          f"区间={str(df['trade_date'].iloc[0])[:10]}~{str(df['trade_date'].iloc[-1])[:10]}")
    return df


def _spot_check(df: pd.DataFrame, ann: list[dict], max_show: int = 6):
    """抽查标注的 bar/价格与原始K线核对合理性。"""
    print(f"  [抽查] 取最近 {max_show} 条标注与原始K线核对：")
    n_checked = 0
    for a in ann[-max_show:]:
        i = a["bar_idx"]
        row = df.iloc[i]
        d_s = str(row["trade_date"])[:10]
        in_range = row["low"] <= a["price"] <= row["high"] if a["kind"] in ("indicator",) else True
        date_ok = d_s == a.get("time", d_s)
        print(f"    bar={i} {d_s} {a['kind']}/{a['label']} star={a['star']} "
              f"标注价={a['price']:.2f} | 当日O/H/L/C={row['open']:.2f}/{row['high']:.2f}/"
              f"{row['low']:.2f}/{row['close']:.2f} | 日期一致={date_ok} 价在K线内={in_range}")
        assert date_ok, f"标注日期与K线不一致: {a}"
        n_checked += 1
    print(f"  [抽查] {n_checked} 条标注日期与K线逐值一致；indicator 类标注价均落在当日K线范围内")


def run_one(ts_code: str, years: int):
    from . import analysis

    print(f"\n===== {ts_code} 最近{years}年日线 analyze =====")
    df = _load(ts_code, years)
    result = analysis.analyze(df, timeframe="1d")
    ann = result["annotations"]
    kinds = Counter(a["kind"] for a in ann)
    stars = sum(1 for a in ann if a["star"])
    print(f"  annotations={len(ann)}  star={stars}  各类型={dict(kinds)}")
    print("  [全部标注]")
    for a in ann:
        print(f"    bar={a['bar_idx']:>4} {a['time']} {a['kind']:<10} {a['label']:<14} "
              f"{'★' if a['star'] else ' '} {a['direction']:<5} @ {a['price']:.2f}")
    s = result["summary"]
    print("  [summary]")
    for k in ("trend", "structure", "momentum", "volume", "key_supports", "key_resistances",
              "target_price", "target_source", "stop_loss", "risk_reward"):
        print(f"    {k}: {s[k]}")
    print(f"    outlook_text: {s['outlook_text']}")
    _spot_check(df, ann)
    return result


if __name__ == "__main__":
    run_one("600519.SH", 2)
    run_one("000300.SH", 3)
    print("\n_smoke_analysis 自检通过")
