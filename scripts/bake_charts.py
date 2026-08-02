"""本地烘焙 8 宽基指数的 1d K线与推背图分析 → data/baked_charts.json（提交入仓）。

用途：Render 冷启动时 /api/bars、/api/analysis 直接读烘焙文件秒回，
服务端启动后由后台线程增量追新（见 main.py _warm_baked_symbols）。

用法：python scripts/bake_charts.py     # 标的固定为 config.BROAD_INDEX_CODES 8 个指数

产出结构（单行 JSON，UTF-8，ensure_ascii=False）：
{"date": 最新交易日, "version": 1,
 "symbols": {ts_code: {"kind":"index", "name", "bars": [{time,o,h,l,c,v,amount}...],
                       "analysis": {与 /api/analysis 响应同构}}}}
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import config, ts_api  # noqa: E402
from backend.app import analysis as analysis_mod  # noqa: E402
# import main 不会启动服务（uvicorn 仅在 __main__ 下运行）
from backend.app.main import _annotations_to_epoch, _json_safe, _to_epoch_sec  # noqa: E402

_OUT = config.DATA_DIR / "baked_charts.json"


def _bake_index(ts_code: str) -> dict:
    """单指数：KLINE_DISPLAY_START（2020）起 1d bars + 推背图分析（与 /api/bars、/api/analysis 同口径）。"""
    df = ts_api.load_index_daily(ts_code)
    df = df[df["trade_date"] >= pd.Timestamp(config.KLINE_DISPLAY_START)].reset_index(drop=True)
    if df.empty or len(df) < 60:
        raise RuntimeError(f"数据不足（{len(df)} 行）")
    name = ts_api.get_security_name(ts_code) or config.BROAD_INDEX_NAMES.get(ts_code, ts_code)

    # bars：与 /api/bars 输出形态一致（time = naive 日期当 UTC 的 UNIX 秒）
    ts = _to_epoch_sec(df["trade_date"])
    bars = [
        {"time": int(t), "o": _json_safe(r.open), "h": _json_safe(r.high),
         "l": _json_safe(r.low), "c": _json_safe(r.close),
         "v": _json_safe(r.vol), "amount": _json_safe(r.amount)}
        for t, r in zip(ts, df.itertuples())
    ]

    # analysis：与 /api/analysis 同构（analyze + annotations 转 epoch + 补充字段）
    result = analysis_mod.analyze(df.copy(), "1d")
    dfts = df.rename(columns={"trade_date": "ts"})
    result["annotations"] = _annotations_to_epoch(result["annotations"], dfts)
    result["ts_code"] = ts_code
    result["timeframe"] = "1d"
    result["asof_date"] = str(df["trade_date"].iloc[-1].date())
    result["name"] = name

    return {"kind": "index", "name": name, "bars": bars, "analysis": result}


def main() -> None:
    symbols = list(config.BROAD_INDEX_CODES)
    print(f"标的 {len(symbols)} 个宽基指数")
    t0 = time.time()
    out_symbols = {}
    for i, code in enumerate(symbols, 1):
        ts = time.time()
        try:
            out_symbols[code] = _bake_index(code)
            e = out_symbols[code]
            print(f"[{i}/{len(symbols)}] {code} {e['name']}: bars={len(e['bars'])} "
                  f"annotations={len(e['analysis']['annotations'])} ({time.time() - ts:.1f}s)")
        except Exception as exc:
            print(f"[{i}/{len(symbols)}] {code} 失败（{exc}），跳过")

    if not out_symbols:
        print("错误：无成功标的，不写文件")
        sys.exit(1)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": str(ts_api.latest_trade_date()), "version": 1,
               "symbols": out_symbols}
    _OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"已写入 {_OUT}（{_OUT.stat().st_size / 1e6:.1f}MB，{len(out_symbols)} 只），"
          f"总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
