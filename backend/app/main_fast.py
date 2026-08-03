"""Kchart 高速入口。

在旧接口保持兼容的前提下新增 /api/chart：一次返回 K线、指标和推背图。
热缓存直接返回已经编码好的 JSON 字节，避免每次请求重复序列化大对象。
"""
from __future__ import annotations

import logging
import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse, Response

from . import chart_cache, chart_service, config, main as legacy, results_db, updater
from .ts_api import TsApiError

log = logging.getLogger("ryan.main_fast")
app = FastAPI(
    title="RYAN K线推背图",
    version=config.MODEL_VERSION,
    default_response_class=ORJSONResponse,
)
# 默认最高压缩级别会增加CPU等待；4级在网页JSON上压缩率接近、响应更快。
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)


@app.exception_handler(TsApiError)
async def _ts_api_error_handler(request, exc):
    return ORJSONResponse({"detail": str(exc)}, status_code=503)


def _serve_chart(ts_code: str, timeframe: str, refresh: int):
    if timeframe != "1d":
        raise HTTPException(400, "高速统一接口当前仅支持日线")
    started = time.perf_counter()
    try:
        result = chart_service.get(ts_code, timeframe, bool(refresh))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    elapsed_ms = (time.perf_counter() - started) * 1000
    meta = result.get("meta") or {}
    refreshing = bool(meta.get("refreshing"))
    cache_hit = bool(meta.get("cache_hit"))
    state = "refreshing" if refreshing else "hit" if cache_hit else "miss"
    headers = {
        "Server-Timing": f"chart;dur={elapsed_ms:.1f}",
        "X-Chart-Cache": state,
    }

    # 新鲜热缓存的主体完全不变，直接返回保存时生成的JSON，省掉FastAPI编码与JSON序列化。
    if cache_hit and not refreshing:
        raw = chart_cache.get_raw(ts_code, timeframe)
        if raw is not None:
            return Response(content=raw, media_type="application/json", headers=headers)

    return ORJSONResponse(content=result, headers=headers)


@app.get("/api/chart")
def api_chart(
    ts_code: str = Query(..., min_length=6),
    timeframe: str = "1d",
    refresh: int = 0,
):
    return _serve_chart(ts_code, timeframe, refresh)


@app.get("/api/chart/{ts_code}")
def api_chart_path(ts_code: str, refresh: int = 0):
    return _serve_chart(ts_code, "1d", refresh)


# 旧 API 与静态前端继续由原应用提供。
app.mount("/", legacy.app)


def _startup() -> None:
    results_db.get_con().close()
    log.info("高速入口启动：model=%s | TS_URL=%s", config.MODEL_VERSION, config.TS_URL)
    updater.start(chart_service.refresh_many)
    # 只预热固定指数；不根据用户搜索行为提前请求任何个股。
    threading.Thread(
        target=chart_service.refresh_many,
        args=(list(config.BROAD_INDEX_CODES), "1d"),
        daemon=True,
        name="chart-index-prewarm",
    ).start()


if __name__ == "__main__":
    import uvicorn

    _startup()
    uvicorn.run(app, host=config.HOST, port=config.PORT)
