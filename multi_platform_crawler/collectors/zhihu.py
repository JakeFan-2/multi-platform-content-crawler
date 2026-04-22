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
知乎平台采集器
基于机构号后台 - 内容分析 -> 单篇文章分析
===============================================================

数据字段:
- 文章标题 (title)
- 文章链接 (url)
- 发布时间 (publish_time)
- 阅读量 (read_count)
- 点赞量 (like_count) = 赞同数 + 喜欢数
- 评论数 (comment_count)
- 收藏数 (collect_count)
- 分享数 (share_count)

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
            for field in self.config[section]:
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
    str(log_dir / "zhihu_crawler_{time:YYYY-MM-DD}.log"),
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

PLATFORM_NAME = "zhihu"
config = ConfigLoader(PLATFORM_NAME)
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
ARTICLE_LIST_URL = config.get("article_list_url", "")
# USERNAME 和 PASSWORD 已从 .env 文件通过 get_platform_credentials 加载
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
        先访问 main_url，再检查用户头像元素是否存在
        """
        try:
            # 先访问 main_url 验证登录态
            logger.info(f"🔍 验证登录态，访问: {self.main_url}")
            await page.goto(self.main_url, wait_until="networkidle",
                          timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_timeout(1000)

            current_url = page.url
            # 如果在登录页面，肯定未登录
            if "/signin" in current_url or "/login" in current_url:
                logger.info("📝 当前在登录页面，需要登录")
                return False

            # 检查登录后元素（用户头像）
            logged_in_selectors = self.config.get("login_check.logged_in_element", [])
            for selector in logged_in_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        is_visible = await locator.is_visible(timeout=5000)
                        if is_visible:
                            logger.success("✅ 登录态验证通过")
                            return True
                    except:
                        continue

            logger.info("⚠️ 未找到登录后元素，可能未登录")
            return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        执行知乎登录流程
        1. 导航到登录页
        2. 点击"密码登录"标签
        3. 输入账号密码
        4. 点击登录按钮
        5. 处理可能的验证码（人工或等待）
        """
        try:
            logger.info("🔐 开始执行知乎登录...")
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # 步骤1: 点击"密码登录"标签（知乎默认显示验证码登录）
            password_tab_selectors = self.config.get("login.password_tab_selectors", [])
            tab_clicked = False
            for selector in password_tab_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            await locator.click()
                            tab_clicked = True
                            logger.info("✅ 已切换到密码登录")
                            await page.wait_for_timeout(500)
                            break
                    except:
                        continue

            if not tab_clicked:
                logger.info("⚠️ 未找到密码登录标签，可能已在密码登录页面")

            # 步骤2: 填充用户名
            username_selectors = self.config.get("login.username_selectors", [])
            username_filled = False
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            if self.config.get("enable_human_typing"):
                                await self.anti_spider.human_typing_selector(locator, self.username)
                            else:
                                await locator.fill(self.username)
                            username_filled = True
                            logger.info("✅ 用户名已输入")
                            break
                    except:
                        continue

            if not username_filled:
                logger.error("❌ 未能输入用户名")
                return False

            await page.wait_for_timeout(300)

            # 步骤3: 填充密码
            password_selectors = self.config.get("login.password_selectors", [])
            password_filled = False
            logger.info(f"🔍 尝试填充密码，共有 {len(password_selectors)} 个选择器")
            for i, selector in enumerate(password_selectors):
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        is_visible = await locator.is_visible(timeout=3000)
                        logger.info(f"  选择器 {i+1}: {selector}, 可见: {is_visible}")
                        if is_visible:
                            await locator.click()  # 先点击确保聚焦
                            await locator.clear()
                            if self.config.get("enable_human_typing"):
                                await self.anti_spider.human_typing_selector(locator, self.password)
                            else:
                                await locator.fill(self.password)
                            password_filled = True
                            logger.info("✅ 密码已输入")
                            break
                    except Exception as e:
                        logger.warning(f"  选择器 {i+1} 失败: {e}")
                        continue
                else:
                    logger.warning(f"  选择器 {i+1}: {selector} 无法创建 locator")

            if not password_filled:
                logger.error("❌ 未能输入密码")
                # 打印当前页面所有 input 元素用于调试
                try:
                    inputs = await page.locator("input").all()
                    logger.info(f"📋 页面共有 {len(inputs)} 个 input 元素")
                    for idx, inp in enumerate(inputs):
                        try:
                            placeholder = await inp.get_attribute("placeholder")
                            input_type = await inp.get_attribute("type")
                            name = await inp.get_attribute("name")
                            logger.info(f"  input[{idx}]: type={input_type}, placeholder={placeholder}, name={name}")
                        except:
                            pass
                except:
                    pass
                return False

            await page.wait_for_timeout(300)

            # 步骤4: 点击登录按钮
            login_button_selectors = self.config.get("login.login_button_selectors", [])
            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            await locator.click()
                            logger.info("✅ 已点击登录按钮")
                            break
                    except:
                        continue

            # 步骤5: 等待登录结果（可能需要处理验证码）
            logger.info("⏳ 等待登录结果...")
            
            # 等待登录完成或验证码出现
            max_wait = 30  # 最多等待30秒
            for i in range(max_wait):
                await page.wait_for_timeout(1000)
                
                # 检查是否登录成功
                if await self.is_logged_in(page):
                    logger.success("✅ 登录成功！")
                    return True
                
                # 检查是否有验证码
                captcha_selectors = [
                    "div.Geetest",
                    ".geetest_slider",
                    "[class*='captcha']",
                    "iframe[src*='captcha']"
                ]
                for cap_selector in captcha_selectors:
                    try:
                        captcha = page.locator(cap_selector)
                        if await captcha.is_visible(timeout=500):
                            logger.warning("⚠️ 检测到验证码，请在浏览器中手动完成验证...")
                            break
                    except:
                        pass

            # 最终验证
            if await self.is_logged_in(page):
                logger.success("✅ 登录成功！")
                return True
            else:
                logger.error("❌ 登录超时或失败")
                return False

        except Exception as e:
            logger.error(f"登录过程异常: {e}")
            return False

    def _get_locator(self, page, selector: Dict) -> Optional[Any]:
        """多策略获取元素定位器"""
        try:
            if isinstance(selector, str):
                return page.locator(selector)
            elif isinstance(selector, dict):
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

    async def ensure_login(self, page, context) -> bool:
        """确保登录状态"""
        # 先尝试加载Cookie
        storage_state = self.load_storage_state()
        if storage_state:
            logger.info("📁 发现已保存的登录态，尝试验证...")
            # Cookie会在context创建时自动加载

        # 验证当前登录状态
        if await self.is_logged_in(page):
            logger.success("✅ 已处于登录状态")
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
        """
        导航到文章列表页
        1. 访问数据后台URL
        2. 点击"单篇文章分析"标签
        """
        try:
            article_list_url = self.config.get("article_list_url", "")
            if not article_list_url:
                logger.error("❌ 未配置article_list_url")
                return False

            logger.info(f"📍 导航到文章数据页面: {article_list_url}")
            await page.goto(article_list_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # 检查URL是否已经包含tab=single
            if "tab=single" in page.url:
                logger.success("✅ 已在单篇文章分析页面")
                return True

            # 点击"单篇文章分析"标签
            tab_selectors = self.config.get("data_page.single_article_tab", [])
            for selector in tab_selectors:
                try:
                    if isinstance(selector, dict):
                        if "role" in selector:
                            locator = page.get_by_role(selector["role"], name=selector.get("name", ""))
                        elif "css" in selector:
                            locator = page.locator(selector["css"])
                        else:
                            continue
                    else:
                        locator = page.locator(selector)

                    if await locator.is_visible(timeout=3000):
                        await locator.click()
                        await page.wait_for_timeout(1000)
                        logger.success("✅ 已切换到单篇文章分析")
                        return True
                except:
                    continue

            # 如果URL已经正确，直接返回成功
            if "tab=single" in page.url:
                logger.success("✅ 已在单篇文章分析页面")
                return True

            logger.warning("⚠️ 未找到单篇文章分析标签，继续尝试...")
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
    """文章列表数据提取器"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()
        # 互动数解析正则
        self.interaction_pattern = re.compile(
            r'(\d+)\s*赞同\s*·\s*(\d+)\s*评论\s*·\s*(\d+)\s*喜欢\s*·\s*(\d+)\s*收藏\s*·\s*(\d+)\s*分享'
        )

    def parse_interaction(self, text: str) -> Dict[str, int]:
        """解析互动数字符串"""
        result = {
            'agree_count': 0,  # 赞同数
            'comment_count': 0,
            'like_count': 0,   # 喜欢数
            'collect_count': 0,
            'share_count': 0
        }
        
        match = self.interaction_pattern.search(text)
        if match:
            result['agree_count'] = int(match.group(1))
            result['comment_count'] = int(match.group(2))
            result['like_count'] = int(match.group(3))  # 喜欢数
            result['collect_count'] = int(match.group(4))
            result['share_count'] = int(match.group(5))
        
        return result

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        遍历文章列表，匹配目标标题，提取数据
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []
        max_pages = 6  # 最多翻6页，约60篇文章（每页10条）
        current_page = 1

        logger.info(f"🎯 开始提取文章，目标: {target_titles}")

        while remaining_titles and current_page <= max_pages:
            logger.info(f"📄 正在扫描第 {current_page} 页...")
            
            # 等待表格加载
            await page.wait_for_timeout(1000)

            # 获取所有数据行
            row_selector = self.config.get("article_list.row_selectors", [{}])[0]
            if isinstance(row_selector, dict):
                row_css = row_selector.get("css", ".CreatorTable-tableRow")
            else:
                row_css = str(row_selector) if row_selector else ".CreatorTable-tableRow"

            rows = await page.locator(row_css).all()
            logger.info(f"📊 当前页发现 {len(rows)} 行数据")

            # 跳过表头行（第一行）
            data_rows = rows[1:] if len(rows) > 1 else rows

            for row in data_rows:
                if not remaining_titles:
                    break

                try:
                    # 提取标题
                    title_selectors = self.config.get("article_list.field_selectors.title", [])
                    title = None
                    for selector in title_selectors:
                        try:
                            title_locator = row.locator(selector.get("css", selector) if isinstance(selector, dict) else selector)
                            if await title_locator.is_visible(timeout=1000):
                                title = await title_locator.inner_text()
                                if title:
                                    break
                        except:
                            continue

                    if not title:
                        continue

                    # 匹配标题
                    if not self.title_matcher.match(title, remaining_titles):
                        continue

                    logger.info(f"✅ 匹配成功: {title}")

                    # 提取文章链接
                    url_selectors = self.config.get("article_list.field_selectors.url", [])
                    article_url = None
                    for selector in url_selectors:
                        try:
                            url_locator = row.locator(selector.get("css", selector) if isinstance(selector, dict) else selector)
                            if await url_locator.is_visible(timeout=1000):
                                article_url = await url_locator.get_attribute("href")
                                if article_url:
                                    break
                        except:
                            continue

                    # 提取发布日期
                    date_selectors = self.config.get("article_list.field_selectors.publish_date", [])
                    publish_date = None
                    for selector in date_selectors:
                        try:
                            date_locator = row.locator(selector.get("css", selector) if isinstance(selector, dict) else selector)
                            if await date_locator.is_visible(timeout=1000):
                                publish_date = await date_locator.inner_text()
                                if publish_date:
                                    break
                        except:
                            continue

                    # 提取阅读量
                    read_count = 0
                    try:
                        # 阅读量在第2列
                        cells = row.locator("td.CreatorTable-tableData")
                        cell_count = await cells.count()
                        if cell_count >= 2:
                            read_text = await cells.nth(1).inner_text()
                            read_count = int(read_text.strip()) if read_text.strip().isdigit() else 0
                    except:
                        pass

                    # 提取互动数
                    interaction_data = {
                        'agree_count': 0,
                        'comment_count': 0,
                        'like_count': 0,
                        'collect_count': 0,
                        'share_count': 0
                    }
                    try:
                        # 互动数在第3列
                        cells = row.locator("td.CreatorTable-tableData")
                        cell_count = await cells.count()
                        if cell_count >= 3:
                            interaction_text = await cells.nth(2).inner_text()
                            interaction_data = self.parse_interaction(interaction_text)
                    except:
                        pass

                    # 构建数据
                    article_data = {
                        'title': title,
                        'url': article_url or '',
                        'publish_time': publish_date or '',
                        'read_count': read_count,
                        # 点赞量 = 赞同数 + 喜欢数
                        'like_count': interaction_data['agree_count'] + interaction_data['like_count'],
                        'comment_count': interaction_data['comment_count'],
                        'collect_count': interaction_data['collect_count'],
                        'share_count': interaction_data['share_count']
                    }

                    extracted_articles.append(article_data)
                    logger.success(f"📊 提取数据: {title} | 阅读: {read_count} | 点赞: {article_data['like_count']}")

                    # 从待匹配列表中移除（使用模糊匹配找到对应的标题）
                    title_to_remove = None
                    for t in remaining_titles:
                        if self.title_matcher.match(title, [t]):
                            title_to_remove = t
                            break
                    if title_to_remove:
                        remaining_titles.remove(title_to_remove)

                except Exception as e:
                    logger.warning(f"处理行数据时出错: {e}")
                    continue

            # 如果还有未匹配的，尝试翻页
            if remaining_titles and current_page < max_pages:
                # 点击下一页
                next_btn_selectors = self.config.get("pagination.next_button", [])
                next_clicked = False
                for selector in next_btn_selectors:
                    try:
                        if isinstance(selector, dict):
                            next_btn = page.locator(selector.get("css", ""))
                        else:
                            next_btn = page.locator(selector)
                        
                        if await next_btn.is_visible(timeout=2000):
                            is_disabled = await next_btn.get_attribute("disabled")
                            if is_disabled is None:
                                await next_btn.click()
                                next_clicked = True
                                await page.wait_for_timeout(2000)
                                current_page += 1
                                break
                    except:
                        continue

                if not next_clicked:
                    logger.info("📄 没有更多页面")
                    break

        logger.info(f"📊 提取完成，成功: {len(extracted_articles)}，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles


# ============================================================
# 6. CSV导出模块
# ============================================================

class CSVExporter:
    """数据导出模块"""

    def __init__(self):
        self.output_dir = DATA_DIR
        self.output_dir.mkdir(exist_ok=True)

    def generate_filename(self, prefix: str = "zhihu_articles") -> str:
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

            # 标准字段顺序 (策略文档 L269-282)
            # publish_time, title, platform, url, exposure, read, recommend, comment, like, forward, collect
            fieldnames = [
                'publish_time',      # 发布日期
                'title',            # 文章标题
                'platform',         # 发布平台
                'url',              # 发布链接/URL
                'exposure',         # 曝光量 (固定 '/')
                'read',             # 阅读量
                'recommend',        # 推荐 (固定 '/')
                'comment',          # 评论量
                'like',             # 点赞量
                'forward',          # 转发/分享量
                'collect'           # 收藏量
            ]

            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for article in articles:
                    row = {
                        'publish_time': article.get('publish_time', ''),
                        'title': article.get('title', ''),
                        'platform': '极客公园知乎号',  # 知乎号固定值
                        'url': article.get('url', ''),
                        'exposure': '/',               # 固定填充
                        'read': article.get('read_count', ''),
                        'recommend': '/',              # 固定填充
                        'comment': article.get('comment_count', ''),
                        'like': article.get('like_count', ''),
                        'forward': article.get('share_count', ''),  # 分享对应转发
                        'collect': article.get('collect_count', '')
                    }
                    writer.writerow(row)

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
                login_failed_callback("zhihu")
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
            data['platform'] = "极客公园知乎号"
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

async def run(p: Playwright) -> None:
    """独立运行入口（直接运行采集器时调用）"""
    context = None
    browser = None

    try:
        browser = await p.chromium.launch(headless=False)
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
        await run(p)


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("zhihu", None, "知乎", is_stable=True)

