"""Render 常驻进程内的盘后自动刷新任务。

不依赖个人电脑。每个交易日上海时间分批刷新：
15:40、16:40、18:00、20:00。这样即使上游15点多尚未发布当日日线，
后续批次也会继续追新，而不是把旧数据误认为今天已更新。
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
_SLOTS = ((15, 40), (16, 40), (18, 0), (20, 0))
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
    log.info("盘后自动刷新线程已启动（上海时间 %s）", ", ".join(f"{h:02d}:{m:02d}" for h, m in _SLOTS))


def _latest_due_slot(now: dt.datetime) -> tuple[int, int] | None:
    due = [slot for slot in _SLOTS if (now.hour, now.minute) >= slot]
    return due[-1] if due else None


def _loop(refresh_many) -> None:
    last_run: tuple[dt.date, tuple[int, int]] | None = None
    while True:
        try:
            now = dt.datetime.now(_TZ)
            slot = _latest_due_slot(now) if now.weekday() < 5 else None
            run_key = (now.date(), slot) if slot is not None else None
            if run_key is not None and run_key != last_run:
                codes = list(config.BROAD_INDEX_CODES)
                codes.extend(chart_cache.list_codes("1d"))
                unique = sorted(set(codes))
                log.info("开始盘后刷新 %02d:%02d：%d 只标的", slot[0], slot[1], len(unique))
                report = refresh_many(unique, "1d") or {}
                last_run = run_key
                log.info(
                    "盘后刷新完成：requested=%s updated=%s failed=%s",
                    report.get("requested"), report.get("updated"), report.get("failed"),
                )
        except Exception:
            log.exception("盘后刷新任务异常")
        time.sleep(60)
