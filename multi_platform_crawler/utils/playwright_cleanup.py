"""
Playwright 会话收尾：须关闭 context、browser，并调用 playwright.stop()，
否则 async_playwright().start() 创建的驱动进程可能残留，阻塞后续有头/无头启动。
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger


async def shutdown_chromium_session(
    *,
    playwright: Optional[Any] = None,
    context: Optional[Any] = None,
    browser: Optional[Any] = None,
    log_label: str = "playwright",
) -> None:
    """幂等关闭：忽略重复 close/stop 抛出的异常。"""
    if context is not None:
        try:
            await context.close()
        except Exception as e:
            logger.debug(f"[{log_label}] context.close: {e}")
    if browser is not None:
        try:
            await browser.close()
        except Exception as e:
            logger.debug(f"[{log_label}] browser.close: {e}")
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception as e:
            logger.debug(f"[{log_label}] playwright.stop: {e}")
