"""
===============================================================
异常处理与故障快照模块
实现平台级异常隔离、快照保存、自动清理
===============================================================
"""

import os
import json
import shutil
import traceback
from pathlib import Path
from typing import Union
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from loguru import logger

from utils.path_helper import SNAPSHOTS_DIR


class SnapshotManager:
    """
    故障快照管理器
    负责保存采集失败时的截图、DOM、日志快照
    """

    def __init__(self, snapshot_dir: Union[str, Path, None] = None, retention_days: int = 7):
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir is not None else SNAPSHOTS_DIR
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days

    def save_snapshot(
        self,
        platform: str,
        error: Exception,
        page_screenshot: Optional[bytes] = None,
        page_html: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        保存故障快照

        Args:
            platform: 平台标识
            error: 异常对象
            page_screenshot: 页面截图二进制数据
            page_html: 页面HTML源码
            context: 附加上下文信息

        Returns:
            快照目录路径
        """
        # 生成快照目录名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"{platform}_{timestamp}"
        snapshot_path = self.snapshot_dir / snapshot_name
        snapshot_path.mkdir(exist_ok=True)

        # 保存错误信息
        error_info = {
            "platform": platform,
            "timestamp": timestamp,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "context": context or {}
        }

        with open(snapshot_path / "error.json", "w", encoding="utf-8") as f:
            json.dump(error_info, f, ensure_ascii=False, indent=2)

        # 保存截图
        if page_screenshot:
            try:
                with open(snapshot_path / "screenshot.png", "wb") as f:
                    f.write(page_screenshot)
            except Exception as e:
                logger.warning(f"保存截图失败: {e}")

        # 保存HTML
        if page_html:
            try:
                with open(snapshot_path / "page.html", "w", encoding="utf-8") as f:
                    f.write(page_html)
            except Exception as e:
                logger.warning(f"保存HTML失败: {e}")

        # 保存日志
        self._save_recent_logs(snapshot_path)

        logger.info(f"故障快照已保存: {snapshot_path}")
        return snapshot_path

    def _save_recent_logs(self, snapshot_path: Path):
        """
        保存最近的日志到快照目录

        Args:
            snapshot_path: 快照目录路径
        """
        # 尝试从loguru获取最近的日志
        try:
            from loguru._defaults import importlib
            import sys

            # 获取loguru的handler
            for handler in logger._core.handlers:
                if hasattr(handler, "rotation"):
                    # 尝试读取日志文件
                    if hasattr(handler, "filename"):
                        log_file = Path(handler.filename)
                        if log_file.exists():
                            # 复制最近100行
                            with open(log_file, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                                recent_lines = lines[-100:] if len(lines) > 100 else lines

                            with open(snapshot_path / "recent_logs.txt", "w", encoding="utf-8") as f:
                                f.writelines(recent_lines)
        except Exception as e:
            logger.debug(f"保存日志失败: {e}")

    def cleanup_old_snapshots(self) -> int:
        """
        清理超过保留期的快照

        Returns:
            清理的快照数量
        """
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        cleaned_count = 0

        for snapshot_dir in self.snapshot_dir.iterdir():
            if not snapshot_dir.is_dir():
                continue

            # 检查修改时间
            mtime = datetime.fromtimestamp(snapshot_dir.stat().st_mtime)
            if mtime < cutoff_date:
                try:
                    shutil.rmtree(snapshot_dir)
                    cleaned_count += 1
                    logger.info(f"已清理过期快照: {snapshot_dir.name}")
                except Exception as e:
                    logger.warning(f"清理快照失败 {snapshot_dir.name}: {e}")

        if cleaned_count > 0:
            logger.info(f"共清理 {cleaned_count} 个过期快照")
        else:
            logger.debug("没有需要清理的过期快照")

        return cleaned_count


class RetryHandler:
    """
    重试处理器
    实现可重试异常的自动重试
    """

    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def should_retry(self, exception: Exception) -> bool:
        """
        判断异常是否应该重试

        Args:
            exception: 异常对象

        Returns:
            是否应该重试
        """
        # 网络相关异常可以重试
        retryable_exceptions = (
            ConnectionError,
            TimeoutError,
            OSError,
        )

        # Playwright相关异常
        try:
            from playwright.async_api import Error as PlaywrightError
            retryable_exceptions += (PlaywrightError,)
        except ImportError:
            pass

        return isinstance(exception, retryable_exceptions)

    async def execute_with_retry(
        self,
        coro_func,
        *args,
        **kwargs
    ):
        """
        带重试的执行

        Args:
            coro_func: 异步函数
            *args, **kwargs: 函数参数

        Returns:
            函数返回值

        Raises:
            最后一次尝试的异常
        """
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                result = await coro_func(*args, **kwargs)
                return result
            except Exception as e:
                last_exception = e
                if not self.should_retry(e):
                    logger.error(f"不可重试异常: {e}")
                    raise

                if attempt < self.max_retries - 1:
                    import asyncio
                    wait_time = self.retry_delay * (2 ** attempt)  # 指数退避
                    logger.warning(f"第 {attempt + 1} 次尝试失败: {e}, {wait_time:.1f}秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"达到最大重试次数 ({self.max_retries}), 放弃: {e}")

        raise last_exception


class ErrorTracker:
    """
    错误追踪器
    记录和统计各平台的错误信息
    """

    def __init__(self):
        self.errors: Dict[str, list] = {}

    def record_error(self, platform: str, error: Exception, context: Optional[Dict] = None):
        """
        记录错误

        Args:
            platform: 平台标识
            error: 异常对象
            context: 附加上下文
        """
        if platform not in self.errors:
            self.errors[platform] = []

        error_info = {
            "timestamp": datetime.now().isoformat(),
            "error_type": type(error).__name__,
            "message": str(error),
            "context": context or {}
        }
        self.errors[platform].append(error_info)

    def get_error_count(self, platform: str) -> int:
        """
        获取平台错误数量

        Args:
            platform: 平台标识

        Returns:
            错误数量
        """
        return len(self.errors.get(platform, []))

    def get_error_summary(self) -> Dict[str, int]:
        """
        获取错误统计摘要

        Returns:
            {platform: error_count}
        """
        return {platform: len(errors) for platform, errors in self.errors.items()}

    def clear(self):
        """清空错误记录"""
        self.errors.clear()
