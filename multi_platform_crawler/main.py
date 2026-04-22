# -*- coding: utf-8 -*-
# ========== Playwright 浏览器路径（须在任何可能加载 playwright 的 import 之前）==========
import os

_local_appdata = os.environ.get(
    "LOCALAPPDATA",
    os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local"),
)
_browser_path = os.path.join(_local_appdata, "ms-playwright")
if os.path.isdir(_browser_path):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _browser_path
else:
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
# ========== Playwright 路径修复结束 ==========

# 主程序入口：多平台内容采集系统（GUI + 调度 + 采集线程）

import io
import sys
import asyncio
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer
from loguru import logger

# 先加入入口目录，再按 path_helper 规则补齐冻结环境下的 PROJECT_ROOT（外置 collectors）
_entry_dir = Path(__file__).resolve().parent
if str(_entry_dir) not in sys.path:
    sys.path.insert(0, str(_entry_dir))

from dotenv import load_dotenv
from utils.path_helper import (
    COLLECTORS_DIR,
    ENV_PATH,
    LOGS_DIR,
    PLATFORMS_DIR,
    PROJECT_ROOT,
    ensure_runtime_dirs,
    ensure_sys_path_for_imports,
    is_bundled_runtime,
)

ensure_sys_path_for_imports(_entry_dir)

import aiohttp  # noqa: F401  # 飞书导入依赖；确保从 main 入口可被 PyInstaller 完整收集
import tenacity  # noqa: F401  # 飞书 feishu_exporter 重试依赖；确保打入 exe
import utils.playwright_cleanup  # noqa: F401  # 采集器 finally 内动态导入；确保 onefile 含该模块

from gui import MainWindow, PLATFORMS_INFO
from thread import CrawlThread
from controller import CrawlScheduler


def _bootstrap_write(line: str) -> None:
    """
    仅用内置 open 写日志：路径固定为「exe 所在目录/logs/bootstrap.txt」。
    不依赖 loguru、不依赖 path_helper 的 PROJECT_ROOT，避免打包后路径误判时「零日志」。
    """
    try:
        log_dir = Path(sys.executable).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "bootstrap.txt", "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass


class _ScratchBytesIO(io.BytesIO):
    """
    无控制台时用作 stdout/stderr 底层缓冲。外置采集器会 logger.remove、再 logger.add(sys.stdout)，
    且雪球等会再次包装 sys.stdout；旧 TextIOWrapper 析构时会 close 底层流，若用 devnull 或普通 BytesIO
    会导致已注册到 loguru 的 sink 变成「对已关闭文件写」→ ValueError: I/O operation on closed file。
    禁止真正 close，保证缓冲在整个进程内始终可写。
    """

    def close(self) -> None:  # type: ignore[override]
        return


# 模块级强引用，避免仅被 sys.stdout 引用一轮后被 GC 连带误伤（与 _ScratchBytesIO 配合）
_SCRATCH_STDOUT_BUF = _ScratchBytesIO()
_SCRATCH_STDERR_BUF = _ScratchBytesIO()


def _ensure_stdio_not_none() -> None:
    """无控制台进程（PyInstaller --windowed）下补齐 stdout/stderr，供 loguru 与外置采集器使用。"""
    if sys.stdout is None:
        sys.stdout = io.TextIOWrapper(
            _SCRATCH_STDOUT_BUF,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )
    if sys.stderr is None:
        sys.stderr = io.TextIOWrapper(
            _SCRATCH_STDERR_BUF,
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
        )


def _load_collectors():
    """预加载所有稳定采集器（触发模块内 register），与 controller 动态加载同源。"""
    from utils.module_loader import load_collector
    from utils.platform_registry import PlatformRegistry, get_platform_registry

    for platform in PlatformRegistry.STABLE_PLATFORMS:
        try:
            load_collector(platform)
            logger.info(f"✅ 采集器 {platform} 预加载成功")
        except Exception:
            import traceback

            _bootstrap_write(f"--- 采集器 {platform} 预加载异常 ---\n{traceback.format_exc()}")
            logger.exception("采集器 {} 预加载失败（详见堆栈）", platform)

    # 验证注册结果
    registry = get_platform_registry()
    registered = registry.get_stable_platforms()
    logger.info(f"📋 已注册稳定平台: {registered} (共{len(registered)}个)")
    _bootstrap_write(
        f"预加载结束: registered={registered!r} count={len(registered)} | "
        f"path_helper PROJECT_ROOT={PROJECT_ROOT} | COLLECTORS_DIR={COLLECTORS_DIR} is_dir={COLLECTORS_DIR.is_dir()} | "
        f"PLATFORMS_DIR={PLATFORMS_DIR} is_dir={PLATFORMS_DIR.is_dir()}"
    )
    if not registered:
        logger.error(
            "未注册任何稳定平台。请检查 exe 同级是否包含 collectors/ 与 platforms/，并查看 logs/startup.log。"
            " COLLECTORS_DIR={} is_dir={} | PLATFORMS_DIR={} is_dir={}",
            COLLECTORS_DIR,
            COLLECTORS_DIR.is_dir(),
            PLATFORMS_DIR,
            PLATFORMS_DIR.is_dir(),
        )


class CrawlerApp:
    """
    采集系统应用程序
    整合 GUI、线程、调度中心
    """

    def __init__(self):
        # 创建应用程序
        self.app = QApplication(sys.argv)
        self.app.setStyle("Fusion")

        # 创建主窗口
        self.main_window = MainWindow()
        self.main_window.setWindowTitle("多平台内容采集系统 v1.0")

        from utils.platform_registry import get_platform_registry

        if not get_platform_registry().get_stable_platforms():
            QMessageBox.warning(
                self.main_window,
                "平台未加载",
                "未注册任何采集平台。\n\n"
                "请打开与本程序同级的 logs 文件夹，查看 bootstrap.txt（启动诊断）与 startup.log。\n"
                "并确认 collectors、platforms 文件夹与 exe 在同一目录。",
            )

        # 创建采集线程
        self.crawl_thread = CrawlThread()

        # 创建调度中心
        self.scheduler = CrawlScheduler(self.crawl_thread)

        # 初始化信号连接
        self._init_connections()

        # 启动线程
        self.crawl_thread.start()

    def _init_connections(self):
        """初始化信号连接"""

        # GUI -> Scheduler
        self.main_window.start_signal.connect(self._on_start)
        self.main_window.pause_signal.connect(self._on_pause)
        self.main_window.resume_signal.connect(self._on_resume)
        self.main_window.stop_signal.connect(self._on_stop)
        self.main_window.manual_login_signal.connect(self._on_manual_login_clicked)

        # Thread -> GUI
        self.crawl_thread.log_signal.connect(self._on_log)
        self.crawl_thread.data_signal.connect(self._on_data)
        self.crawl_thread.unmatched_signal.connect(self._on_unmatched)
        self.crawl_thread.progress_signal.connect(self._on_progress)
        self.crawl_thread.error_signal.connect(self._on_error)
        self.crawl_thread.finish_signal.connect(self._on_finish)
        self.crawl_thread.login_required_signal.connect(self._on_login_required)
        self.crawl_thread.login_success_signal.connect(self._on_login_success)

        # Scheduler -> GUI
        self.scheduler.platform_started.connect(self._on_platform_started)
        self.scheduler.platform_finished.connect(self._on_platform_finished)
        self.scheduler.login_required.connect(self._on_login_required)
        self.scheduler.task_finished.connect(self._on_task_finished)
        self.scheduler.manual_crawl_finished.connect(self._on_manual_crawl_finished)

    def _on_start(self, platforms: list, titles_with_index: list, keywords: list):
        """
        启动采集任务

        Args:
            platforms: 平台列表
            titles_with_index: 标题列表（含索引和是否需要关键词的标记）
            keywords: 关键词列表
        """
        # 设置平台队列
        self.scheduler.set_platforms(platforms)

        # 在线程中执行采集任务
        self.crawl_thread.submit_task(self._run_crawl, titles_with_index, keywords)

    async def _run_crawl(self, titles_with_index: list, keywords: list):
        """
        在异步环境中运行采集任务

        Args:
            titles_with_index: 标题列表（含索引和是否需要关键词的标记）
            keywords: 关键词列表
        """
        try:
            await self.scheduler.run(titles_with_index, keywords)
        except Exception as e:
            self.main_window.show_error(f"采集任务异常: {str(e)}")

    def _on_pause(self):
        """暂停任务"""
        self.scheduler.pause()

    def _on_resume(self):
        """恢复任务"""
        self.scheduler.resume()

    def _on_stop(self):
        """停止任务"""
        self.scheduler.stop()
        self.crawl_thread.stop()

    def _gui_titles_with_index(self) -> list:
        """从主窗口标题/关键词框构建与调度器一致的 targets_with_index（仅非空标题）。"""
        titles_with_index = []
        for i, line_edit in enumerate(self.main_window.title_inputs):
            title = line_edit.text().strip()
            if not title:
                continue
            keyword_text = ""
            if i < len(self.main_window.keyword_inputs):
                keyword_text = self.main_window.keyword_inputs[i].text().strip()
            titles_with_index.append({
                "index": i,
                "title": title,
                "char_count": len(title),
                "use_keyword": len(title) > 30,
                "keyword": keyword_text,
            })
        return titles_with_index

    def _on_manual_login_clicked(self, platform: str):
        """
        模块4「手动登录采集」与模块5「手动登录并采集」共用：提交 execute_manual_crawl 到 CrawlThread。

        Args:
            platform: 平台标识
        """
        from utils.platform_registry import get_platform_registry

        registry = get_platform_registry()
        name = registry.get_platform_display_name(platform)

        titles_with_index = self._gui_titles_with_index()
        if not titles_with_index:
            self.main_window.append_log("手动登录采集：请至少输入一个目标标题")
            QMessageBox.warning(
                self.main_window,
                "提示",
                "请至少输入一个目标标题（与主界面标题输入框相同）",
            )
            self.main_window.manual_login_btn.setEnabled(True)
            self.main_window.manual_collect_btn.setEnabled(True)
            return

        self.main_window.append_log(f"开始手动登录采集: {name}（将弹出有头浏览器）")

        self.crawl_thread.submit_task(
            self.scheduler.execute_manual_crawl,
            platform,
            titles_with_index,
        )

    def _on_manual_crawl_finished(self, platform: str):
        """一次手动有头采集协程结束，恢复两个手动入口按钮（与 gui 侧防连点配合）。"""
        self.main_window.manual_login_btn.setEnabled(True)
        self.main_window.manual_collect_btn.setEnabled(True)

    def _on_log(self, message: str):
        """日志消息"""
        self.main_window.append_log(message)

    def _on_data(self, data: dict):
        """采集数据"""
        self.main_window.add_data(data)

    def _on_unmatched(self, platform: str, title: str):
        """未匹配标题"""
        self.main_window.add_unmatched(platform, title)

    def _on_progress(self, current: int, total: int, platform: str):
        """进度更新"""
        self.main_window.update_progress(current, total, platform)

    def _on_error(self, error: str):
        """错误发生"""
        self.main_window.append_log(f"[错误] {error}")

    def _on_finish(self, data: list, unmatched: list):
        """任务完成"""
        self.main_window.task_finished()

    def _on_platform_started(self, platform: str):
        """平台开始"""
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        name = registry.get_platform_display_name(platform)
        self.main_window.append_log(f"开始采集: {name}")

    def _on_platform_finished(self, platform: str, success: bool):
        """平台完成"""
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        name = registry.get_platform_display_name(platform)
        status = "成功" if success else "失败"
        self.main_window.append_log(f"平台 {name} 执行{status}")

    def _on_login_required(self, platform: str):
        """
        自动采集登录失败：仅加入「4. 手动登录平台」列表并写日志，不弹出阻塞对话框。

        扫码类平台（雪球等）在 headless 下无法完成登录；请用户在列表中单选后点击
        【手动登录采集】或到「最终补采」使用有头浏览器。
        """
        from utils.platform_registry import get_platform_registry

        registry = get_platform_registry()
        name = registry.get_platform_display_name(platform)
        self.main_window.append_log(
            f"自动登录失败，已加入「4. 手动登录平台」: {name}。"
            "请在该列表中单选本平台后点击【手动登录采集】（有头浏览器）；勿依赖弹窗。"
        )
        self.main_window.add_manual_login_platform(platform)

    def _on_login_success(self, platform: str):
        """登录成功"""
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()
        name = registry.get_platform_display_name(platform)
        self.main_window.append_log(f"登录成功: {name}")

    def _on_task_finished(self, data: list, unmatched: list):
        """任务全部完成"""
        self.main_window.append_log("=" * 50)
        self.main_window.append_log("采集任务完成")
        self.main_window.append_log(f"总数据: {len(data)} 条")
        self.main_window.append_log(f"未匹配: {len(unmatched)} 条")

    def run(self):
        """运行应用程序"""
        self.main_window.show()
        return self.app.exec()


def main():
    """主函数"""
    _ensure_stdio_not_none()
    # 禁用Playwright的asyncio警告
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    # ========== 启动时加载所有采集器（触发注册）==========
    _load_collectors()

    # 创建应用
    app = CrawlerApp()

    # 运行
    sys.exit(app.run())


if __name__ == "__main__":
    _ensure_stdio_not_none()

    _bootstrap_write("=== 程序入口 __main__ ===")
    _bootstrap_write(
        "PLAYWRIGHT_BROWSERS_PATH="
        f"{os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '')!r}"
    )
    load_dotenv(ENV_PATH)
    _bootstrap_write(
        f"frozen={getattr(sys, 'frozen', False)} _MEIPASS={bool(getattr(sys, '_MEIPASS', None))} "
        f"is_bundled_runtime={is_bundled_runtime()}"
    )
    _bootstrap_write(f"sys.executable={sys.executable}")
    _bootstrap_write(f"path_helper PROJECT_ROOT={PROJECT_ROOT} LOGS_DIR={LOGS_DIR}")
    # 打包后双击 exe 时 cwd 常为 System32 等，相对路径与部分库行为会错位；统一到 exe 同级目录
    if is_bundled_runtime():
        try:
            os.chdir(PROJECT_ROOT)
            _bootstrap_write(f"os.chdir 成功 cwd={Path.cwd()}")
        except OSError as e:
            _bootstrap_write(f"os.chdir 失败: {e}")
            logger.warning("无法 os.chdir 到 PROJECT_ROOT（{}）: {}", PROJECT_ROOT, e)
    ensure_runtime_dirs()
    # 须在 _load_collectors() 之前；enqueue=False + backtrace 避免冻结环境日志丢失、便于排错
    try:
        logger.add(
            str(LOGS_DIR / "startup.log"),
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
            level="DEBUG",
            encoding="utf-8",
            enqueue=False,
            backtrace=True,
            diagnose=True,
            rotation="5 MB",
            retention=3,
        )
        _bootstrap_write(f"logger.add startup.log -> {LOGS_DIR / 'startup.log'}")
    except Exception as e:
        _bootstrap_write(f"logger.add startup.log 失败: {e}")
    main()
