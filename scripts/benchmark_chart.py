"""测量部署后的统一图表接口冷/热响应。

用法：
  python scripts/benchmark_chart.py https://你的服务.onrender.com 000001.SH 600519.SH
"""
from __future__ import annotations

import argparse
import time

import requests


def hit(base: str, code: str) -> tuple[float, str, str]:
    url = base.rstrip("/") + "/api/chart"
    t0 = time.perf_counter()
    resp = requests.get(url, params={"ts_code": code, "timeframe": "1d"}, timeout=60)
    elapsed = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    data = resp.json()
    return elapsed, resp.headers.get("X-Chart-Cache", "-"), data.get("data_asof", "-")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("codes", nargs="*", default=["000001.SH", "600519.SH"])
    args = parser.parse_args()

    for code in args.codes:
        first = hit(args.base_url, code)
        second = hit(args.base_url, code)
        print(
            f"{code}: first={first[0]:.0f}ms cache={first[1]} asof={first[2]} | "
            f"second={second[0]:.0f}ms cache={second[1]} asof={second[2]}"
        )


if __name__ == "__main__":
    main()
