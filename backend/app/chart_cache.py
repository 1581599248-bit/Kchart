"""K线、指标与推背图统一缓存。

两级缓存：进程内内存优先，磁盘作为进程重启后的热启动来源。
同时缓存已经序列化的 JSON 字节，热命中时可直接返回，避免再次遍历大对象。
"""
from __future__ import annotations

import threading
from time import time

import orjson

from . import config

CACHE_DIR = config.DATA_DIR / "chart_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_memory: dict[str, dict] = {}
_raw_memory: dict[str, bytes] = {}
_lock = threading.RLock()
_JSON_OPTIONS = orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY


def make_key(code: str, timeframe: str = "1d") -> str:
    return f"{code.upper()}@{timeframe}"


def _path(key: str):
    safe = key.replace(".", "_").replace("@", "__").replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _default(value):
    """兼容少量 pandas/自定义标量；常见 numpy 类型由 orjson 原生处理。"""
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError


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
        raw = path.read_bytes()
        data = orjson.loads(raw)
    except (OSError, orjson.JSONDecodeError):
        return None

    with _lock:
        _memory[key] = data
        _raw_memory[key] = raw
    return data


def get_raw(code: str, timeframe: str = "1d") -> bytes | None:
    """读取已经编码好的 JSON；供图表接口直接返回。"""
    key = make_key(code, timeframe)
    with _lock:
        raw = _raw_memory.get(key)
        if raw is not None:
            return raw
    path = _path(key)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    with _lock:
        _raw_memory[key] = raw
    return raw


def save(code: str, timeframe: str, payload: dict) -> dict:
    key = make_key(code, timeframe)
    serializable = dict(payload)
    serializable["cached_at"] = int(time())
    raw = orjson.dumps(serializable, option=_JSON_OPTIONS, default=_default)
    # 内存对象直接由最终JSON反解，保证后续动态响应中不存在 pandas/numpy 特殊类型。
    data = orjson.loads(raw)
    path = _path(key)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    with _lock:
        _memory[key] = data
        _raw_memory[key] = raw
    return data


def clear(code: str | None = None, timeframe: str = "1d") -> None:
    with _lock:
        if code is None:
            _memory.clear()
            _raw_memory.clear()
            return
        key = make_key(code, timeframe)
        _memory.pop(key, None)
        _raw_memory.pop(key, None)


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
