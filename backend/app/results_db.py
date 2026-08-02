"""研究结果库（ARCHITECTURE.md 第3节 results_db.py 规范，系统记忆）。

独立库 data/results.duckdb（可写，与只读权威库分离）。本次任务只建表结构与读写函数。
所有写入先查后写、幂等（INSERT OR REPLACE / 按主键删除后插入）。
"""
from __future__ import annotations

import datetime as dt
import json

import duckdb
import pandas as pd

from . import config

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS scores_daily (
        trade_date     DATE NOT NULL,
        ts_code        VARCHAR NOT NULL,
        score          DOUBLE,
        rank           INTEGER,
        group_json     VARCHAR,
        model_version  VARCHAR,
        computed_at    TIMESTAMP,
        PRIMARY KEY (trade_date, ts_code)
    )
    """,
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
    CREATE TABLE IF NOT EXISTS backtest_runs (
        run_id        VARCHAR PRIMARY KEY,
        params_json   VARCHAR,
        metrics_json  VARCHAR,
        created_at    TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_nav (
        run_id      VARCHAR NOT NULL,
        trade_date  DATE NOT NULL,
        nav         DOUBLE,
        bench_nav   DOUBLE,
        pool_nav    DOUBLE,
        PRIMARY KEY (run_id, trade_date)
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


# ---------- 打分 ----------

def save_scores(trade_date, scores_df: pd.DataFrame, model_version: str = config.MODEL_VERSION) -> int:
    """幂等写入某日打分。scores_df 列：ts_code, score, rank, group_scores(dict 或 json str)。返回写入行数。"""
    if scores_df.empty:
        return 0
    rows = []
    for _, r in scores_df.iterrows():
        g = r.get("group_scores")
        rows.append((
            trade_date, r["ts_code"], float(r["score"]), int(r["rank"]),
            g if isinstance(g, str) else json.dumps(g, ensure_ascii=False),
            model_version, _now(),
        ))
    with get_con() as con:
        con.execute("DELETE FROM scores_daily WHERE trade_date = ?", [trade_date])
        con.executemany(
            "INSERT INTO scores_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows
        )
    return len(rows)


def get_scores(trade_date) -> pd.DataFrame:
    with get_con() as con:
        return con.execute(
            "SELECT * FROM scores_daily WHERE trade_date = ? ORDER BY rank",
            [trade_date],
        ).fetchdf()


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


# ---------- 回测 ----------

def save_backtest(run_id: str, params: dict, metrics: dict, nav_df: pd.DataFrame) -> None:
    """nav_df 列：trade_date, nav, bench_nav, pool_nav。"""
    with get_con() as con:
        con.execute(
            "INSERT OR REPLACE INTO backtest_runs VALUES (?, ?, ?, ?)",
            [run_id, json.dumps(params, ensure_ascii=False),
             json.dumps(metrics, ensure_ascii=False), _now()],
        )
        con.execute("DELETE FROM backtest_nav WHERE run_id = ?", [run_id])
        if not nav_df.empty:
            con.executemany(
                "INSERT INTO backtest_nav VALUES (?, ?, ?, ?, ?)",
                [(run_id, r["trade_date"], float(r["nav"]),
                  float(r["bench_nav"]), float(r["pool_nav"]))
                 for _, r in nav_df.iterrows()],
            )


def get_backtest_list() -> pd.DataFrame:
    with get_con() as con:
        return con.execute(
            "SELECT run_id, params_json, metrics_json, created_at "
            "FROM backtest_runs ORDER BY created_at DESC"
        ).fetchdf()


def get_backtest(run_id: str) -> dict | None:
    with get_con() as con:
        run = con.execute(
            "SELECT params_json, metrics_json, created_at FROM backtest_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if not run:
            return None
        nav = con.execute(
            "SELECT trade_date, nav, bench_nav, pool_nav FROM backtest_nav "
            "WHERE run_id = ? ORDER BY trade_date",
            [run_id],
        ).fetchdf()
    return {
        "run_id": run_id,
        "params": json.loads(run[0]),
        "metrics": json.loads(run[1]),
        "created_at": run[2],
        "nav": nav,
    }


# ---------- 系统元信息 ----------

def set_meta(key: str, value: str) -> None:
    with get_con() as con:
        con.execute("INSERT OR REPLACE INTO system_meta VALUES (?, ?)", [key, value])


def get_meta(key: str) -> str | None:
    with get_con() as con:
        row = con.execute("SELECT value FROM system_meta WHERE key = ?", [key]).fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    df = pd.DataFrame([
        {"ts_code": "600519.SH", "score": 88.5, "rank": 1, "group_scores": {"G1": 80}},
        {"ts_code": "000300.SH", "score": 70.0, "rank": 2, "group_scores": {"G2": 75}},
    ])
    n = save_scores(dt.date(2026, 7, 31), df)
    n2 = save_scores(dt.date(2026, 7, 31), df)  # 幂等重写
    got = get_scores(dt.date(2026, 7, 31))
    print("save_scores:", n, n2, "| get_scores rows:", len(got))
    assert len(got) == 2

    save_analysis("600519.SH", "1d", dt.date(2026, 7, 31), {"summary": {"trend": "up"}})
    print("get_analysis:", get_analysis("600519.SH", "1d", dt.date(2026, 7, 31)))

    nav = pd.DataFrame([
        {"trade_date": dt.date(2026, 7, 24), "nav": 1.0, "bench_nav": 1.0, "pool_nav": 1.0},
        {"trade_date": dt.date(2026, 7, 31), "nav": 1.02, "bench_nav": 1.01, "pool_nav": 1.005},
    ])
    save_backtest("smoke_run", {"top_n": 10}, {"annual": 0.12}, nav)
    print("get_backtest_list rows:", len(get_backtest_list()))
    bt = get_backtest("smoke_run")
    print("get_backtest nav rows:", len(bt["nav"]), "| metrics:", bt["metrics"])

    set_meta("smoke_key", "smoke_value")
    print("get_meta:", get_meta("smoke_key"))
    print("results_db 自检通过 ->", config.RESULTS_DB_PATH)
