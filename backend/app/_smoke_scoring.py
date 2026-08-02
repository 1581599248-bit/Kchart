"""scoring.py + backtest.py 单元级自检（合成数据，不依赖权威库）。

构造 100 只虚拟股票 × 3 年（约 750 个交易日）面板，验证：
1. 因子数值正确性（RET5/TURN20/VOL20 手算比对）；
2. 方向正确性（G1 组得分与 RET5 原始值负相关；G4 与 VOL20 负相关）；
3. 无未来函数（截断面板后同一日期得分逐值一致 —— shift/rolling 方向检查）；
4. 无 NaN 泄漏（预热期后得分无缺失、排名连续）；
5. G5 结构因子：人工双底在右侧确认前不得有分值、突破后分值为正并衰减；
6. backtest.simulate：一字涨停顺延、净值序列完整、换手率为正、DSR 可算。

运行：python -m backend.app._smoke_scoring
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import backtest, scoring

N_STOCKS = 100
N_DAYS = 750
SEED = 42


def make_panel() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    frames = []
    for k in range(N_STOCKS):
        code = f"T{k:04d}.SZ"
        drift = rng.normal(0.0002, 0.0003)
        vol = abs(rng.normal(0.02, 0.008))
        ret = rng.normal(drift, vol, N_DAYS)
        # #0 近期暴跌（G1 应高分），#1 近期暴涨（G1 应低分）
        if k == 0:
            ret[-5:] = -0.04
        if k == 1:
            ret[-5:] = 0.05
        close = 10 * np.exp(np.cumsum(ret))
        open_ = close * (1 + rng.normal(0, 0.003, N_DAYS))
        high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.004, N_DAYS)))
        low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.004, N_DAYS)))
        volume = rng.lognormal(15, 0.5, N_DAYS)
        turn = np.clip(rng.lognormal(0.5, 0.6, N_DAYS), 0.05, 30)
        frames.append(pd.DataFrame({
            "ts_code": code, "trade_date": dates,
            "open": open_, "high": high, "low": low, "close": close,
            "vol": volume, "amount": volume * close,
            "turnover_rate": turn,
            # 回测执行列（默认无涨跌停限制）
            "open_raw": open_, "low_raw": low, "high_raw": high, "close_raw": close,
            "limit_up_price": close * 1.5, "limit_down_price": close * 0.5,
            "hit_limit_up": False, "hit_limit_down": False,
        }))
    return pd.concat(frames, ignore_index=True)


def make_double_bottom_panel() -> pd.DataFrame:
    """单股人工双底：i1=100, i2=140 两底等低，颈线在 i=120，i=170 收盘突破。"""
    n = 300
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = np.full(n, 10.0)
    low = np.full(n, 9.5)
    high = np.full(n, 10.5)
    low[100] = 8.0
    low[140] = 8.05                      # 双底，价差 < 5%
    high[120] = 11.0                     # 颈线
    close[100] = close[140] = 8.5
    close[170] = 11.5                    # 突破颈线
    high[170] = 11.6
    open_ = close.copy()
    return pd.DataFrame({
        "ts_code": "DB00.SZ", "trade_date": dates,
        "open": open_, "high": high, "low": low, "close": close,
        "vol": 1e6, "amount": 1e7, "turnover_rate": 1.0,
        "open_raw": open_, "low_raw": low, "high_raw": high, "close_raw": close,
        "limit_up_price": close * 1.5, "limit_down_price": close * 0.5,
        "hit_limit_up": False, "hit_limit_down": False,
    })


def main() -> None:
    panel = make_panel()
    dates = sorted(panel["trade_date"].unique())

    print("[1] 原始因子计算 ...")
    fac = scoring.compute_raw_factors(panel)

    # 1a. RET5 / TURN20 / VOL20 手算比对（抽查 #42 第 300 行）
    sub = fac[fac["ts_code"] == "T0042.SZ"].reset_index(drop=True)
    i = 300
    manual = sub["close"].iloc[i] / sub["close"].iloc[i - 5] - 1
    assert abs(sub["RET5"].iloc[i] - manual) < 1e-12, "RET5 数值错误"
    manual_t = sub["turnover_rate"].iloc[i - 19:i + 1].mean()
    assert abs(sub["TURN20"].iloc[i] - manual_t) < 1e-9, "TURN20 数值错误"
    ret1 = sub["close"].pct_change()
    manual_v = ret1.iloc[i - 19:i + 1].std()
    assert abs(sub["VOL20"].iloc[i] - manual_v) < 1e-9, "VOL20 数值错误"
    print("    因子数值抽查通过（RET5/TURN20/VOL20）")

    print("[2] 横截面打分 ...")
    scores = scoring.compute_scores_panel(fac)
    warm = scores[scores["trade_date"] >= dates[300]]
    assert warm["score"].notna().all(), "预热期后得分存在 NaN（NaN 泄漏）"
    assert ((warm["score"] > 0) & (warm["score"] <= 100)).all(), "得分越界 0~100"
    for d, gday in warm.groupby("trade_date"):
        r = sorted(gday["rank"].astype(int))
        assert r == list(range(1, len(r) + 1)), f"{d} 排名不连续"
    print(f"    得分范围/排名连续性通过（{warm['trade_date'].nunique()} 个截面）")

    print("[3] 方向检查 ...")
    last = dates[-1]
    day = scores[scores["trade_date"] == last].merge(
        fac[fac["trade_date"] == last][["ts_code", "RET5", "VOL20", "TURN20"]], on="ts_code")
    rho_g1 = day["G1"].rank().corr(day["RET5"].rank())
    rho_g4 = day["G4"].rank().corr(day["VOL20"].rank())
    assert rho_g1 < -0.5, f"G1 方向错误 (ρ={rho_g1:.3f})"
    assert rho_g4 < -0.5, f"G4 方向错误 (ρ={rho_g4:.3f})"
    s0 = day.loc[day["ts_code"] == "T0000.SZ", "G1"].iloc[0]
    s1 = day.loc[day["ts_code"] == "T0001.SZ", "G1"].iloc[0]
    assert s0 > s1, "暴跌股 G1 应高于暴涨股"
    print(f"    G1xRET5 rho={rho_g1:.3f}，G4xVOL20 rho={rho_g4:.3f}；"
          f"暴跌股 G1={s0:.2f} > 暴涨股 {s1:.2f} ✓")

    print("[4] 无未来函数检查（截断一致性）...")
    T = dates[600]
    fac_trunc = scoring.compute_raw_factors(panel[panel["trade_date"] <= T])
    sc_full = scoring.compute_scores_panel(fac, dates={T}).set_index("ts_code")
    sc_trunc = scoring.compute_scores_panel(fac_trunc, dates={T}).set_index("ts_code")
    common = sc_full.index.intersection(sc_trunc.index)
    assert len(common) > 0
    diff = (sc_full.loc[common, "score"] - sc_trunc.loc[common, "score"]).abs().max()
    assert diff < 1e-9, f"截断后得分变化 {diff}，存在未来函数！"
    g5_diff = (sc_full.loc[common, "G5"] - sc_trunc.loc[common, "G5"]).abs().max()
    assert g5_diff < 1e-9, f"G5 截断后变化 {g5_diff}，结构因子存在未来函数！"
    print(f"    截断日 {T.date()} 全池得分逐值一致（max|d|={diff:.2e}）✓")

    print("[5] G5 双底结构检查 ...")
    dbp = make_double_bottom_panel()
    g5 = scoring.compute_raw_factors(dbp)["G5"].to_numpy()
    # 第二底 i2=140，右确认 145：之前必须为 0
    assert (g5[:145] == 0).all(), "右侧确认前出现结构分值（未来函数）"
    assert g5[146] > 0, "第二底确认后构筑中应为正分"
    assert g5[170] > g5[169], "突破日分值应跳升"
    assert g5[200] < g5[170], "突破后应随半衰期衰减"
    print(f"    确认前=0，构筑中 g5[146]={g5[146]:.3f}，突破 g5[170]={g5[170]:.3f}，"
          f"衰减 g5[200]={g5[200]:.3f} ✓")

    print("[6] backtest.simulate 逻辑检查 ...")
    inv_scores = scores.copy()
    exec_panel = panel.copy()
    # 撮合情形：最后一个信号周排名第一的股票 exec 日一字涨停 → 应顺延
    sig_all = backtest.weekly_signal_dates(list(dates))
    last_sig = [s for s in sig_all if s < dates[-1]][-1]
    cal_idx = {d: i for i, d in enumerate(dates)}
    exec_d = dates[cal_idx[last_sig] + 1]
    top_code = inv_scores[inv_scores["trade_date"] == last_sig] \
        .sort_values("score", ascending=False)["ts_code"].iloc[0]
    m = (exec_panel["ts_code"] == top_code) & (exec_panel["trade_date"] == exec_d)
    lim = exec_panel.loc[m, "close"].iloc[0] * 1.1
    exec_panel.loc[m, "limit_up_price"] = lim
    exec_panel.loc[m, "open_raw"] = lim
    exec_panel.loc[m, "low_raw"] = lim
    exec_panel.loc[m, "high_raw"] = lim
    exec_panel.loc[m, "hit_limit_up"] = True

    sim = backtest.simulate(inv_scores, exec_panel, list(dates), dates[300], dates[-1], top_n=10)
    nav = sim["nav"]
    assert nav.notna().all() and (nav > 0).all(), "净值异常"
    assert sim["trades"]["skipped_limit_up"] >= 1, "一字涨停未触发顺延"
    assert sim["turnover_weekly"] > 0, "换手率异常"
    wk = sim["weekly_nav"]
    assert len(wk) >= 2, "周净值不足"
    dsr = backtest._dsr_report(wk, n_trials=3)
    assert dsr["dsr"] is not None and 0 <= dsr["dsr"] <= 1, "DSR 计算异常"
    print(f"    净值 {len(nav)} 个点，买入 {sim['trades']['buys']} 次，"
          f"一字涨停顺延 {sim['trades']['skipped_limit_up']} 次，"
          f"周换手 {sim['turnover_weekly']:.1%}，DSR={dsr['dsr']:.3f} ✓")

    print("\n全部单元级检查通过 ✔")


if __name__ == "__main__":
    main()
