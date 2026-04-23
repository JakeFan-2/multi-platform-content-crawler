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
百家号采集器
基于通用模板构建，适用于 baijiahao 平台
===============================================================

平台信息：
- 登录页面：https://baijiahao.baidu.com/builder/theme/bjh/login
- 数据后台：https://baijiahao.baidu.com/builder/rc/content
===============================================================
"""

import os
import asyncio
from utils.title_matcher import match_any_target
from utils.env_loader import get_platform_credentials_with_fallback
from utils.path_helper import PLATFORMS_DIR, COOKIES_DIR, LOGS_DIR, DATA_DIR
import random
import json
import re
import sys
import csv
import yaml
from urllib.parse import urlparse, parse_qs
from typing import Callable, Type, Tuple, Optional, Any, List, Dict
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
from loguru import logger


# ===================== 平台配置项（请勿修改逻辑，仅修改参数） =====================
PLATFORM_NAME = "百家号"  # 平台名称（页面定位用）
ACCOUNT_NICKNAME = "账号名称"  # 账号昵称（侧边栏/页面判断用，替换原硬编码字符）
# ==============================================================================

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
# 平台配置初始化
# ============================================================

PLATFORM_NAME = "baijiahao"
config = ConfigLoader(PLATFORM_NAME)


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
    str(log_dir / f"{PLATFORM_NAME}_crawler_{{time:YYYY-MM-DD}}.log"),
    rotation="00:00",
    retention="7 days",
    compression="zip",
    format="{{time:YYYY-MM-DD HH:mm:ss}} | {{level: <8}} | {{module}}:{{line}} | {{message}}",
    level="DEBUG",
    enqueue=True
)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
DATA_PAGE_URL = config.get("data_page_url", "")
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

# 数据采集限制
MAX_ARTICLES = config.get("max_articles", 60)


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
        self.data_page_url = self.config.get("data_page_url", "")

    async def is_logged_in(self, page) -> bool:
        """
        验证当前登录态是否有效
        百家号：检查是否跳转到数据后台或包含登录后特征
        """
        try:
            # 优先检查数据后台页面
            await page.goto(self.data_page_url, wait_until="domcontentloaded",
                          timeout=60000)
            await page.wait_for_timeout(3000)

            # 检查URL是否包含登录相关路径
            current_url = page.url
            if "/login" in current_url or "login" in current_url.lower():
                return False

            # 检查是否包含登录后元素特征
            # 百家号登录后通常会跳转到 /builder/ 路径
            if "/builder/" in current_url:
                logger.info(f"✅ 已登录，跳转至: {current_url}")
                return True

            # 检查页面内容是否有登录后特征
            page_content = await page.content()
            if "退出" in page_content or "logout" in page_content.lower():
                return True

            # 未检测到登录后特征，返回False
            return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        执行自动登录流程
        百家号：需要先勾选协议才能登录
        """
        try:
            # 增加超时时间，因为百度登录可能需要更长时间
            await page.goto(self.login_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)

            # 百家号需要先检查是否有登录对话框，如果没有则点击登录按钮打开对话框
            try:
                # 尝试查找登录对话框中的用户名输入框
                login_dialog = page.locator("#TANGRAM__PSP_4__userName")
                if not await login_dialog.is_visible(timeout=3000):
                    logger.info("未检测到登录对话框，点击登录按钮...")
                    # 点击"登录/注册百家号"按钮打开登录对话框
                    login_btn = page.get_by_role("button", name="登录/注册百家号")
                    await login_btn.click()
                    await page.wait_for_timeout(2000)
            except Exception as e:
                logger.debug(f"检查登录对话框: {e}")

            # 等待登录对话框出现
            await page.wait_for_selector("#TANGRAM__PSP_4__userName",
                                        timeout=self.config.get("element_timeout", 10000))

            # 1. 勾选同意协议复选框（百家号必须勾选）
            agreement_selectors = self.config.get("login.agreement_checkbox_selectors", [])
            agreement_checked = False
            for selector in agreement_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.click()
                        agreement_checked = True
                        logger.info("✅ 已勾选同意协议")
                        break
                    except Exception:
                        continue
            if not agreement_checked:
                logger.warning("⚠️ 无法勾选协议，可能影响登录")

            await page.wait_for_timeout(300)

            # 2. 填充用户名
            username_selectors = self.config.get("login.username_selectors", [])
            username_filled = False
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.wait_for(state="visible", timeout=5000)
                        if self.config.get("enable_human_typing"):
                            await self.anti_spider.human_typing_selector(locator, self.username)
                        else:
                            await locator.fill(self.username)
                        username_filled = True
                        logger.info(f"✅ 用户名已填充: {self.username[:3]}***")
                        break
                    except Exception as e:
                        logger.warning(f"填充用户名失败: {e}")
                        continue
            if not username_filled:
                logger.error("❌ 无法填充用户名")
                return False

            await page.wait_for_timeout(500)

            # 3. 填充密码
            password_selectors = self.config.get("login.password_selectors", [])
            password_filled = False
            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.wait_for(state="visible", timeout=5000)
                        if self.config.get("enable_human_typing"):
                            await self.anti_spider.human_typing_selector(locator, self.password)
                        else:
                            await locator.fill(self.password)
                        password_filled = True
                        logger.info("✅ 密码已填充")
                        break
                    except Exception as e:
                        logger.warning(f"填充密码失败: {e}")
                        continue
            if not password_filled:
                logger.error("❌ 无法填充密码")
                return False

            await page.wait_for_timeout(500)

            # 4. 点击登录按钮
            login_button_selectors = self.config.get("login.login_button_selectors", [])
            login_clicked = False
            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        await locator.wait_for(state="visible", timeout=5000)
                        # 获取按钮信息用于调试
                        btn_text = await locator.get_attribute("value") or await locator.inner_text()
                        logger.info(f"点击登录按钮: {btn_text}")
                        await locator.click()
                        login_clicked = True
                        login_clicked = True
                        logger.info("✅ 已点击登录按钮")
                        break
                    except Exception as e:
                        logger.warning(f"点击登录按钮失败: {e}")
                        continue
            if not login_clicked:
                logger.error("❌ 无法点击登录按钮")
                return False

            # 5. 等待登录结果
            await page.wait_for_timeout(5000)

            # 验证登录结果
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
    """

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到文章列表页（数据后台）
        百家号：直接导航到数据后台URL，点击"图文-已发布"筛选
        """
        try:
            data_page_url = self.config.get("data_page_url", "")
            if not data_page_url:
                logger.error("❌ 未配置数据后台URL")
                return False

            logger.info(f"📍 导航到数据后台: {data_page_url}")
            await page.goto(data_page_url, wait_until="domcontentloaded",
                          timeout=60000)
            await page.wait_for_timeout(3000)

            # 检查是否需要重新登录
            if "/login" in page.url:
                logger.warning("⚠️ 需要重新登录")
                return False

            # 点击"图文"筛选（已发布内容）
            try:
                # 尝试多种选择器点击"图文"选项卡
                text_tab_selectors = [
                    "text=图文",
                    "button:has-text('图文')",
                    "[class*='tabs'] >> text=图文",
                    ".ant-tabs-tab:has-text('图文')"
                ]

                for selector in text_tab_selectors:
                    tab = page.locator(selector).first
                    if await tab.count() > 0:
                        await tab.click()
                        logger.info("✅ 已点击「图文」选项卡")
                        await page.wait_for_timeout(1500)
                        break
            except Exception as e:
                logger.warning(f"⚠️ 点击图文筛选失败: {e}")

            # 点击"已发布"状态筛选
            try:
                # 先尝试直接点击tab
                published_selectors = [
                    "#rc-tabs-1-tab-publish",
                    "div[role='tab'][id*='publish']",
                    ".cheetah-tabs-tab-btn:has-text('已发布')"
                ]

                for selector in published_selectors:
                    published_elem = page.locator(selector).first
                    if await published_elem.count() > 0:
                        await published_elem.click(force=True, timeout=5000)
                        logger.info("✅ 已点击「已发布」筛选")
                        await page.wait_for_timeout(1500)
                        break
            except Exception as e:
                logger.warning(f"⚠️ 点击已发布筛选失败: {e}")

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
    百家号：实现文章数据提取
    """

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        严格按照策略一：自上而下遍历匹配

        策略一流程：
        1. 程序按自上而下顺序遍历文章列表（限制60篇=6页）
        2. 对每篇文章提取标题，与待匹配列表逐一比对
        3. 匹配成功：提取数据，从待匹配列表移除，检查是否为空
        4. 匹配失败：继续遍历下一篇
        5. 终止条件：所有目标匹配完成 或 遍历完60篇文章
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []

        # 策略一总则第4条：翻页式平台保证提取约60篇文章
        total_pages = 6  # 每页10篇，共6页
        articles_per_page = 10
        max_total_articles = total_pages * articles_per_page

        logger.info(f"📊 开始提取，目标文章: {len(remaining_titles)}篇，计划遍历{max_total_articles}篇文章({total_pages}页)")

        try:
            for page_num in range(1, total_pages + 1):
                # 策略一终止条件1：所有目标已匹配完成
                if not remaining_titles:
                    logger.info("✅ 所有目标文章已匹配完成，终止采集")
                    break

                logger.info(f"📄 正在处理第 {page_num}/{total_pages} 页")

                # 等待当前页加载
                await page.wait_for_timeout(2000)

                # 提取当前页所有文章
                for idx in range(articles_per_page):
                    # 策略一终止条件2：待匹配列表为空
                    if not remaining_titles:
                        logger.info("✅ 所有目标文章已匹配完成")
                        break

                    # 提取单篇文章数据
                    article_data = await self._extract_single_article_from_page(page, idx)

                    if not article_data:
                        break

                    title = article_data.get('title', '')

                    if not title:
                        continue

                    # 策略一第3步：匹配标题
                    if self.title_matcher.match(title, remaining_titles):
                        logger.info(f"✅ 匹配成功: {title[:40]}...")
                        extracted_articles.append(article_data)

                        # 策略一第3步：从待匹配列表中移除已匹配的
                        for rt in remaining_titles[:]:
                            if self.title_matcher.match(title, [rt]):
                                remaining_titles.remove(rt)
                                break
                    else:
                        # 策略一第4步：匹配失败继续遍历
                        pass

                # 当前页处理完，如果还有待匹配，尝试翻页
                if page_num < total_pages and remaining_titles:
                    has_next = await self._go_to_next_page(page)
                    if not has_next:
                        logger.warning("⚠️ 无法翻页，终止采集")
                        break
                elif page_num >= total_pages:
                    logger.info("📊 已完成6页采集")

            # 策略一全局终止规则
            logger.info(f"📊 提取完成，匹配成功: {len(extracted_articles)}, 剩余未匹配: {len(remaining_titles)}")

            if remaining_titles:
                for t in remaining_titles:
                    logger.warning(f"⚠️ 未匹配文章: {t[:40]}...")

        except Exception as e:
            logger.error(f"提取文章数据异常: {e}")

        return remaining_titles, extracted_articles

    async def _extract_single_article_from_page(self, page, index: int) -> Optional[Dict]:
        """
        从页面提取单篇文章数据（基于实际页面结构）
        百家号页面结构：
        - 每篇文章是一个div容器 (class包含articleItem)
        - 标题链接在 div.title 下的 a 标签中
        - 发布时间在 div.time 中
        - 6个数据指标在 .count-item div 中，顺序为：展现、阅读、评论、点赞、收藏、收入
        """
        try:
            # 查找所有文章容器 - 使用正确的选择器
            article_items = await page.locator("div[class*='articleItem']").all()

            if not article_items or index >= len(article_items):
                logger.warning(f"⚠️ 未找到第 {index + 1} 篇文章")
                return None

            # 获取当前文章容器
            item = article_items[index]

            # 获取标题链接 - 第二个链接是标题（第一个是封面图链接）
            # 使用 .title a 选择器更精确
            title_links = await item.locator(".title a").all()

            if not title_links:
                # 备用：查找所有包含文章ID的链接
                title_links = await item.locator("a[href*='baijiahao.baidu.com/s?id=']").all()

            if not title_links or len(title_links) < 1:
                logger.warning(f"⚠️ 第 {index + 1} 篇文章: 未找到标题链接")
                return None

            # 获取标题（第一个有文本的链接）
            link_elem = title_links[0]
            title = await link_elem.text_content()
            title = title.strip() if title else ""

            if not title:
                logger.warning(f"⚠️ 第 {index + 1} 篇文章: 标题为空")
                return None

            # 获取URL
            url = await link_elem.get_attribute("href") or ""

            # 提取发布时间 - 使用 div.time 选择器
            publish_time = ""
            time_elem = (await item.locator("div.time").all())[0] if (await item.locator("div.time").count()) > 0 else None
            if time_elem:
                raw_time = (await time_elem.text_content() or "").strip()
                # 统一格式: YYYY/MM/DD（删除时间部分，月份和日期补零）
                time_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', raw_time)
                if time_match:
                    year, month, day = time_match.groups()
                    publish_time = f"{year}/{int(month):02d}/{int(day):02d}"
                else:
                    publish_time = raw_time

            # 提取6个数据指标 - 使用 .count-item 选择器
            # 顺序: 展现、阅读、评论、点赞、收藏、收入
            count_items = await item.locator(".count-item").all()

            data = {
                'title': title,
                'url': url,
                'exposure': '',       # 展现量
                'read_count': '',     # 阅读量
                'like_count': '',     # 点赞量
                'comment_count': '', # 评论量
                'share_count': '',   # 分享量
                'collect_count': '', # 收藏量
                'income': '',        # 收入
                'publish_time': publish_time
            }

            # 提取各个指标
            if count_items:
                num_values = []
                for i, count_item in enumerate(count_items):
                    try:
                        # 获取count-item中的数字div
                        num_divs = await count_item.locator("div").all()
                        if num_divs:
                            text = (await num_divs[0].text_content() or "").strip()
                            # 接受数字、"0"、或带小数的数字
                            if text and (text.isdigit() or text.replace('.', '').isdigit()):
                                num_values.append(text)
                    except Exception as e:
                        logger.debug(f"提取指标失败: {e}")
                        continue

                # 按顺序映射到字段（曝光量由调度中心从静态配置注入，此处不提取）
                if len(num_values) >= 2:
                    data['read_count'] = num_values[1]   # 阅读量
                if len(num_values) >= 3:
                    data['comment_count'] = num_values[2]  # 评论量
                if len(num_values) >= 4:
                    data['like_count'] = num_values[3]    # 点赞量
                if len(num_values) >= 5:
                    data['collect_count'] = num_values[4] # 收藏量
                if len(num_values) >= 6:
                    data['income'] = num_values[5]        # 收入

            logger.debug(f"📄 提取文章: {title[:20]}..., 阅读:{data.get('read_count', '/')}, 评论:{data.get('comment_count', '/')}, 点赞:{data.get('like_count', '/')}, 收藏:{data.get('collect_count', '/')}")

            return data

        except Exception as e:
            logger.error(f"提取第 {index} 篇文章失败: {e}")
            return None

    async def _extract_article_data(self, row, title: str) -> Optional[Dict]:
        """提取单篇文章的详细数据（兼容旧版本）"""
        # 调用新方法
        return await self._extract_single_article_from_page(row, 0)

    async def _go_to_next_page(self, page) -> bool:
        """
        翻到下一页
        百家号使用分页控件
        """
        try:
            # 查找"下一页"按钮
            next_button = page.locator("button:has-text('下一页'), button[aria-label='下一页'], li:has-text('下一页') button").first

            if await next_button.count() > 0:
                if await next_button.is_disabled():
                    logger.info("⚠️ 已到达最后一页")
                    return False
                await next_button.click()
                logger.info("✅ 已翻到下一页")
                return True

            # 尝试点击分页数字
            # 查找当前页码后面的页码
            current_page = 1
            page_buttons = await page.locator("li button").all()

            for btn in page_buttons:
                try:
                    text = await btn.inner_text()
                    if text.strip().isdigit():
                        page_num = int(text.strip())
                        if page_num > current_page:
                            await btn.click()
                            logger.info(f"✅ 已翻到第 {page_num} 页")
                            return True
                except:
                    continue

            # 如果点击翻页失败，尝试使用URL参数翻页
            try:
                # 从URL中提取当前页码
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(page.url)
                params = parse_qs(parsed.query)
                current = int(params.get('currentPage', ['1'])[0])
                page_size = int(params.get('pageSize', ['10'])[0])
                
                # 直接设置目标页码：当前页+1
                next_page = current + 1

                # 构建新URL - 强制使用 pageSize=10，确保已发布筛选生效
                new_params = {
                    'currentPage': str(next_page),
                    'pageSize': '10',
                    'collection': 'publish'  # 保持已发布筛选状态
                }
                new_query = '&'.join(f"{k}={v}" for k, v in new_params.items())
                new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

                logger.info(f"ℹ️ 使用URL翻页: 第{current}页 -> 第{next_page}页 (pageSize={page_size})")
                await page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                return True
            except Exception as e:
                logger.warning(f"URL翻页失败: {e}")

            logger.info("ℹ️ 未找到下一页按钮")
            return False

        except Exception as e:
            logger.warning(f"翻页失败: {e}")
            return False


# ============================================================
# 6. CSV导出模块
# ============================================================

class CSVExporter:
    """数据导出模块"""

    # 标准字段映射 - 根据COLLECTOR_STRATEGY.md
    STANDARD_KEYS = [
        "publish_time",    # 发布日期
        "title",          # 文章标题
        "platform",       # 发布平台
        "url",            # 发布链接
        "exposure",       # 曝光量
        "read",           # 阅读量
        "recommend",      # 推荐
        "comment",        # 评论量
        "like",           # 点赞量
        "forward",        # 转发量
        "collect"         # 收藏量
    ]

    # 百家号字段映射: 代码字段 -> 标准字段
    FIELD_MAPPING = {
        'exposure': 'exposure',       # 展现量
        'read_count': 'read',        # 阅读量
        'like_count': 'like',        # 点赞量
        'comment_count': 'comment',  # 评论量
        'share_count': 'forward',    # 转发/分享量
        'collect_count': 'collect',  # 收藏量
        'publish_time': 'publish_time',
    }

    def __init__(self):
        self.output_dir = DATA_DIR
        self.output_dir.mkdir(exist_ok=True)

    def generate_filename(self, prefix: str = "articles") -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{timestamp}.csv"

    def _map_to_standard_fields(self, article: Dict, platform: str) -> Dict:
        """将文章数据映射到标准字段"""
        standard_data = {key: '' for key in self.STANDARD_KEYS}

        # 复制已有字段并映射
        for old_key, new_key in self.FIELD_MAPPING.items():
            if old_key in article:
                standard_data[new_key] = article[old_key]

        # 设置平台和标题
        standard_data['platform'] = platform
        standard_data['title'] = article.get('title', '')
        standard_data['url'] = article.get('url', '')

        return standard_data

    async def save_articles(self, articles: List[Dict], filename: Optional[str] = None, platform: str = "") -> bool:
        """保存文章到CSV"""
        if not articles:
            logger.warning("⚠️ 没有数据需要导出")
            return False

        try:
            if not filename:
                filename = self.generate_filename()

            filepath = self.output_dir / filename
            logger.info(f"📄 开始导出数据到 {filepath}...")

            # 使用标准字段
            fieldnames = self.STANDARD_KEYS

            # 转换文章数据到标准格式
            standard_articles = []
            for article in articles:
                standard_article = self._map_to_standard_fields(article, platform or PLATFORM_NAME)
                standard_articles.append(standard_article)

            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(standard_articles)

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

        # 执行采集
        anti_spider = AntiSpiderHelper(config)
        retry_manager = RetryManager(config)
        login_mgr = LoginManager(config, anti_spider, retry_manager)
        nav_mgr = NavigationManager(config)
        article_extractor = ArticleListExtractor(config)

        if not await login_mgr.ensure_login(page, context):
            logger.error(f"❌ {PLATFORM_NAME} 登录失败")
            # 通知登录失败，让调度器加入手动登录队列
            if login_failed_callback:
                login_failed_callback("baijiahao")
            return [], targets

        if not await nav_mgr.navigate_to_article_list(page):
            logger.error(f"❌ {PLATFORM_NAME} 导航失败")
            return [], targets

        # 使用传入的 targets 参数进行匹配
        remaining, extracted = await article_extractor.extract_articles(page, targets)
        remaining_targets = remaining

        # 处理结果
        for data in extracted:
            data['platform'] = "百家号"
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
        p: Playwright实例
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
            await exporter.save_articles(extracted, platform=PLATFORM_NAME)

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
get_platform_registry().register("baijiahao", None, "百家号", is_stable=True)

