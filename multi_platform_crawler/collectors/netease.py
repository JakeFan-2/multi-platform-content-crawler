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
网易号采集器
基于 template_collector.py 模板实现
===============================================================
"""

import os
import asyncio
import random
import json
import re
import sys
import csv
import yaml
from typing import Callable, Type, Tuple, Optional, Any, List, Dict
from utils.title_matcher import match_any_target
from utils.platform_registry import PlatformRegistry
from utils.snapshot import SnapshotManager
from utils.env_loader import get_platform_credentials_with_fallback
from utils.path_helper import PLATFORMS_DIR, COOKIES_DIR, LOGS_DIR, DATA_DIR
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
from loguru import logger


# ============================================================
# 1. 配置加载模块
# ============================================================

class ConfigLoader:
    """加载平台配置（YAML），提供点号路径访问方法"""

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
        required_sections = {
            'login': ['username_selectors', 'password_selectors', 'login_button_selectors'],
        }

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
    str(log_dir / "netease_crawler_{time:YYYY-MM-DD}.log"),
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

PLATFORM_NAME = "netease"
config = ConfigLoader(PLATFORM_NAME)

# 从 .env 优先，YAML 根级 username/password 降级
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
# USERNAME 和 PASSWORD 已从 .env 文件加载
# USERNAME = config.get("username", "")
# PASSWORD = config.get("password", "")
DATA_PAGE_URL = config.get("data_page_url", "")

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


# ============================================================
# 2. 通用工具模块
# ============================================================

class AntiSpiderHelper:
    """反爬工具：随机UA、人类输入模拟"""

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
        self.max_retries = config.get("max_retries", 2)

    def get_random_retry_delay(self) -> int:
        return random.randint(
            self.config.get("retry_delay_min", 3000),
            self.config.get("retry_delay_max", 5000)
        )

    def async_retry_on_failure(self, func):
        """异步重试装饰器"""
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(self.max_retries):
                try:
                    return await func(*args, **kwargs)
                except self.retryable_exceptions as e:
                    last_exception = e
                    if attempt < self.max_retries - 1:
                        delay = self.get_random_retry_delay()
                        logger.warning(f"⏳ 重试 {attempt + 1}/{self.max_retries}，等待 {delay}ms: {e}")
                        await asyncio.sleep(delay / 1000)
            logger.error(f"❌ 重试 {self.max_retries} 次后仍失败: {last_exception}")
            raise last_exception
        return wrapper


# ============================================================
# 3. 登录模块
# ============================================================

class LoginManager:
    """管理登录流程：Cookie加载/保存、登录态验证、自动登录（支持iframe）"""

    def __init__(self, config: ConfigLoader, anti_spider: AntiSpiderHelper, retry_manager: RetryManager):
        self.config = config
        self.anti_spider = anti_spider
        self.retry_manager = retry_manager
        self.platform = config.platform

        # 从环境变量获取账号密码（已从 .env 文件加载到全局变量 USERNAME/PASSWORD）
        self.username = USERNAME
        self.password = PASSWORD
        self.login_url = self.config.get("login_url", "")
        self.main_url = self.config.get("main_url", "")

    async def is_login_ui_disappeared(self, page) -> bool:
        """
        检测登录界面元素是否消失
        Returns:
            bool: True表示登录界面已消失（登录成功），False表示登录界面仍存在
        """
        try:
            # 检查登录iframe是否存在
            iframe_selectors = self.config.get("login.iframe_selectors", [])
            iframe_found = False
            for selector in iframe_selectors:
                if "css" in selector:
                    try:
                        iframe_locator = page.frame_locator(selector["css"])
                        # 尝试访问iframe内的元素
                        test_locator = iframe_locator.locator("body")
                        # 尝试等待元素消失，如果成功说明元素已存在但已隐藏
                        await test_locator.wait_for(state="hidden", timeout=2000)
                        # 如果能成功等待到hidden状态，说明元素曾经存在但现在隐藏了
                        iframe_found = True
                        logger.debug("✅ 登录iframe已消失")
                        return True
                    except Exception as e:
                        # 元素可能不存在，或者仍然是可见的
                        continue

            # 检查登录输入框是否存在
            username_selectors = self.config.get("login.username_selectors", [])
            password_selectors = self.config.get("login.password_selectors", [])

            # 检查用户名输入框
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        count = await locator.count(timeout=2000)
                        if count > 0:
                            logger.debug("❌ 用户名输入框仍存在，登录界面未消失")
                            return False
                    except:
                        continue

            # 检查密码输入框
            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        count = await locator.count(timeout=2000)
                        if count > 0:
                            logger.debug("❌ 密码输入框仍存在，登录界面未消失")
                            return False
                    except:
                        continue

            # 如果登录iframe或输入框都不存在，说明登录界面已消失
            logger.debug("✅ 登录界面元素已全部消失")
            return True

        except Exception as e:
            logger.debug(f"检测登录界面消失状态异常: {e}")
            return False

    async def is_logged_in(self, page) -> bool:
        """
        验证当前登录态是否有效（先跳转到内容数据页面，再检测登录界面元素是否消失）
        注意：虽然放弃URL校验作为判断依据，但仍需跳转到页面来触发登录状态检查
        Returns:
            bool: True表示已登录，False表示未登录
        """
        try:
            # 先跳转到内容数据页面，触发登录状态检查
            data_page_url = self.config.get("data_page_url", "")
            if data_page_url:
                try:
                    logger.info(f"📍 跳转到内容数据页面以验证登录态: {data_page_url}")
                    await page.goto(data_page_url, wait_until="domcontentloaded",
                                  timeout=self.config.get("default_timeout", 30000))
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"⚠️ 跳转失败: {e}")

            # 检测登录界面元素是否消失
            login_ui_disappeared = await self.is_login_ui_disappeared(page)

            if login_ui_disappeared:
                logger.info("✅ 登录态有效")
                return True
            else:
                logger.warning("❌ 登录态无效，登录界面仍存在")
                return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """执行自动登录流程（支持iframe登录，强制校验登录结果）"""
        try:
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # 步骤1：检测是否需要点击登录按钮
            # 如果页面已显示登录iframe，则跳过点击登录按钮步骤
            iframe_selectors = self.config.get("login.iframe_selectors", [])
            needs_click_login = True

            for selector in iframe_selectors:
                if "css" in selector:
                    try:
                        iframe_test = page.frame_locator(selector["css"])
                        test_element = iframe_test.locator("body")
                        await test_element.wait_for(state="visible", timeout=2000)
                        # iframe已存在，不需要点击登录按钮
                        needs_click_login = False
                        logger.info("ℹ️ 检测到登录iframe已显示，跳过点击登录按钮")
                        break
                    except:
                        continue

            # 如果需要，点击登录按钮
            if needs_click_login:
                login_button_selectors = self.config.get("login.login_button_selectors", [])
                for selector in login_button_selectors:
                    locator = self._get_locator(page, selector)
                    if locator:
                        try:
                            await locator.click()
                            await page.wait_for_timeout(1000)
                            break
                        except:
                            continue

            # 步骤2：切换到登录iframe
            iframe = None
            for selector in iframe_selectors:
                try:
                    if "css" in selector:
                        iframe = page.frame_locator(selector["css"])
                        if iframe:
                            logger.info("✅ 找到登录iframe")
                            break
                except:
                    continue

            if not iframe:
                logger.error("❌ 未找到登录iframe")
                return False

            # 步骤3：在iframe中进行登录操作
            logger.info("📍 开始在iframe中执行登录操作...")

            # 3.1 切换到邮箱登录
            try:
                email_tab_selectors = self.config.get("login.email_login_tab_selectors", [])
                for selector in email_tab_selectors:
                    locator = self._get_locator(iframe, selector)
                    if locator:
                        try:
                            await locator.click()
                            await page.wait_for_timeout(500)
                            logger.info("✅ 已切换到邮箱登录")
                            break
                        except:
                            continue
            except Exception as e:
                logger.warning(f"⚠️ 切换邮箱登录失败: {e}")

            # 3.2 填充用户名
            try:
                username_selectors = self.config.get("login.username_selectors", [])
                for selector in username_selectors:
                    locator = self._get_locator(iframe, selector)
                    if locator:
                        # 清空并填充
                        await locator.clear()
                        if self.config.get("enable_human_typing"):
                            await self.anti_spider.human_typing_selector(locator, self.username)
                        else:
                            await locator.fill(self.username)
                        logger.info("✅ 用户名填充完成")
                        break
            except Exception as e:
                logger.error(f"❌ 用户名填充失败: {e}")
                return False

            await page.wait_for_timeout(500)

            # 3.3 填充密码（精准定位真实密码框）
            try:
                password_selectors = self.config.get("login.password_selectors", [])
                password_filled = False
                for selector in password_selectors:
                    locator = self._get_locator(iframe, selector)
                    if locator:
                        # 清空并填充
                        await locator.clear()
                        if self.config.get("enable_human_typing"):
                            await self.anti_spider.human_typing_selector(locator, self.password)
                        else:
                            await locator.fill(self.password)
                        logger.info("✅ 密码填充完成")
                        password_filled = True
                        break

                if not password_filled:
                    logger.error("❌ 密码框定位失败，所有选择器均无效")
                    return False
            except Exception as e:
                logger.error(f"❌ 密码填充失败: {e}")
                return False

            await page.wait_for_timeout(500)

            # 3.4 点击"十天内免登录"复选框
            try:
                remember_me_selectors = self.config.get("login.remember_me_checkbox", [])
                for selector in remember_me_selectors:
                    locator = self._get_locator(iframe, selector)
                    if locator:
                        try:
                            # 检查是否已经选中
                            is_checked = await locator.is_checked()
                            if not is_checked:
                                await locator.click()
                                logger.info("✅ 已勾选'十天内免登录'")
                            else:
                                logger.debug("ℹ️ '十天内免登录'已勾选，跳过")
                            break
                        except:
                            continue
            except Exception as e:
                logger.warning(f"⚠️ 勾选'十天内免登录'失败: {e}")

            # 3.5 点击登录按钮
            try:
                submit_selectors = self.config.get("login.login_submit_button_selectors", [])
                button_clicked = False
                for selector in submit_selectors:
                    locator = self._get_locator(iframe, selector)
                    if locator:
                        try:
                            await locator.click()
                            logger.info("✅ 已点击登录按钮")
                            button_clicked = True
                            break
                        except Exception as e:
                            logger.debug(f"点击登录按钮失败（选择器: {selector}）: {e}")
                            continue

                if not button_clicked:
                    logger.error("❌ 登录按钮定位失败")
                    return False
            except Exception as e:
                logger.error(f"❌ 点击登录按钮失败: {e}")
                return False

            # 3.6 统一判断登录结果（10秒+30秒二次检测）
            logger.info("⏳ 登录操作完成，等待登录结果（10秒）...")
            await page.wait_for_timeout(10000)

            # 第一次检测：检查登录界面元素是否消失
            login_ui_disappeared = await self.is_login_ui_disappeared(page)

            if login_ui_disappeared:
                # 第一次检测成功，登录界面已消失
                logger.success("✅ 登录成功：已离开登录界面")

                # 严格等待20秒，让页面完全加载
                logger.info("⏳ 等待20秒以确保页面完全加载...")
                await page.wait_for_timeout(20000)

                # 执行目标页面跳转
                data_page_url = self.config.get("data_page_url", "")
                if data_page_url:
                    try:
                        logger.info(f"📍 跳转到内容数据页面: {data_page_url}")
                        await page.goto(data_page_url, wait_until="networkidle",
                                      timeout=self.config.get("default_timeout", 30000))
                        await page.wait_for_timeout(2000)
                        logger.info(f"✅ 跳转完成，当前URL: {page.url}")
                    except Exception as e:
                        logger.warning(f"⚠️ 跳转到内容数据页面失败: {e}")
                        logger.info("ℹ️ 但登录本身已成功，继续执行采集流程")
                else:
                    logger.warning("⚠️ 未配置data_page_url，跳过页面跳转")

                return True

            # 第一次检测失败，额外等待30秒后再次检测
            logger.info("⏳ 登录界面元素未消失，额外等待30秒后再次检测...")
            await page.wait_for_timeout(30000)

            # 第二次检测
            login_ui_disappeared = await self.is_login_ui_disappeared(page)

            if login_ui_disappeared:
                # 第二次检测成功
                logger.success("✅ 登录成功：已离开登录界面")

                # 严格等待20秒，让页面完全加载
                logger.info("⏳ 等待20秒以确保页面完全加载...")
                await page.wait_for_timeout(20000)

                # 执行目标页面跳转
                data_page_url = self.config.get("data_page_url", "")
                if data_page_url:
                    try:
                        logger.info(f"📍 跳转到内容数据页面: {data_page_url}")
                        await page.goto(data_page_url, wait_until="networkidle",
                                      timeout=self.config.get("default_timeout", 30000))
                        await page.wait_for_timeout(2000)
                        logger.info(f"✅ 跳转完成，当前URL: {page.url}")
                    except Exception as e:
                        logger.warning(f"⚠️ 跳转到内容数据页面失败: {e}")
                        logger.info("ℹ️ 但登录本身已成功，继续执行采集流程")
                else:
                    logger.warning("⚠️ 未配置data_page_url，跳过页面跳转")

                return True
            else:
                # 第二次检测仍失败，判定为登录失败
                logger.error("❌ 登录失败：两次检测登录界面元素仍未消失")
                logger.info(f"当前URL: {page.url}")
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程异常: {e}")
            return False

    def _get_locator(self, page, selector: Dict) -> Optional[Any]:
        """多策略获取元素定位器"""
        try:
            if "role" in selector:
                return page.get_by_role(selector["role"], name=selector.get("name", ""))
            elif "css" in selector:
                return page.locator(selector["css"])
            elif "id" in selector:
                return page.locator(f"#{selector['id']}")
            elif "xpath" in selector:
                return page.locator(selector["xpath"])
            elif "text" in selector:
                return page.get_by_text(selector["text"])
        except Exception:
            pass
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

    async def ensure_login(self, page, context, manual_login_callback=None) -> bool:
        """
        确保登录状态
        Args:
            page: Playwright页面对象
            context: Playwright上下文对象
            manual_login_callback: 需要人工登录时的回调函数（可选）
        Returns:
            bool: 是否登录成功
        """
        if await self.is_logged_in(page):
            return True

        logger.info("🔐 需要执行账号密码登录...")
        login_success = await self.perform_login(page, context)

        if login_success:
            await self.save_storage_state(context)
            return True
        else:
            # 自动登录失败，进入人工登录模式
            logger.warning("⚠️ 自动登录失败，进入人工登录模式")
            return await self.wait_for_manual_login(page, context, manual_login_callback)

    async def navigate_to_content_data_page(self, page) -> bool:
        """
        跳转到内容数据页面并验证是否成功
        Returns:
            bool: 是否成功跳转到内容数据页面
        """
        try:
            data_page_url = self.config.get("data_page_url", "")
            if not data_page_url:
                logger.error("❌ 未配置data_page_url")
                return False

            logger.info(f"📍 正在跳转到内容数据页面: {data_page_url}")
            await page.goto(data_page_url, wait_until="networkidle",
                          timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_timeout(2000)

            # 检查是否在内容数据页面
            return await self.is_on_content_data_page(page)

        except Exception as e:
            logger.error(f"❌ 跳转到内容数据页面失败: {e}")
            return False

    async def is_on_content_data_page(self, page) -> bool:
        """
        判断当前是否在内容数据页面
        Returns:
            bool: 是否在内容数据页面
        """
        try:
            # 检查URL
            current_url = page.url
            content_data_elements = self.config.get("login_check.content_data_page_elements", [])

            if not content_data_elements:
                logger.error("❌ 未配置content_data_page_elements")
                return False

            # 第一项是URL检查
            if isinstance(content_data_elements[0], dict):
                url_contains = content_data_elements[0].get("url_contains", "")
                if url_contains and url_contains not in current_url:
                    logger.debug(f"URL不包含'{url_contains}': {current_url}")
                    return False

            # 检查页面关键元素（从第二项开始）
            for element in content_data_elements[1:]:
                if isinstance(element, dict):
                    # 处理字典形式的选择器
                    for key, value in element.items():
                        try:
                            if key == "text":
                                page_element = page.get_by_text(value)
                            elif key == "css":
                                page_element = page.locator(value)
                            else:
                                continue

                            count = await page_element.count(timeout=3000)
                            if count > 0:
                                logger.debug(f"✅ 找到页面元素: {key}={value}")
                            else:
                                logger.debug(f"❌ 未找到页面元素: {key}={value}")
                                return False
                        except:
                            logger.debug(f"❌ 检查页面元素失败: {key}={value}")
                            return False
                elif isinstance(element, str):
                    # 简单文本检查（向后兼容）
                    try:
                        page_element = page.get_by_text(element)
                        count = await page_element.count(timeout=3000)
                        if count > 0:
                            logger.debug(f"✅ 找到页面元素: {element}")
                        else:
                            logger.debug(f"❌ 未找到页面元素: {element}")
                            return False
                    except:
                        logger.debug(f"❌ 检查页面元素失败: {element}")
                        return False

            return True

        except Exception as e:
            logger.debug(f"判断内容数据页面失败: {e}")
            return False

    async def wait_for_manual_login(self, page, context, manual_login_callback=None) -> bool:
        """
        等待人工登录（放弃URL校验，仅使用登录界面元素消失判断）
        Args:
            page: Playwright页面对象
            context: Playwright上下文对象
            manual_login_callback: 回调函数，通知需要人工登录
        Returns:
            bool: 是否登录成功
        """
        try:
            total_wait_time = self.config.get("manual_login.total_wait_time", 300)
            check_interval = self.config.get("manual_login.check_interval", 30)

            logger.info(f"⏳ 等待人工登录（总时长: {total_wait_time}秒，每{check_interval}秒检测一次）")
            logger.info(f"🔗 请手动访问: {self.login_url}")

            # 通知需要人工登录
            if manual_login_callback:
                manual_login_callback("netease")

            # 先跳转到内容数据页面，触发登录状态检查
            data_page_url = self.config.get("data_page_url", "")
            if data_page_url:
                try:
                    logger.info(f"📍 跳转到内容数据页面以触发登录状态检查: {data_page_url}")
                    await page.goto(data_page_url, wait_until="domcontentloaded",
                                  timeout=self.config.get("default_timeout", 30000))
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"⚠️ 初始跳转失败: {e}")

            # 等待用户手动登录
            elapsed = 0
            while elapsed < total_wait_time:
                logger.info(f"⏰ 等待中... ({elapsed}/{total_wait_time}秒)")

                # 检测登录状态：检查登录界面元素是否消失
                login_ui_disappeared = await self.is_login_ui_disappeared(page)

                if login_ui_disappeared:
                    logger.success("✅ 人工登录成功：已离开登录界面！")

                    # 保存登录态
                    await self.save_storage_state(context)

                    # 跳转到内容数据页面
                    if data_page_url:
                        try:
                            logger.info(f"📍 跳转到内容数据页面: {data_page_url}")
                            await page.goto(data_page_url, wait_until="networkidle",
                                          timeout=self.config.get("default_timeout", 30000))
                            await page.wait_for_timeout(2000)
                            logger.info(f"✅ 跳转完成，当前URL: {page.url}")
                        except Exception as e:
                            logger.warning(f"⚠️ 跳转到内容数据页面失败: {e}")
                    else:
                        logger.warning("⚠️ 未配置data_page_url，跳过页面跳转")

                    return True

                # 如果登录界面元素仍存在，等待下一次检测
                await asyncio.sleep(check_interval)
                elapsed += check_interval

            logger.error("❌ 人工登录等待超时：登录界面元素仍未消失")
            return False

        except Exception as e:
            logger.error(f"❌ 人工登录过程异常: {e}")
            return False


# ============================================================
# 4. 导航模块
# ============================================================

class NavigationManager:
    """管理页面导航：导航到单篇统计页面"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到文章列表页（内容管理 - 图文）
        基于配置文件选择器实现
        """
        try:
            logger.info("📍 开始导航到内容管理页面")

            # 从配置文件获取内容管理页面URL
            content_manage_url = self.config.get("navigation.content_manage_url",
                "https://mp.163.com/subscribe_v4/index.html#/content-manage")
            logger.info(f"目标URL: {content_manage_url}")

            # 跳转到内容管理页面
            logger.info("⏳ 跳转到内容管理页面...")
            await page.goto(content_manage_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # 验证页面加载
            current_url = page.url
            logger.info(f"当前页面URL: {current_url}")

            # 等待页面关键元素出现
            data_load_wait_time = self.config.get("navigation.data_load_wait_time", 2)
            logger.info(f"⏳ 等待页面加载完成（{data_load_wait_time}秒）...")
            await page.wait_for_timeout(data_load_wait_time * 1000)

            # 点击"图文"标签（使用配置文件中的选择器）
            logger.info("尝试点击'图文'标签")
            if not await self._click_element_with_fallback(page, "navigation.tuwen_tab_selectors"):
                logger.warning("⚠️ 点击'图文'标签失败（可能已在图文页面）")
            else:
                logger.info("✅ 已点击'图文'标签")
                await page.wait_for_timeout(2000)

            # 验证内容管理页面关键元素（使用配置文件中的选择器）
            logger.info("验证内容管理页面加载...")
            if await self._wait_element_visible(page, "navigation.content_validate_selectors"):
                logger.info("✅ 内容管理页面验证成功")
            else:
                logger.warning("⚠️ 内容管理页面验证超时")

            # 等待文章列表加载
            logger.info("⏳ 等待文章列表加载...")
            await page.wait_for_timeout(2000)

            logger.info("✅ 导航准备完成")
            return True

        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False

    async def _click_element_with_fallback(self, page, config_path: str) -> bool:
        """
        使用配置文件中的备选选择器列表尝试点击元素
        Args:
            page: 页面对象
            config_path: 配置路径，如 "navigation.tuwen_tab_selectors"
        Returns:
            bool: 是否成功点击
        """
        selectors = self.config.get(config_path, [])
        if not selectors:
            logger.debug(f"配置路径 {config_path} 为空")
            return False

        for selector in selectors:
            try:
                locator = self._get_locator_from_selector(page, selector)
                if locator:
                    count = await locator.count()
                    if count > 0:
                        await locator.first.click(timeout=3000)
                        logger.debug(f"✅ 点击成功: {selector}")
                        return True
            except Exception as e:
                logger.debug(f"选择器 {selector} 点击失败: {e}")
                continue

        return False

    async def _wait_element_visible(self, page, config_path: str, timeout: int = 8000) -> bool:
        """
        使用配置文件中的备选选择器列表等待元素可见
        Args:
            page: 页面对象
            config_path: 配置路径
            timeout: 超时时间（毫秒）
        Returns:
            bool: 元素是否可见
        """
        selectors = self.config.get(config_path, [])
        if not selectors:
            return False

        for selector in selectors:
            try:
                locator = self._get_locator_from_selector(page, selector)
                if locator:
                    count = await locator.count()
                    if count > 0:
                        await locator.first.wait_for(state="visible", timeout=timeout)
                        logger.debug(f"✅ 元素可见: {selector}")
                        return True
            except Exception as e:
                logger.debug(f"选择器 {selector} 等待失败: {e}")
                continue

        return False

    def _get_locator_from_selector(self, page, selector) -> Optional[Any]:
        """
        根据选择器配置获取页面定位器
        Args:
            page: 页面对象
            selector: 选择器配置（dict 或 string）
        Returns:
            定位器对象
        """
        try:
            if isinstance(selector, dict):
                if "role" in selector and "name" in selector:
                    return page.get_by_role(selector["role"], name=selector["name"])
                elif "css" in selector:
                    return page.locator(selector["css"])
                elif "text" in selector:
                    return page.get_by_text(selector["text"])
            elif isinstance(selector, str):
                return page.get_by_text(selector)
        except Exception as e:
            logger.debug(f"解析选择器失败: {selector}, 错误: {e}")
        return None

    def _get_locator(self, page, selector: Dict) -> Optional[Any]:
        """多策略获取元素定位器"""
        try:
            if "role" in selector:
                return page.get_by_role(selector["role"], name=selector.get("name", ""))
            elif "css" in selector:
                return page.locator(selector["css"])
            elif "id" in selector:
                return page.locator(f"#{selector['id']}")
            elif "xpath" in selector:
                return page.locator(selector["xpath"])
            elif "text" in selector:
                return page.get_by_text(selector["text"])
        except Exception:
            pass
        return None


# ============================================================
# 5. 数据提取模块
# ============================================================

class TitleMatcher:
    """
    标题匹配器（仅使用 re 库 + 原生字符串包含）
    """
    def match(self, current_title: str, target_titles: List[str]) -> bool:
        """
        判断当前文章标题是否匹配目标标题列表中的任意一个
        """
        return match_any_target(target_titles, current_title)


class ArticleListExtractor:
    """文章列表数据提取器"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()

    @staticmethod
    def _normalize_list_title(title: str) -> str:
        if not title:
            return ""
        s = title.replace("\r", " ").replace("\n", " ")
        return " ".join(s.split()).strip()

    def _get_locator_from_selector(self, page, selector) -> Optional[Any]:
        """
        根据选择器配置获取页面定位器
        Args:
            page: 页面对象
            selector: 选择器配置（dict 或 string）
        Returns:
            定位器对象
        """
        try:
            if isinstance(selector, dict):
                if "role" in selector and "name" in selector:
                    return page.get_by_role(selector["role"], name=selector["name"])
                elif "css" in selector:
                    return page.locator(selector["css"])
                elif "text" in selector:
                    return page.get_by_text(selector["text"])
            elif isinstance(selector, str):
                return page.get_by_text(selector)
        except Exception as e:
            logger.debug(f"解析选择器失败: {selector}, 错误: {e}")
        return None

    def _parse_field_selector(self, selector_str: str, parent_locator) -> Any:
        """
        解析字段选择器，支持:
        - nth(N): 获取第N个子元素（索引从0开始）
        - CSS选择器: 直接使用CSS
        Args:
            selector_str: 选择器字符串，如 "nth(0)" 或 ".title"
            parent_locator: 父定位器
        Returns:
            字段定位器
        """
        if not selector_str:
            return None

        # 解析 nth(N) 格式
        if selector_str.startswith("nth(") and selector_str.endswith(")"):
            try:
                index = int(selector_str[4:-1])
                return parent_locator.nth(index)
            except (ValueError, IndexError):
                return None

        # CSS选择器
        return parent_locator.locator(selector_str)

    async def _extract_field_with_fallback(self, parent_locator, selector_configs: List, field_name: str = "") -> str:
        """
        从父定位器中提取字段值，尝试多个备选选择器
        Args:
            parent_locator: 父定位器
            selector_configs: 字段选择器配置列表
            field_name: 字段名称（用于日志）
        Returns:
            字段文本值
        """
        if not selector_configs:
            return ""

        for selector in selector_configs:
            try:
                if isinstance(selector, str):
                    element_locator = self._parse_field_selector(selector, parent_locator)
                elif isinstance(selector, dict):
                    if "css" in selector:
                        element_locator = parent_locator.locator(selector["css"])
                    elif "nth" in selector:
                        element_locator = parent_locator.nth(selector["nth"])
                    else:
                        continue
                else:
                    continue

                if element_locator:
                    try:
                        count = await element_locator.count()
                        if count > 0:
                            text = await element_locator.first.inner_text(timeout=3000)
                            if text.strip():
                                logger.debug(f"✅ 成功提取{field_name}: {text.strip()[:30]}...")
                                return text.strip()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"备选选择器失败: {selector}, 错误: {e}")
                continue

        return ""

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略一：自上而下遍历匹配
        新版内容管理页面：URL已直接在文章数据中获取，无需额外弹窗操作
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []
        max_articles = 60
        article_count = 0

        # 从配置中获取最大翻页数
        max_pages = self.config.get("pagination.max_pages", 6)
        current_page = 0

        logger.info(f"📊 开始提取文章，目标: {len(remaining_titles)} 篇，限制翻页: {max_pages} 页")

        # 策略一：自上而下遍历匹配
        while article_count < max_articles and remaining_titles and current_page < max_pages:
            current_page += 1
            logger.info(f"📄 开始第 {current_page}/{max_pages} 页")

            # 提取当前页面的文章（URL已在提取时获取）
            articles = await self._extract_page_articles(page, include_url=True)

            if not articles:
                logger.warning("⚠️ 当前页面无文章数据")
                break

            for article in articles:
                article_count += 1

                if not remaining_titles:
                    break

                # 匹配标题
                atitle = article.get("title", "") or ""
                if self.title_matcher.match(atitle, remaining_titles):
                    # 匹配成功
                    logger.info(f"🔍 匹配成功标题: {atitle[:30]}...")

                    # URL已在提取时获取
                    url = article.get('url', '')
                    if url:
                        logger.info(f"✅ 文章URL: {url[:50] if url else 'N/A'}...")
                    else:
                        logger.warning("⚠️ 未获取到文章URL")

                    # 添加到提取列表
                    extracted_articles.append(article)
                    remaining_titles = [
                        t for t in remaining_titles
                        if not self.title_matcher.match(atitle, [t])
                    ]
                    logger.info(f"✅ 匹配成功并添加到提取列表: {atitle[:30]}...")

            # 如果还有待匹配目标，尝试翻页
            if remaining_titles:
                has_next = await self._go_to_next_page(page)
                if not has_next:
                    logger.info("ℹ️ 已到达最后一页")
                    break
                await page.wait_for_timeout(2000)

        logger.info(f"📊 提取完成，匹配成功: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}")

        # 输出未匹配的文章标题（平台名称 + 未匹配的标题）
        if remaining_titles:
            platform_name = self.config.get("platform", "网易号")
            logger.info(f"📋 【{platform_name}】未匹配的文章标题:")
            for idx, title in enumerate(remaining_titles, 1):
                logger.info(f"   {idx}. {title}")

        return remaining_titles, extracted_articles

    async def _extract_page_articles(self, page, include_url=True) -> List[Dict]:
        """
        提取当前页面的所有文章数据
        基于配置文件中的选择器实现
        Args:
            page: 页面对象
            include_url: 是否获取文章URL（默认True，新版页面直接从link元素获取）
        """
        articles = []

        try:
            # 等待页面数据加载完成
            data_load_wait = self.config.get("article_list.data_load_wait_time", 2)
            await page.wait_for_timeout(data_load_wait * 1000)

            # 使用标题链接定位文章（更可靠）
            title_selectors = self.config.get("article_list.title_selectors",
                ['a[href*="163.com/dy/article"]'])

            # 尝试使用备选选择器
            title_links = None
            for selector in title_selectors:
                locator = page.locator(selector)
                try:
                    count = await locator.count()
                    if count > 0:
                        title_links = locator
                        logger.debug(f"✅ 使用标题选择器定位文章: {selector}, 找到 {count} 篇")
                        break
                except:
                    continue

            # 备选：使用默认选择器
            if not title_links:
                title_links = page.locator('a[href*="163.com"]')

            count = await title_links.count()

            if count == 0:
                logger.warning("⚠️ 未找到文章标题链接")
                return articles

            logger.info(f"📄 发现 {count} 篇文章，开始提取数据")

            # 提取每篇文章的数据
            for i in range(count):
                try:
                    logger.debug(f"正在提取第 {i + 1}/{count} 篇文章")
                    link_elem = title_links.nth(i)

                    # 获取标题和URL
                    raw_title = await link_elem.inner_text(timeout=3000)
                    title = self._normalize_list_title(raw_title or "")
                    href = await link_elem.get_attribute('href')

                    # 规范化URL
                    url = ""
                    if href:
                        if href.startswith('//'):
                            url = 'https:' + href
                        elif href.startswith('/'):
                            url = 'https://mp.163.com' + href
                        else:
                            url = href

                    if not title:
                        logger.debug(f"⚠️ 第 {i + 1} 篇文章：无标题")
                        continue

                    # 获取文章行的完整元素（从链接向上查找）
                    row = link_elem.locator('xpath=..')

                    # 尝试获取创建时间和统计数据
                    publish_time = ""
                    exposure = ""
                    read = ""
                    like = ""
                    comment = ""

                    try:
                        # 向上查找包含"创建于"的元素
                        parent = row
                        for _ in range(5):  # 最多向上5层
                            parent = parent.locator('xpath=..')
                            created_count = await parent.locator('text=创建于').count()
                            if created_count > 0:
                                # 找到了包含"创建于"的容器
                                created_elem = parent.locator('text=创建于').first
                                created_text = await created_elem.inner_text()
                                created_time = created_text.replace("创建于", "").strip()
                                now = datetime.now()
                                if "-" in created_time:
                                    publish_time = f"{now.year}-{created_time.replace(' ', '-')}"
                                else:
                                    publish_time = f"{now.year}-{now.month:02d}-{now.day:02d} {created_time}"

                                # 获取统计数据（在同一父级元素内）
                                row_text = await parent.inner_text()
                                import re
                                # 曝光量改为静态配置，不再从页面提取
                                read_match = re.search(r'(\d+(?:\.\d+)?(?:万|亿)?)次\s*观看', row_text)
                                like_match = re.search(r'(\d+(?:\.\d+)?(?:万|亿)?)个\s*顶', row_text)
                                comment_match = re.search(r'(\d+(?:\.\d+)?(?:万|亿)?)跟\s*贴\s*互动', row_text)

                                if read_match:
                                    read = read_match.group(0)
                                if like_match:
                                    like = like_match.group(0)
                                if comment_match:
                                    comment = comment_match.group(0)
                                break
                    except Exception as e:
                        logger.debug(f"提取时间和统计数据失败: {e}")

                    article_data = {
                        'title': title,
                        'publish_time': publish_time.strip(),
                        'url': url.strip(),
                        'read': read.strip(),
                        'exposure': exposure.strip(),
                        'like': like.strip(),
                        'comment': comment.strip(),
                    }

                    if article_data['title']:
                        articles.append(article_data)
                        logger.debug(f"✅ 成功提取: {article_data['title'][:40]}...")
                    else:
                        logger.debug(f"⚠️ 第 {i + 1} 篇文章：无有效数据")

                except Exception as e:
                    logger.warning(f"提取第 {i + 1} 篇文章失败: {e}", exc_info=True)
                    continue

            logger.info(f"📊 本页成功提取 {len(articles)}/{count} 篇文章")

        except Exception as e:
            logger.warning(f"提取页面文章失败: {e}", exc_info=True)

        return articles

    async def _extract_single_article(self, row, page, include_url=False) -> Optional[Dict]:
        """
        提取单篇文章数据（旧版内容数据页面）
        MCP分析结果：文章行包含所有字段，以tab/换行分隔
        结构: 标题\n发布时间\t阅读\t展现\t跟帖\t分享\t进度\t数据详情
        """
        try:
            # 获取文章行的完整文本
            row_text = await row.inner_text(timeout=5000)

            if not row_text or len(row_text.strip()) == 0:
                logger.debug("文章行文本为空，跳过")
                return None

            # 使用tab和换行分割文本
            # 格式: 标题\n发布时间\t阅读\t展现\t跟帖\t分享\t进度\t数据详情
            import re
            parts = re.split(r'[\t\n]+', row_text)

            # 过滤空字符串并去除空白
            parts = [p.strip() for p in parts if p.strip()]

            if len(parts) < 2:
                logger.debug(f"文章行数据不足: {row_text[:50]}...")
                return None

            # 解析字段
            # parts[0] = 标题（可能包含标题+发布时间在第一个子元素中）
            # parts[1] = 发布时间（格式: 2026-03-31 17:14）
            # parts[2] = 阅读数
            # parts[3] = 展现数
            # parts[4] = 跟帖数
            # parts[5] = 分享数
            # parts[6] = 平均阅读进度 (可选)
            # parts[7] = "数据详情" 按钮文本

            title = parts[0] if len(parts) > 0 else ""
            publish_time_full = parts[1] if len(parts) > 1 else ""
            read = parts[2] if len(parts) > 2 else ""
            # 曝光量改为静态配置，不再从页面提取
            comment = parts[4] if len(parts) > 4 else ""
            forward = parts[5] if len(parts) > 5 else ""
            avg_progress = parts[6] if len(parts) > 6 else ""

            # 只保留发布时间的日期部分（年月日）
            publish_time = publish_time_full.split(" ")[0] if publish_time_full else ""

            logger.debug(f"提取数据: 标题={title[:30] if title else ''}..., 阅读={read}")

            # 只有在需要时才获取文章URL（避免不必要的弹窗操作）
            url = ""
            if include_url:
                url = await self._get_article_url_from_config(page, row)

            # 检查是否有有效数据
            if not title and not publish_time:
                logger.debug("该行未提取到任何有效数据，跳过")
                return None

            article_data = {
                'title': title.strip(),
                'publish_time': publish_time.strip(),
                'read': read.strip(),
                'exposure': exposure.strip(),
                'comment': comment.strip(),
                'forward': forward.strip(),
                'url': url,
            }

            logger.debug(f"✅ 成功提取文章数据: 标题={title[:30]}..., 阅读={read}, 展现={exposure}")

            return article_data

        except Exception as e:
            logger.debug(f"解析文章行失败: {e}")
            return None

    async def _extract_single_article_new(self, row, page, include_url=False) -> Optional[Dict]:
        """
        提取单篇文章数据（新版内容管理页面）
        基于配置文件中的选择器实现
        - 标题: link元素，带href属性（如 //www.163.com/dy/article/xxx.html）
        - 创建时间: "创建于12:12" 或 "创建于3-31 17:14"
        - 统计数据: "0次展现", "0次观看", "0个顶", "0跟贴互动"
        """
        try:
            # 提取标题和URL（使用配置文件中的选择器）
            title = ""
            url = ""
            try:
                title_selectors = self.config.get("article_list.title_selectors",
                    ['a[href*="163.com/dy/article"]'])

                # 尝试使用备选选择器
                title_link = None
                for selector in title_selectors:
                    try:
                        locator = row.locator(selector)
                        count = await locator.count()
                        if count > 0:
                            title_link = locator
                            break
                    except:
                        continue

                # 备选：使用默认选择器
                if not title_link:
                    title_link = row.locator('a[href*="163.com"]')

                link_count = await title_link.count()
                if link_count > 0:
                    raw_title = await title_link.first.inner_text(timeout=3000)
                    title = self._normalize_list_title(raw_title or "")
                    href = await title_link.first.get_attribute('href')
                    if href:
                        # 规范化URL
                        if href.startswith('//'):
                            url = 'https:' + href
                        elif href.startswith('/'):
                            url = 'https://mp.163.com' + href
                        else:
                            url = href
                else:
                    logger.debug("未找到标题链接")
            except Exception as e:
                logger.debug(f"提取标题/URL失败: {e}")

            if not title:
                logger.debug("未提取到标题，跳过")
                return None

            # 提取创建时间
            publish_time = ""
            try:
                created_time_text = self.config.get("article_list.created_time_text", "创建于")
                created_elem = row.locator(f'text={created_time_text}')
                created_count = await created_elem.count()
                if created_count > 0:
                    created_text = await created_elem.first.inner_text(timeout=3000)
                    # 解析时间格式："创建于12:12" 或 "创建于3-31 17:14"
                    created_time = created_text.replace(created_time_text, "").strip()
                    # 由于只有日期部分，需要结合当前年份
                    now = datetime.now()
                    if "-" in created_time:
                        # 格式：3-31 17:14，需要补全年份
                        publish_time = f"{now.year}-{created_time.replace(' ', '-')}"
                    else:
                        # 格式：12:12，今天的时间
                        publish_time = f"{now.year}-{now.month:02d}-{now.day:02d} {created_time}"
            except Exception as e:
                logger.debug(f"提取创建时间失败: {e}")

            # 提取统计数据
            read = ""
            like = ""
            comment = ""

            try:
                row_text = await row.inner_text()
                # 解析统计数据
                # 格式："0次展现", "0次观看", "0个顶", "0跟贴互动"
                import re
                read_match = re.search(r'(\d+(?:\.\d+)?(?:万|亿)?)次\s*观看', row_text)
                like_match = re.search(r'(\d+(?:\.\d+)?(?:万|亿)?)个\s*顶', row_text)
                comment_match = re.search(r'(\d+(?:\.\d+)?(?:万|亿)?)跟\s*贴\s*互动', row_text)

                if read_match:
                    read = read_match.group(0)
                if like_match:
                    like = like_match.group(0)
                if comment_match:
                    comment = comment_match.group(0)

            except Exception as e:
                logger.debug(f"提取统计数据失败: {e}")

            logger.debug(f"提取数据: 标题={title[:30] if title else ''}..., URL={url[:30] if url else ''}...")

            article_data = {
                'title': title,
                'publish_time': publish_time.strip(),
                'url': url.strip(),
                'read': read.strip(),
                'like': like.strip(),
                'comment': comment.strip(),
            }

            logger.debug(f"✅ 成功提取文章数据: 标题={title[:30]}...")

            return article_data

        except Exception as e:
            logger.debug(f"解析文章行失败: {e}")
            return None

    async def _extract_field(self, parent_locator, selector_config, field_name: str = "") -> str:
        """
        从父定位器中提取字段值（支持多备选选择器）
        Args:
            parent_locator: 父定位器
            selector_config: 字段选择器配置（字符串或列表）
            field_name: 字段名称（用于日志）
        Returns:
            字段文本值
        """
        try:
            # 统一处理为列表
            selectors = selector_config if isinstance(selector_config, list) else [selector_config]
            
            for selector in selectors:
                try:
                    if isinstance(selector, str):
                        # 字符串选择器（如 "nth(0)"）
                        element_locator = self._parse_field_selector(selector, parent_locator)
                    elif isinstance(selector, dict):
                        # 字典选择器（如 {"nth": 0}）
                        if "nth" in selector:
                            element_locator = parent_locator.nth(selector["nth"])
                        elif "css" in selector:
                            element_locator = parent_locator.locator(selector["css"])
                        elif "text" in selector:
                            # 直接获取父元素的文本
                            text = await parent_locator.inner_text(timeout=3000)
                            return text.strip()
                        else:
                            continue
                    else:
                        continue
                    
                    if element_locator:
                        text = await element_locator.inner_text(timeout=3000)
                        if text.strip():
                            return text.strip()
                except Exception as e:
                    logger.debug(f"备选选择器失败: {selector}, 错误: {e}")
                    continue
                    
        except Exception as e:
            logger.debug(f"提取{field_name}失败: {e}")
        return ""

    async def _get_article_url_from_config(self, page, row) -> str:
        """通过配置的数据详情按钮获取文章URL"""
        url = ""
        try:
            # 从配置中获取数据详情按钮选择器
            field_selectors = self.config.get("article_list.field_selectors", {})
            detail_selector = field_selectors.get("data_detail_button", "nth(7)")

            # 解析选择器
            children = row.locator("generic")
            detail_element = self._parse_field_selector(detail_selector, children)

            if detail_element:
                count = await detail_element.count()
                if count > 0:
                    # 监听新窗口/标签页
                    async with page.expect_event("popup") as popup_info:
                        await detail_element.click(timeout=5000)

                    new_page = await popup_info.value
                    await new_page.wait_for_load_state("networkidle", timeout=10000)

                    url = new_page.url
                    await new_page.close()
                    logger.debug(f"获取到文章URL: {url[:50]}...")
        except Exception as e:
            logger.debug(f"获取URL失败: {e}", exc_info=True)

        return url

    async def _get_article_url(self, page, row) -> str:
        """
        通过点击数据详情获取文章URL
        MCP分析结果：使用 text="数据详情" 定位按钮
        注意：点击后跳转到内容管理页面，URL中可能包含文章ID
        """
        url = ""
        try:
            # 使用MCP验证的方法：定位"数据详情"文本元素
            detail_element = row.locator('text=数据详情')
            count = await detail_element.count()

            if count > 0:
                # 监听新窗口/标签页
                async with page.expect_event("popup") as popup_info:
                    await detail_element.click(timeout=5000)

                new_page = await popup_info.value
                await new_page.wait_for_load_state("networkidle", timeout=10000)

                url = new_page.url
                await new_page.close()
                logger.debug(f"获取到文章URL: {url[:80] if url else 'N/A'}...")
        except Exception as e:
            logger.debug(f"获取URL失败: {e}", exc_info=True)

        return url

    async def _go_to_next_page(self, page) -> bool:
        """
        翻到下一页（内容管理页面使用页码按钮翻页）
        基于配置文件中的选择器实现
        """
        try:
            # 从配置获取当前页码和最大页码
            max_pages = self.config.get("pagination.max_pages", 6)
            # 跟踪当前页码
            if not hasattr(self, '_current_page'):
                self._current_page = 1

            current_page = self._current_page
            next_page = current_page + 1

            if next_page > max_pages:
                logger.info(f"📄 已到达第{max_pages}页，无法继续翻页")
                return False

            logger.info(f"🔄 尝试翻到第 {next_page}/{max_pages} 页")

            # 滚动到页面底部，确保按钮可见
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)

            # 方法1：使用页码按钮翻页
            try:
                page_button = page.get_by_role("button", name=str(next_page))
                count = await page_button.count()

                if count > 0:
                    await page_button.first.click()
                    logger.info(f"✅ 已点击第{next_page}页，等待加载...")
                    await page.wait_for_timeout(2000)
                    self._current_page = next_page
                    return True
            except Exception as e:
                logger.debug(f"点击页码按钮失败: {e}")

            # 方法2：使用"下一页"按钮（从配置获取选择器）
            next_button_selectors = self.config.get("pagination.next_button_selectors", [])
            for selector in next_button_selectors:
                try:
                    locator = self._get_locator_from_selector(page, selector)
                    if locator:
                        count = await locator.count()
                        if count > 0 and not await locator.first.is_disabled():
                            await locator.first.click()
                            logger.info("✅ 已点击'下一页'按钮")
                            await page.wait_for_timeout(2000)
                            self._current_page = next_page
                            return True
                except Exception as e2:
                    logger.debug(f"下一页按钮失败: {e2}")
                    continue

            logger.warning("⚠️ 翻页失败")
            return False

        except Exception as e:
            logger.warning(f"翻页失败: {e}", exc_info=True)
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

            # 标准字段（网易号）- 按照COLLECTOR_STRATEGY.md L270-282顺序
            fieldnames = [
                "publish_time",
                "title",
                "platform",
                "url",
                "exposure",
                "read",
                "recommend",
                "comment",
                "like",
                "forward",
                "collect"
            ]

            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(articles)

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
    headless: bool = True,
    login_failed_callback=None
) -> Tuple[List[Dict], List[str]]:
    """
    GUI调用接口 - 供CrawlThread调用
    Args:
        targets: 待匹配的目标标题列表（最多5个）
        result_callback: 单条数据回调函数
        unmatched_callback: 未匹配目标回调函数
        headless: 是否使用无头模式，默认True
        login_failed_callback: 登录失败回调函数，接收平台ID参数
    Returns:
        (success_data, unmatched): 成功数据列表和未匹配目标列表
    """
    targets = targets[:5]
    success_data = []
    remaining_targets = targets.copy()
    browser = None
    context = None
    page = None
    p = None

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

        # 确保登录
        login_success = await login_mgr.ensure_login(page, context)
        if not login_success:
            logger.error(f"❌ {PLATFORM_NAME} 登录失败")
            # 通知登录失败，让调度器加入手动登录队列
            if login_failed_callback:
                login_failed_callback("netease")
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
            # 添加固定值字段
            article = {
                'publish_time': data.get('publish_time', ''),
                'title': data.get('title', ''),
                'platform': '极客公园网易号',
                'url': data.get('url', ''),
                'exposure': data.get('exposure', ''),
                'read': data.get('read', ''),
                'recommend': '/',
                'comment': data.get('comment', ''),
                'like': '/',
                'forward': data.get('forward', ''),
                'collect': '/'
            }
            success_data.append(article)
            if result_callback:
                result_callback(article)

        for target in remaining:
            if unmatched_callback:
                # 未匹配文章注明：平台名称 + 文章标题
                unmatched_callback(f"极客公园网易号 {target}")

        # 保存登录态
        await login_mgr.save_storage_state(context)

        return success_data, remaining

    except Exception as e:
        logger.error(f"❌ {PLATFORM_NAME} 采集异常: {e}")
        # 尝试保存异常快照
        try:
            if page:
                screenshot = await page.screenshot()
                html = await page.content()
                snapshot_mgr = SnapshotManager()
                snapshot_mgr.save_snapshot(
                    platform=PLATFORM_NAME,
                    error=e,
                    page_screenshot=screenshot,
                    page_html=html,
                    context={"url": page.url, "targets": targets}
                )
                logger.info(f"✅ 已保存异常快照")
        except Exception as snap_error:
            logger.warning(f"保存快照失败: {snap_error}")

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
    """独立运行入口"""
    context = None
    browser = None

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

        # 独立运行模式下不传manual_login_callback，失败后会自动进入人工登录等待
        if not await login_mgr.ensure_login(page, context):
            logger.error("❌ 登录失败")
            return

        if not await nav_mgr.navigate_to_article_list(page):
            logger.error("❌ 导航失败")
            return

        remaining, extracted = await article_extractor.extract_articles(page, DEBUG_TARGETS)

        if extracted:
            # 添加标准字段
            articles_for_export = []

            for data in extracted:
                article = {
                    'publish_time': data.get('publish_time', ''),
                    'title': data.get('title', ''),
                    'platform': '极客公园网易号',
                    'url': data.get('url', ''),
                    'exposure': data.get('exposure', ''),
                    'read': data.get('read', ''),
                    'recommend': '/',
                    'comment': data.get('comment', ''),
                    'like': '/',
                    'forward': data.get('forward', ''),
                    'collect': '/'
                }
                articles_for_export.append(article)

            exporter = CSVExporter()
            await exporter.save_articles(articles_for_export)

        logger.info(f"📊 提取完成，匹配成功: {len(extracted)} 篇，剩余未匹配: {len(remaining)}")

        logger.info("📌 按回车键关闭浏览器...")
        await asyncio.get_event_loop().run_in_executor(None, input)
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


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("netease", None, "网易号", is_stable=True)
