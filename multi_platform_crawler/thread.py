"""
===============================================================
采集工作线程模块
提供 QThread + 独立 asyncio 事件循环的线程安全封装
===============================================================
"""

import asyncio
import threading
from typing import Optional, Callable, Any, Dict, List
from PySide6.QtCore import QThread, Signal, QTimer
from loguru import logger

from utils.path_helper import LOGS_DIR


class CrawlThread(QThread):
    """
    采集工作子线程
    负责在独立线程中运行 asyncio 事件循环，执行采集任务
    """

    # 线程安全信号定义
    # 日志信号：传递日志消息到GUI显示
    log_signal = Signal(str)
    # 数据信号：传递采集到的单条数据
    data_signal = Signal(dict)
    # 未匹配信号：传递未匹配的目标标题 (platform, title)
    unmatched_signal = Signal(str, str)
    # 进度信号：传递当前进度 (current, total, platform)
    progress_signal = Signal(int, int, str)
    # 错误信号：传递错误信息
    error_signal = Signal(str)
    # 完成信号：传递最终结果 (success_data, unmatched_data)
    finish_signal = Signal(list, list)
    # 登录请求信号：需要手动登录
    login_required_signal = Signal(str)
    # 登录成功信号：手动登录完成
    login_success_signal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._tasks: List[Callable] = []
        self._executor = None

    def run(self):
        """
        线程主入口
        创建独立的 asyncio 事件循环
        """
        self._running = True

        # 创建新的事件循环
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # 配置日志输出到GUI
        self._setup_logging_handler()

        logger.info("采集线程事件循环已启动")

        try:
            # 运行事件循环
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"事件循环异常: {e}")
            self.error_signal.emit(f"事件循环异常: {e}")
        finally:
            # 清理资源
            if self._loop and not self._loop.is_closed():
                self._loop.close()
            logger.info("采集线程事件循环已停止")

    def _setup_logging_handler(self):
        """
        配置日志处理器，将日志输出到GUI
        """
        import sys

        class GuiLogHandler:
            def __init__(self, signal):
                self.signal = signal

            def write(self, message):
                if message.strip():
                    self.signal.emit(message.strip())

            def flush(self):
                pass

        # 添加GUI日志处理器
        handler = GuiLogHandler(self.log_signal)
        # 配置loguru输出到handler
        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level="INFO"
        )
        # 添加GUI处理器
        logger.add(
            handler,
            format="{message}",
            level="INFO",
            enqueue=True
        )
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(LOGS_DIR / "crawl_thread_{time:YYYY-MM-DD}.log"),
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level="INFO",
            rotation="1 day",
            retention="14 days",
            encoding="utf-8",
            enqueue=True,
        )

    def submit_task(self, coro_func: Callable, *args, **kwargs):
        """
        提交异步任务到事件循环

        Args:
            coro_func: 异步函数
            *args, **kwargs: 传递给coro_func的参数
        """
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(coro_func(*args, **kwargs), self._loop)
        else:
            logger.warning("事件循环未运行，无法提交任务")

    def stop(self):
        """
        安全停止线程
        """
        logger.info("正在停止采集线程...")
        self._running = False

        if self._loop and self._loop.is_running():
            # 停止事件循环
            self._loop.call_soon_threadsafe(self._loop.stop)

        # 等待线程结束
        self.wait(5000)
        logger.info("采集线程已停止")

    def is_running(self) -> bool:
        """
        检查线程是否正在运行
        """
        return self._running and self.isRunning()

    def emit_log(self, message: str):
        """
        发送日志消息到GUI
        """
        self.log_signal.emit(message)

    def emit_data(self, data: Dict):
        """
        发送采集数据到GUI
        """
        self.data_signal.emit(data)

    def emit_unmatched(self, platform: str, title: str):
        """
        发送未匹配标题到GUI
        """
        self.unmatched_signal.emit(platform, title)

    def emit_error(self, error: str):
        """
        发送错误信息到GUI
        """
        self.error_signal.emit(error)

    def emit_finish(self, success_data: List[Dict], unmatched: List[str]):
        """
        发送完成信号到GUI
        """
        self.finish_signal.emit(success_data, unmatched)

    def emit_progress(self, current: int, total: int, platform: str):
        """
        发送进度信息到GUI
        """
        self.progress_signal.emit(current, total, platform)

    def emit_login_required(self, platform: str):
        """
        请求手动登录
        """
        self.login_required_signal.emit(platform)

    def emit_login_success(self, platform: str):
        """
        手动登录完成
        """
        self.login_success_signal.emit(platform)


class AsyncTaskRunner:
    """
    异步任务运行器
    提供便捷的异步任务提交和执行接口
    """

    def __init__(self, thread: CrawlThread):
        self.thread = thread

    def run_async(self, coro_func: Callable, *args, **kwargs):
        """
        在采集线程中运行异步函数
        """
        self.thread.submit_task(coro_func, *args, **kwargs)
