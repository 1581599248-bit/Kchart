"""周度轮换 TOP N 回测（ARCHITECTURE.md §3 backtest.py；MODEL_DESIGN.md §5-§6）。

规则（全部冻结，不做历史拟合）：
- 信号日 = 当周最后一个交易日收盘后打分；下一交易日开盘价买入；下周最后交易日收盘价卖出。
- 成本：佣金万2.5双边 + 滑点千1双边 + 卖出印花税万5（config 常量）。
- 可投资域：strict 池 ∧ days_since_listing>=120 ∧ median_amount_cny_20>=5000万。
- 撮合约束：买入日一字涨停顺延排名下一位；卖出日一字跌停/停牌持有至下一可交易日；
  停牌期间按最后可得收盘价冻结。
- 基准：当期可投资池等权组合（日频）+ 沪深300。

防过拟合报告（每次运行必输出）：
a) DSR（Bailey & López de Prado 2014），N 从 results_db system_meta['dsr_trial_count'] 读取
   （缺失时按协议默认至少记 3），报告 DSR/判定(>0.95)/MinBTL；
b) 分年度收益表；c) 五组各自等权子组合累计收益；d) 滚动12个月 ICIR 加权对照曲线；
e) 五组得分两年样本 Spearman 相关时序均值矩阵，|ρ|>0.6 报警。

数据加载与模拟解耦：`_load_panel` 负责权威库只读查询（memory_limit 4GB、按年分块），
`simulate` 只吃内存 DataFrame（可用合成数据单测，不依赖数据库）。
禁止逐股逐周循环拉库：因子与得分全部由 scoring.py 面板向量化产出。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics

import duckdb
import numpy as np
import pandas as pd

from . import config, results_db, scoring

# 可投资域阈值（MODEL_DESIGN.md §6，冻结）
MIN_DAYS_SINCE_LISTING = 120
MIN_MEDIAN_AMOUNT = 5e7          # 20日中位成交额 5000万 CNY
ONE_WORD_TOL = 0.001             # 一字板判定容差（开/低/高价贴限价 0.1%）

RF_ANNUAL = 0.02                 # 无风险利率 2%
TRADING_DAYS = 252
WEEKS_PER_YEAR = 52
ICIR_ROLL_WEEKS = 52             # 滚动 12 个月 ≈ 52 周
CORR_SAMPLE_YEARS = 2            # 因子相关矩阵样本：近两年
CORR_ALERT = 0.6
EULER_GAMMA = 0.5772
DEFAULT_DSR_TRIALS = 3           # 协议：N 默认至少记 3

_MEMORY_LIMIT = "4GB"

_PANEL_COLS = (
    "s.ts_code, s.trade_date, "
    "b.open * f.adj_factor / fx.max_f AS open, "
    "b.high * f.adj_factor / fx.max_f AS high, "
    "b.low  * f.adj_factor / fx.max_f AS low, "
    "b.close * f.adj_factor / fx.max_f AS close, "
    "b.open AS open_raw, b.low AS low_raw, b.high AS high_raw, b.close AS close_raw, "
    "b.vol, b.amount, v.turnover_rate, s.median_amount_cny_20, "
    "st.limit_up_price, st.limit_down_price, st.hit_limit_up, st.hit_limit_down, "
    "st.is_suspended, st.days_since_listing"
)

_PANEL_SQL = (
    "WITH f AS ("
    "  SELECT ts_code, trade_date, adj_factor FROM adj_factors_full"
    "  WHERE trade_date BETWEEN ? AND ?"
    "), fx AS ("
    "  SELECT ts_code, max(adj_factor) AS max_f FROM adj_factors_full GROUP BY ts_code"
    ") "
    f"SELECT {_PANEL_COLS} "
    "FROM research_daily_bars_strict s "
    "JOIN daily_bars_full b ON b.ts_code = s.ts_code AND b.trade_date = s.trade_date "
    "JOIN f  ON f.ts_code = s.ts_code AND f.trade_date = s.trade_date "
    "JOIN fx ON fx.ts_code = s.ts_code "
    "LEFT JOIN valuation_equity_daily v "
    "  ON v.ts_code = s.ts_code AND v.trade_date = strftime(s.trade_date, '%Y%m%d') "
    "LEFT JOIN security_trading_status st "
    "  ON st.ts_code = s.ts_code AND st.trade_date = s.trade_date "
    "WHERE s.trade_date BETWEEN ? AND ? "
    "ORDER BY s.ts_code, s.trade_date"
)


def _auth_con() -> duckdb.DuckDBPyConnection:
    """权威库只读连接（任务级内存上限 4GB）。"""
    con = duckdb.connect(config.AUTH_DB_PATH, read_only=True)
    con.execute(f"SET memory_limit='{_MEMORY_LIMIT}'")
    con.execute("SET enable_progress_bar=false")
    return con


def _load_panel(start, end, con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    """strict 池全池面板（前复权 OHLC + 换手 + 成交额 + 涨跌停状态），限定必要列。"""
    own = con is None
    con = con or _auth_con()
    try:
        return con.execute(_PANEL_SQL, [start, end, start, end]).fetchdf()
    finally:
        if own:
            con.close()


def _load_panel_chunked(start, end, buffer_days: int = 420) -> pd.DataFrame:
    """按日历年分块加载 [start-buffer_days, end]，控制单次查询内存。"""
    start = pd.Timestamp(start) - pd.Timedelta(days=buffer_days)
    end = pd.Timestamp(end)
    chunks = []
    with _auth_con() as con:
        for year in range(start.year, end.year + 1):
            lo = max(start, pd.Timestamp(year, 1, 1))
            hi = min(end, pd.Timestamp(year, 12, 31))
            if lo > hi:
                continue
            chunks.append(_load_panel(lo.date(), hi.date(), con=con))
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True).drop_duplicates(
        subset=["ts_code", "trade_date"]
    )


def _trade_calendar(start, end) -> list[pd.Timestamp]:
    con = _auth_con()
    try:
        df = con.execute(
            "SELECT cal_date FROM trading_calendar "
            "WHERE exchange='SSE' AND is_open=1 AND cal_date BETWEEN ? AND ? ORDER BY cal_date",
            [start, end],
        ).fetchdf()
    finally:
        con.close()
    return list(pd.to_datetime(df["cal_date"]))


def _load_csi300(start, end) -> pd.Series:
    con = _auth_con()
    try:
        df = con.execute(
            "SELECT trade_date, close FROM index_daily_bars "
            "WHERE ts_code='000300.SH' AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [start, end],
        ).fetchdf()
    finally:
        con.close()
    s = pd.Series(df["close"].to_numpy(), index=pd.to_datetime(df["trade_date"]))
    return s / s.iloc[0]


# ------------------------------------------------------------------ 周历

def weekly_signal_dates(calendar: list[pd.Timestamp]) -> list[pd.Timestamp]:
    """每个 ISO 周的最后一个交易日（信号日）。"""
    cal = pd.Series(calendar)
    iso = cal.dt.isocalendar()
    grp = iso["year"].astype(int) * 100 + iso["week"].astype(int)
    return list(cal.groupby(grp).max())


# ------------------------------------------------------------------ 模拟核心

class _Bars:
    """单股日线数组（searchsorted 定位，O(log n) 查价）。"""
    __slots__ = ("dates", "open", "close", "open_raw", "low_raw", "high_raw",
                 "limit_up", "limit_down", "hit_up", "hit_down")

    def __init__(self, sub: pd.DataFrame):
        self.dates = sub["trade_date"].to_numpy()
        self.open = sub["open"].to_numpy(dtype=float)
        self.close = sub["close"].to_numpy(dtype=float)
        self.open_raw = sub["open_raw"].to_numpy(dtype=float)
        self.low_raw = sub["low_raw"].to_numpy(dtype=float)
        self.high_raw = sub["high_raw"].to_numpy(dtype=float)
        self.limit_up = sub["limit_up_price"].to_numpy(dtype=float)
        self.limit_down = sub["limit_down_price"].to_numpy(dtype=float)
        self.hit_up = sub["hit_limit_up"].fillna(False).to_numpy(dtype=bool)
        self.hit_down = sub["hit_limit_down"].fillna(False).to_numpy(dtype=bool)

    def idx(self, d) -> int:
        i = np.searchsorted(self.dates, np.datetime64(d))
        if i < len(self.dates) and self.dates[i] == np.datetime64(d):
            return i
        return -1

    def last_close(self, d) -> float:
        """d 日或之前最后可得收盘价（停牌冻结口径）。"""
        i = np.searchsorted(self.dates, np.datetime64(d), side="right") - 1
        return self.close[i] if i >= 0 else np.nan

    def one_word_up(self, i: int) -> bool:
        return bool(self.hit_up[i]) and self.low_raw[i] >= self.limit_up[i] * (1 - ONE_WORD_TOL)

    def one_word_down(self, i: int) -> bool:
        return bool(self.hit_down[i]) and self.high_raw[i] <= self.limit_down[i] * (1 + ONE_WORD_TOL)


def _build_store(exec_panel: pd.DataFrame) -> dict[str, _Bars]:
    store = {}
    for code, sub in exec_panel.groupby("ts_code", sort=False):
        store[code] = _Bars(sub)
    return store


def simulate(scores: pd.DataFrame, exec_panel: pd.DataFrame,
             calendar: list[pd.Timestamp], start, end,
             top_n: int = 10, rank_col: str = "score", weight: float | None = None,
             commission: float = config.COMMISSION_RATE,
             slippage: float = config.SLIPPAGE_RATE,
             stamp: float = config.STAMP_TAX_SELL) -> dict:
    """周度轮换模拟（纯内存，可接合成数据）。

    scores：investable 过滤后的得分面板，至少含 [trade_date, ts_code, rank_col]；
    exec_panel：含 open/close/open_raw/low_raw/high_raw/limit_up/limit_down/hit 列；
    calendar：全交易日列表（pd.Timestamp）。
    返回 {nav: Series(日频), weekly_nav: Series(信号日), trades: {buys, skipped_limit_up,
    deferred_sells}, turnover_weekly: float}
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    cal = [d for d in calendar if start <= d <= end]
    if not cal:
        raise ValueError("空交易日历")
    w = weight if weight is not None else 1.0 / top_n
    buy_cost = commission + slippage
    sell_cost = commission + slippage + stamp

    sig_dates = weekly_signal_dates(cal)
    sig_dates = [d for d in sig_dates if d >= start]
    cal_set_idx = {d: i for i, d in enumerate(cal)}
    exec_day = {}
    for s in sig_dates:
        i = cal_set_idx[s]
        if i + 1 < len(cal):
            exec_day[s] = cal[i + 1]
    sig_dates = [s for s in sig_dates if s in exec_day]
    if not sig_dates:
        raise ValueError("区间内无完整调仓周")

    store = _build_store(exec_panel)
    sig_set = set(sig_dates)
    score_by_date = {
        d: sub.sort_values(rank_col, ascending=False)
        for d, sub in scores[scores["trade_date"].isin(sig_set)].groupby("trade_date")
    }

    cash = 1.0
    positions: dict[str, dict] = {}   # code -> {shares, sell_after}
    nav_idx, nav_val = [], []
    n_buys = n_skip_limit = n_defer = 0
    weekly_turnover = []
    week_buy_val = week_sell_val = 0.0

    sim_days = [d for d in cal if d >= exec_day[sig_dates[0]]]
    pending_signals = list(sig_dates)
    next_signal = pending_signals.pop(0)

    for d in sim_days:
        # 1) 卖出：所有到期的持仓（信号日收盘卖；一字跌停/无交易则顺延）
        is_sig = d in sig_set
        for code in list(positions):
            pos = positions[code]
            if d < pos["sell_after"]:
                continue
            bars = store.get(code)
            i = bars.idx(d) if bars else -1
            if i < 0:
                n_defer += 1          # 停牌/无 bar：冻结顺延
                continue
            if bars.one_word_down(i):
                n_defer += 1          # 一字跌停：持有至下一可交易日
                continue
            proceeds = pos["shares"] * bars.close[i] * (1 - sell_cost)
            cash += proceeds
            week_sell_val += pos["shares"] * bars.close[i]
            del positions[code]

        # 2) 买入：信号日次一交易日开盘
        if next_signal is not None and d == exec_day[next_signal]:
            ranked = score_by_date.get(next_signal)
            budget_each = None
            bought = 0
            if ranked is not None:
                for _, row in ranked.iterrows():
                    if bought >= top_n:
                        break
                    code = row["ts_code"]
                    if code in positions:
                        continue
                    bars = store.get(code)
                    i = bars.idx(d) if bars else -1
                    if i < 0:
                        continue       # 停牌/无 bar，顺延下一位
                    if bars.one_word_up(i):
                        n_skip_limit += 1   # 一字涨停买不进，顺延
                        continue
                    nav_now = cash + sum(
                        p["shares"] * store[c].last_close(d) for c, p in positions.items()
                    )
                    if budget_each is None:
                        budget_each = nav_now * w
                    budget = min(budget_each, cash)
                    if budget <= 1e-12:
                        break
                    shares = budget * (1 - buy_cost) / bars.open[i]
                    cash -= budget
                    week_buy_val += budget
                    positions[code] = {"shares": shares, "sell_after": _next_signal(sig_dates, next_signal)}
                    bought += 1
                    n_buys += 1
            next_signal = pending_signals.pop(0) if pending_signals else None

        # 3) 收盘估值
        nav = cash
        for code, p in positions.items():
            nav += p["shares"] * store[code].last_close(d)
        nav_idx.append(d)
        nav_val.append(nav)

        # 4) 周末记录换手
        if is_sig or d == sim_days[-1]:
            weekly_turnover.append((week_buy_val + week_sell_val) / 2.0 / max(nav, 1e-12))
            week_buy_val = week_sell_val = 0.0

    nav_s = pd.Series(nav_val, index=pd.DatetimeIndex(nav_idx), name="nav")
    weekly_nav = nav_s.reindex([d for d in sig_dates if d in nav_s.index])
    return {
        "nav": nav_s,
        "weekly_nav": weekly_nav,
        "trades": {"buys": n_buys, "skipped_limit_up": n_skip_limit, "deferred_sells": n_defer},
        "turnover_weekly": float(np.mean(weekly_turnover)) if weekly_turnover else np.nan,
    }


def _next_signal(sig_dates: list, after) -> pd.Timestamp:
    for s in sig_dates:
        if s > after:
            return s
    return pd.Timestamp.max


# ------------------------------------------------------------------ 指标

def _perf_metrics(nav: pd.Series, rf: float = RF_ANNUAL) -> dict:
    r = nav.pct_change().dropna()
    if r.empty:
        return {}
    n = len(r)
    years = n / TRADING_DAYS
    total = nav.iloc[-1] / nav.iloc[0] - 1
    annual = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    dd = (nav / nav.cummax() - 1).min()
    rf_d = rf / TRADING_DAYS
    sharpe = (r.mean() - rf_d) / r.std() * math.sqrt(TRADING_DAYS) if r.std() > 0 else np.nan
    calmar = annual / abs(dd) if dd < 0 else np.nan
    return {
        "total_return": float(total), "annual_return": float(annual),
        "max_drawdown": float(dd), "sharpe": float(sharpe),
        "calmar": float(calmar), "win_rate_daily": float((r > 0).mean()),
    }


def _weekly_win_rate(weekly_nav: pd.Series) -> float:
    r = weekly_nav.pct_change().dropna()
    return float((r > 0).mean()) if len(r) else np.nan


def _excess_ir(nav: pd.Series, bench: pd.Series) -> tuple[float, float]:
    df = pd.concat([nav.pct_change(), bench.pct_change()], axis=1, keys=["s", "b"]).dropna()
    if df.empty:
        return np.nan, np.nan
    ex = df["s"] - df["b"]
    annual_ex = float(ex.mean() * TRADING_DAYS)
    ir = float(ex.mean() / ex.std() * math.sqrt(TRADING_DAYS)) if ex.std() > 0 else np.nan
    return annual_ex, ir


def _yearly_table(nav: pd.Series, pool: pd.Series, bench: pd.Series) -> pd.DataFrame:
    df = pd.concat([nav, pool, bench], axis=1, keys=["strategy", "pool", "csi300"]).ffill().dropna()
    rows = []
    for year, sub in df.groupby(df.index.year):
        s = sub["strategy"].iloc[-1] / sub["strategy"].iloc[0] - 1
        p = sub["pool"].iloc[-1] / sub["pool"].iloc[0] - 1
        b = sub["csi300"].iloc[-1] / sub["csi300"].iloc[0] - 1
        rows.append({"year": int(year), "strategy": s, "pool": p, "csi300": b, "excess_vs_pool": s - p})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ DSR

def _dsr_report(weekly_nav: pd.Series, n_trials: int, rf: float = RF_ANNUAL) -> dict:
    """Bailey & López de Prado (2014) Deflated Sharpe Ratio（周频口径）。"""
    r = weekly_nav.pct_change().dropna()
    T = len(r)
    if T < 10:
        return {"N": n_trials, "dsr": None, "pass": None, "note": "样本周数不足"}
    rf_w = rf / WEEKS_PER_YEAR
    ex = r - rf_w
    sr = float(ex.mean() / r.std()) if r.std() > 0 else np.nan  # 周频 SR
    sr_ann = sr * math.sqrt(WEEKS_PER_YEAR)
    g3 = float(r.skew())
    g4 = float(r.kurtosis() + 3)   # 非超额峰度
    norm = statistics.NormalDist()
    var_sr = (1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) / (T - 1)
    var_sr = max(var_sr, 1e-12)
    e_max = math.sqrt(var_sr) * (
        (1 - EULER_GAMMA) * norm.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * norm.inv_cdf(1 - 1 / (n_trials * math.e))
    )
    dsr = norm.cdf((sr - e_max) / math.sqrt(var_sr))
    min_btl = 2 * math.log(n_trials) / (sr_ann ** 2) if sr_ann and sr_ann > 0 else np.inf
    return {
        "N": n_trials, "T_weeks": T, "sr_weekly": sr, "sr_annual": sr_ann,
        "skew": g3, "kurtosis": g4, "e_max_sr": float(e_max),
        "dsr": float(dsr), "pass": bool(dsr > 0.95),
        "min_btl_years": float(min_btl),
    }


def _get_dsr_trials() -> int:
    """N = results_db system_meta 累计试验次数；缺失按协议默认至少记 3 并写回。"""
    try:
        v = results_db.get_meta("dsr_trial_count")
        n = int(v) if v is not None else DEFAULT_DSR_TRIALS
    except Exception:
        n = DEFAULT_DSR_TRIALS
    n = max(n, DEFAULT_DSR_TRIALS)
    try:
        results_db.set_meta("dsr_trial_count", str(n))
    except Exception:
        pass
    return n


# ------------------------------------------------------------------ ICIR 对照与因子相关

def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 5:
        return np.nan
    ra, rb = a.rank(), b.rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(ra.corr(rb))


def _forward_weekly_returns(scores: pd.DataFrame, exec_panel: pd.DataFrame,
                            sig_dates: list, exec_map: dict, next_sig: dict) -> pd.DataFrame:
    """每个信号日 t 全池股票的前向周收益（exec 日开盘 → 下一信号日收盘），向量化 merge。"""
    rows = scores[scores["trade_date"].isin(sig_dates)][["trade_date", "ts_code"]].copy()
    rows["exec_d"] = rows["trade_date"].map(exec_map)
    rows["sell_d"] = rows["trade_date"].map(next_sig)
    px_open = exec_panel[["ts_code", "trade_date", "open"]].rename(
        columns={"trade_date": "exec_d", "open": "open_e"})
    px_close = exec_panel[["ts_code", "trade_date", "close"]].rename(
        columns={"trade_date": "sell_d", "close": "close_s"})
    out = rows.merge(px_open, on=["ts_code", "exec_d"], how="left") \
              .merge(px_close, on=["ts_code", "sell_d"], how="left")
    out["fwd_ret"] = out["close_s"] / out["open_e"] - 1.0
    return out[["trade_date", "ts_code", "fwd_ret"]]


def _icir_contrast(scores_g: pd.DataFrame, exec_panel: pd.DataFrame,
                   calendar: list, start, end, top_n: int, cost_kw: dict) -> dict:
    """滚动 12 个月 ICIR 加权 vs 固定等权 的对照曲线（只作对照，不进主模型）。"""
    cal = [d for d in calendar if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    sig_dates = [d for d in weekly_signal_dates(cal) if d >= pd.Timestamp(start)]
    cal_idx = {d: i for i, d in enumerate(cal)}
    exec_map = {s: cal[cal_idx[s] + 1] for s in sig_dates if cal_idx[s] + 1 < len(cal)}
    next_sig = {sig_dates[i]: sig_dates[i + 1] for i in range(len(sig_dates) - 1)}
    usable = [s for s in sig_dates if s in exec_map and s in next_sig]
    fwd = _forward_weekly_returns(scores_g, exec_panel, usable, exec_map, next_sig)
    glist = list(scoring.GROUPS)
    base = scores_g[scores_g["trade_date"].isin(usable)][["trade_date", "ts_code"] + glist]
    m = base.merge(fwd, on=["trade_date", "ts_code"], how="left")

    # 每周每组 Spearman IC
    ic_rows = []
    for d, sub in m.groupby("trade_date"):
        row = {"trade_date": d}
        for gname in glist:
            row[gname] = _spearman(sub[gname], sub["fwd_ret"])
        ic_rows.append(row)
    ic = pd.DataFrame(ic_rows).set_index("trade_date").sort_index()

    # ICIR 权重（滚动 52 周均值/标准差，负值截 0；首个完整窗口前等权）
    w_df = pd.DataFrame(1.0 / len(glist), index=ic.index, columns=glist)
    roll = ic.rolling(ICIR_ROLL_WEEKS, min_periods=ICIR_ROLL_WEEKS // 2)
    icir = roll.mean() / roll.std().replace(0, np.nan)
    w = icir.clip(lower=0)
    wsum = w.sum(axis=1)
    has = wsum > 0
    w_df.loc[has, :] = (w[has].div(wsum[has], axis=0)).to_numpy()

    sig_scores = scores_g[scores_g["trade_date"].isin(usable)].copy()
    sig_scores = sig_scores.merge(
        w_df.reset_index().rename(columns={"index": "trade_date"}),
        on="trade_date", how="left", suffixes=("", "_w"),
    )
    comp = 0.0
    for gname in glist:
        comp = comp + sig_scores[gname] * sig_scores[gname + "_w"].fillna(1.0 / len(glist))
    sig_scores["score_icir"] = comp

    sim = simulate(sig_scores[["trade_date", "ts_code", "score_icir"]], exec_panel,
                   calendar, start, end, top_n=top_n, rank_col="score_icir", **cost_kw)
    return {
        "nav": sim["nav"],
        "metrics": _perf_metrics(sim["nav"]),
        "ic_mean": {g: float(ic[g].mean()) for g in glist},
    }


def _factor_corr_matrix(scores_g: pd.DataFrame, end) -> tuple[pd.DataFrame, list]:
    """五组得分近两年样本 Spearman 相关（每周截面 → 时序均值）；|ρ|>0.6 报警。"""
    end = pd.Timestamp(end)
    lo = end - pd.Timedelta(days=int(CORR_SAMPLE_YEARS * 365.25))
    sub = scores_g[scores_g["trade_date"] >= lo]
    glist = list(scoring.GROUPS)
    acc = pd.DataFrame(0.0, index=glist, columns=glist)
    cnt = pd.DataFrame(0, index=glist, columns=glist)
    for _, day in sub.groupby("trade_date"):
        if len(day) < 30:
            continue
        ranks = day[glist].rank()
        c = ranks.corr()  # Spearman = Pearson(ranks)
        ok = c.notna()
        acc = acc.add(c.where(ok, 0.0), fill_value=0.0)
        cnt = cnt + ok.astype(int)
    mat = acc / cnt.replace(0, np.nan)
    alerts = []
    for i, gi in enumerate(glist):
        for gj in glist[i + 1:]:
            v = mat.loc[gi, gj]
            if pd.notna(v) and abs(v) > CORR_ALERT:
                alerts.append(f"{gi}-{gj}: ρ={v:.3f} 超过 {CORR_ALERT} 阈值，需合并或删除其一")
    return mat, alerts


# ------------------------------------------------------------------ 主入口

def run_backtest(start: str = "2016-01-01", end: str | None = None, top_n: int = 10,
                 weight: float | None = None, rebalance: str = "W",
                 universe_codes: list[str] | None = None,
                 top_amount_n: int | None = None,
                 save: bool = True, run_id: str | None = None,
                 verbose: bool = True) -> dict:
    """完整回测 + 防过拟合报告。

    universe_codes：限定股票集合（小样本验证用）；
    top_amount_n：按窗口内 median_amount_cny_20 均值取前 N 只固定集合（小样本验证用）。
    """
    assert rebalance == "W", "仅支持周度轮换"
    if end is None:
        con = _auth_con()
        try:
            end = con.execute(
                "SELECT max(cal_date) FROM trading_calendar WHERE exchange='SSE' AND is_open=1"
            ).fetchone()[0]
        finally:
            con.close()
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    t0 = pd.Timestamp.now()
    if verbose:
        print(f"[1/6] 加载面板 {start_ts.date()} ~ {end_ts.date()}（含420天缓冲，按年分块）...")
    panel = _load_panel_chunked(start_ts, end_ts)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    if verbose:
        print(f"      面板 {len(panel):,} 行 / {panel['ts_code'].nunique()} 只 "
              f"({(pd.Timestamp.now() - t0).total_seconds():.0f}s)")

    # 可投资域过滤（打分池与回测池一致：strict ∧ 上市>=120 ∧ 中位成交额>=5000万）
    inv_mask = (panel["days_since_listing"] >= MIN_DAYS_SINCE_LISTING) & \
               (panel["median_amount_cny_20"] >= MIN_MEDIAN_AMOUNT)
    if universe_codes is not None:
        inv_mask &= panel["ts_code"].isin(set(universe_codes))
    if top_amount_n is not None:
        top_codes = panel.loc[inv_mask].groupby("ts_code")["median_amount_cny_20"] \
            .mean().nlargest(top_amount_n).index
        inv_mask &= panel["ts_code"].isin(set(top_codes))
    inv_panel = panel.loc[inv_mask].copy()
    if verbose:
        print(f"[2/6] 可投资域 {inv_panel['ts_code'].nunique()} 只 / {len(inv_panel):,} 行")

    if verbose:
        print("[3/6] 面板向量化因子与横截面打分 ...")
    t1 = pd.Timestamp.now()
    fac = scoring.compute_raw_factors(inv_panel)
    scores = scoring.compute_scores_panel(fac)
    scores["trade_date"] = pd.to_datetime(scores["trade_date"])
    scores = scores[scores["trade_date"] >= start_ts]
    if verbose:
        print(f"      打分完成 ({(pd.Timestamp.now() - t1).total_seconds():.0f}s)，"
              f"信号截面 {scores['trade_date'].nunique()} 个")

    exec_panel = inv_panel[inv_panel["trade_date"] >= start_ts].copy()
    calendar = sorted(exec_panel["trade_date"].unique())
    calendar = list(pd.to_datetime(calendar))
    cost_kw = {}  # 成本用 config 默认

    if verbose:
        print("[4/6] 主组合 + 五组子组合模拟 ...")
    main = simulate(scores, exec_panel, calendar, start_ts, end_ts,
                    top_n=top_n, weight=weight, **cost_kw)
    group_sims = {}
    for gname in scoring.GROUPS:
        group_sims[gname] = simulate(
            scores[["trade_date", "ts_code", gname]], exec_panel, calendar,
            start_ts, end_ts, top_n=top_n, rank_col=gname, **cost_kw)

    if verbose:
        print("[5/6] 基准 / ICIR 对照 / 因子相关矩阵 ...")
    # 可投资池等权基准（日频，含exec_panel全部成员的日收益均值）
    ep = exec_panel.sort_values(["ts_code", "trade_date"])
    ret1 = ep.groupby("ts_code", sort=False)["close"].pct_change()
    pool_ret = ret1.groupby(ep["trade_date"]).mean()
    pool_nav = (1 + pool_ret.fillna(0)).cumprod()
    pool_nav = pool_nav / pool_nav.iloc[0]
    csi300 = _load_csi300(start_ts, end_ts)

    icir = _icir_contrast(scores, exec_panel, calendar, start_ts, end_ts, top_n, cost_kw)
    corr_mat, corr_alerts = _factor_corr_matrix(scores, end_ts)

    if verbose:
        print("[6/6] 汇总指标与防过拟合报告 ...")
    nav = main["nav"]
    metrics = _perf_metrics(nav)
    metrics["win_rate_weekly"] = _weekly_win_rate(main["weekly_nav"])
    metrics["turnover_weekly"] = main["turnover_weekly"]
    metrics["turnover_annual"] = main["turnover_weekly"] * WEEKS_PER_YEAR
    ex_p, ir_p = _excess_ir(nav, pool_nav)
    ex_b, ir_b = _excess_ir(nav, csi300)
    metrics.update({
        "excess_vs_pool_annual": ex_p, "ir_vs_pool": ir_p,
        "excess_vs_csi300_annual": ex_b, "ir_vs_csi300": ir_b,
        "trades": main["trades"],
    })
    bench_metrics = {
        "pool": _perf_metrics(pool_nav), "csi300": _perf_metrics(csi300),
    }
    yearly = _yearly_table(nav, pool_nav, csi300)
    n_trials = _get_dsr_trials()
    dsr = _dsr_report(main["weekly_nav"], n_trials)
    group_curves = {
        g: {"total_return": float(s["nav"].iloc[-1] / s["nav"].iloc[0] - 1),
            "metrics": _perf_metrics(s["nav"])}
        for g, s in group_sims.items()
    }

    nav_df = pd.concat([nav, csi300, pool_nav], axis=1,
                       keys=["nav", "bench_nav", "pool_nav"]).ffill().dropna().reset_index()
    nav_df.columns = ["trade_date", "nav", "bench_nav", "pool_nav"]
    nav_df["trade_date"] = nav_df["trade_date"].dt.date

    result = {
        "params": {"start": str(start_ts.date()), "end": str(end_ts.date()),
                   "top_n": top_n, "weight": weight or 1.0 / top_n,
                   "universe_codes": len(universe_codes) if universe_codes else None,
                   "top_amount_n": top_amount_n,
                   "model_version": scoring.MODEL_VERSION},
        "metrics": metrics,
        "benchmarks": bench_metrics,
        "yearly": yearly,
        "dsr": dsr,
        "group_curves": group_curves,
        "icir_contrast": {"metrics": icir["metrics"], "ic_mean": icir["ic_mean"]},
        "corr_matrix": corr_mat,
        "corr_alerts": corr_alerts,
        "nav": nav,
        "nav_df": nav_df,
        "scores": scores,
        "elapsed_sec": (pd.Timestamp.now() - t0).total_seconds(),
    }

    if save:
        rid = run_id or f"bt_{start_ts:%Y%m%d}_{end_ts:%Y%m%d}_top{top_n}"
        metrics_json = {
            "metrics": metrics, "benchmarks": bench_metrics,
            "yearly": yearly.to_dict("records"), "dsr": dsr,
            "group_curves": group_curves, "icir_contrast": result["icir_contrast"],
            "corr_matrix": corr_mat.round(4).to_dict(), "corr_alerts": corr_alerts,
            "elapsed_sec": result["elapsed_sec"],
        }
        results_db.save_backtest(rid, result["params"],
                                 json.loads(json.dumps(metrics_json, default=str)), nav_df)
        results_db.set_meta("last_backtest_run", rid)
        result["run_id"] = rid
        if verbose:
            print(f"      已落库 run_id={rid}")
    return result


def print_report(res: dict) -> None:
    """人类可读完整报告（命令行/自检共用）。"""
    m = res["metrics"]
    p = res["params"]
    print("=" * 72)
    print(f"回测 {p['start']} ~ {p['end']}  TOP{p['top_n']} 周度轮换  模型 {p['model_version']}")
    print("=" * 72)
    print(f"总收益      : {m['total_return']:>10.2%}   年化: {m['annual_return']:>8.2%}")
    print(f"最大回撤    : {m['max_drawdown']:>10.2%}   夏普(rf=2%): {m['sharpe']:>6.3f}   卡玛: {m['calmar']:.3f}")
    print(f"日胜率      : {m['win_rate_daily']:>10.2%}   周胜率: {m['win_rate_weekly']:>8.2%}")
    print(f"周换手(单边): {m['turnover_weekly']:>10.2%}   年化换手: {m['turnover_annual']:>8.1f}x")
    print(f"超额(vs池)  : {m['excess_vs_pool_annual']:>10.2%}   IR: {m['ir_vs_pool']:>8.3f}")
    print(f"超额(vs300) : {m['excess_vs_csi300_annual']:>10.2%}   IR: {m['ir_vs_csi300']:>8.3f}")
    print(f"撮合        : 买入{m['trades']['buys']}次 一字涨停顺延{m['trades']['skipped_limit_up']}次 "
          f"卖出顺延{m['trades']['deferred_sells']}次")
    b = res["benchmarks"]
    print("-" * 72)
    print(f"基准 池等权 : 年化 {b['pool'].get('annual_return', float('nan')):>8.2%}  "
          f"回撤 {b['pool'].get('max_drawdown', float('nan')):>8.2%}")
    print(f"基准 沪深300: 年化 {b['csi300'].get('annual_return', float('nan')):>8.2%}  "
          f"回撤 {b['csi300'].get('max_drawdown', float('nan')):>8.2%}")
    print("-" * 72)
    print("分年度收益表：")
    y = res["yearly"].copy()
    for c in ["strategy", "pool", "csi300", "excess_vs_pool"]:
        y[c] = (y[c] * 100).round(1).astype(str) + "%"
    print(y.to_string(index=False))
    print("-" * 72)
    print("五组各自等权子组合（总收益 / 年化 / 最大回撤 / 夏普）：")
    for gname, gc in res["group_curves"].items():
        gm = gc["metrics"]
        print(f"  {gname} {scoring.GROUP_NAMES[gname]:<5}: {gc['total_return']:>9.2%} / "
              f"{gm.get('annual_return', float('nan')):>8.2%} / "
              f"{gm.get('max_drawdown', float('nan')):>8.2%} / {gm.get('sharpe', float('nan')):>6.3f}")
    ic = res["icir_contrast"]
    print(f"ICIR 对照组: 年化 {ic['metrics'].get('annual_return', float('nan')):.2%}  "
          f"夏普 {ic['metrics'].get('sharpe', float('nan')):.3f}   "
          f"(各组周IC均值: {', '.join(f'{k}={v:+.4f}' for k, v in ic['ic_mean'].items())})")
    print("-" * 72)
    print("五组得分 Spearman 相关矩阵（近两年时序均值）：")
    print(res["corr_matrix"].round(3).to_string())
    if res["corr_alerts"]:
        print("⚠ 相关报警：" + "；".join(res["corr_alerts"]))
    else:
        print(f"相关性检查通过（无 |ρ|>{CORR_ALERT} 的组对）")
    print("-" * 72)
    d = res["dsr"]
    if d.get("dsr") is not None:
        verdict = "通过(>0.95)" if d["pass"] else "未通过(<=0.95)"
        print(f"DSR 防过拟合: N={d['N']} T={d['T_weeks']}周 SR(周)={d['sr_weekly']:.4f} "
              f"SR(年)={d['sr_annual']:.3f} E[maxSR]={d['e_max_sr']:.4f}")
        print(f"  DSR={d['dsr']:.4f} → {verdict}；MinBTL={d['min_btl_years']:.2f} 年")
    else:
        print(f"DSR: {d.get('note')}")
    print(f"耗时: {res['elapsed_sec']:.0f}s   run_id: {res.get('run_id', '(未落库)')}")


if __name__ == "__main__":
    # 质量红线 §5 冒烟：小样本（成交额前 80 只、近一年）跑通全流程
    _end = dt.date(2026, 7, 31)
    res = run_backtest("2025-08-01", _end, top_n=10, top_amount_n=80, save=False)
    print_report(res)
