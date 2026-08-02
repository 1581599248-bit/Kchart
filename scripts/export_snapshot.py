# -*- coding: utf-8 -*-
"""导出 Render 部署用的精简快照库（只含线上站点必需的 7 张表）。

用法：
    .venv/Scripts/python.exe scripts/export_snapshot.py

输出 data/kline_snapshot.duckdb（git 忽略）。快照库不含个股 60 分钟线
（hourly_bars 占权威库体积大头，线上以快照模式隐藏个股 60m 按钮；
指数 60m 走新浪在线源，不受影响）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from backend.app import config  # noqa: E402

TABLES = [
    "daily_bars_full",          # 全市场日线（原始价）
    "adj_factors_full",         # 全市场复权因子
    "research_daily_bars_strict",  # 打分资格池
    "index_daily_bars",         # 指数日线
    "index_master",             # 指数主数据
    "security_master_history",  # 个股名称
    "trading_calendar",         # 交易日历
]

OUT = config.DATA_DIR / "kline_snapshot.duckdb"


def main() -> None:
    src = config.AUTH_DB_PATH
    if not os.path.exists(src):
        raise SystemExit(f"权威库不可达: {src}")
    if OUT.exists():
        OUT.unlink()
    t0 = time.time()
    con = duckdb.connect(str(OUT))
    con.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
    for t in TABLES:
        t1 = time.time()
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"{t:32s} {n:>12,} rows  ({time.time() - t1:.0f}s)", flush=True)
    con.execute("CHECKPOINT")
    con.close()
    mb = OUT.stat().st_size / 1024 / 1024
    print(f"\n完成: {OUT}  {mb:.0f} MB  总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
