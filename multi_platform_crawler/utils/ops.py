"""
===============================================================
运维工具模块
实现日志轮转、执行顺序配置、灰度标记
===============================================================
"""

import os
import json
import shutil
import configparser
from pathlib import Path
from typing import List, Dict, Optional, Union
from datetime import datetime, timedelta
from loguru import logger

from utils.path_helper import (
    COLLECTORS_DIR,
    CONFIG_DIR,
    COOKIES_DIR,
    DATA_DIR,
    LOGS_DIR,
    PLATFORMS_DIR,
    SNAPSHOTS_DIR,
)


class LogManager:
    """
    日志管理器
    实现日志按天分割、压缩、保留30天
    """

    def __init__(
        self,
        log_dir: Union[str, Path, None] = None,
        retention_days: int = 30,
        compression: str = "zip"
    ):
        self.log_dir = Path(log_dir) if log_dir is not None else LOGS_DIR
        self.log_dir.mkdir(exist_ok=True)
        self.retention_days = retention_days
        self.compression = compression

    def setup_logging(self):
        """
        配置loguru日志系统
        """
        # 移除默认handler
        logger.remove()

        # 控制台输出
        logger.add(
            self.log_dir / "crawler.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="INFO",
            rotation="1 day",  # 每天分割
            retention=f"{self.retention_days} days",  # 保留30天
            compression=self.compression,  # 压缩
            encoding="utf-8"
        )

        # 错误日志单独文件
        logger.add(
            self.log_dir / "error.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="ERROR",
            rotation="1 day",
            retention=f"{self.retention_days} days",
            compression=self.compression,
            encoding="utf-8"
        )

        # 控制台输出
        logger.add(
            lambda msg: print(msg, end=""),
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level="INFO"
        )

        logger.info("日志系统已配置")

    def cleanup_old_logs(self) -> int:
        """
        清理过期日志

        Returns:
            清理的日志文件数量
        """
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        cleaned_count = 0

        for log_file in self.log_dir.iterdir():
            if not log_file.is_file():
                continue

            # 检查修改时间
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if mtime < cutoff_date:
                try:
                    # 如果是压缩文件或日志文件
                    if log_file.suffix in [".log", ".zip", ".gz"]:
                        log_file.unlink()
                        cleaned_count += 1
                        logger.info(f"已清理过期日志: {log_file.name}")
                except Exception as e:
                    logger.warning(f"清理日志失败 {log_file.name}: {e}")

        return cleaned_count


class PlatformOrderManager:
    """
    平台执行顺序管理器
    支持GUI拖拽调整平台执行顺序并持久化
    """

    def __init__(self, config_file: Union[str, Path, None] = None):
        self.config_file = (
            Path(config_file) if config_file is not None else CONFIG_DIR / "platform_order.json"
        )
        self.platforms: List[str] = []

    def load_order(self) -> List[str]:
        """
        加载平台执行顺序

        Returns:
            平台标识列表
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.platforms = data.get("order", [])
                    logger.info(f"平台顺序已加载: {self.platforms}")
                    return self.platforms
            except Exception as e:
                logger.error(f"加载平台顺序失败: {e}")

        # 默认顺序
        self.platforms = [
            "toutiao", "weibo", "zhihu", "xueqiu",
            "yidian", "zaker", "qq",
            "netease", "baijiahao"
        ]
        return self.platforms

    def save_order(self, platforms: List[str]) -> bool:
        """
        保存平台执行顺序

        Args:
            platforms: 平台标识列表

        Returns:
            是否保存成功
        """
        try:
            self.platforms = platforms
            data = {
                "order": platforms,
                "updated_at": datetime.now().isoformat()
            }
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"平台顺序已保存: {platforms}")
            return True

        except Exception as e:
            logger.error(f"保存平台顺序失败: {e}")
            return False

    def move_platform(self, from_index: int, to_index: int) -> bool:
        """
        移动平台顺序

        Args:
            from_index: 原始位置
            to_index: 目标位置

        Returns:
            是否移动成功
        """
        if 0 <= from_index < len(self.platforms) and 0 <= to_index < len(self.platforms):
            platform = self.platforms.pop(from_index)
            self.platforms.insert(to_index, platform)
            self.save_order(self.platforms)
            return True
        return False


class PlatformFeatureFlag:
    """
    平台灰度运行标记
    实现平台灰度运行与人工抽检兼容
    """

    def __init__(self, config_file: Union[str, Path, None] = None):
        self.config_file = (
            Path(config_file) if config_file is not None else CONFIG_DIR / "platform_flags.json"
        )
        self.flags: Dict[str, Dict] = {}

    def load_flags(self) -> Dict[str, Dict]:
        """
        加载平台标记

        Returns:
            平台标记字典
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self.flags = json.load(f)
                    logger.info(f"平台标记已加载: {len(self.flags)} 个")
                    return self.flags
            except Exception as e:
                logger.error(f"加载平台标记失败: {e}")

        # 默认标记
        self.flags = {
            platform: {
                "enabled": True,
                "gray": False,
                "manual_check": False
            }
            for platform in [
                "toutiao", "weibo", "zhihu", "xueqiu",
                "yidian", "zaker", "qq",
                "netease", "baijiahao"
            ]
        }
        return self.flags

    def save_flags(self) -> bool:
        """
        保存平台标记

        Returns:
            是否保存成功
        """
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.flags, f, ensure_ascii=False, indent=2)
            logger.info("平台标记已保存")
            return True
        except Exception as e:
            logger.error(f"保存平台标记失败: {e}")
            return False

    def is_enabled(self, platform: str) -> bool:
        """
        检查平台是否启用

        Args:
            platform: 平台标识

        Returns:
            是否启用
        """
        return self.flags.get(platform, {}).get("enabled", True)

    def is_gray(self, platform: str) -> bool:
        """
        检查平台是否灰度运行

        Args:
            platform: 平台标识

        Returns:
            是否灰度
        """
        return self.flags.get(platform, {}).get("gray", False)

    def is_manual_check(self, platform: str) -> bool:
        """
        检查平台是否需要人工抽检

        Args:
            platform: 平台标识

        Returns:
            是否需要人工抽检
        """
        return self.flags.get(platform, {}).get("manual_check", False)

    def set_flag(self, platform: str, enabled: bool = None, gray: bool = None, manual_check: bool = None):
        """
        设置平台标记

        Args:
            platform: 平台标识
            enabled: 是否启用
            gray: 是否灰度
            manual_check: 是否需要人工抽检
        """
        if platform not in self.flags:
            self.flags[platform] = {"enabled": True, "gray": False, "manual_check": False}

        if enabled is not None:
            self.flags[platform]["enabled"] = enabled
        if gray is not None:
            self.flags[platform]["gray"] = gray
        if manual_check is not None:
            self.flags[platform]["manual_check"] = manual_check

        self.save_flags()


class SystemHealthChecker:
    """
    系统健康检查器
    检查依赖、环境、配置
    """

    def __init__(self):
        self.issues: List[str] = []

    def check_dependencies(self) -> bool:
        """
        检查依赖是否安装

        Returns:
            是否通过
        """
        required_packages = [
            "PySide6",
            "playwright",
            "yaml",
            "loguru",
            "cryptography"
        ]

        all_ok = True
        for package in required_packages:
            try:
                __import__(package)
            except ImportError:
                self.issues.append(f"缺少依赖: {package}")
                all_ok = False

        return all_ok

    def check_directories(self) -> bool:
        """
        检查必要目录是否存在

        Returns:
            是否通过
        """
        required_dirs = [
            PLATFORMS_DIR,
            COLLECTORS_DIR,
            COOKIES_DIR,
            DATA_DIR,
            LOGS_DIR,
            SNAPSHOTS_DIR,
        ]

        all_ok = True
        for dir_path in required_dirs:
            if not dir_path.exists():
                self.issues.append(f"缺少目录: {dir_path}")
                all_ok = False

        return all_ok

    def check_all(self) -> bool:
        """
        执行所有检查

        Returns:
            是否全部通过
        """
        self.issues.clear()

        deps_ok = self.check_dependencies()
        dirs_ok = self.check_directories()

        if self.issues:
            logger.warning("系统健康检查发现问题:")
            for issue in self.issues:
                logger.warning(f"  - {issue}")

        return deps_ok and dirs_ok

    def get_report(self) -> str:
        """
        获取健康检查报告

        Returns:
            报告文本
        """
        if not self.issues:
            return "系统健康检查通过"

        report = "系统健康检查发现问题:\n"
        for issue in self.issues:
            report += f"  - {issue}\n"
        return report
