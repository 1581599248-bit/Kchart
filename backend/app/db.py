"""权威库只读访问层（ARCHITECTURE.md 第3节 db.py 规范）。

所有连接 read_only=True，每次新建、用完关闭（context manager）。
查询限定列与日期范围，禁止物化复制全表。

===== 实际核实到的数据口径（DESCRIBE/SELECT/视图定义实测，2026-08-01）=====
- daily_bars_qfq 视图：OHLC 已前复权，但底层基表 daily_bars 仅 15 个代码
  （8宽基指数相关 + 600519.SH 等观测样本），不能服务全市场。
- 全市场日线：daily_bars_full（基表，1420万行，5808 代码，至 2026-07-31），
  OHLC 为原始价，列名 vol/amount（单位实测：vol=手、amount=千元，
  与 daily_bars 口径一致，600519.SH 2026-07-24 两表逐值相等）。
- 全市场复权因子：adj_factors_full(ts_code, trade_date, adj_factor)，5812 代码。
  前复权价 = 原始价 × adj_factor / max(adj_factor)（锚定最新因子）。
  已验证：600519.SH 2026-06-25 计算值 1179.0945 与 daily_bars_qfq 视图
  1179.0945028509307 完全一致；000002.SZ 2026-07-29 计算 close 3.28 与
  hourly_bars_qfq 当日 15:00 close 3.28 一致。
  → 个股日线一律用 daily_bars_full × adj_factors_full 现算前复权。
- 60分钟线：hourly_bars_qfq 视图（底层 hourly_bars，3070万行，5572 代码，
  2020-01 起），视图内 open/high/low/close 已是前复权价（*_raw 为原始价），
  volume_unit='share'(股)、amount_unit='CNY'(元)，frequency='60min'，
  trade_time 粒度 10:30/11:30/14:00/15:00（实测）。
  注意与日线单位不同：日线 vol=手/amount=千元，小时线 vol=股/amount=元。
  research_hourly_bars_strict 同口径但带资格过滤（可能有缺口），
  画 K 线用未过滤的 hourly_bars_qfq 更完整。
- index_daily_bars：无复权概念，直接用；列名 vol/amount。
- research_daily_bars_strict：合格股票池（OHLC 原始价，vol=手/amount=千元），
  打分/回测资格过滤用；取前复权价需 JOIN daily_bars_full + adj_factors_full。
=====================================================================
"""
from __future__ import annotations

import datetime as dt
import os

import duckdb
import pandas as pd

from . import config

# DuckDB 单连接内存上限：Render 免费层 512MB 容器需调小（环境变量 RYAN_DUCK_MEM，如 400MB）
_MEMORY_LIMIT = os.environ.get("RYAN_DUCK_MEM", "2GB")
_THREADS = "4"

# 个股前复权日线 SQL：原始价 × 当日因子 / 该股最新因子（锚定最新，与 daily_bars_qfq 视图口径一致）
_QFQ_DAILY_SQL = (
    "WITH f AS ("
    "  SELECT trade_date, adj_factor,"
    "         max(adj_factor) OVER () AS max_f"
    "  FROM adj_factors_full WHERE ts_code = ?"
    ") "
    "SELECT b.trade_date,"
    "       b.open  * f.adj_factor / f.max_f AS open,"
    "       b.high  * f.adj_factor / f.max_f AS high,"
    "       b.low   * f.adj_factor / f.max_f AS low,"
    "       b.close * f.adj_factor / f.max_f AS close,"
    "       b.vol, b.amount "
    "FROM daily_bars_full b JOIN f ON f.trade_date = b.trade_date "
    "WHERE b.ts_code = ?"
)


def get_con() -> duckdb.DuckDBPyConnection:
    """每次新建只读连接（线程安全；权威库严禁写入）。"""
    con = duckdb.connect(config.AUTH_DB_PATH, read_only=True)
    con.execute(f"SET memory_limit='{_MEMORY_LIMIT}'")
    con.execute(f"SET threads={_THREADS}")
    return con


def _norm_date(d):
    if d is None:
        return None
    if isinstance(d, (dt.date, dt.datetime)):
        return d
    return dt.datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def load_daily_qfq(ts_code: str, start=None, end=None) -> pd.DataFrame:
    """个股前复权日线 → DataFrame[trade_date, open, high, low, close, vol, amount]，日期升序。

    数据源：daily_bars_full × adj_factors_full（全市场覆盖，见文件头核实记录）。
    """
    sql = _QFQ_DAILY_SQL
    params = [ts_code, ts_code]
    if start is not None:
        sql += " AND b.trade_date >= ?"
        params.append(_norm_date(start))
    if end is not None:
        sql += " AND b.trade_date <= ?"
        params.append(_norm_date(end))
    sql += " ORDER BY b.trade_date"
    with get_con() as con:
        return con.execute(sql, params).fetchdf()


def load_daily_qfq_universe(start, end) -> pd.DataFrame:
    """合格股票池全池面板（打分/回测用）。

    research_daily_bars_strict（资格过滤）JOIN daily_bars_full + adj_factors_full（前复权价）。
    输出列：ts_code, trade_date, open, high, low, close, vol, amount（qfq 口径，vol=手, amount=千元）。
    """
    sql = (
        "WITH f AS ("
        "  SELECT ts_code, trade_date, adj_factor,"
        "         max(adj_factor) OVER (PARTITION BY ts_code) AS max_f"
        "  FROM adj_factors_full"
        "  WHERE trade_date BETWEEN ? AND ?"
        "), "
        "fx AS (SELECT ts_code, max(adj_factor) AS max_f FROM adj_factors_full GROUP BY ts_code) "
        "SELECT s.ts_code, s.trade_date,"
        "       b.open  * f.adj_factor / fx.max_f AS open,"
        "       b.high  * f.adj_factor / fx.max_f AS high,"
        "       b.low   * f.adj_factor / fx.max_f AS low,"
        "       b.close * f.adj_factor / fx.max_f AS close,"
        "       b.vol, b.amount "
        "FROM research_daily_bars_strict s "
        "JOIN daily_bars_full b ON b.ts_code = s.ts_code AND b.trade_date = s.trade_date "
        "JOIN f  ON f.ts_code = s.ts_code AND f.trade_date = s.trade_date "
        "JOIN fx ON fx.ts_code = s.ts_code "
        "WHERE s.trade_date BETWEEN ? AND ? "
        "ORDER BY s.ts_code, s.trade_date"
    )
    d0, d1 = _norm_date(start), _norm_date(end)
    with get_con() as con:
        return con.execute(sql, [d0, d1, d0, d1]).fetchdf()


def load_hourly(ts_code: str, start=None, end=None) -> pd.DataFrame:
    """个股60分钟线（前复权）→ DataFrame[trade_time, open, high, low, close, vol, amount]。

    口径：hourly_bars_qfq 视图 OHLC 已是前复权价（见文件头核实记录）。
    vol 单位=股，amount 单位=元（与日线 手/千元 口径不同，下游注意）。
    """
    sql = (
        "SELECT trade_time, open, high, low, close, volume AS vol, amount "
        "FROM hourly_bars_qfq WHERE ts_code = ? AND frequency = '60min'"
    )
    params = [ts_code]
    if start is not None:
        sql += " AND trade_time >= ?"
        params.append(_norm_date(start))
    if end is not None:
        # end 当天的小时线也要包含，故 < end+1天
        sql += " AND trade_time < ? + INTERVAL 1 DAY"
        params.append(_norm_date(end))
    sql += " ORDER BY trade_time"
    with get_con() as con:
        return con.execute(sql, params).fetchdf()


def load_index_daily(ts_code: str, start=None, end=None) -> pd.DataFrame:
    """指数日线（无复权）→ DataFrame[trade_date, open, high, low, close, vol, amount]。"""
    sql = (
        "SELECT trade_date, open, high, low, close, vol, amount "
        "FROM index_daily_bars WHERE ts_code = ?"
    )
    params = [ts_code]
    if start is not None:
        sql += " AND trade_date >= ?"
        params.append(_norm_date(start))
    if end is not None:
        sql += " AND trade_date <= ?"
        params.append(_norm_date(end))
    sql += " ORDER BY trade_date"
    with get_con() as con:
        return con.execute(sql, params).fetchdf()


def list_securities() -> pd.DataFrame:
    """全市场个股+指数清单 → DataFrame[ts_code, name, kind, market]（kind: equity/index）。

    个股取 security_master_history WHERE is_current；指数取 index_master。
    """
    sql = (
        "SELECT ts_code, security_name AS name, 'equity' AS kind, market "
        "FROM security_master_history "
        "WHERE is_current AND security_type = 'equity' "  # 实测 is_current 下仅此一种
        "UNION ALL "
        "SELECT ts_code, name, 'index' AS kind, market FROM index_master"
    )
    with get_con() as con:
        df = con.execute(sql).fetchdf()
    return df.drop_duplicates(subset=["ts_code", "kind"]).reset_index(drop=True)


def get_security_name(ts_code: str) -> str | None:
    """单标的名称（个股优先，其次指数）。"""
    with get_con() as con:
        row = con.execute(
            "SELECT security_name FROM security_master_history "
            "WHERE ts_code = ? AND is_current LIMIT 1",
            [ts_code],
        ).fetchone()
        if row:
            return row[0]
        row = con.execute(
            "SELECT name FROM index_master WHERE ts_code = ? LIMIT 1", [ts_code]
        ).fetchone()
        return row[0] if row else None


def is_index(ts_code: str) -> bool:
    """ts_code 是否为指数（存在于 index_master）。"""
    with get_con() as con:
        return (
            con.execute(
                "SELECT COUNT(*) FROM index_master WHERE ts_code = ?", [ts_code]
            ).fetchone()[0]
            > 0
        )


def latest_trade_date() -> dt.date:
    """SSE 最近一个已开市交易日。"""
    with get_con() as con:
        row = con.execute(
            "SELECT max(cal_date) FROM trading_calendar "
            "WHERE exchange = 'SSE' AND is_open = 1"
        ).fetchone()
    return row[0]


def trade_calendar(start, end) -> list[dt.date]:
    """[start, end] 内 SSE 开市交易日列表（升序）。"""
    with get_con() as con:
        df = con.execute(
            "SELECT cal_date FROM trading_calendar "
            "WHERE exchange = 'SSE' AND is_open = 1 AND cal_date >= ? AND cal_date <= ? "
            "ORDER BY cal_date",
            [_norm_date(start), _norm_date(end)],
        ).fetchdf()
    return list(df["cal_date"])


if __name__ == "__main__":
    print("latest_trade_date:", latest_trade_date())
    d = load_daily_qfq("600519.SH", start="2026-01-01")
    print("load_daily_qfq 600519.SH rows:", len(d), "| cols:", list(d.columns))
    print(d.tail(2).to_string())
    d2 = load_daily_qfq("000002.SZ", start="2026-07-01")
    print("load_daily_qfq 000002.SZ rows:", len(d2), "| last close:", d2["close"].iloc[-1])
    h = load_hourly("600519.SH", start="2026-07-01")
    print("load_hourly 600519.SH rows:", len(h), "| cols:", list(h.columns))
    print(h.tail(2).to_string())
    i = load_index_daily("000300.SH", start="2026-01-01")
    print("load_index_daily 000300.SH rows:", len(i))
    print(i.tail(2).to_string())
    u = load_daily_qfq_universe("2026-07-30", "2026-07-31")
    print("load_daily_qfq_universe rows:", len(u), "codes:", u["ts_code"].nunique())
    s = list_securities()
    print("list_securities rows:", len(s), "| equity:", (s.kind == "equity").sum(),
          "| index:", (s.kind == "index").sum())
    print("name 600519.SH:", get_security_name("600519.SH"),
          "| name 000300.SH:", get_security_name("000300.SH"))
    print("calendar 2026-07 len:", len(trade_calendar("2026-07-01", "2026-07-31")))
