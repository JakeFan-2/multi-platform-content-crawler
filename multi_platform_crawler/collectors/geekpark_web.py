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
极客公园官网采集器
基于模板采集器适配
===============================================================

平台信息：
- 平台名称：geekpark_web（极客公园官网）
- 数据后台：https://admin.geekpark.net/posts
- 登录页面：https://account.geekpark.net/

采集字段：
- 发布日期（publish_time）
- 标题（title）- 用户给定
- 发布链接（url）
- PV阅读量（read_count）

文章URL格式：https://www.geekpark.net/news/{文章ID}
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

logger.add(
    sys.stdout,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
    level="INFO",
    colorize=False
)

logger.add(
    str(log_dir / "geekpark_web_crawler_{time:YYYY-MM-DD}.log"),
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

PLATFORM_NAME = "geekpark_web"
config = ConfigLoader(PLATFORM_NAME)
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
# USERNAME = config.get("username", "")
# PASSWORD = config.get("password", "")

COOKIE_DIR = COOKIES_DIR
COOKIE_FILE = COOKIE_DIR / f"{PLATFORM_NAME}.json"

DEFAULT_TIMEOUT = config.get("default_timeout", 30000)
ELEMENT_TIMEOUT = config.get("element_timeout", 10000)
ELEMENT_SHORT_TIMEOUT = config.get("element_short_timeout", 5000)

MAX_RETRIES = config.get("max_retries", 3)
RETRY_DELAY_MIN = config.get("retry_delay_min", 2000)
RETRY_DELAY_MAX = config.get("retry_delay_max", 4000)

USER_AGENTS: List[str] = config.get("user_agents", [])
TYPING_DELAY_MIN = config.get("typing_delay_min", 50)
TYPING_DELAY_MAX = config.get("typing_delay_max", 150)

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
            self.config.get("retry_delay_min", 2000),
            self.config.get("retry_delay_max", 4000)
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
        """验证当前登录态是否有效"""
        try:
            await page.goto(self.main_url, wait_until="networkidle",
                          timeout=self.config.get("default_timeout", 30000))

            # 检查是否跳转到登录页面
            current_url = page.url
            if "account.geekpark.net" in current_url and "login" in current_url:
                logger.warning("⚠️ 未登录，需要执行登录流程")
                return False

            # 检查登录后元素是否存在（文章列表页面特征）
            try:
                # 方式1：检查"文章列表"标题
                heading = page.get_by_role("heading", name="文章列表")
                if await heading.is_visible(timeout=3000):
                    logger.success("✅ 登录态验证成功（检测到文章列表标题）")
                    return True
            except:
                pass

            try:
                # 方式2：检查"已发布"按钮
                published_btn = page.get_by_role("button", name="已发布")
                if await published_btn.is_visible(timeout=3000):
                    logger.success("✅ 登录态验证成功（检测到已发布按钮）")
                    return True
            except:
                pass

            # 元素检测失败，尝试刷新页面后再检测（解决403问题）
            logger.warning("⚠️ 未检测到登录后元素，尝试刷新页面...")
            await page.reload(wait_until="networkidle")
            await page.wait_for_timeout(1500)

            try:
                heading = page.get_by_role("heading", name="文章列表")
                if await heading.is_visible(timeout=3000):
                    logger.success("✅ 刷新后登录态验证成功")
                    return True
            except:
                pass

            try:
                published_btn = page.get_by_role("button", name="已发布")
                if await published_btn.is_visible(timeout=3000):
                    logger.success("✅ 刷新后登录态验证成功")
                    return True
            except:
                logger.warning("⚠️ 刷新后仍未检测到登录后元素")
                return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """执行自动登录流程"""
        try:
            logger.info(f"🔐 开始登录流程: {self.login_url}")
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # 获取登录选择器
            username_selectors = self.config.get("login.username_selectors", [])
            password_selectors = self.config.get("login.password_selectors", [])
            login_button_selectors = self.config.get("login.login_button_selectors", [])

            # 填充用户名
            username_filled = False
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.wait_for(timeout=5000)
                        if self.config.get("enable_human_typing"):
                            await self.anti_spider.human_typing_selector(locator, self.username)
                        else:
                            await locator.fill(self.username)
                        username_filled = True
                        logger.info("✅ 用户名填充成功")
                        break
                    except Exception as e:
                        logger.debug(f"选择器尝试失败: {selector}, 错误: {e}")
                        continue

            if not username_filled:
                logger.error("❌ 用户名输入框定位失败")
                return False

            await page.wait_for_timeout(500)

            # 填充密码
            password_filled = False
            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.wait_for(timeout=5000)
                        if self.config.get("enable_human_typing"):
                            await self.anti_spider.human_typing_selector(locator, self.password)
                        else:
                            await locator.fill(self.password)
                        password_filled = True
                        logger.info("✅ 密码填充成功")
                        break
                    except Exception as e:
                        logger.debug(f"选择器尝试失败: {selector}, 错误: {e}")
                        continue

            if not password_filled:
                logger.error("❌ 密码输入框定位失败")
                return False

            await page.wait_for_timeout(500)

            # 点击登录按钮
            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.wait_for(timeout=5000)
                        await locator.click()
                        logger.info("✅ 登录按钮点击成功")
                        break
                    except Exception as e:
                        logger.debug(f"选择器尝试失败: {selector}, 错误: {e}")
                        continue

            # 等待登录完成
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            # 导航到文章管理后台并刷新（解决403问题）
            logger.info("📄 导航到文章管理后台...")
            await page.goto(self.main_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)
            logger.info("🔄 刷新页面解决403问题...")
            await page.reload(wait_until="networkidle")
            await page.wait_for_timeout(1500)

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
    """管理页面导航"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """导航到文章列表页"""
        try:
            article_list_url = self.config.get("main_url", "")
            logger.info(f"📄 导航到文章列表: {article_list_url}")
            await page.goto(article_list_url, wait_until="networkidle", timeout=30000)

            # 点击"已发布"按钮确保显示已发布文章
            try:
                published_btn = page.get_by_role("button", name="已发布")
                await published_btn.wait_for(timeout=5000)
                await published_btn.click()
                await page.wait_for_timeout(2000)
                logger.info("✅ 已切换到已发布列表")
            except Exception as e:
                logger.debug(f"切换已发布列表失败: {e}")

            logger.success("✅ 导航完成")
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
    """文章列表数据提取器 - 极客公园官网版"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()
        self.article_url_pattern = config.get("article_url_pattern", "https://www.geekpark.net/news/{article_id}")

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略一：自上而下遍历匹配
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []
        max_articles = 100  # 最多提取100篇文章（10页 x 10条）
        article_count = 0
        max_pages = self.config.get("pagination.max_pages", 10)
        page_wait_time = self.config.get("pagination.page_wait_time", 2000)
        table_wait_time = self.config.get("pagination.table_wait_time", 1500)

        logger.info(f"📊 开始提取文章，目标: {len(remaining_titles)} 篇，最大遍历: {max_pages} 页")

        current_page = 1
        while article_count < max_articles and remaining_titles and current_page <= max_pages:
            # 等待表格加载完成
            await page.wait_for_timeout(table_wait_time)

            # 提取当前页面的文章
            articles = await self._extract_page_articles(page)

            if not articles:
                logger.warning("⚠️ 当前页面无文章数据")
                break

            for article in articles:
                article_count += 1

                if not remaining_titles:
                    break

                current_title = article.get('title', '')

                # 匹配标题
                if self.title_matcher.match(current_title, remaining_titles):
                    extracted_articles.append(article)
                    # 从 remaining_titles 中移除已匹配的标题
                    remaining_titles = [t for t in remaining_titles
                                        if not self.title_matcher.match(t, [current_title])]
                    logger.info(f"✅ 匹配成功: {current_title[:50]}...")

                    # 策略文档 L230：匹配成功后检查待匹配列表是否为空，若为空直接终止
                    if not remaining_titles:
                        logger.info("🎯 所有目标文章已匹配完成，终止采集")
                        return remaining_titles, extracted_articles
                else:
                    # 匹配失败时显示前几篇文章的标题用于调试（前5篇）
                    if article_count <= 5:
                        logger.info(f"🔍 第{article_count}篇（未匹配）: {current_title[:60]}...")

            # 如果还有待匹配目标，尝试翻页
            if remaining_titles and article_count < max_articles and current_page < max_pages:
                has_next = await self._go_to_next_page(page)
                if not has_next:
                    logger.info("ℹ️ 已到达最后一页")
                    break
                current_page += 1
                await page.wait_for_timeout(page_wait_time)

        logger.info(f"📊 提取完成，匹配成功: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles

    async def _extract_page_articles(self, page) -> List[Dict]:
        """提取当前页面的所有文章数据"""
        articles = []

        try:
            # 等待表格容器加载完成
            try:
                table_container = page.locator(".el-table__body-wrapper")
                await table_container.wait_for(timeout=10000)
            except Exception:
                pass

            # 等待一小段时间确保DOM完全渲染
            await page.wait_for_timeout(1000)

            # 使用Element UI标准表格行选择器
            rows = await page.locator("tr.el-table__row").all()

            if not rows:
                # 备用选择器
                rows = await page.locator("table.el-table tbody tr").all()

            logger.info(f"📄 当前页找到 {len(rows)} 行数据")

            if not rows:
                logger.warning("⚠️ 当前页面无文章数据")
                return articles

            # 提取文章数据
            for row in rows:
                article = await self._extract_single_article(row)
                if article and article.get('title'):
                    articles.append(article)

            logger.info(f"📄 本页成功提取 {len(articles)} 篇文章")

        except Exception as e:
            logger.warning(f"⚠️ 提取页面文章失败: {e}")

        return articles

    async def _extract_single_article(self, row) -> Optional[Dict]:
        """提取单篇文章数据"""
        try:
            # 提取文章ID
            article_id = ""
            try:
                id_cell = row.locator(".el-table_1_column_1 .cell")
                article_id = await id_cell.inner_text()
                article_id = article_id.strip()
            except:
                pass

            # 提取标题
            title = ""
            try:
                title_cell = row.locator(".el-table_1_column_2 .cell")
                title = await title_cell.inner_text()
                title = title.strip()
            except:
                pass

            # 提取发布时间
            publish_time = ""
            try:
                time_cell = row.locator(".el-table_1_column_5 .cell")
                raw_time = await time_cell.inner_text()
                raw_time = raw_time.strip()
                # 统一格式: YYYY/MM/DD（删除时间部分，月份和日期补零）
                time_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', raw_time)
                if time_match:
                    year, month, day = time_match.groups()
                    publish_time = f"{year}/{int(month):02d}/{int(day):02d}"
                else:
                    publish_time = raw_time
            except:
                pass

            # 提取PV阅读量
            read_count = "/"
            try:
                pv_cell = row.locator(".el-table_1_column_7 .cell")
                pv_text = await pv_cell.inner_text()
                pv_text = pv_text.strip()
                if pv_text and pv_text != "":
                    read_count = pv_text
            except:
                pass

            # 拼接文章URL
            url = ""
            if article_id:
                url = self.article_url_pattern.replace("{article_id}", article_id)

            return {
                "title": title,
                "url": url,
                "publish_time": publish_time,
                "read": read_count,              # PV阅读量
                "exposure": "/",                  # 曝光量（该平台不需要）
                "recommend": "/",                 # 推荐（固定"/"）
                "comment": "/",                   # 评论量（该平台不需要）
                "like": "/",                      # 点赞量（该平台不需要）
                "forward": "/",                   # 转发量（该平台不需要）
                "collect": "/"                   # 收藏量（该平台不需要）
            }

        except Exception as e:
            logger.debug(f"解析文章行失败: {e}")
            return None

    async def _go_to_next_page(self, page) -> bool:
        """翻到下一页"""
        try:
            next_button = page.locator(".el-pagination .btn-next")

            # 检查是否禁用
            is_disabled = await next_button.is_disabled()
            if is_disabled:
                return False

            await next_button.click()
            logger.info("📄 翻到下一页")
            return True

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

    def generate_filename(self, prefix: str = "geekpark_web") -> str:
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

            # 策略文档 L299-311 字段顺序：publish_time, title, platform, url, exposure, read, recommend, comment, like, forward, collect
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

        if COOKIE_FILE.exists():
            context_args["storage_state"] = str(COOKIE_FILE)

        if ENABLE_USER_AGENT_RANDOM and USER_AGENTS:
            context_args["user_agent"] = random.choice(USER_AGENTS)

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        if ENABLE_STEALTH:
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

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
                login_failed_callback("geekpark_web")
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
            data['platform'] = "极客公园官网"
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
        await run(p)


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("geekpark_web", None, "极客公园官网", is_stable=True)

