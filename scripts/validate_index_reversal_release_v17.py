"""生产适配验收：指数只恢复M/W与颈线确认，个股保持安全模式。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import analysis, analysis_v7, indicators


def _frame(n: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(20260804)
    close = 100 + np.cumsum(rng.normal(0.03, 0.7, n))
    return pd.DataFrame({
        "trade_date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": close + rng.normal(0, 0.15, n),
        "high": close + rng.uniform(0.2, 0.8, n),
        "low": close - rng.uniform(0.2, 0.8, n),
        "close": close,
        "vol": rng.integers(100_000, 2_000_000, n).astype(float),
        "amount": rng.uniform(1e7, 1e9, n),
    })


def _m_event(df: pd.DataFrame) -> dict:
    p1, mid, p2, confirm = 80, 110, 145, 160
    neckline = 102.0
    return {
        "kind": "double_top",
        "name": "M顶",
        "direction": "bear",
        "start_idx": p1,
        "middle_idx": mid,
        "end_idx": p2,
        "confirm_idx": confirm,
        "key_levels": {
            "neckline": neckline,
            "measure_target": 92.0,
            "invalidation": 112.0,
            "extreme1": 112.0,
            "extreme2": 111.0,
        },
        "score": 70,
        "star": True,
        "note": "M顶真实颈线确认。",
        "trace": [
            {
                "points": [
                    {"t": str(df["trade_date"].iloc[p1].date()), "p": 112.0},
                    {"t": str(df["trade_date"].iloc[mid].date()), "p": neckline},
                    {"t": str(df["trade_date"].iloc[p2].date()), "p": 111.0},
                ],
                "style": "solid",
            },
            {
                "points": [
                    {"t": str(df["trade_date"].iloc[mid].date()), "p": neckline},
                    {"t": str(df["trade_date"].iloc[confirm].date()), "p": neckline},
                ],
                "style": "dashed",
            },
        ],
        "active": True,
        "causal": True,
    }


def validate_index_adapter() -> None:
    df = indicators.compute_all(_frame())
    event = _m_event(df)
    with patch("backend.app.analysis_v7.index_reversals_v17.find_index_reversals", return_value=[event]), \
         patch("backend.app.analysis_v7.fibonacci_history.find_fibonacci_touches", return_value=[]), \
         patch("backend.app.analysis_v7.harmonics_history.find_harmonic_annotations", return_value=[]):
        result = analysis_v7.analyze(df, asset_kind="index")

    assert result["diagnostics"]["analysis_version"] == "analysis_v11.0-index-reversal"
    assert result["diagnostics"]["mode"] == "index_reversal_only"
    assert result["diagnostics"]["index_reversals"] == 1
    labels = [item["label"] for item in result["annotations"]]
    assert labels == ["M顶", "跌破颈线"], labels
    trace = result["annotations"][0]
    confirm = result["annotations"][1]
    assert trace["trace_only"] is True and trace["history_label"] is True
    assert len(trace["polylines"]) == 2
    assert confirm["star"] is True and not confirm["polylines"]
    assert "M顶" in result["summary"]["structure"]
    assert "结构失效" not in labels
    assert not any("EMA" in label or "MACD" in label or "RSI" in label for label in labels)


def validate_equity_safe_mode() -> None:
    df = indicators.compute_all(_frame())
    with patch("backend.app.analysis_v7.index_reversals_v17.find_index_reversals") as reversal_call, \
         patch("backend.app.analysis_v7.fibonacci_history.find_fibonacci_touches", return_value=[]), \
         patch("backend.app.analysis_v7.harmonics_history.find_harmonic_annotations", return_value=[]):
        result = analysis_v7.analyze(df, asset_kind="equity")
    reversal_call.assert_not_called()
    assert result["diagnostics"]["mode"] == "safe_equity"
    assert result["diagnostics"]["index_reversals"] == 0
    assert result["annotations"] == []
    assert result["summary"]["structure"] == "自动形态识别重构中，暂不输出"


def validate_legacy_proxy_is_safe() -> None:
    df = indicators.compute_all(_frame())
    with patch("backend.app.analysis_v7.fibonacci_history.find_fibonacci_touches", return_value=[]), \
         patch("backend.app.analysis_v7.harmonics_history.find_harmonic_annotations", return_value=[]):
        result = analysis.analyze(df)
    assert analysis.ANALYSIS_VERSION == analysis_v7.ANALYSIS_VERSION
    assert result["diagnostics"]["mode"] == "safe_equity"
    assert not any(item.get("kind") == "pattern" for item in result["annotations"])


if __name__ == "__main__":
    validate_index_adapter()
    validate_equity_safe_mode()
    validate_legacy_proxy_is_safe()
    print("index reversal release v17 validation OK")
