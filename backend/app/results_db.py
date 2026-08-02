"""研究结果库（ARCHITECTURE.md 第3节 results_db.py 规范，系统记忆）。

独立库 data/results.duckdb（可写）。API 数据源改造后仅保留：
- analysis_cache：/api/analysis 结果缓存（含启动后 8 指数预计算）；
- system_meta：系统元信息键值。
TOP20 榜单与回测功能已下线，相关表不再维护。
"""
from __future__ import annotations

import datetime as dt
import json

import duckdb

from . import config

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS analysis_cache (
        ts_code      VARCHAR NOT NULL,
        timeframe    VARCHAR NOT NULL,
        asof_date    DATE NOT NULL,
        result_json  VARCHAR,
        computed_at  TIMESTAMP,
        PRIMARY KEY (ts_code, timeframe, asof_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_meta (
        key    VARCHAR PRIMARY KEY,
        value  VARCHAR
    )
    """,
]


def get_con() -> duckdb.DuckDBPyConnection:
    """新建可写连接；确保 data/ 目录与表结构存在。"""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.RESULTS_DB_PATH))
    for ddl in _DDL:
        con.execute(ddl)
    return con


def _now() -> dt.datetime:
    return dt.datetime.now()


# ---------- 分析缓存 ----------

def save_analysis(ts_code: str, timeframe: str, asof_date, result: dict) -> None:
    with get_con() as con:
        con.execute(
            "INSERT OR REPLACE INTO analysis_cache VALUES (?, ?, ?, ?, ?)",
            [ts_code, timeframe, asof_date, json.dumps(result, ensure_ascii=False), _now()],
        )


def get_analysis(ts_code: str, timeframe: str, asof_date) -> dict | None:
    with get_con() as con:
        row = con.execute(
            "SELECT result_json FROM analysis_cache WHERE ts_code = ? AND timeframe = ? AND asof_date = ?",
            [ts_code, timeframe, asof_date],
        ).fetchone()
    return json.loads(row[0]) if row else None


# ---------- 系统元信息 ----------

def set_meta(key: str, value: str) -> None:
    with get_con() as con:
        con.execute("INSERT OR REPLACE INTO system_meta VALUES (?, ?)", [key, value])


def get_meta(key: str) -> str | None:
    with get_con() as con:
        row = con.execute("SELECT value FROM system_meta WHERE key = ?", [key]).fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    save_analysis("600519.SH", "1d", dt.date(2026, 7, 31), {"summary": {"trend": "up"}})
    print("get_analysis:", get_analysis("600519.SH", "1d", dt.date(2026, 7, 31)))
    set_meta("smoke_key", "smoke_value")
    print("get_meta:", get_meta("smoke_key"))
    print("results_db 自检通过 ->", config.RESULTS_DB_PATH)
