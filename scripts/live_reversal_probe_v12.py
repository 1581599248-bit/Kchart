"""用生产上证K线验证v12硬规则反转引擎。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from backend.app import indicators, reversal_patterns_v11, reversal_patterns_v12

URL = "https://kchart.onrender.com/api/chart?ts_code=000001.SH&timeframe=1d&refresh=0"


def fetch_df() -> pd.DataFrame:
    last = None
    for attempt in range(8):
        try:
            response = requests.get(URL, timeout=90)
            response.raise_for_status()
            bars = response.json().get("bars") or []
            if len(bars) < 200:
                raise RuntimeError(f"only {len(bars)} bars")
            df = pd.DataFrame(bars).rename(columns={
                "time": "trade_date", "o": "open", "h": "high",
                "l": "low", "c": "close", "v": "vol",
            })
            df["trade_date"] = (
                pd.to_datetime(df["trade_date"], unit="s", utc=True)
                .dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
            )
            df["amount"] = 0.0
            return indicators.compute_all(
                df.rename(columns={"trade_date": "ts"})
            ).rename(columns={"ts": "trade_date"})
        except Exception as exc:  # pragma: no cover - live audit
            last = exc
            time.sleep(15 + attempt * 5)
    raise RuntimeError(last)


def main() -> None:
    df = fetch_df()
    events = reversal_patterns_v12.find_index_reversals(df)
    print(f"asof={df['trade_date'].iloc[-1].date()} events={len(events)}")
    for event in events:
        print(
            event["name"],
            df["trade_date"].iloc[event["start_idx"]].date(),
            df["trade_date"].iloc[event["end_idx"]].date(),
            df["trade_date"].iloc[event["confirm_idx"]].date(),
            "score=", event["score"],
            "validated=", event["validated_history"],
            event["note"],
        )
        assert len(event.get("trace") or []) == 2
        shape = event["trace"][0]["points"]
        assert len(shape) == 3
        assert event["trace"][1]["points"][0]["p"] == event["key_levels"]["neckline"]

    recent = [e for e in events if e["kind"] == "double_top" and e["confirm_idx"] >= len(df) - 20]
    assert recent, events
    best = recent[-1]
    assert abs(best["key_levels"]["extreme1"] - 4258.86) < 2.0, best
    assert abs(best["key_levels"]["neckline"] - 3927.85) < 2.0, best
    assert abs(best["key_levels"]["extreme2"] - 4175.35) < 2.0, best
    assert str(df["trade_date"].iloc[best["confirm_idx"]].date()) == "2026-07-17", best
    assert best["score"] < reversal_patterns_v11.MIN_SCORE, (
        "该真实结构应证明评分不得作为有效性硬阈值", best
    )
    assert len(events) <= reversal_patterns_v11.MAX_DISPLAY_EVENTS
    print("live reversal v12 validation OK")


if __name__ == "__main__":
    main()
