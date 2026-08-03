"""真实多指数盲测v15：使用实际交易bar做因果截断。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from backend.app import indicators
from backend.app import reversal_patterns_v11 as base
from backend.app import reversal_patterns_v15 as engine

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
        except Exception as exc:  # pragma: no cover
            last = exc
            time.sleep(15 + attempt * 5)
    raise RuntimeError(last)


def event_key(event: dict) -> tuple:
    levels = event["key_levels"]
    return (
        event["kind"], int(event["start_idx"]), int(event["end_idx"]),
        int(event["confirm_idx"]), round(float(levels["neckline"]), 4),
        round(float(levels["extreme1"]), 4), round(float(levels["extreme2"]), 4),
    )


def evaluate(df: pd.DataFrame, events: list[dict]) -> dict:
    target = invalid = unresolved = 0
    for event in events:
        outcome = base._outcome(df, event)
        if outcome.target_hit:
            target += 1
        elif outcome.invalidated:
            invalid += 1
        else:
            unresolved += 1
    return {"target": target, "invalid": invalid, "unresolved": unresolved}


def validate_shanghai(df: pd.DataFrame) -> None:
    events = engine.find_index_reversals(df)
    recent = [e for e in events if e["kind"] == "double_top" and e["confirm_idx"] >= len(df) - 20]
    assert len(recent) == 1, recent
    best = recent[0]
    assert abs(best["key_levels"]["extreme1"] - 4258.86) < 2.0, best
    assert abs(best["key_levels"]["neckline"] - 3927.85) < 2.0, best
    assert abs(best["key_levels"]["extreme2"] - 4175.35) < 2.0, best
    assert str(df["trade_date"].iloc[best["confirm_idx"]].date()) == "2026-07-17", best
    assert best.get("causal") is True
    assert "outcome" not in best and "validated_history" not in best

    # 使用确认后第5根实际交易bar，避免依赖某个自然日是否开市。
    cutoff = min(len(df), int(best["confirm_idx"]) + 6)
    assert cutoff > best["confirm_idx"] + 1
    prefix = df.iloc[:cutoff].copy().reset_index(drop=True)
    full_raw = [e for e in engine._raw_candidates_causal(df) if e["confirm_idx"] < cutoff]
    prefix_raw = engine._raw_candidates_causal(prefix)
    full_keys = {event_key(e) for e in full_raw}
    prefix_keys = {event_key(e) for e in prefix_raw}
    assert full_keys == prefix_keys, (full_keys - prefix_keys, prefix_keys - full_keys)


def main() -> None:
    total_events = 0
    for code in CODES:
        df = fetch_df(code)
        raw = engine._raw_candidates_causal(df)
        displayed = engine.find_index_reversals(df)
        stats = evaluate(df, raw)
        total_events += len(raw)
        print(
            code,
            f"bars={len(df)}", f"asof={df['trade_date'].iloc[-1].date()}",
            f"raw={len(raw)}", f"displayed={len(displayed)}",
            f"target={stats['target']}", f"invalid={stats['invalid']}",
            f"unresolved={stats['unresolved']}",
        )
        assert len(displayed) <= base.MAX_DISPLAY_EVENTS
        assert all(e.get("causal") is True for e in displayed)
        if code == "000001.SH":
            validate_shanghai(df)
    assert total_events > 0
    print("live causal multi-index v16 audit OK")


if __name__ == "__main__":
    main()
