"""
===============================================================
平台采集器通用模板
基于 ZAKER 采集器抽象而成
===============================================================

使用方法：
1. 复制本模板为新平台的采集器文件 (如 toutiao.py)
2. 修改配置加载器中的 platform 名称
3. 根据目标平台修改 YAML 配置文件
4. 实现各模块中的 TODO 标记部分
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
from utils.env_loader import get_env, get_platform_credentials_with_fallback
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

    # 平台标识到环境变量前缀的映射
    PLATFORM_ENV_MAP = {
        "baijiahao": "BAIJIAHAO",
        "geekpark_web": "GEEKPARK_WEB",
        "geekpark_wechat": "GEEKPARK_WECHAT",
        "netease": "NETEASE",
        "qq": "QQ",
        "sohu": "SOHU",
        "toutiao": "TOUTIAO",
        "weibo": "WEIBO",
        "zhihu": "ZHIHU",
        "yidian": "YIDIAN",
        "zaker": "ZAKER",
        "xueqiu": "XUEQIU",
    }

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

    def get_credentials(self) -> tuple[str, str]:
        """
        从 .env 文件获取当前平台的账号密码
        
        Returns:
            (username, password) 元组
        """
        return get_platform_credentials_with_fallback(self.platform, self.config)

    def _validate_config(self):
        """配置项完整性检查"""
        logger.info("🔍 开始验证配置文件...")
        # TODO: 根据平台需要定义必需的配置段
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

# TODO: 修改为实际平台名称
PLATFORM_NAME = "{{platform_name}}"  # 如 "zaker", "toutiao"
config = ConfigLoader(PLATFORM_NAME)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")

USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

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

    async def is_logged_in(self, page) -> bool:
        """
        验证当前登录态是否有效
        TODO: 根据平台实际情况修改验证逻辑
        """
        try:
            await page.goto(self.main_url, wait_until="networkidle",
                          timeout=self.config.get("default_timeout", 30000))

            # TODO: 修改为平台实际的登录后特征
            # 示例：检查URL是否包含登录页
            if "/login/" in page.url:
                return False

            # 示例：检查登录后元素是否存在
            # content_management = page.get_by_role("link", name="内容管理")
            # return await content_management.is_visible(timeout=5000)

            return True

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        执行自动登录流程
        TODO: 根据平台登录页面修改选择器
        """
        try:
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # ===== TODO: 修改为平台实际的登录选择器 =====
            username_selectors = self.config.get("login.username_selectors", [])
            password_selectors = self.config.get("login.password_selectors", [])
            login_button_selectors = self.config.get("login.login_button_selectors", [])

            # 填充用户名
            username_filled = False
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.username)
                    else:
                        await locator.fill(self.username)
                    username_filled = True
                    break
            if not username_filled:
                return False

            await page.wait_for_timeout(500)

            # 填充密码
            password_filled = False
            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.password)
                    else:
                        await locator.fill(self.password)
                    password_filled = True
                    break
            if not password_filled:
                return False

            # 点击登录按钮
            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    await locator.click()
                    break

            await page.wait_for_load_state("networkidle")

            # 验证登录
            return await self.is_logged_in(page)

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
    TODO: 根据平台实际导航结构修改
    """

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到文章列表页
        TODO: 实现平台特定的导航逻辑
        """
        try:
            # 示例：直接访问文章列表页
            # article_list_url = self.config.get("article_list_url", "")
            # await page.goto(article_list_url)

            logger.info("✅ 导航完成")
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
    """
    def match(self, current_title: str, target_titles: List[str]) -> bool:
        """
        判断当前文章标题是否匹配目标标题列表中的任意一个
        """
        return match_any_target(target_titles, current_title)


class ArticleListExtractor:
    """
    文章列表数据提取器
    TODO: 根据平台实际页面结构修改
    """

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略一：自上而下遍历匹配
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []
        max_articles = 60  # 最多提取60篇文章
        article_count = 0

        logger.info(f"📊 开始提取文章，目标: {len(remaining_titles)} 篇")

        # 策略一：自上而下遍历匹配
        while article_count < max_articles and remaining_titles:
            # 提取当前页面的文章
            articles = await self._extract_page_articles(page)

            if not articles:
                logger.warning("⚠️ 当前页面无文章数据")
                break

            for article in articles:
                # 先增加计数（无论是否匹配）
                article_count += 1

                if not remaining_titles:
                    break

                # 匹配标题
                if self.title_matcher.match(article.get('title', ''), remaining_titles):
                    # 匹配成功，添加到结果
                    extracted_articles.append(article)
                    # 从待匹配列表中移除
                    current_title = article.get('title', '')
                    remaining_titles = [t for t in remaining_titles
                                       if not self.title_matcher.match(t, [current_title])]
                    logger.info(f"✅ 匹配成功: {article.get('title', '')[:30]}...")

            # 如果还有待匹配目标，尝试翻页
            if remaining_titles:
                has_next = await self._go_to_next_page(page)
                if not has_next:
                    logger.info("ℹ️ 已到达最后一页")
                    break
                await page.wait_for_timeout(2000)

        logger.info(f"📊 提取完成，匹配成功: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles

    async def _extract_page_articles(self, page) -> List[Dict]:
        """
        提取当前页面的所有文章数据
        TODO: 根据平台实际页面结构修改选择器
        """
        articles = []

        try:
            # 示例：获取表格行
            # rows = await page.locator("selector_for_rows").all()
            # for row in rows:
            #     article = await self._extract_single_article(row)
            #     if article:
            #         articles.append(article)
            pass

        except Exception as e:
            logger.warning(f"提取页面文章失败: {e}")

        return articles

    async def _extract_single_article(self, row) -> Optional[Dict]:
        """
        提取单篇文章数据
        TODO: 根据平台实际页面结构修改
        """
        try:
            # 示例：
            # title = await row.locator(".title").inner_text()
            # url = await row.locator(".title a").get_attribute("href")
            # read_count = await row.locator(".read-count").inner_text()
            # ...
            # return {...}
            return {}

        except Exception as e:
            logger.debug(f"解析文章行失败: {e}")
            return None

    async def _go_to_next_page(self, page) -> bool:
        """
        翻到下一页
        TODO: 根据平台实际分页结构修改
        """
        try:
            # 示例：
            # next_button = page.locator(".pagination .next")
            # if await next_button.is_disabled():
            #     return False
            # await next_button.click()
            # return True
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

            # 标准字段（必须包含所有字段）
            fieldnames = [
                "platform",
                "title",
                "url",
                "publish_time",
                "read_count",
                "exposure_count",
                "like_count",
                "comment_count",
                "share_count",
                "collect_count",
                "crawl_time"
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
    unmatched_callback=None
) -> Tuple[List[Dict], List[str]]:
    """
    GUI调用接口 - 供CrawlThread调用

    Args:
        targets: 待匹配的目标标题列表（最多5个）
        result_callback: 单条数据回调函数
        unmatched_callback: 未匹配目标回调函数

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
        # GUI调用时使用headless=True（不显示浏览器）
        browser = await p.chromium.launch(headless=True)

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
            data['platform'] = PLATFORM_NAME
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

        # TODO: 设置目标标题列表
        target_articles = ["示例标题1", "示例标题2"]

        # 执行采集
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

        remaining, extracted = await article_extractor.extract_articles(page, target_articles)

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
        await run(p)


if __name__ == "__main__":
    asyncio.run(main())
