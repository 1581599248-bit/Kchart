"""本地烘焙 TOP20 榜单 → 静态文件 data/baked_top20.json（提交进 git，/api/top20 只读它）。

用法：
  python scripts/bake_top20.py                 # 全量：420 自然日窗口，取 top20
  python scripts/bake_top20.py --days 30 --topn 3   # 小规模自测
需要环境变量 TS_TOKEN（tushare 兼容 API token）。

流程：trade_cal 定窗口 → 逐交易日并发拉 daily/adj_factor（全窗口）+ daily_basic
（最近 W+30 个交易日，W=scoring 换手率因子最长窗口）→ 拼面板（qfq=价×f/窗口内最新f）
→ 可投资域过滤（上市≥120交易日 且 20日中位成交额≥5000万）→ scoring 打分 →
补 name/change_pct/analysis_brief → 写 JSON。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from backend.app import config, scoring, ts_api  # noqa: E402

BUFFER_DAYS = 420                 # 默认窗口（自然日），与原 precompute_scores.py 一致
MIN_DAYS_SINCE_LISTING = 120      # 上市满 120 个交易日
MIN_MEDIAN_AMOUNT = 5e7           # 20 日中位成交额 5000 万 CNY
TURN_WINDOW = max(scoring.TURN_WIN, scoring.ABN_WIN)  # 换手率因子最长窗口（ABNTURN=250）
MAX_WORKERS = 6

_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount"
_FACTOR_FIELDS = "ts_code,trade_date,adj_factor"
_BASIC_FIELDS = "ts_code,trade_date,turnover_rate"


def _fetch_day(d: dt.date, with_basic: bool, retries: int = 2) -> dict:
    """拉单个交易日的 daily(+adj_factor+daily_basic)，失败重试 retries 次。"""
    ymd = d.strftime("%Y%m%d")
    for attempt in range(retries + 1):
        try:
            out = {
                "daily": ts_api.call_api("daily", params={"trade_date": ymd},
                                         fields=_DAILY_FIELDS),
                "factor": ts_api.call_api("adj_factor", params={"trade_date": ymd},
                                          fields=_FACTOR_FIELDS),
            }
            if with_basic:
                out["basic"] = ts_api.call_api("daily_basic", params={"trade_date": ymd},
                                               fields=_BASIC_FIELDS)
            return out
        except Exception as e:
            if attempt >= retries:
                raise
            print(f"  {ymd} 拉取失败（{e}），重试 {attempt + 1}/{retries}")
            time.sleep(2)


def _build_panel(days: list[dt.date], basic_days: set[dt.date], latest: dt.date) -> pd.DataFrame:
    """逐交易日并发拉取并组装全市场面板（原始价 + 因子 + 换手率）。"""
    t0 = time.time()
    daily_parts, factor_parts, basic_parts = [], [], []
    failed = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_fetch_day, d, d in basic_days): d for d in days}
        for i, fut in enumerate(as_completed(futs), 1):
            d = futs[fut]
            try:
                r = fut.result()
                daily_parts.append(r["daily"])
                factor_parts.append(r["factor"])
                if "basic" in r:
                    basic_parts.append(r["basic"])
            except Exception as e:
                if d == latest:
                    print(f"错误：最新交易日 {d} 拉取失败（{e}），榜单日期无法保证有效，退出")
                    sys.exit(1)
                failed.append(d)
                print(f"  警告：{d} 多次重试仍失败（{e}），跳过该日")
            if i % 20 == 0 or i == len(futs):
                print(f"  进度 {i}/{len(futs)} 天，已用 {time.time() - t0:.0f}s")
    if failed:
        print(f"共 {len(failed)} 天跳过: {[str(d) for d in sorted(failed)]}")
    if not daily_parts:
        print("错误：未拉到任何日线数据")
        sys.exit(1)

    daily = pd.concat(daily_parts, ignore_index=True)
    factor = pd.concat(factor_parts, ignore_index=True)
    for df in (daily, factor):
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    panel = daily.merge(factor[["ts_code", "trade_date", "adj_factor"]],
                        on=["ts_code", "trade_date"], how="inner")
    if basic_parts:
        basic = pd.concat(basic_parts, ignore_index=True)
        basic["trade_date"] = pd.to_datetime(basic["trade_date"], format="%Y%m%d")
        panel = panel.merge(basic[["ts_code", "trade_date", "turnover_rate"]],
                            on=["ts_code", "trade_date"], how="left")
    else:
        panel["turnover_rate"] = float("nan")
    return panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=BUFFER_DAYS, help="回看窗口（自然日）")
    ap.add_argument("--topn", type=int, default=20, help="榜单只数")
    ap.add_argument("--out", default=str(config.DATA_DIR / "baked_top20.json"), help="输出路径")
    args = ap.parse_args()
    t0 = time.time()

    # 1) 日历与窗口
    latest = ts_api.latest_trade_date()
    full_cal = ts_api.trade_calendar(dt.date(1990, 12, 19), latest)  # 全日历，算上市天数用
    start = latest - dt.timedelta(days=args.days)
    days = [d for d in full_cal if d >= start]
    # 2) 换手率只需最近 W+30 个交易日（W=250，保证最新日 TURN20/ABNTURN 取值精确）
    basic_days = set(days[-(TURN_WINDOW + 30):])
    print(f"榜单日期 {latest} | 窗口 {days[0]} ~ {latest} 共 {len(days)} 个交易日"
          f"（daily_basic 仅 {len(basic_days)} 天）")

    # 3) 拉数据拼面板
    panel = _build_panel(days, basic_days, latest)
    print(f"面板 {len(panel):,} 行，{panel['ts_code'].nunique()} 只")

    # 榜单日期 = 面板内实际有数据的最新交易日
    # （日历最新日可能尚未收盘/数据未发布，如早盘运行时，自动回退到最近有数据的交易日）
    score_date = panel["trade_date"].max()
    if score_date.date() < latest:
        print(f"提示：{latest} 尚无日线数据（未收盘或未发布），榜单日期回退为 {score_date.date()}")
    latest_ts = score_date

    # 4) 前复权：OHLC × f / 窗口内最新 f（口径同原 db.py）；vol/amount 不动
    f_max = panel.groupby("ts_code")["adj_factor"].transform("max")
    ratio = panel["adj_factor"] / f_max
    for c in ("open", "high", "low", "close"):
        panel[c] = panel[c] * ratio

    # 5) 派生列：上市天数（日历序号差）、20 日中位成交额（元）
    sec = ts_api.list_securities()
    names = dict(zip(sec["ts_code"], sec["name"]))
    list_dates = dict(zip(sec["ts_code"], sec.get("list_date")))
    cal_idx = {d: i for i, d in enumerate(full_cal)}
    import bisect
    cal_list = sorted(full_cal)

    def _days_since(code, trade_date):
        ld = list_dates.get(code)
        if not ld or not isinstance(ld, str):
            return 0
        ld_date = dt.datetime.strptime(ld, "%Y%m%d").date()
        i0 = bisect.bisect_left(cal_list, ld_date)   # 上市日（含）之后首个交易日序号
        return cal_idx.get(trade_date.date(), 0) - i0

    panel["days_since_listing"] = [
        _days_since(c, d) for c, d in zip(panel["ts_code"], panel["trade_date"])
    ]
    panel["median_amount_cny_20"] = (
        panel.groupby("ts_code")["amount"]
        .transform(lambda s: s.rolling(20, min_periods=20).median()) * 1000.0
    )

    # 6) 可投资域过滤：取榜单日期满足条件的股票，保留其窗口内全部历史
    # （原 precompute 逐行过滤在 420 日窗口下等价；小窗口下逐行过滤会丢光历史，故按股票过滤）
    last_rows = panel[panel["trade_date"] == latest_ts]
    ok_codes = set(last_rows.loc[(last_rows["days_since_listing"] >= MIN_DAYS_SINCE_LISTING)
                                 & (last_rows["median_amount_cny_20"] >= MIN_MEDIAN_AMOUNT),
                                 "ts_code"])
    inv = panel[panel["ts_code"].isin(ok_codes)].copy()
    print(f"可投资域 {len(ok_codes)} 只")

    # 7) 打分，取榜单日期 top N
    fac = scoring.compute_raw_factors(inv)
    scores = scoring.compute_scores_panel(fac, dates={latest_ts})
    if scores.empty:
        print(f"错误：{latest_ts.date()} 无可打分股票（窗口可能太短）")
        sys.exit(1)
    top = scores.sort_values("rank").head(args.topn)

    # 8) 补 name / change_pct / analysis_brief
    pct = (panel.loc[panel["trade_date"] == latest_ts, ["ts_code", "pct_chg"]]
           .set_index("ts_code")["pct_chg"].to_dict())
    items = []
    for r in top.itertuples():
        groups = {g: (round(float(getattr(r, g)), 4) if pd.notna(getattr(r, g)) else None)
                  for g in scoring.GROUPS}
        items.append({
            "rank": int(r.rank), "ts_code": r.ts_code,
            "name": names.get(r.ts_code, r.ts_code),
            "score": round(float(r.score), 1),
            "group_scores": groups,
            "change_pct": (round(float(pct[r.ts_code]), 4)
                           if r.ts_code in pct and pd.notna(pct[r.ts_code]) else None),
            "analysis_brief": scoring.make_analysis_brief(groups),
        })

    # 9) 写 JSON（UTF-8，不转义中文；该文件提交进 git）
    score_date_s = str(latest_ts.date())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"date": score_date_s, "items": items},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    # 10) 摘要
    print(f"\nTOP{len(items)} 榜单（{score_date_s}）前 5：")
    for it in items[:5]:
        print(f"  {it['rank']:>2}. {it['ts_code']} {it['name']} "
              f"score={it['score']} change={it['change_pct']}% {it['analysis_brief']}")
    print(f"已写入 {out}，总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
