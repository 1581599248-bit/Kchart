"""最终生产引擎真实验收：六指数密度、上证最新M顶与截断因果一致性。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from backend.app import index_reversals_v17 as engine
from backend.app import indicators

BASE_URL = "https://kchart.onrender.com/api/chart"
CODES = ["000001.SH", "000300.SH", "000905.SH", "000688.SH", "399001.SZ", "399006.SZ"]


def fetch_df(code: str) -> pd.DataFrame:
    last = None
    for attempt in range(8):
        try:
            response = requests.get(
                BASE_URL,
                params={"ts_code": code, "timeframe": "1d", "refresh": 0},
                timeout=90,
            )
            response.raise_for_status()
            bars = response.json().get("bars") or []
            if len(bars) < 200:
                raise RuntimeError(f"{code}: only {len(bars)} bars")
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
        except Exception as exc:  # pragma: no cover - live network audit
            last = exc
            time.sleep(15 + attempt * 5)
    raise RuntimeError(last)


def event_key(event: dict) -> tuple:
    levels = event["key_levels"]
    return (
        event["kind"], int(event["start_idx"]), int(event["middle_idx"]),
        int(event["end_idx"]), int(event["confirm_idx"]),
        round(float(levels["neckline"]), 4),
        round(float(levels["extreme1"]), 4),
        round(float(levels["extreme2"]), 4),
    )


def independent_outcome(df: pd.DataFrame, event: dict, horizon: int = 80) -> str:
    """仅做独立统计，绝不参与生产候选选择。"""
    confirm = int(event["confirm_idx"])
    levels = event["key_levels"]
    neckline = float(levels["neckline"])
    target = float(levels["measure_target"])
    invalidation = float(levels["invalidation"])
    half_target = (neckline + target) / 2.0
    end = min(len(df), confirm + horizon + 1)
    for idx in range(confirm + 1, end):
        high = float(df["high"].iloc[idx])
        low = float(df["low"].iloc[idx])
        if event["direction"] == "bear":
            if high > invalidation:
                return "invalid"
            if low <= half_target:
                return "target"
        else:
            if low < invalidation:
                return "invalid"
            if high >= half_target:
                return "target"
    return "unresolved"


def validate_shanghai(df: pd.DataFrame) -> None:
    displayed = engine.find_index_reversals(df)
    recent = [
        event for event in displayed
        if event["kind"] == "double_top" and event["confirm_idx"] >= len(df) - 20
    ]
    assert len(recent) == 1, recent
    event = recent[0]
    levels = event["key_levels"]
    assert abs(levels["extreme1"] - 4258.86) < 2.0, event
    assert abs(levels["neckline"] - 3927.85) < 2.0, event
    assert abs(levels["extreme2"] - 4175.35) < 2.0, event
    assert str(df["trade_date"].iloc[event["confirm_idx"]].date()) == "2026-07-17", event
    assert event.get("causal") is True

    cutoff = min(len(df), int(event["confirm_idx"]) + 6)
    prefix = df.iloc[:cutoff].copy().reset_index(drop=True)
    full_candidates = [
        item for item in engine.find_confirmed_candidates(df)
        if item["confirm_idx"] < cutoff
    ]
    prefix_candidates = engine.find_confirmed_candidates(prefix)
    assert {event_key(item) for item in full_candidates} == {
        event_key(item) for item in prefix_candidates
    }


def main() -> None:
    total = 0
    for code in CODES:
        df = fetch_df(code)
        candidates = engine.find_confirmed_candidates(df)
        displayed = engine.find_index_reversals(df)
        outcomes = {"target": 0, "invalid": 0, "unresolved": 0}
        for event in candidates:
            outcomes[independent_outcome(df, event)] += 1
        total += len(candidates)
        print(
            code,
            f"bars={len(df)}", f"asof={df['trade_date'].iloc[-1].date()}",
            f"confirmed={len(candidates)}", f"displayed={len(displayed)}",
            f"target={outcomes['target']}", f"invalid={outcomes['invalid']}",
            f"unresolved={outcomes['unresolved']}",
        )
        assert len(displayed) <= engine.MAX_DISPLAY_EVENTS
        assert all(event.get("causal") is True for event in candidates)
        if code == "000001.SH":
            validate_shanghai(df)
    assert 10 <= total <= 80, total
    print("live index reversal release v17 validation OK")


if __name__ == "__main__":
    main()
