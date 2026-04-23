# ========== 独立调试支持（不影响主程序）==========
import sys
import os
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ========== 结束 ==========

# ========== 调试用目标文章列表（仅独立运行时生效）==========
DEBUG_TARGETS = [
    "测试文章标题一",
    "测试文章标题二",
    "测试文章标题三",
    "测试文章标题四",
    "测试文章标题五"
]
# ========== 结束 ==========

"""
===============================================================
雪球采集器
基于 template_collector.py 模板实现
登录方式:信扫码登录
数据后台:https://mp.xueqiu.com/dataview/works
===============================================================
"""

import os
import sys
from pathlib import Path

# 添加包根目录到路径（解决直接运行时的模块导入问题）
_pkg_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_root))

import asyncio
import random
import json
import re
import csv
import yaml
import unicodedata
from typing import Callable, Type, Tuple, Optional, Any, List, Dict
from utils.title_matcher import match_crawled_in_target
from utils.env_loader import get_platform_credentials_with_fallback
from utils.path_helper import PLATFORMS_DIR, COOKIES_DIR, LOGS_DIR, DATA_DIR
from datetime import datetime
from playwright.async_api import async_playwright, Playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
from loguru import logger

# ===================== 平台配置项（请勿修改逻辑，仅修改参数） =====================
PLATFORM_NAME = "雪球"  # 平台名称（页面定位用）
ACCOUNT_NICKNAME = "账号名称"  # 账号昵称（侧边栏/页面判断用，替换原硬编码字符）
# ==============================================================================

# 设置控制台输出编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ============================================================
# 1. 配置加载模块
# ============================================================

class ConfigLoader:
    """加载平台配置(YAML)，提供点号路径访问方法"""

    def __init__(self, platform: str):
        self.platform = platform
        try:
            self.config = self._load_config()
            self._validate_config()
            logger.success(f"✅ 配置文件加载成功: {platform}")
        except Exception as e:
            logger.critical(f"❌ 配置初始化失败: {e}")
            raise

    def _load_config(self) -> Dict:
        """加载平台配置文件"""
        config_path = PLATFORMS_DIR / f"{self.platform}.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"❌ 配置文件 {config_path} 不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            if config is None:
                raise ValueError(f"❌ 配置文件为空: {config_path}")

        # 处理动态路径
        if "cookie_file" in config:
            config["cookie_file"] = config["cookie_file"].replace(
                "{{cookie_dir}}", config.get("cookie_dir", str(COOKIES_DIR))
            ).replace("{{platform}}", self.platform)

        return config

    def _validate_config(self):
        """配置项完整性检查"""
        logger.info("🔍 开始验证配置文件...")
        # 雪球使用扫码登录，不需要用户名密码
        required_sections = {}

        missing_fields = []
        for section, fields in required_sections.items():
            if section not in self.config:
                logger.warning(f"⚠️ 配置段缺失: {section}")
                continue
            for field in fields:
                if field not in self.config[section]:
                    missing_fields.append(f"{section}.{field}")

        if missing_fields:
            logger.warning(f"⚠️ 配置项缺失: {', '.join(missing_fields)}")
        else:
            logger.success("✅ 配置验证完成，所有必需字段存在")

    def get(self, path: str, default: Any = None) -> Any:
        """通过点号路径获取配置值"""
        keys = path.split(".")
        value = self.config

        try:
            for key in keys:
                if isinstance(value, list):
                    if all(isinstance(item, dict) for item in value):
                        value = next((item for item in value if key in item), {})
                        value = value.get(key)
                    else:
                        value = value[int(key)] if key.isdigit() else None
                else:
                    value = value.get(key)
                if value is None:
                    return default
            return value
        except (KeyError, IndexError, AttributeError):
            return default


# ============================================================
# 日志配置
# ============================================================

logger.remove()
log_dir = LOGS_DIR
log_dir.mkdir(exist_ok=True)

# 控制台输出
logger.add(
    sys.stdout,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
    level="INFO",
    colorize=False
)

# 文件输出
logger.add(
    str(log_dir / "{platform}_crawler_{{time:YYYY-MM-DD}}.log".format(platform="{{platform}}")),
    rotation="00:00",
    retention="7 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{line} | {message}",
    level="DEBUG",
    enqueue=True
)


# ============================================================
# 平台配置初始化
# ============================================================

PLATFORM_NAME = "xueqiu"
config = ConfigLoader(PLATFORM_NAME)

USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
DATA_PAGE_URL = config.get("data_page_url", "")
# 已从环境变量加载，注释掉从 YAML 读取的代码
# USERNAME = config.get("username", "")
# PASSWORD = config.get("password", "")

# 路径配置
COOKIE_DIR = COOKIES_DIR
COOKIE_FILE = COOKIE_DIR / f"{PLATFORM_NAME}.json"

# 超时配置
DEFAULT_TIMEOUT = config.get("default_timeout", 30000)
ELEMENT_TIMEOUT = config.get("element_timeout", 8000)
ELEMENT_SHORT_TIMEOUT = config.get("element_short_timeout", 5000)

# 重试配置
MAX_RETRIES = config.get("max_retries", 2)
RETRY_DELAY_MIN = config.get("retry_delay_min", 3000)
RETRY_DELAY_MAX = config.get("retry_delay_max", 5000)

# 用户代理
USER_AGENTS: List[str] = config.get("user_agents", [])
TYPING_DELAY_MIN = config.get("typing_delay_min", 50)
TYPING_DELAY_MAX = config.get("typing_delay_max", 150)

# 功能开关
ENABLE_STEALTH = config.get("enable_stealth", True)
ENABLE_USER_AGENT_RANDOM = config.get("enable_user_agent_random", True)
ENABLE_HUMAN_TYPING = config.get("enable_human_typing", True)

# 数据采集限制
MAX_ARTICLES = config.get("max_articles", 60)


# ============================================================
# 2. 通用工具模块
# ============================================================

class AntiSpiderHelper:
    """反爬工具:随机UA、人类输入模拟"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    def get_random_user_agent(self) -> str:
        user_agents = self.config.get("user_agents")
        return random.choice(user_agents) if user_agents else ""

    async def human_typing(self, page, selector: str, text: str) -> None:
        locator = page.locator(selector)
        await locator.clear()
        for char in text:
            delay = random.randint(
                self.config.get("typing_delay_min", 50),
                self.config.get("typing_delay_max", 150)
            )
            await locator.type(char, delay=delay)

    async def human_typing_selector(self, locator, text: str) -> None:
        await locator.clear()
        chunk_size = 3
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            delay = random.randint(
                self.config.get("typing_delay_min", 50),
                self.config.get("typing_delay_max", 150)
            )
            await locator.type(chunk, delay=delay)


class RetryManager:
    """重试装饰器"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.retryable_exceptions = (
            PlaywrightTimeoutError,
            TimeoutError,
            ConnectionError,
            OSError,
        )

    def get_random_retry_delay(self) -> int:
        return random.randint(
            self.config.get("retry_delay_min", 3000),
            self.config.get("retry_delay_max", 5000)
        )


# ============================================================
# 3. 登录模块(雪球使用微信扫码登录)
# ============================================================

class LoginManager:
    """管理登录流程:Cookie加载/保存、登录态验证、扫码登录"""

    def __init__(self, config: ConfigLoader, anti_spider: AntiSpiderHelper, retry_manager: RetryManager):
        self.config = config
        self.anti_spider = anti_spider
        self.retry_manager = retry_manager
        self.platform = config.platform

        self.login_url = self.config.get("login_url", "")
        self.main_url = self.config.get("main_url", "")
        self.data_page_url = self.config.get("data_page_url", "")

    async def is_logged_in(self, page) -> bool:
        """
        验证当前登录态是否有效（导航到数据后台验证）

        验证逻辑：
        1. 访问数据后台页面：https://mp.xueqiu.com/dataview/works
        2. 等待页面完全加载（需要2-3秒让数据渲染）
        3. 检查右上角文字：
           - 显示「未登录」→ Cookie 失效，返回 False
           - 显示账号名称（如「{ACCOUNT_NICKNAME}」）→ Cookie 有效，继续验证
        4. 检查文章列表：
           - 显示「暂无数据」且「共0条」 → 可能是真正无数据，也可能登录态异常
           - 有文章数据 → 登录成功，返回 True

        Returns:
            bool: True表示登录有效，False表示需要重新登录
        """
        try:
            # 访问数据后台页面
            data_page_url = self.data_page_url
            logger.info(f"🔍 验证登录态: {data_page_url}")

            await page.goto(data_page_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)

            # 【关键】等待数据加载完成（根据验证结果，需要2-3秒）
            await page.wait_for_timeout(3000)

            # 步骤1：检查右上角文字（登录态主要判断依据）
            user_name_text = None
            user_name_element_found = False

            # 策略1：直接定位用户名 span 元素（最准确）
            try:
                # 使用多个选择器尝试定位
                user_name_selectors = [
                    "span[class*='user-name']",  # class 包含 user-name
                    ".header_user-name",  # class 包含 header_user-name
                    "span[class*='header_user']",  # class 包含 header_user
                    "nav div span",  # nav 下的 div 下的 span
                    "xpath=//nav//div//span",  # xpath 定位
                    "xpath=//*[@id=\"app\"]/div/div/div[1]/nav/div/div/span",  # 完整 xpath
                ]

                for selector in user_name_selectors:
                    try:
                        user_name_span = page.locator(selector).first
                        if await user_name_span.count() > 0:
                            user_name_text = await user_name_span.text_content()
                            if user_name_text:
                                user_name_text = user_name_text.strip()
                                logger.debug(f"✅ 通过选择器「{selector}」找到用户名: {user_name_text}")
                                user_name_element_found = True
                                break
                    except Exception as e:
                        logger.debug(f"选择器「{selector}」定位失败: {e}")
                        continue

            except Exception as e:
                logger.debug(f"策略1定位失败: {e}")

            # 策略2：通过 JavaScript 查找（备用方案）
            if not user_name_element_found:
                try:
                    user_name_text = await page.evaluate("""
                        () => {
                            // 方法1：查找包含 user-name 的 span
                            const userNameSpan = document.querySelector('span[class*="user-name"]');
                            if (userNameSpan) {
                                return userNameSpan.textContent.trim();
                            }

                            // 方法2：查找 nav 下的 span
                            const nav = document.querySelector('nav');
                            if (nav) {
                                const navSpan = nav.querySelector('span');
                                if (navSpan) {
                                    return navSpan.textContent.trim();
                                }
                            }

                            // 方法3：查找所有 span，排除常见非用户名的
                            const allSpans = Array.from(document.querySelectorAll('span'));
                            for (const span of allSpans) {
                                const text = span.textContent.trim();
                                // 排除常见非用户名文本
                                if (text && text.length > 0 && text.length < 20 &&
                                    text !== '首页' && text !== '发布' && text !== '草稿箱' &&
                                    text !== '内容管理' && text !== '数据中心' &&
                                    text !== '创作者权益' && text !== '我的专栏' &&
                                    text !== '投诉中心') {
                                    return text;
                                }
                            }

                            return null;
                        }
                    """)
                except Exception as e:
                    logger.debug(f"策略2定位失败: {e}")

            if user_name_text:
                logger.info(f"👤 右上角显示: {user_name_text}")

                # 检查是否显示「未登录」
                if user_name_text == "未登录":
                    logger.warning("⚠️ 检测到「未登录」，Cookie 失效")
                    return False

                # 显示账号名称（如「{ACCOUNT_NICKNAME}」），登录态有效
                logger.success(f"✅ 检测到账号名称: {user_name_text}")
            else:
                logger.warning("⚠️ 未找到用户名元素，可能登录态异常")
                # 不要立即返回 False，继续检查文章列表
                return False

            # 步骤2：检查文章列表数据（辅助验证）
            try:
                # 检查是否显示「暂无数据」
                no_data_locator = page.get_by_text("暂无数据")
                if await no_data_locator.is_visible(timeout=2000):
                    # 检查分页信息是否显示「共0条」
                    page_info_text = await page.evaluate("""
                        () => {
                            const pageInfo = Array.from(document.querySelectorAll('li')).find(li => li.textContent.includes('共'));
                            return pageInfo ? pageInfo.textContent.trim() : null;
                        }
                    """)

                    if page_info_text and "共0条" in page_info_text:
                        logger.warning(f"⚠️ 显示「暂无数据」且「共0条」，可能是未登录")
                        # 但右上角已显示账号名称，所以可能是真正无数据
                        # 根据任务要求，右上角显示账号名称即为登录成功
                        logger.info(f"✅ 虽然无数据，但登录态有效（右上角显示 {ACCOUNT_NICKNAME}）")

                # 检查是否有文章数据行
                data_rows = page.locator("tbody tr")
                row_count = await data_rows.count()
                logger.info(f"📊 检测到 {row_count} 行数据")

                if row_count > 0:
                    # 检查第一行是否是真正的数据行（排除"暂无数据"）
                    first_row_text = await data_rows.first.inner_text()
                    if "暂无数据" not in first_row_text:
                        logger.success("✅ 检测到文章数据，登录态有效")

            except Exception as e:
                logger.debug(f"检查文章列表数据: {e}")

            # 所有检查通过，登录态有效
            return True

        except Exception as e:
            logger.error(f"❌ 验证登录态失败: {e}")
            return False

    async def _check_login_status_on_current_page(self, page) -> bool:
        """
        轻量级登录状态检查：在当前页面（不导航）检查右上角是否显示 {ACCOUNT_NICKNAME}（非「未登录」）。
        用于手动登录等待中的轮询，避免刷新页面打断用户操作。
        """
        try:
            # 尝试多种选择器定位用户名
            user_name_selectors = [
                "span[class*='user-name']",
                ".header_user-name",
                "span[class*='header_user']",
                "nav div span",
                "xpath=//nav//div//span",
                "xpath=//*[@id=\"app\"]/div/div/div[1]/nav/div/div/span",
            ]
            for selector in user_name_selectors:
                try:
                    user_name_span = page.locator(selector).first
                    if await user_name_span.count() > 0:
                        text = await user_name_span.text_content()
                        if text and text.strip() and text.strip() != "未登录":
                            logger.debug(f"✅ 当前页面检测到用户名: {text.strip()}")
                            return True
                except Exception:
                    continue

            # 备用：JavaScript 获取
            user_name = await page.evaluate("""
                () => {
                    const span = document.querySelector('span[class*="user-name"]');
                    if (span) return span.textContent.trim();
                    const navSpan = document.querySelector('nav span');
                    if (navSpan) return navSpan.textContent.trim();
                    return null;
                }
            """)
            if user_name and user_name != "未登录":
                logger.debug(f"✅ JS检测到用户名: {user_name}")
                return True

            return False
        except Exception as e:
            logger.debug(f"检查当前页面登录态失败: {e}")
            return False

    async def wait_for_manual_login(self, page, context, timeout_seconds: int = 300) -> bool:
        """
        等待用户手动登录（适用于扫码登录场景，无任何自动操作）

        流程：
        1. 清除当前上下文 Cookie，避免半失效态导致后台页不跳转、用户看不到登录入口
        2. 直接打开配置中的登录页（雪球为 https://xueqiu.com/），不依赖访问 mp 后台再重定向
        3. 轮询检测登录状态（不主动刷新页面），每30秒一次；再用数据后台 is_logged_in 做最终确认
        4. 通过后保存 Cookie
        """
        logger.info("💡 请手动完成登录（扫码或输入账号密码）")
        logger.info("📌 程序将每30秒检查一次登录状态（不刷新页面），最长等待5分钟")

        await context.clear_cookies()
        logger.debug("🧹 已清除浏览器 Cookie，准备打开登录页")

        open_url = (self.login_url or self.main_url or "https://xueqiu.com/").strip()
        if not open_url:
            open_url = "https://xueqiu.com/"
        logger.info(f"📍 导航到登录页面: {open_url}")
        await page.goto(open_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        await page.wait_for_timeout(3000)

        logger.info("✅ 已打开登录页面，请完成扫码或账号登录")

        waited = 0
        interval = 30
        while waited < timeout_seconds:
            await asyncio.sleep(interval)
            waited += interval

            # 主站与 mp 后台 DOM 不同：后台用 _check_login_status_on_current_page；主站用 _verify_login_success
            on_page = await self._check_login_status_on_current_page(page)
            on_page = on_page or await self._verify_login_success(page)
            if on_page:
                logger.success("✅ 检测到登录成功！")
                if await self.is_logged_in(page):
                    await self.save_storage_state(context)
                    return True
                logger.warning("⚠️ 当前页似已登录但数据后台验证未通过，继续等待")
                continue

            logger.info(f"⏳ 等待手动登录中... ({waited}/{timeout_seconds}秒)")

        logger.error(f"❌ 手动登录超时（{timeout_seconds}秒）")
        return False

    async def ensure_login(self, page, context, headless: bool = False) -> bool:
        """
        确保登录状态

        核心逻辑：
        1. 优先使用 Cookie 登录
        2. Cookie 失效时，根据 headless 参数决定：
           - headless=True（GUI调度）：直接返回失败，由调度器处理手动登录
           - headless=False（独立运行或手动登录）：进入手动等待模式

        Args:
            page: Playwright 页面对象
            context: Playwright 上下文对象
            headless: 是否为无头模式

        Returns:
            bool: True表示登录成功，False表示登录失败
        """
        try:
            logger.info("🔐 开始确保登录状态...")

            # 步骤1：尝试使用 Cookie 登录
            logger.info("🍪 尝试使用 Cookie 登录...")
            is_cookie_valid = await self.is_logged_in(page)

            if is_cookie_valid:
                logger.success("✅ Cookie 登录成功！")
                return True

            # 步骤2：Cookie 失效，根据 headless 决定处理方式
            if headless:
                logger.warning("⚠️ 无头模式下 Cookie 失效，无法自动登录，请使用手动登录模式")
                return False
            else:
                logger.warning("⚠️ Cookie 失效，启动手动登录模式...")
                return await self.wait_for_manual_login(page, context)

        except Exception as e:
            logger.error(f"❌ 确保登录状态失败: {e}")
            return False

    async def perform_login(self, page, context, headless: bool = False) -> bool:
        """
        执行微信扫码登录流程（已被 wait_for_manual_login 替代，保留以防其他场景使用）

        登录流程：
        1. 访问雪球首页：https://xueqiu.com/
        2. 点击登录按钮（立即登录/注册）
        3. 切换到二维码登录模式
        4. 点击「微信登录」按钮
        5. 等待用户扫码完成
        6. 验证登录成功（右上角显示 {ACCOUNT_NICKNAME}）
        7. 保存 Cookie

        Args:
            page: Playwright 页面对象
            context: Playwright 上下文对象
            headless: 是否为无头模式（雪球扫码必须为 False）

        Returns:
            bool: True表示登录成功，False表示登录失败
        """
        if headless:
            logger.warning(
                "无头模式下禁止执行 perform_login（扫码需有头浏览器）；"
                "GUI 自动采集应走 ensure_login(headless=True) 失败并由调度器加入手动登录队列。"
            )
            return False
        try:
            logger.info("🔄 开始执行微信扫码登录流程...")

            # 步骤1：访问雪球首页
            logger.info(f"📍 访问雪球首页: {self.main_url}")
            await page.goto(self.main_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
            await page.wait_for_timeout(2000)

            # 步骤2：点击登录按钮
            login_button = None

            # 策略1：使用配置文件中的选择器
            login_button_selectors = self.config.get("login.login_button_selectors", [])
            for selector_config in login_button_selectors:
                try:
                    if "css" in selector_config:
                        login_button = page.locator(selector_config["css"]).first
                    elif "text" in selector_config:
                        login_button = page.get_by_text(selector_config["text"], exact=False)

                    if await login_button.count() > 0 and await login_button.is_visible(timeout=2000):
                        logger.info(f"✅ 通过配置找到登录按钮")
                        break
                except Exception as e:
                    logger.debug(f"配置选择器失败: {selector_config}, 错误: {e}")
                    continue

            # 策略2：使用通用选择器（备用）
            if not login_button or await login_button.count() == 0:
                logger.info("🔍 使用备用选择器查找登录按钮...")
                common_login_selectors = [
                    "button:has-text('登录')",
                    "a:has-text('登录')",
                    "div:has-text('登录')",
                    "span:has-text('登录')",
                    "[class*='login']",
                    "[class*='Login']",
                ]

                for selector in common_login_selectors:
                    try:
                        login_button = page.locator(selector).first
                        if await login_button.count() > 0 and await login_button.is_visible(timeout=2000):
                            logger.info(f"✅ 通过备用选择器「{selector}」找到登录按钮")
                            break
                    except Exception as e:
                        logger.debug(f"备用选择器「{selector}」失败: {e}")
                        continue

            if not login_button or await login_button.count() == 0:
                logger.error("❌ 未找到登录按钮，尝试截图诊断...")
                try:
                    await page.screenshot(path="debug_login_page.png")
                    logger.info("📸 已保存调试截图: debug_login_page.png")
                except Exception:
                    pass
                return False

            # 点击登录按钮（使用 force: true 强制点击，解决元素在视口外的问题）
            try:
                await login_button.click(force=True)
                await page.wait_for_timeout(2000)
                logger.info("✅ 已点击登录按钮")
            except Exception as e:
                logger.warning(f"点击登录按钮失败: {e}")
                return False

            # 步骤3：切换到二维码登录模式
            switch_qrcode_selectors = self.config.get("login.switch_to_qrcode_selectors", [])

            qrcode_button = None
            for selector_config in switch_qrcode_selectors:
                try:
                    if "text" in selector_config:
                        qrcode_button = page.get_by_text(selector_config["text"], exact=False)

                    if await qrcode_button.is_visible(timeout=3000):
                        logger.info(f"✅ 找到二维码登录切换按钮")
                        break
                except Exception:
                    continue

            if qrcode_button and await qrcode_button.is_visible():
                await qrcode_button.click()
                await page.wait_for_timeout(1000)
                logger.info("✅ 已切换到二维码登录模式")

            # 步骤4：点击微信登录
            wechat_login_selectors = self.config.get("login.wechat_login_selectors", [])

            wechat_button = None
            for selector_config in wechat_login_selectors:
                try:
                    if "text" in selector_config:
                        wechat_button = page.get_by_text(selector_config["text"], exact=False)

                    if await wechat_button.is_visible(timeout=3000):
                        logger.info(f"✅ 找到微信登录按钮")
                        break
                except Exception:
                    continue

            if not wechat_button:
                logger.warning("⚠️ 未找到微信登录按钮，可能已在微信登录模式")
            else:
                await wechat_button.click()
                await page.wait_for_timeout(1000)
                logger.info("✅ 已点击微信登录")

            # 步骤5：等待用户扫码登录
            logger.info("⏳ 等待用户扫码登录（最多等待5分钟）...")
            logger.info("💡 请使用微信扫描页面上的二维码完成登录")
            logger.info("📌 程序将每30秒检查一次登录态，最长等待5分钟")

            # 等待登录成功的标志（右上角显示 {ACCOUNT_NICKNAME}）
            max_wait_seconds = 300  # 5分钟
            check_interval = 30      # 每30秒检查一次
            waited_seconds = 0

            while waited_seconds < max_wait_seconds:
                await page.wait_for_timeout(check_interval * 1000)
                waited_seconds += check_interval

                # 刷新页面以获取最新的登录状态（登录成功后对话框会关闭）
                try:
                    await page.goto(self.main_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass

                # 检查是否登录成功
                if await self._verify_login_success(page):
                    logger.success("✅ 扫码登录成功！")
                    return True

                logger.info(f"⏳ 等待扫码中... ({waited_seconds}/{max_wait_seconds}秒)")

            logger.error("❌ 扫码登录超时，请在5分钟内完成扫码")
            return False

        except Exception as e:
            logger.error(f"❌ 执行微信扫码登录失败: {e}")
            return False

    async def _verify_login_success(self, page) -> bool:
        """
        验证登录是否成功（辅助方法）

        验证逻辑：
        - 检查右上角是否显示 {ACCOUNT_NICKNAME}（非「未登录」）
        - 可选：检查是否有登录态标志元素

        Args:
            page: Playwright 页面对象

        Returns:
            bool: True表示登录成功，False表示未登录
        """
        try:
            # 方法1：检查右上角文字（主要验证方式）
            try:
                # 使用与 is_logged_in 相同的定位策略
                user_name_text = None
                user_name_element_found = False

                # 使用多个选择器尝试定位
                user_name_selectors = [
                    "span[class*='user-name']",  # class 包含 user-name
                    ".header_user-name",  # class 包含 header_user-name
                    "span[class*='header_user']",  # class 包含 header_user
                    "nav div span",  # nav 下的 div 下的 span
                    "xpath=//nav//div//span",  # xpath 定位
                ]

                for selector in user_name_selectors:
                    try:
                        user_name_span = page.locator(selector).first
                        if await user_name_span.count() > 0:
                            user_name_text = await user_name_span.text_content()
                            if user_name_text:
                                user_name_text = user_name_text.strip()
                                if user_name_text != "未登录":
                                    logger.info(f"✅ 验证登录成功: 右上角显示「{user_name_text}」")
                                    return True
                    except Exception as e:
                        logger.debug(f"选择器「{selector}」验证失败: {e}")
                        continue

                # JavaScript 备用方案
                user_name_text = await page.evaluate("""
                    () => {
                        const userNameSpan = document.querySelector('span[class*="user-name"]');
                        if (userNameSpan) {
                            return userNameSpan.textContent.trim();
                        }
                        return null;
                    }
                """)

                if user_name_text and user_name_text != "未登录":
                    logger.info(f"✅ 验证登录成功: 右上角显示「{user_name_text}」")
                    return True

            except Exception as e:
                logger.debug(f"方法1验证失败: {e}")

            # 方法2：使用配置文件中的登录态验证选择器
            verify_selectors = self.config.get("verify_logged_in_selectors", [])

            for selector_config in verify_selectors:
                try:
                    if "text" in selector_config:
                        # 检查文本是否存在
                        element = page.get_by_text(selector_config["text"], exact=False)
                        if await element.count() > 0:
                            # 获取文本内容，确保不是"未登录"
                            text_content = await element.text_content()
                            if text_content and text_content.strip() != "未登录":
                                logger.debug(f"✅ 验证登录成功: 找到文本「{selector_config['text']}」")
                                return True

                    elif "css" in selector_config:
                        element = page.locator(selector_config["css"])
                        if await element.count() > 0:
                            text_content = await element.text_content()
                            if text_content and text_content.strip() != "未登录":
                                logger.debug(f"✅ 验证登录成功: 找到元素「{selector_config['css']}」")
                                return True

                    elif "xpath" in selector_config:
                        element = page.locator(selector_config["xpath"])
                        if await element.count() > 0:
                            text_content = await element.text_content()
                            if text_content and text_content.strip() != "未登录":
                                logger.debug(f"✅ 验证登录成功: 找到元素「{selector_config['xpath']}」")
                                return True

                except Exception as e:
                    logger.debug(f"选择器验证失败: {selector_config}, 错误: {e}")
                    continue

            logger.debug("❌ 验证登录失败: 未找到登录态标志")
            return False

        except Exception as e:
            logger.debug(f"❌ 验证登录成功时发生异常: {e}")
            return False

    def _get_locator(self, page, selector: Dict) -> Optional[Any]:
        """
        多策略获取元素定位器

        支持的选择器类型：
        - css: CSS 选择器
        - xpath: XPath 选择器
        - text: 文本选择器
        - role: 角色选择器（如 "button", "textbox"）

        Args:
            page: Playwright 页面对象
            selector: 选择器配置字典

        Returns:
            Locator 或 None: 返回元素定位器，如果未找到则返回 None
        """
        try:
            if "css" in selector:
                return page.locator(selector["css"])

            elif "xpath" in selector:
                return page.locator(selector["xpath"])

            elif "text" in selector:
                return page.get_by_text(selector["text"])

            elif "role" in selector:
                role = selector["role"]
                name = selector.get("name")
                if name:
                    return page.get_by_role(role, name=name)
                else:
                    return page.get_by_role(role)

            return None

        except Exception as e:
            logger.debug(f"获取定位器失败: {selector}, 错误: {e}")
            return None

    async def save_storage_state(self, context, filepath=None) -> None:
        """保存浏览器状态"""
        if filepath is None:
            COOKIE_DIR.mkdir(exist_ok=True)
            filepath = COOKIE_FILE

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        storage_state = await context.storage_state()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(storage_state, f, ensure_ascii=False, indent=2)
        logger.success(f"✅ 登录态已保存到 {filepath}")

    def load_storage_state(self, filepath=None) -> Optional[str]:
        """加载保存的浏览器状态"""
        if filepath is None:
            filepath = COOKIE_FILE
        if os.path.exists(filepath):
            return filepath
        return None


# ============================================================
# 4. 导航模块
# ============================================================

class NavigationManager:
    """管理页面导航"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到作品详情页面

        逻辑：
        - Cookie登录有效时：直接导航到数据后台页面
        - Cookie登录失效时：由ensure_login处理扫码登录，登录成功后再导航
        - 登录态已由 ensure_login 验证过，这里只需要等待数据加载

        注意：根据验证结果，数据加载需要2-3秒
        """
        try:
            # 直接导航到数据后台页面（不需要先访问首页）
            # 登录态由 ensure_login 已经验证过了
            data_page_url = self.config.get("data_page_url", "")
            logger.info(f"🔄 导航到数据后台: {data_page_url}")

            # 使用 domcontentloaded 而非 networkidle，适配雪球动态后台+长连接
            await page.goto(data_page_url, wait_until="domcontentloaded", timeout=60000)

            # 【关键】等待数据完全加载（根据验证结果，需要2-3秒）
            logger.info("⏳ 等待数据后台数据加载（3秒）...")
            await page.wait_for_timeout(3000)

            # 等待核心元素加载
            try:
                # 使用用户名选择器或表格
                await page.wait_for_selector("span[class*='user-name'], .header_user-name, table", timeout=10000)
                logger.debug("✅ 核心元素已加载")
            except Exception:
                # 核心元素加载失败，继续执行，后续验证会捕获错误
                logger.warning("⚠️ 核心元素加载超时，继续执行...")

            # 验证是否成功进入数据后台（而非登录页）
            if "/auth/" in page.url or "/login/" in page.url:
                logger.warning(f"⚠️ 被重定向到登录页: {page.url}")
                return False

            # 检查右上角是否显示「未登录」（兜底验证）
            try:
                user_name_text = await page.evaluate("""
                    () => {
                        const userNameSpan = document.querySelector('span[class*="user-name"]');
                        if (userNameSpan) {
                            return userNameSpan.textContent.trim();
                        }
                        return null;
                    }
                """)

                if user_name_text and user_name_text == "未登录":
                    logger.warning("⚠️ 数据后台显示「未登录」，登录态失效")
                    return False

            except Exception as e:
                logger.debug(f"检查登录态失败: {e}")

            logger.info("✅ 导航到作品详情页面完成")
            return True

        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False


# ============================================================
# 5. 数据提取模块
# ============================================================

class TitleMatcher:
    """
    标题匹配器（仅使用 re 库 + 原生字符串包含）

    ⚠️ 重要：雪球号特殊匹配逻辑
    ----------------------------------------
    雪球平台的文章列表中显示的标题会被截断（只显示前半部分），
    而我们的目标标题是完整的。因此不能使用常规的"目标 in 抓取"匹配，
    而是需要使用"抓取 in 目标"的反向匹配逻辑。

    例如：
        - 目标标题（完整）："微信直接能用!腾讯这只小龙虾，帮我找到了最强股市薅羊毛姿势"
        - 抓取标题（截断）："微信直接能用!腾讯这只小龙虾"
        - 匹配结果：True（因为截断标题被完整标题包含）

    匹配规则：clean(抓取标题) in clean(目标标题)
    """
    def match(self, current_title: str, target_titles: List[str]) -> bool:
        """
        判断当前抓取到的文章标题是否被目标标题列表中的任意一个包含

        Args:
            current_title: 从雪球文章列表中抓取到的截断标题
            target_titles: 完整的待匹配目标标题列表

        Returns:
            True 如果截断标题被任意一个目标标题包含，否则 False
        """
        return match_crawled_in_target(target_titles, current_title)


class ArticleListExtractor:
    """文章列表数据提取器"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """提取文章数据"""
        remaining_titles = target_titles.copy()
        extracted_articles = []
        max_articles = MAX_ARTICLES
        article_count = 0

        logger.info(f"📊 开始提取文章，目标: {len(remaining_titles)} 篇，目标标题: {remaining_titles}")

        while article_count < max_articles and remaining_titles:
            articles = await self._extract_page_articles(page)

            if not articles:
                logger.warning("⚠️ 当前页面无文章数据")
                break

            logger.info(f"📊 本页提取到 {len(articles)} 篇文章，成功翻页")

            for article in articles:
                article_count += 1
                article_title = article.get('title', '')

                if not remaining_titles:
                    break

                # 匹配标题
                is_matched = self.title_matcher.match(article_title, remaining_titles)

                if is_matched:
                    extracted_articles.append(article)
                    # 【修复】找到并移除匹配到的目标标题（雪球号：抓取标题被目标标题包含）
                    matched_target = None
                    for target in remaining_titles:
                        if self.title_matcher.match(article_title, [target]):
                            matched_target = target
                            break
                    if matched_target:
                        remaining_titles.remove(matched_target)
                        logger.info(f"✅ 匹配成功: {article_title} -> 目标: {matched_target}")
                    else:
                        logger.info(f"✅ 匹配成功: {article_title}")

                    # 【修复】若目标列表为空，立即终止遍历
                    if not remaining_titles:
                        logger.info("🎯 所有目标已匹配完成，提前终止遍历")
                        break

            if remaining_titles:
                has_next = await self._go_to_next_page(page)
                if not has_next:
                    logger.info("ℹ️ 已到达最后一页")
                    break
                # 【优化】删除固定延时，因为 _extract_page_articles 内部已有 wait_for_selector 等待表格加载
                # await page.wait_for_timeout(2000)

        logger.info(f"📊 提取完成，共遍历 {article_count} 篇文章，匹配成功: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles

    async def _extract_page_articles(self, page) -> List[Dict]:
        """提取当前页面的所有文章数据"""
        articles = []

        try:
            # 等待表格加载完成
            await page.wait_for_selector("table", timeout=10000)

            # 获取所有数据行(跳过表头)
            rows = await page.locator("tbody tr").all()
            logger.debug(f"🔍 检测到 {len(rows)} 行数据")

            for row in rows:
                try:
                    # 检查是否是空数据行
                    cells = await row.locator("td").all()
                    if len(cells) < 2:
                        logger.debug("⚠️ 行单元格数少于2，跳过")
                        continue

                    # 检查是否显示"暂无数据"
                    first_cell_text = await cells[0].inner_text()
                    if "暂无数据" in first_cell_text:
                        logger.debug("⚠️ 检测到'暂无数据'行，跳过")
                        continue

                    article = await self._extract_single_article(row)
                    if article:
                        articles.append(article)

                except Exception as e:
                    logger.debug(f"解析文章行失败: {e}")
                    continue

            # logger.info(f"📊 本页提取到 {len(articles)} 篇文章")

        except Exception as e:
            logger.warning(f"提取页面文章失败: {e}")

        return articles

    async def _extract_single_article(self, row) -> Optional[Dict]:
        """
        提取单篇文章数据

        数据结构（7列）：
        1. 作品名称
        2. 阅读量
        3. 讨论量
        4. 转发量
        5. 点赞量
        6. 收藏量
        7. 个人主页访问量（新增）
        """
        try:
            # 获取所有单元格
            cells = await row.locator("td").all()

            # 至少需要7列数据（标题 + 6个数据字段）
            if len(cells) < 7:
                logger.debug(f"⚠️ 行单元格数不足: {len(cells)}")
                return None

            # 提取标题和链接(第一个单元格)
            title_cell = cells[0]

            # 【修复】雪球数据后台使用span标签而不是a标签
            title_span = title_cell.locator("span")

            # 提取完整标题和链接
            title = ""
            url = ""

            # 优先从title属性获取完整标题（雪球在td上有title属性，通常包含完整的未截断标题）
            title = await title_cell.get_attribute("title") or ""

            # 如果title属性为空，尝试从span获取
            if not title:
                title = await title_span.text_content() or ""

            # 最后尝试从整个单元格获取文本
            if not title or len(title) < 10:
                title = await title_cell.text_content() or ""

            # 【修复】从span获取href属性（雪球使用span的href属性，而不是a标签）
            if await title_span.count() > 0:
                href = await title_span.get_attribute("href")
                if href:
                    if href.startswith("http"):
                        url = href
                    else:
                        url = "https://xueqiu.com" + href
                else:
                    # 尝试其他可能的方式
                    href = await title_span.evaluate("el => el.getAttribute('href')")
                    if href:
                        if href.startswith("http"):
                            url = href
                        else:
                            url = "https://xueqiu.com" + href

            # 如果仍然没有获取到URL，尝试查找a标签（备用方案）
            if not url:
                title_link = title_cell.locator("a")
                if await title_link.count() > 0:
                    href = await title_link.get_attribute("href")
                    if href:
                        if href.startswith("http"):
                            url = href
                        else:
                            url = "https://xueqiu.com" + href

            # 清理标题：移除多余空格和换行
            title = re.sub(r'\s+', ' ', title).strip()

            # 提取各项数据（根据验证结果，共7列）
            read_count = await self._extract_cell_number(cells[1])
            comment_count = await self._extract_cell_number(cells[2])
            share_count = await self._extract_cell_number(cells[3])
            like_count = await self._extract_cell_number(cells[4])
            collect_count = await self._extract_cell_number(cells[5])
            profile_view_count = await self._extract_cell_number(cells[6])  # 新增：个人主页访问量

            return {
                "title": title,
                "url": url,
                "read_count": read_count,
                "comment_count": comment_count,
                "share_count": share_count,
                "like_count": like_count,
                "collect_count": collect_count,
                "profile_view_count": profile_view_count,  # 新增字段
            }

        except Exception as e:
            logger.debug(f"解析文章数据失败: {e}")
            return None

    async def _extract_cell_number(self, cell) -> int:
        """提取单元格中的数字"""
        try:
            text = await cell.inner_text()
            # 提取数字，处理万、万一等单位
            text = text.strip()
            if not text:
                return 0

            # 处理"暂无数据"等
            if "暂无" in text:
                return 0

            # 提取数字
            numbers = re.findall(r'[\d,]+', text)
            if numbers:
                num_str = numbers[0].replace(',', '')
                return int(num_str)

            return 0
        except Exception:
            return 0

    async def _go_to_next_page(self, page) -> bool:
        """翻到下一页"""
        try:
            # 查找分页区域中的下一页按钮（排除快速跳转的下一页）
            # 使用更精确的定位：定位到包含"下一页"文本的按钮
            next_button = page.locator("button[aria-label='next page']").last

            # 检查是否禁用
            is_disabled = await next_button.get_attribute("disabled")
            if is_disabled is not None:
                logger.info("下一页按钮已禁用")
                return False

            # 检查是否可见
            if await next_button.is_visible(timeout=2000):
                await next_button.click()
                # 【修复】使用 domcontentloaded 而非 networkidle，适配雪球动态页面
                await page.wait_for_load_state("domcontentloaded")
                logger.info("✅ 已翻到下一页")
                return True

            return False

        except Exception as e:
            logger.warning(f"翻页失败: {e}")
            return False


# ============================================================
# 6. CSV导出模块
# ============================================================

class CSVExporter:
    """数据导出模块"""

    def __init__(self):
        self.output_dir = DATA_DIR
        self.output_dir.mkdir(exist_ok=True)

    def generate_filename(self, prefix: str = "articles") -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{timestamp}.csv"

    async def save_articles(self, articles: List[Dict], filename: Optional[str] = None) -> bool:
        """保存文章到CSV"""
        if not articles:
            logger.warning("⚠️ 没有数据需要导出")
            return False

        try:
            if not filename:
                filename = self.generate_filename()

            filepath = self.output_dir / filename
            logger.info(f"📄 开始导出数据到 {filepath}...")

            # 标准字段(根据COLLECTOR_STRATEGY.md L270-282和L416-428的雪球字段定义)
            fieldnames = [
                "publish_time",    # 发布日期
                "title",           # 文章标题（用户给定待采集目标文章）
                "platform",        # 发布平台
                "url",             # 发布链接/URL
                "exposure",        # 曝光量（雪球固定"/"）
                "read",            # 阅读量
                "recommend",       # 推荐（雪球固定"/"）
                "comment",         # 评论量
                "like",            # 点赞量
                "forward",         # 转发量
                "collect",         # 收藏量
                "profile_view"     # 个人主页访问量（新增字段）
            ]

            # 转换数据格式
            standardized_articles = []
            for article in articles:
                standardized_articles.append({
                    "publish_time": "",  # 雪球数据后台不显示发布时间
                    "title": article.get("title", ""),
                    "platform": "雪球",
                    "url": article.get("url", ""),
                    "exposure": "/",  # 雪球不提供曝光量
                    "read": article.get("read_count", 0),
                    "recommend": "/",  # 雪球不提供推荐量
                    "comment": article.get("comment_count", 0),
                    "like": article.get("like_count", 0),
                    "forward": article.get("share_count", 0),
                    "collect": article.get("collect_count", 0),
                    "profile_view": article.get("profile_view_count", 0)  # 新增字段
                })

            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(standardized_articles)

            logger.success(f"✅ 数据已成功导出到 {filepath}")
            return True

        except Exception as e:
            logger.error(f"❌ 数据导出失败: {e}")
            return False


# ============================================================
# 7. GUI调用接口
# ============================================================

async def crawl(
    targets: List[str],
    result_callback=None,
    unmatched_callback=None,
    headless: bool = False,  # 雪球需要扫码，默认False
    login_failed_callback=None
) -> Tuple[List[Dict], List[str]]:
    """
    GUI调用接口 - 供CrawlThread调用

    Args:
        targets: 待匹配的目标标题列表(最多5个)
        result_callback: 单条数据回调函数
        unmatched_callback: 未匹配目标回调函数
        headless: 是否为无头模式(雪球需要扫码，默认False)
        login_failed_callback: 登录失败回调函数，接收平台ID参数

    Returns:
        (success_data, unmatched): 成功数据列表和未匹配目标列表
    """
    targets = targets[:5]
    success_data = []
    remaining_targets = targets.copy()
    browser = None
    context = None
    p = None

    # 注意：此处不再强制切换 headless，保持传入参数原样

    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=headless)

        context_args = {"viewport": None}

        # 加载Cookie
        if COOKIE_FILE.exists():
            context_args["storage_state"] = str(COOKIE_FILE)

        if ENABLE_USER_AGENT_RANDOM and USER_AGENTS:
            context_args["user_agent"] = random.choice(USER_AGENTS)

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        # 应用stealth
        if ENABLE_STEALTH:
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

        # 初始化管理器
        anti_spider = AntiSpiderHelper(config)
        retry_manager = RetryManager(config)
        login_mgr = LoginManager(config, anti_spider, retry_manager)
        nav_mgr = NavigationManager(config)
        article_extractor = ArticleListExtractor(config)

        # 确保登录(传递headless参数)
        login_success = await login_mgr.ensure_login(page, context, headless=headless)
        if not login_success:
            logger.error(f"❌ {PLATFORM_NAME} 登录失败，终止采集")
            # 通知登录失败，让调度器加入手动登录队列
            if login_failed_callback:
                login_failed_callback("xueqiu")
            # 返回未匹配的目标列表
            for target in targets:
                if unmatched_callback:
                    unmatched_callback(target)
            return [], targets

        # 导航到文章列表
        if not await nav_mgr.navigate_to_article_list(page):
            logger.error(f"❌ {PLATFORM_NAME} 导航失败")
            return [], targets

        # 提取文章数据
        remaining, extracted = await article_extractor.extract_articles(page, targets)
        remaining_targets = remaining

        # 处理结果
        for data in extracted:
            data['platform'] = "雪球"
            data['crawl_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            success_data.append(data)
            if result_callback:
                result_callback(data)

        for target in remaining:
            if unmatched_callback:
                unmatched_callback(target)

        # 保存登录态
        await login_mgr.save_storage_state(context)

        return success_data, remaining

    except Exception as e:
        logger.error(f"❌ {PLATFORM_NAME} 采集异常: {e}")
        return success_data, remaining_targets

    finally:
        from utils.playwright_cleanup import shutdown_chromium_session

        await shutdown_chromium_session(
            playwright=p, context=context, browser=browser, log_label=PLATFORM_NAME
        )


# ============================================================
# 8. 独立运行入口
# ============================================================

async def run(p: Playwright, headless: bool = False) -> None:
    """
    独立运行入口

    Args:
        p: Playwright实例
        headless: 是否为无头模式(雪球需要扫码，默认False)
    """
    context = None
    browser = None

    # 雪球必须使用非无头模式进行扫码登录
    if headless:
        logger.warning("⚠️ 雪球号需要微信扫码登录，已强制切换为非无头模式")
        headless = False

    try:
        browser = await p.chromium.launch(headless=headless)
        context_args = {"viewport": None}

        if ENABLE_USER_AGENT_RANDOM and USER_AGENTS:
            context_args["user_agent"] = random.choice(USER_AGENTS)

        # 加载Cookie
        login_mgr = LoginManager(config, AntiSpiderHelper(config), RetryManager(config))
        storage_state = login_mgr.load_storage_state()
        if storage_state:
            context_args["storage_state"] = storage_state

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        if ENABLE_STEALTH:
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

        # 执行采集（使用调试目标列表）
        anti_spider = AntiSpiderHelper(config)
        retry_manager = RetryManager(config)
        login_mgr = LoginManager(config, anti_spider, retry_manager)
        nav_mgr = NavigationManager(config)
        article_extractor = ArticleListExtractor(config)

        # 【修复】传递headless参数
        if not await login_mgr.ensure_login(page, context, headless=headless):
            logger.error("❌ 登录失败")
            return

        if not await nav_mgr.navigate_to_article_list(page):
            logger.error("❌ 导航失败")
            return

        remaining, extracted = await article_extractor.extract_articles(page, DEBUG_TARGETS)

        if extracted:
            exporter = CSVExporter()
            await exporter.save_articles(extracted)

        logger.info("📌 浏览器将在3秒后自动关闭...")
        await page.wait_for_timeout(3000)
        await login_mgr.save_storage_state(context)

    except Exception as e:
        logger.error(f"❌ 程序执行异常: {e}", exc_info=e)
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()


async def main():
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    async with async_playwright() as p:
        await run(p)


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("xueqiu", None, "雪球", is_stable=True)


if __name__ == "__main__":
    asyncio.run(main())