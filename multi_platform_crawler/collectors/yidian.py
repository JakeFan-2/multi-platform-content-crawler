# ========== 独立调试支持（不影响主程序）==========
import sys
import os
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ========== 结束 ==========

# ========== 调试用目标文章列表（仅独立运行时生效）==========
DEBUG_TARGETS = [
    "在黑客松上，开发者们下注鸿蒙",
    "越来越多的人，已经把小红书玩成了 AI 孵化器",
]
# ========== 结束 ==========

"""
===============================================================
一点资讯平台采集器
基于通用模板构建
===============================================================

使用方法：
1. 配置文件：platforms/yidian.yaml
2. 运行采集器执行数据提取
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
        """
        初始化配置加载器
        Args:
            platform: 平台标识符，需与 platforms/*.yaml 文件名一致
        """
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
            'article_list': ['field_selectors'],
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

# 文件输出 - 使用平台名称作为文件名的一部分
log_file_path = str(log_dir / f"yidian_crawler_{{time:YYYY-MM-DD}}.log")
logger.add(
    log_file_path,
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

PLATFORM_NAME = "yidian"
config = ConfigLoader(PLATFORM_NAME)
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
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

# 数据字段选择器
FIELD_SELECTORS = config.get("article_list.field_selectors", {})

# 分页配置
PAGINATION_CONFIG = config.get("pagination", {})


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

    def get_random_retry_delay(self) -> int:
        return random.randint(
            self.config.get("retry_delay_min", 3000),
            self.config.get("retry_delay_max", 5000)
        )


# ============================================================
# 3. 登录模块
# ============================================================

class LoginManager:
    """管理登录流程：Cookie加载/保存、登录态验证、自动登录"""

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

    async def is_logged_in(self, page, check_current_page_only=False) -> bool:
        """
        验证当前登录态是否有效

        Args:
            page: Playwright page对象
            check_current_page_only: 是否只检查当前页面，不进行导航
        """
        try:
            # 如果不只检查当前页面，则先导航到主页面
            if not check_current_page_only:
                await page.goto(self.main_url, wait_until="networkidle",
                              timeout=self.config.get("default_timeout", 30000))
                await page.wait_for_timeout(2000)

            # 检查是否显示登录表单（未登录状态）
            login_form = page.locator("input[name='username'], input[placeholder*='邮箱'], input[placeholder*='手机'], input[placeholder*='密码']")
            if await login_form.count() > 0:
                logger.info("🔒 检测到未登录状态（显示登录表单）")
                return False

            # 检查是否有登录按钮（也是未登录状态）
            login_button = page.locator("button:has-text('登录'), button[type='submit'], .login-btn")
            if await login_button.count() > 0:
                logger.info("🔒 检测到未登录状态（显示登录按钮）")
                return False

            # 获取页面内容进行更全面的检查
            page_content = await page.content()
            # 检查是否包含登录相关元素
            if "邮箱" in page_content and "密码" in page_content and "登录" in page_content:
                if "input" in page_content and "登录" in page_content:
                    logger.info("🔒 检测到未登录状态（页面包含登录表单元素）")
                    return False

            # 检查登录后页面特征
            current_url = page.url
            if "/ArticleManual/original/publish" in current_url or "/Home" in current_url:
                # 检查是否有用户名显示
                user_name = page.locator("text=极客公园")
                if await user_name.count() > 0:
                    logger.info("✅ 登录态验证成功（找到用户名）")
                    return True

                # 检查是否有内容管理菜单
                content_menu = page.locator("a[href='#/ArticleManual/original/publish']")
                if await content_menu.count() > 0:
                    logger.info("✅ 登录态验证成功（找到内容菜单）")
                    return True

                # 检查是否有文章列表
                article_list = page.locator("generic:has(h4 a[href*='www.yidianzixun.com/article/'])")
                if await article_list.count() > 0:
                    logger.info("✅ 登录态验证成功（找到文章列表）")
                    return True

            logger.info(f"⚠️ 登录状态不确定，当前URL: {current_url}")
            return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        执行自动登录流程
        前提：ensure_login已经导航到登录页面，登录表单应该已经显示
        """
        try:
            logger.info("🔐 开始填写登录表单...")

            logger.info("🔐 开始登录流程...")

            # 获取登录选择器
            username_selectors = self.config.get("login.username_selectors", [])
            password_selectors = self.config.get("login.password_selectors", [])
            login_button_selectors = self.config.get("login.login_button_selectors", [])

            # 填充用户名
            username_filled = False
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    logger.info(f"📝 填写用户名...")
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.username)
                    else:
                        await locator.fill(self.username)
                    username_filled = True
                    break
            if not username_filled:
                logger.error("❌ 无法定位用户名输入框")
                return False

            await page.wait_for_timeout(500)

            # 填充密码
            password_filled = False
            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    logger.info(f"🔑 填写密码...")
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.password)
                    else:
                        await locator.fill(self.password)
                    password_filled = True
                    break
            if not password_filled:
                logger.error("❌ 无法定位密码输入框")
                return False

            # 点击登录按钮
            login_clicked = False
            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    logger.info(f"🖱️ 点击登录按钮...")
                    await locator.click()
                    login_clicked = True
                    break

            if not login_clicked:
                logger.error("❌ 无法定位登录按钮")
                return False

            # 等待登录跳转
            logger.info("⏳ 等待登录跳转...")
            await page.wait_for_timeout(3000)

            # 等待网络空闲
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass

            # 获取当前URL
            current_url = page.url
            logger.info(f"📍 登录后页面URL: {current_url}")

            # 检查登录表单是否消失（说明登录成功）
            login_form = page.locator("input[name='username'], input[name='password']")
            if await login_form.count() > 0:
                logger.warning("⚠️ 登录表单仍然存在，登录可能失败")
                await page.wait_for_timeout(2000)
                if await login_form.count() > 0:
                    logger.error("❌ 登录失败，登录表单未消失")
                    return False

            # 检查是否跳转到目标页面或显示内容
            page_content = await page.content()
            if "/ArticleManual/original/publish" in current_url or "内容管理" in page_content or "文章" in page_content:
                logger.info("✅ 登录成功")
                return True

            logger.info("✅ 登录成功（基于表单消失判断）")
            return True

        except Exception as e:
            logger.error(f"登录过程异常: {e}")
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

    async def ensure_login(self, page, context) -> bool:
        """确保登录状态"""
        if await self.is_logged_in(page):
            return True

        logger.info("🔐 需要执行账号密码登录...")

        # 直接访问文章列表页，登录后会自动跳转到文章列表
        article_list_url = "http://mp.yidianzixun.com/#/ArticleManual/original/publish"
        logger.info(f"📱 导航到登录页面: {article_list_url}")
        await page.goto(article_list_url, timeout=self.config.get("default_timeout", 30000))
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # 第一步：检查是否需要点击"登录"按钮进入登录表单
        login_button_text = page.locator("text=登录").first
        if await login_button_text.count() > 0:
            # 检查是否是登录按钮（不是登录表单中的登录按钮）
            login_form = page.locator("input[name='username'], input[placeholder*='邮箱'], input[placeholder*='手机']")
            if await login_form.count() == 0:
                # 没有登录表单，说明在着陆页，需要点击"登录"按钮
                logger.info("🖱️ 点击着陆页的登录按钮...")
                try:
                    await login_button_text.click()
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"点击登录按钮失败: {e}")

        # 第二步：检查是否显示了登录表单
        login_form = page.locator("input[name='username'], input[name='password'], input[placeholder*='邮箱'], input[placeholder*='手机'], input[placeholder*='密码']")
        if await login_form.count() == 0:
            # 没有登录表单，说明可能已经登录了
            logger.info("⚠️ 未找到登录表单，尝试验证登录状态...")
            return await self.is_logged_in(page)

        # 第三步：执行登录
        login_success = await self.perform_login(page, context)
        if login_success:
            await self.save_storage_state(context)
        return login_success


# ============================================================
# 4. 导航模块
# ============================================================

class NavigationManager:
    """
    管理页面导航
    """

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def wait_for_article_rows(self, page, timeout_ms: int = 28000) -> bool:
        """
        等待列表区出现可采集的文章链接（Vue SPA 在无头模式下仅靠 URL 不足以判定列表已渲染）。
        """
        link_sel = "a[href*='yidianzixun.com/article']"
        try:
            await page.wait_for_selector(link_sel, state="attached", timeout=timeout_ms)
            await page.wait_for_timeout(800)
            return True
        except Exception as e:
            logger.warning(f"⚠️ 等待文章列表链接超时或未出现: {e}")
            return False

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到文章列表页
        处理两种情况：
        1. 已登录：直接访问文章列表
        2. 未登录：显示登录表单，需要先登录
        """
        try:
            article_list_url = "http://mp.yidianzixun.com/#/ArticleManual/original/publish"
            logger.info(f"📍 导航到文章列表页: {article_list_url}")

            await page.goto(article_list_url, wait_until="networkidle",
                        timeout=self.config.get("default_timeout", 30000))

            # 等待页面加载
            await page.wait_for_timeout(5000)

            # 等待文章列表加载
            try:
                await page.wait_for_selector("h4", timeout=10000)
            except:
                logger.warning("⚠️ 等待文章列表超时")

            # 检查当前URL
            current_url = page.url
            logger.info(f"📍 当前URL: {current_url}")

            # 如果在首页（#/Home），需要点击"内容管理"
            if "/Home" in current_url:
                logger.info("📍 当前在首页，点击内容管理...")
                content_menu = page.locator("a[href='#/ArticleManual/original/publish']")
                if await content_menu.count() > 0:
                    await content_menu.click()
                    await page.wait_for_timeout(2000)
                else:
                    logger.warning("⚠️ 未找到内容管理链接")
                    return False

            # 再次检查是否到达文章列表页
            current_url = page.url
            if "/ArticleManual/original/publish" not in current_url and "/Home" in current_url:
                logger.warning("⚠️ 仍在首页，可能未成功跳转")

            # 以真实 DOM（文章链接）为准，避免无头模式下仅 URL 匹配但列表未挂载仍判成功
            if await self.wait_for_article_rows(page):
                logger.info("✅ 成功导航到文章列表页（列表链接已出现）")
                return True

            logger.error("❌ 未能等到文章列表链接（页面可能未加载完成或未登录）")
            return False

        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False


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
    """
    文章列表数据提取器
    """

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()
        self.field_selectors = config.get("article_list.field_selectors", {})
        self.pagination_config = config.get("pagination", {})

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据

        策略说明（符合COLLECTOR_STRATEGY.md）：
        1. 按自上而下顺序遍历文章列表
        2. 对每篇文章提取标题并匹配
        3. 匹配成功：提取数据，从待匹配列表移除，检查列表是否为空
        4. 匹配失败：继续下一篇文章
        5. 终止条件：待匹配列表为空 或 已遍历完限制范围内的所有文章（约60篇）
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []

        # 总则第4条：文章列表范围限制 - 保证提取约60篇文章
        max_articles_to_check = 60
        articles_checked = 0
        current_page = 1
        max_pages = 6

        logger.info(f"🎯 目标文章数: {len(target_titles)}, 最多检查: {max_articles_to_check} 篇")

        nav_mgr = NavigationManager(self.config)
        if not await nav_mgr.wait_for_article_rows(page):
            logger.error("❌ 文章列表未就绪，放弃提取")
            return remaining_titles, extracted_articles

        while articles_checked < max_articles_to_check and current_page <= max_pages:
            # 检查待匹配列表是否为空（策略终止条件1）
            if not remaining_titles:
                logger.info(f"✅ 所有目标文章已匹配完成")
                break

            logger.info(f"📄 正在采集第 {current_page} 页...")

            await page.wait_for_timeout(2000)
            await nav_mgr.wait_for_article_rows(page, timeout_ms=12000)

            # 获取所有文章项 - 使用 h4 元素
            articles = await page.locator("h4").all()

            # 过滤出包含文章链接的 h4
            valid_articles = []
            for article in articles:
                try:
                    link = article.locator("a")
                    href = await link.get_attribute("href")
                    if href and "yidianzixun.com/article" in href:
                        valid_articles.append(article)
                except:
                    continue

            if not articles:
                logger.warning(f"⚠️ 第 {current_page} 页未找到 h4，等待后重试一次...")
                await page.wait_for_timeout(3500)
                articles = await page.locator("h4").all()
            if not articles:
                logger.warning(f"⚠️ 第 {current_page} 页未找到文章")
                break

            logger.info(f"📊 第 {current_page} 页找到 {len(valid_articles)} 篇文章")

            if not valid_articles:
                logger.warning(f"⚠️ 第 {current_page} 页没有有效文章")
                all_links = await page.locator("a[href*='yidianzixun.com/article']").all()
                logger.info(f"📊 找到 {len(all_links)} 个文章链接")
                current_page += 1
                continue

            # 遍历文章 - 按自上而下顺序
            for article in valid_articles:
                if articles_checked >= max_articles_to_check:
                    logger.info(f"✅ 已检查 {max_articles_to_check} 篇文章，停止遍历")
                    break

                try:
                    title_elem = article
                    title_text = (await title_elem.inner_text()).strip()

                    logger.info(f"📝 检查文章 [{articles_checked + 1}]: {title_text}")

                    # 标题匹配
                    is_matched = self.title_matcher.match(title_text, remaining_titles)

                    if is_matched:
                        logger.info(f"✅ 匹配成功: {title_text}")

                        # 提取完整数据
                        article_data = await self._extract_single_article_by_h4(page, article)

                        if article_data:
                            extracted_articles.append(article_data)
                            logger.info(f"📊 成功提取: {article_data.get('title', 'N/A')}")

                            # 从剩余列表中移除已匹配的标题
                            remaining_titles = [t for t in remaining_titles
                                               if not self.title_matcher.match(t, [title_text])]

                            # 检查待匹配列表是否为空
                            if not remaining_titles:
                                logger.info(f"✅ 所有目标文章已匹配完成")
                                break
                        else:
                            logger.warning(f"⚠️ 提取失败: {title_text}")
                    else:
                        pass

                    articles_checked += 1

                except Exception as e:
                    logger.warning(f"⚠️ 提取文章数据失败: {e}")
                    continue

            # 如果还需要继续检查更多文章，尝试翻页
            if remaining_titles and articles_checked < max_articles_to_check:
                logger.info(f"📄 已检查 {articles_checked}/{max_articles_to_check} 篇，准备翻页...")
                next_success = await self._go_to_next_page(page, current_page)
                if not next_success:
                    logger.info("⚠️ 无法翻到下一页，停止采集")
                    break
                current_page += 1
            else:
                if not remaining_titles:
                    logger.info(f"✅ 所有目标文章已匹配完成，停止采集")
                else:
                    logger.info(f"✅ 已检查 {max_articles_to_check} 篇文章，停止采集")
                break

        logger.info(f"📊 采集完成，成功提取 {len(extracted_articles)} 篇文章，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles

    async def _extract_single_article_by_h4(self, page, h4_elem) -> Optional[Dict]:
        """根据 h4 元素提取单篇文章的完整数据"""
        try:
            data = {
                'platform': "极客公园一点资讯号"
            }

            # 提取标题
            link_elem = h4_elem.locator("a")
            if await link_elem.count() > 0:
                data['title'] = (await link_elem.inner_text()).strip()
                data['url'] = await link_elem.get_attribute('href')
            else:
                data['title'] = ""
                data['url'] = ""

            # 获取 h4 的父元素
            parent = await h4_elem.evaluate_handle("el => el.parentElement")

            # 提取发布时间
            time_text = await page.evaluate("""(parent) => {
                const siblings = parent.querySelectorAll('*');
                for (const el of siblings) {
                    if (el.tagName === 'P' || el.tagName === 'DIV') {
                        const text = el.textContent;
                        if (text.match(/\\d{4}-\\d{2}-\\d{2}/)) {
                            return text;
                        }
                    }
                }
                return '';
            }""", parent)

            if time_text:
                time_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', time_text)
                if time_match:
                    # 统一格式: YYYY/MM/DD（月份和日期补零）
                    year, month, day = time_match.groups()
                    data['publish_time'] = f"{year}/{int(month):02d}/{int(day):02d}"
                else:
                    data['publish_time'] = time_text.strip()
            else:
                data['publish_time'] = ""

            # 提取数据统计
            stats_text = await page.evaluate("""(parent) => {
                const siblings = parent.querySelectorAll('*');
                for (const el of siblings) {
                    const text = el.textContent;
                    if (text.includes('推荐') && text.includes('阅读')) {
                        return text;
                    }
                }
                return '';
            }""", parent)

            if stats_text:
                read_match = re.search(r'阅读\s*(\d+)', stats_text)
                comment_match = re.search(r'评论\s*(\d+)', stats_text)
                share_match = re.search(r'分享\s*(\d+)', stats_text)
                collect_match = re.search(r'收藏\s*(\d+)', stats_text)

                # 曝光量使用静态配置，不在页面提取
                data['exposure'] = '/'
                data['read'] = int(read_match.group(1)) if read_match else 0
                data['recommend'] = '/'
                data['comment'] = int(comment_match.group(1)) if comment_match else 0
                data['like'] = '/'
                data['forward'] = int(share_match.group(1)) if share_match else 0
                data['collect'] = int(collect_match.group(1)) if collect_match else 0
            else:
                data['exposure'] = '/'  # 使用静态配置
                data['read'] = 0
                data['recommend'] = '/'
                data['comment'] = 0
                data['like'] = '/'
                data['forward'] = 0
                data['collect'] = 0

            return data

        except Exception as e:
            logger.warning(f"⚠️ 提取单篇文章失败: {e}")
            return None

    async def _go_to_next_page(self, page, current_page: int) -> bool:
        """翻到下一页"""
        try:
            next_page = current_page + 1
            logger.info(f"📄 点击第 {next_page} 页...")

            # 使用JavaScript精确匹配页码并点击
            js_click = await page.evaluate(f"""
                () => {{
                    const items = document.querySelectorAll('.page-item, div.page-item');
                    for (const item of items) {{
                        if (item.textContent.trim() === '{next_page}') {{
                            item.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)

            if js_click:
                await page.wait_for_timeout(3000)
                logger.info(f"✅ 已跳转到第 {next_page} 页")
                return True

            logger.warning(f"⚠️ 未找到第 {next_page} 页按钮")
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
                filename = self.generate_filename(prefix="yidian")

            filepath = self.output_dir / filename
            logger.info(f"📄 开始导出数据到 {filepath}...")

            # 标准字段定义（符合项目标准）
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
                login_failed_callback("yidian")
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
    独立运行入口（直接运行采集器时调用）

    Args:
        headless: 是否使用无头模式，默认False（显示浏览器）
    """
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

        if not await login_mgr.ensure_login(page, context):
            logger.error("❌ 登录失败")
            return

        if not await nav_mgr.navigate_to_article_list(page):
            logger.error("❌ 导航失败")
            return

        remaining, extracted = await article_extractor.extract_articles(page, DEBUG_TARGETS)

        if extracted:
            exporter = CSVExporter()
            await exporter.save_articles(extracted)

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
        # 独立运行时显示浏览器（headless=False）
        # GUI调用时使用headless=True（在crawl函数中设置）
        await run(p, headless=False)


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("yidian", None, "一点资讯", is_stable=True)

