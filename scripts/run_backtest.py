"""命令行回测入口：跑周度轮换 TOP N 回测并落库 results_db。

用法：
  python scripts/run_backtest.py                          # 2016-01-01 ~ 最新，全池，TOP10
  python scripts/run_backtest.py --start 2024-01-01 --end 2026-07-31 --pool-top 300
  python scripts/run_backtest.py --top-n 20 --no-save     # 不落库
  python scripts/run_backtest.py --show-top10 2026-07-24  # 加印某信号周 TOP10 及五组得分
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import backtest, db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None, help="默认权威库最近交易日")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--pool-top", type=int, default=None,
                    help="限定成交额前 N 只固定集合（小样本验证用）")
    ap.add_argument("--no-save", action="store_true", help="不写入 results_db")
    ap.add_argument("--show-top10", default=None, metavar="DATE",
                    help="加印指定信号日 TOP10 名单及五组得分")
    args = ap.parse_args()

    res = backtest.run_backtest(
        start=args.start, end=args.end, top_n=args.top_n,
        top_amount_n=args.pool_top, save=not args.no_save,
    )
    backtest.print_report(res)

    if args.show_top10:
        import pandas as pd
        d = pd.Timestamp(args.show_top10)
        day = res["scores"][res["scores"]["trade_date"] == d] \
            .sort_values("rank").head(args.top_n).copy()
        if day.empty:
            print(f"\n{args.show_top10} 无得分数据（非信号日或超出区间）")
        else:
            day["name"] = day["ts_code"].map(
                {c: db.get_security_name(c) for c in day["ts_code"]})
            print(f"\n信号日 {args.show_top10} TOP{args.top_n} 名单及五组得分：")
            cols = ["rank", "ts_code", "name", "score", "G1", "G2", "G3", "G4", "G5"]
            print(day[cols].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
