"""生产安全模式验收：主图不得输出未验证形态和交叉买卖标签。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend.app import analysis_v7, indicators


def _frame(n: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(20260804)
    close = 100 + np.cumsum(rng.normal(0.03, 0.8, n))
    return pd.DataFrame({
        "trade_date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": close + rng.normal(0, 0.15, n),
        "high": close + rng.uniform(0.2, 0.8, n),
        "low": close - rng.uniform(0.2, 0.8, n),
        "close": close,
        "vol": rng.integers(100_000, 2_000_000, n).astype(float),
        "amount": rng.uniform(1e7, 1e9, n),
    })


def validate_safe_mode() -> None:
    df = indicators.compute_all(_frame())
    fib = [{
        "bar_idx": 180, "price": 98.0, "kind": "fibonacci", "label": "0.618",
        "direction": "range", "star": False, "detail": "大结构回撤触达",
        "lines": [], "zones": [], "polylines": [], "active": True,
    }]
    harmonic = [{
        "bar_idx": 190, "price": 97.5, "kind": "harmonic", "label": "鲨鱼D点",
        "direction": "bull", "star": True, "detail": "原版谐波完成D点",
        "lines": [], "zones": [], "polylines": [], "active": True,
    }]
    with patch("backend.app.analysis_v7.fibonacci_history.find_fibonacci_touches", return_value=fib), \
         patch("backend.app.analysis_v7.harmonics_history.find_harmonic_annotations", return_value=harmonic):
        result = analysis_v7.analyze(df)

    assert result["diagnostics"]["analysis_version"] == "analysis_v7.4-safe"
    assert result["diagnostics"]["safe_mode"] is True
    assert result["diagnostics"]["patterns_displayed"] == 0
    assert result["summary"]["structure"] == "自动形态识别重构中，暂不输出"

    annotations = result["annotations"]
    assert {e["kind"] for e in annotations} <= {"fibonacci", "harmonic"}, annotations
    blocked = {"结构失效", "EMA金叉", "EMA死叉", "MACD金叉", "MACD死叉"}
    assert not any(e.get("label") in blocked for e in annotations), annotations
    assert any(e.get("label") == "0.618" for e in annotations)
    assert any(e.get("label") == "鲨鱼D点" for e in annotations)


if __name__ == "__main__":
    validate_safe_mode()
    print("safe mode v10 validation OK")
