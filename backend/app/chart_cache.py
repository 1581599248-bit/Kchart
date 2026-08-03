"""K线、指标与推背图统一缓存。

两级缓存：进程内内存优先，磁盘作为进程重启后的热启动来源。
磁盘写入采用临时文件原子替换，避免并发读取半截 JSON。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
from time import time

from . import config

CACHE_DIR = config.DATA_DIR / "chart_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_memory: dict[str, dict] = {}
_lock = threading.RLock()


def make_key(code: str, timeframe: str = "1d") -> str:
    return f"{code.upper()}@{timeframe}"


def _path(key: str):
    safe = key.replace(".", "_").replace("@", "__").replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _clean(value):
    """将 numpy 标量、NaN 与日期递归转换为标准 JSON 类型。"""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def get(code: str, timeframe: str = "1d") -> dict | None:
    key = make_key(code, timeframe)
    with _lock:
        hit = _memory.get(key)
        if hit is not None:
            return hit

    path = _path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    with _lock:
        _memory[key] = data
    return data


def save(code: str, timeframe: str, payload: dict) -> dict:
    key = make_key(code, timeframe)
    data = _clean(dict(payload))
    data["cached_at"] = int(time())
    path = _path(key)
    tmp = path.with_suffix(".json.tmp")
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)
    with _lock:
        _memory[key] = data
    return data


def clear(code: str | None = None, timeframe: str = "1d") -> None:
    with _lock:
        if code is None:
            _memory.clear()
            return
        _memory.pop(make_key(code, timeframe), None)


def list_codes(timeframe: str = "1d") -> list[str]:
    """列出已经生成过磁盘缓存的标的代码。"""
    suffix = f"__{timeframe}.json"
    out = []
    for path in CACHE_DIR.glob(f"*{suffix}"):
        stem = path.name[: -len(suffix)]
        for market in ("SH", "SZ", "BJ", "CSI", "HK"):
            stem = stem.replace(f"_{market}", f".{market}")
        out.append(stem)
    with _lock:
        for key in _memory:
            code, tf = key.rsplit("@", 1)
            if tf == timeframe:
                out.append(code)
    return sorted(set(out))
