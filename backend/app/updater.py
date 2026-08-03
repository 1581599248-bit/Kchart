"""Render 常驻进程内的盘后自动刷新任务。

不依赖个人电脑。每个交易日上海时间 15:20 后执行一次：
固定刷新宽基指数，并刷新曾被用户搜索、已生成 chart_cache 的股票。
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from zoneinfo import ZoneInfo

from . import chart_cache, config

log = logging.getLogger("ryan.updater")
_TZ = ZoneInfo("Asia/Shanghai")
_started = False
_guard = threading.Lock()


def start(refresh_many) -> None:
    global _started
    with _guard:
        if _started:
            return
        _started = True
    threading.Thread(
        target=_loop,
        args=(refresh_many,),
        daemon=True,
        name="post-close-updater",
    ).start()
    log.info("盘后自动刷新线程已启动（上海时间 15:20）")


def _loop(refresh_many) -> None:
    last_run: dt.date | None = None
    while True:
        try:
            now = dt.datetime.now(_TZ)
            if now.weekday() < 5 and (now.hour, now.minute) >= (15, 20) and last_run != now.date():
                codes = list(config.BROAD_INDEX_CODES)
                codes.extend(chart_cache.list_codes("1d"))
                log.info("开始盘后刷新：%d 只标的", len(set(codes)))
                refresh_many(sorted(set(codes)), "1d")
                last_run = now.date()
                log.info("盘后刷新完成")
        except Exception:
            log.exception("盘后刷新任务异常")
        time.sleep(60)
