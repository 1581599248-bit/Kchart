"""统一K线+推背图缓存层。

目标：用户访问时只读取预计算结果，不在请求链路内重复计算。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from time import time


CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "chart_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_memory = {}
_lock = threading.Lock()


def _path(code: str):
    safe = code.replace('.', '_')
    return CACHE_DIR / f"{safe}.json"


def get(code: str):
    """读取缓存，优先内存，避免重复磁盘IO。"""
    with _lock:
        if code in _memory:
            return _memory[code]

    p = _path(code)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    with _lock:
        _memory[code] = data
    return data


def save(code: str, payload: dict):
    """保存完整chart bundle。"""
    payload = dict(payload)
    payload["cached_at"] = int(time())
    p = _path(code)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with _lock:
        _memory[code] = payload
    return payload


def clear(code: str | None = None):
    with _lock:
        if code:
            _memory.pop(code, None)
        else:
            _memory.clear()
