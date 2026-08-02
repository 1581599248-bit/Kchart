"""增量预计算最近交易日全池打分，写入 results_db.scores_daily（幂等）。

用法：
  python scripts/precompute_scores.py            # 补齐缺失日至最近交易日（默认补最近 5 个交易日）
  python scripts/precompute_scores.py --days 30  # 强制回算最近 30 个交易日
打分池 = 可投资域（strict 池 ∧ 上市>=120交易日 ∧ 20日中位成交额>=5000万），与回测口径一致。
同一交易日重复执行只会 DELETE+INSERT 重写（results_db.save_scores 幂等）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from backend.app import backtest, db, results_db, scoring  # noqa: E402

BUFFER_DAYS = 420


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5, help="无历史记录时默认补最近 N 个交易日")
    args = ap.parse_args()

    latest = db.latest_trade_date()
    # 已打分最近日期（幂等增量起点）
    try:
        con = results_db.get_con()
        row = con.execute("SELECT max(trade_date) FROM scores_daily").fetchone()
        con.close()
        last_scored = row[0]
    except Exception:
        last_scored = None

    cal = db.trade_calendar("2000-01-01", latest)
    if last_scored is not None:
        todo = [d for d in cal if pd.Timestamp(d) > pd.Timestamp(last_scored)]
    else:
        todo = cal[-args.days:]
    if not todo:
        print(f"scores_daily 已是最新（{last_scored}），无需计算")
        return
    first = todo[0]
    print(f"待打分 {len(todo)} 个交易日：{first} ~ {todo[-1]}")

    # 面板：首个待打分日前推缓冲
    start = pd.Timestamp(first) - pd.Timedelta(days=BUFFER_DAYS)
    panel = backtest._load_panel(start.date(), latest)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    inv = panel[(panel["days_since_listing"] >= backtest.MIN_DAYS_SINCE_LISTING)
                & (panel["median_amount_cny_20"] >= backtest.MIN_MEDIAN_AMOUNT)].copy()
    print(f"面板 {len(panel):,} 行；可投资域 {inv['ts_code'].nunique()} 只")

    fac = scoring.compute_raw_factors(inv)
    scores = scoring.compute_scores_panel(fac, dates=set(pd.to_datetime(todo)))

    for d in todo:
        day = scores[scores["trade_date"] == pd.Timestamp(d)]
        if day.empty:
            print(f"  {d}：无可打分股票，跳过")
            continue
        out = day[["ts_code", "score", "rank"]].copy()
        out["group_scores"] = day.apply(
            lambda r: {g: round(float(r[g]), 4) for g in scoring.GROUPS}, axis=1)
        n = results_db.save_scores(d, out, model_version=scoring.MODEL_VERSION)
        print(f"  {d}：写入 {n} 只")

    results_db.set_meta("last_scores_date", str(todo[-1]))
    print(f"完成。最近打分日期已记录为 {todo[-1]}")


if __name__ == "__main__":
    main()
