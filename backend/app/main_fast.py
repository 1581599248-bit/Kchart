"""Kchart 高速入口。

在旧接口保持兼容的前提下新增 /api/chart：一次返回 K线、指标和推背图。
外层 FastAPI 先匹配高速接口，再把其余请求交给原 main.app。
"""
from __future__ import annotations

import logging
import threading
import time

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from . import chart_service, config, main as legacy, results_db, updater
from .ts_api import TsApiError

log = logging.getLogger("ryan.main_fast")
app = FastAPI(title="RYAN K线推背图", version=config.MODEL_VERSION)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.exception_handler(TsApiError)
async def _ts_api_error_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


def _serve_chart(response: Response, ts_code: str, timeframe: str, refresh: int):
    if timeframe != "1d":
        raise HTTPException(400, "高速统一接口当前仅支持日线")
    started = time.perf_counter()
    try:
        result = chart_service.get(ts_code, timeframe, bool(refresh))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    meta = result.get("meta") or {}
    state = "refreshing" if meta.get("refreshing") else "hit" if meta.get("cache_hit") else "miss"
    response.headers["Server-Timing"] = f"chart;dur={elapsed_ms:.1f}"
    response.headers["X-Chart-Cache"] = state
    return result


@app.get("/api/chart")
def api_chart(
    response: Response,
    ts_code: str = Query(..., min_length=6),
    timeframe: str = "1d",
    refresh: int = 0,
):
    return _serve_chart(response, ts_code, timeframe, refresh)


@app.get("/api/chart/{ts_code}")
def api_chart_path(response: Response, ts_code: str, refresh: int = 0):
    return _serve_chart(response, ts_code, "1d", refresh)


# 旧 API 与静态前端继续由原应用提供。
app.mount("/", legacy.app)


def _startup() -> None:
    results_db.get_con().close()
    log.info("高速入口启动：model=%s | TS_URL=%s", config.MODEL_VERSION, config.TS_URL)
    updater.start(chart_service.refresh_many)
    # 指数 bundle 在后台预热，不阻塞网站启动。
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
