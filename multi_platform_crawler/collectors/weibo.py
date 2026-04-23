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
微博平台采集器
基于通用模板 template_collector.py 修改
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


# ===================== 平台配置项（请勿修改逻辑，仅修改参数） =====================
PLATFORM_NAME = "微博"  # 平台名称（页面定位用）
ACCOUNT_NICKNAME = "账号名称"  # 账号昵称（侧边栏/页面判断用，替换原硬编码字符）
# ==============================================================================

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
    str(log_dir / f"weibo_crawler_{datetime.now():%Y-%m-%d}.log"),
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

PLATFORM_NAME = "weibo"
config = ConfigLoader(PLATFORM_NAME)

USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
DATA_PAGE_URL = config.get("data_page_url", "")
# USERNAME 和 PASSWORD 已从 .env 文件加载，不再从 YAML 配置读取
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
        """验证当前登录态是否有效"""
        try:
            await page.goto(self.main_url, wait_until="networkidle",
                          timeout=self.config.get("default_timeout", 30000))

            # 检查URL是否包含登录页
            if "/login/" in page.url or "passport" in page.url:
                return False

            # 检查页面是否包含登录后特有元素
            await page.wait_for_timeout(1000)

            # 检查是否存在"首页"或用户相关元素
            try:
                home_link = page.get_by_role("link", name="首页")
                if await home_link.is_visible(timeout=2000):
                    return True
            except:
                pass

            # 检查cookie
            cookies = await page.context.cookies()
            for cookie in cookies:
                if cookie['name'] and 'SUB' in cookie['name']:
                    return True

            return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        执行自动登录流程
        微博登录需要先点击"账号登录"切换到账号密码登录
        """
        try:
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # 等待页面加载完成
            await page.wait_for_timeout(2000)

            # ===== 步骤1: 切换到账号登录 =====
            logger.info("🔐 尝试切换到账号密码登录...")
            try:
                account_login_link = page.get_by_role("link", name="账号登录")
                await account_login_link.click(timeout=3000)
                await page.wait_for_timeout(1000)
                logger.info("✅ 已切换到账号登录")
            except Exception as e:
                logger.warning(f"切换账号登录失败（可能已是账号登录）: {e}")

            # ===== 步骤2: 填写用户名 =====
            username_selectors = self.config.get("login.username_selectors", [])
            username_filled = False

            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        if await locator.is_visible(timeout=2000):
                            if self.config.get("enable_human_typing"):
                                await self.anti_spider.human_typing_selector(locator, self.username)
                            else:
                                await locator.fill(self.username)
                            username_filled = True
                            logger.info(f"✅ 用户名已填写: {self.username}")
                            break
                    except Exception as e:
                        logger.debug(f"用户名选择器不可见: {e}")

            if not username_filled:
                logger.error("❌ 无法填写用户名")
                return False

            await page.wait_for_timeout(500)

            # ===== 步骤3: 填写密码 =====
            password_selectors = self.config.get("login.password_selectors", [])
            password_filled = False

            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        if await locator.is_visible(timeout=2000):
                            if self.config.get("enable_human_typing"):
                                await self.anti_spider.human_typing_selector(locator, self.password)
                            else:
                                await locator.fill(self.password)
                            password_filled = True
                            logger.info("✅ 密码已填写")
                            break
                    except Exception as e:
                        logger.debug(f"密码选择器不可见: {e}")

            if not password_filled:
                logger.error("❌ 无法填写密码")
                return False

            await page.wait_for_timeout(500)

            # ===== 步骤4: 点击登录按钮 =====
            login_button_selectors = self.config.get("login.login_button_selectors", [])
            login_clicked = False

            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator:
                    try:
                        if await locator.is_visible(timeout=2000):
                            await locator.click()
                            login_clicked = True
                            logger.info("✅ 已点击登录按钮")
                            break
                    except Exception as e:
                        logger.debug(f"登录按钮不可见: {e}")

            if not login_clicked:
                logger.error("❌ 无法点击登录按钮")
                return False

            # ===== 步骤5: 等待登录结果（处理各种验证）=====
            logger.info("⏳ 等待登录验证...")
            await page.wait_for_timeout(8000)

            # 检查各种验证类型
            max_wait_time = 60
            wait_interval = 2
            elapsed = 0

            while elapsed < max_wait_time:
                try:
                    # 安全验证弹窗
                    captcha = page.locator("text=请完成安全验证")
                    if await captcha.is_visible(timeout=1000):
                        logger.warning("⚠️ 检测到安全验证，请手动完成验证后按回车继续...")
                        await asyncio.get_event_loop().run_in_executor(None, input)
                        await page.wait_for_timeout(2000)
                        continue

                    # 滑块验证
                    slider = page.locator(".nc_wrapper, .geetest_slider")
                    if await slider.is_visible(timeout=1000):
                        logger.warning("⚠️ 检测到滑块验证，请手动完成验证后按回车继续...")
                        await asyncio.get_event_loop().run_in_executor(None, input)
                        await page.wait_for_timeout(2000)
                        continue
                except:
                    pass

                # 检查是否已通过验证
                try:
                    if "weibo.com" in page.url and "/login" not in page.url:
                        await page.wait_for_timeout(1000)
                        if "首页" in await page.content() or "创作者中心" in await page.content():
                            break
                except:
                    pass

                await page.wait_for_timeout(wait_interval)
                elapsed += wait_interval

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
            elif "placeholder" in selector:
                return page.get_by_placeholder(selector["placeholder"])
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
            data_page_url = self.config.get("data_page_url", "")
            if not data_page_url:
                data_page_url = self.config.get("navigation.data_page_url", "")

            if data_page_url:
                logger.info(f"📄 导航到数据后台: {data_page_url}")
                await page.goto(data_page_url, wait_until="networkidle",
                              timeout=self.config.get("default_timeout", 30000))
                await page.wait_for_timeout(2000)
                logger.info("✅ 导航完成")
                return True
            else:
                logger.error("❌ 未配置数据后台URL")
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

    匹配规则（依据总体方案设计文档）：
    - 调度中心已根据标题字数判断分发：标题>30字时传递关键词，≤30字时传递原始标题
    - 采集器仅接收并匹配调度中心传入的内容，不做额外转换
    - 匹配规则：目标 in 抓取（统一清洗后子串包含）
    - 多关键词支持：空格分隔的关键词，**所有关键词都必须匹配**
    """
    def match(self, current_title: str, target_titles: List[str]) -> bool:
        """
        判断当前文章标题是否匹配目标标题列表中的任意一个

        多关键词匹配规则（AND 逻辑）：
        - 用户按顺序提供空格分隔的关键词，如 "知识库 Agent OS 汪源 操作"
        - 所有关键词（清洗后）都必须包含于抓取标题（清洗后）才匹配成功
        - 清洗规则：英文小写、去除标点符号

        Args:
            current_title: 抓取到的文章标题
            target_titles: 目标标题/关键词列表（调度中心传入）

        Returns:
            True 表示匹配成功
        """
        from utils.title_matcher import clean_for_match

        # 清洗抓取到的标题
        crawled_clean = clean_for_match(current_title)
        if not crawled_clean:
            return False

        for target in target_titles:
            if not target:
                continue

            # 拆分空格分隔的多关键词
            keywords = target.split()

            if len(keywords) > 1:
                # 多关键词模式：所有关键词都必须匹配（AND 逻辑）
                all_matched = True
                for kw in keywords:
                    kw_clean = clean_for_match(kw)
                    if not kw_clean or kw_clean not in crawled_clean:
                        all_matched = False
                        break

                if all_matched:
                    logger.info(f"[微博] 关键词全部匹配成功: '{target}' -> {current_title[:30]}...")
                    return True
            else:
                # 单关键词/原标题模式：原逻辑
                target_clean = clean_for_match(target)
                if target_clean and target_clean in crawled_clean:
                    return True

        return False


class ArticleListExtractor:
    """文章列表数据提取器"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略：自上而下遍历匹配，滚动加载到60篇
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []
        target_article_count = 60

        try:
            await page.wait_for_timeout(2000)

            # 获取文章列表选择器
            row_selectors = self.config.get("article_list.row_selectors", [])
            rows = None

            for selector in row_selectors:
                try:
                    if "css" in selector:
                        rows = page.locator(selector["css"])
                    if rows and await rows.count() > 0:
                        logger.info(f"✅ 找到文章列表，共 {await rows.count()} 条")
                        break
                except:
                    continue

            if not rows or await rows.count() == 0:
                rows = page.locator("div[class*='_item_']")

            if not rows or await rows.count() == 0:
                logger.error("❌ 无法获取文章列表")
                return remaining_titles, extracted_articles

            # 滚动加载到60篇
            current_count = await rows.count()
            logger.info(f"📜 开始滚动加载文章，目标: {target_article_count} 篇...")

            scroll_attempts = 0
            max_scroll_attempts = 20

            while current_count < target_article_count and scroll_attempts < max_scroll_attempts:
                last_row = rows.nth(current_count - 1)
                try:
                    await last_row.scroll_into_view_if_needed()
                except:
                    pass

                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1500)

                new_count = await rows.count()

                if new_count == current_count:
                    scroll_attempts += 1
                    if scroll_attempts >= 3:
                        logger.info("📜 已到达底部或无法加载更多文章")
                        break
                else:
                    scroll_attempts = 0
                    logger.info(f"📜 已加载 {new_count} 篇文章")

                current_count = new_count

            final_count = await rows.count()
            logger.info(f"✅ 滚动加载完成，共 {final_count} 篇文章")
            logger.info(f"📊 开始遍历 {final_count} 篇文章...")

            # 遍历匹配
            for i in range(final_count):
                if not remaining_titles:
                    logger.info("✅ 所有目标文章已匹配完成")
                    break

                try:
                    row = rows.nth(i)
                    title_text = await self._extract_title(row)
                    if not title_text:
                        continue

                    logger.debug(f"📄 第{i+1}篇: {title_text[:30]}...")

                    if self.title_matcher.match(title_text, remaining_titles):
                        logger.info(f"✅ 匹配成功: {title_text[:30]}...")

                        # 点击标题进入文章详情页
                        article_data = await self._extract_article_data_from_detail(row, page)

                        if article_data:
                            extracted_articles.append(article_data)

                            for target in remaining_titles[:]:
                                if self.title_matcher.match(title_text, [target]):
                                    remaining_titles.remove(target)
                                    break

                except Exception as e:
                    logger.warning(f"⚠️ 提取第{i+1}篇失败: {e}")
                    continue

            logger.info(f"📊 提取完成，成功: {len(extracted_articles)}, 剩余未匹配: {len(remaining_titles)}")
            return remaining_titles, extracted_articles

        except Exception as e:
            logger.error(f"❌ 文章提取异常: {e}")
            return remaining_titles, extracted_articles

    async def _extract_title(self, row) -> str:
        """从行元素中提取标题"""
        try:
            content_selectors = self.config.get("article_list.content_selectors", [])
            for selector in content_selectors:
                try:
                    if "css" in selector:
                        content = row.locator(selector["css"]).first
                        if await content.count() > 0:
                            text = await content.inner_text()
                            title = text.split('\n')[0] if '\n' in text else text.split('202')[0]
                            title = title.strip()
                            if title:
                                return title
                except:
                    continue

            text = await row.inner_text()
            if text:
                lines = text.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.isdigit() and len(line) > 2:
                        return line

        except Exception as e:
            logger.debug(f"提取标题失败: {e}")

        return ""

    async def _extract_article_data_from_detail(self, row, page) -> Optional[Dict]:
        """从文章详情页提取完整数据"""
        try:
            title = await self._extract_title(row)
            publish_time = await self._extract_publish_time(row)

            logger.debug(f"🔍 准备提取: {title[:20]}...")

            # 在整个页面中查找标题元素
            title_elem = await self._find_clickable_title(page, title)
            if not title_elem:
                logger.warning("⚠️ 未找到标题元素")
                return None

            logger.debug("✅ 找到标题元素，准备点击...")

            # 点击标题打开新页面 - 使用 JavaScript 点击
            new_page = None
            try:
                async with page.context.expect_page(timeout=15000) as new_page_info:
                    # 使用 JavaScript 点击元素
                    await title_elem.evaluate("node => node.click()")
                    new_page = await new_page_info.value
                logger.debug("✅ 新页面已打开")
            except Exception as e:
                logger.warning(f"⚠️ 打开新页面失败: {e}")
                # 尝试直接获取当前页面的URL
                return None

            if not new_page:
                return None

            # 等待详情页加载完成 (id="articleRoot")
            try:
                await new_page.wait_for_selector('#articleRoot', timeout=20000)
                logger.debug("✅ 详情页加载完成")
            except Exception as e:
                logger.warning(f"⚠️ 等待详情页加载超时: {e}")
                # 尝试直接获取URL
                current_url = new_page.url
                logger.debug(f"当前页面URL: {current_url}")
                # 继续尝试提取数据

            # 提取详情页数据
            article_info = await new_page.evaluate("""
                () => {
                    const result = {
                        url: '',
                        read_count: '0',
                        like_count: '0',
                        comment_count: '0',
                        share_count: '0'
                    };

                    // 1. 提取URL - 优先从 editArticle 获取
                    const editBtn = document.querySelector('[action-type="editArticle"]');
                    if (editBtn) {
                        const actionData = editBtn.getAttribute('action-data');
                        if (actionData) {
                            const idMatch = actionData.match(/id=(\\d+)/);
                            if (idMatch) {
                                result.url = 'https://weibo.com/ttarticle/p/show?id=' + idMatch[1];
                            }
                        }
                    }

                    // 备用: 从 delArticle 获取
                    if (!result.url) {
                        const delBtn = document.querySelector('[action-type="delArticle"]');
                        if (delBtn) {
                            const actionData = delBtn.getAttribute('action-data');
                            if (actionData) {
                                const idMatch = actionData.match(/id=(\\d+)/);
                                if (idMatch) {
                                    result.url = 'https://weibo.com/ttarticle/p/show?id=' + idMatch[1];
                                }
                            }
                        }
                    }

                    // 2. 提取阅读数
                    const bodyText = document.body.innerText;
                    const readMatch = bodyText.match(/阅读数[:：](\\d+)/);
                    if (readMatch) {
                        result.read_count = readMatch[1];
                    }

                    // 3. 提取转发数 (share_count)
                    const forwardBtn = document.querySelector('[action-type="forward"]');
                    if (forwardBtn) {
                        const fText = forwardBtn.innerText || '';
                        const fMatch = fText.match(/(\\d+)/);
                        result.share_count = fMatch ? fMatch[1] : '0';
                    }

                    // 4. 提取评论数
                    const commentBtn = document.querySelector('[node-type="comment_btn"]');
                    if (commentBtn) {
                        const cText = commentBtn.innerText || '';
                        const cMatch = cText.match(/(\\d+)/);
                        result.comment_count = cMatch ? cMatch[1] : '0';
                    }

                    // 5. 提取点赞数
                    const likeBtn = document.querySelector('[node-type="like_origin"]');
                    if (likeBtn) {
                        const lText = likeBtn.innerText || '';
                        const lMatch = lText.match(/(\\d+)/);
                        result.like_count = lMatch ? lMatch[1] : '0';
                    }

                    // 备用: 从页面底部提取
                    if (result.like_count === '0') {
                        const likeAlt = document.querySelector('[action-type="like"]');
                        if (likeAlt) {
                            const laText = likeAlt.innerText || '';
                            const laMatch = laText.match(/(\\d+)/);
                            result.like_count = laMatch ? laMatch[1] : '0';
                        }
                    }

                    return result;
                }
            """)

            # 关闭详情页
            await new_page.close()

            # 格式化发布时间为 YYYY-MM-DD
            if publish_time:
                publish_time = publish_time.split()[0] if ' ' in publish_time else publish_time[:10]

            # 获取采集时间
            crawl_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 使用GUI标准字段格式
            article_data = {
                'publish_time': publish_time,      # 发布日期 YYYY-MM-DD
                'title': title,                     # 文章标题
                'platform': '微博',          # 平台名称
                'url': article_info.get('url', ''), # 文章URL
                'exposure': '/',                    # 曝光数 (微博无此字段)
                'read': article_info.get('read_count', '0'),  # 阅读数
                'recommend': '/',                   # 推荐数 (微博无此字段)
                'comment': article_info.get('comment_count', '0'),  # 评论数
                'like': article_info.get('like_count', '0'),  # 点赞数
                'forward': article_info.get('share_count', '0'),  # 转发数
                'collect': '/',                     # 收藏数 (微博无此字段)
            }

            logger.info(f"📊 提取数据 - 阅读:{article_info.get('read_count')} 点赞:{article_info.get('like_count')} 评论:{article_info.get('comment_count')} 转发:{article_info.get('share_count')}")

            return article_data

        except Exception as e:
            logger.warning(f"⚠️ 从详情页提取文章数据失败: {e}")
            return None

    async def _find_clickable_title(self, page, title_text: str):
        """查找并返回可点击的标题元素"""
        try:
            # 方法1: 直接在页面中通过精确文本查找
            title_elem = page.get_by_text(title_text[:30], exact=False).first
            if await title_elem.count() > 0:
                is_visible = await title_elem.is_visible()
                if is_visible:
                    logger.debug(f"✅ 找到标题元素: {title_text[:20]}...")
                    return title_elem

            # 方法2: 通过部分文本查找
            # 提取标题中的关键词
            keywords = re.findall(r'[\u4e00-\u9fa5]{4,}', title_text)
            for keyword in keywords[:2]:  # 取前2个关键词
                title_elem = page.get_by_text(keyword, exact=False).first
                if await title_elem.count() > 0:
                    is_visible = await title_elem.is_visible()
                    if is_visible:
                        logger.debug(f"✅ 通过关键词找到标题: {keyword}")
                        return title_elem

            # 方法3: 查找包含cursor=pointer的generic元素
            clickable = page.locator('div[cursor="pointer"]').filter(has_text=title_text[:10]).first
            if await clickable.count() > 0:
                logger.debug("✅ 通过cursor=pointer找到可点击元素")
                return clickable

            return None

        except Exception as e:
            logger.debug(f"查找标题元素失败: {e}")
            return None

    async def _extract_article_data(self, row) -> Optional[Dict]:
        """提取单篇文章的完整数据 (从列表页，已弃用)"""
        # 此方法已弃用，现在从详情页提取数据
        # 保留此方法以防详情页提取失败时的备选
        try:
            title = await self._extract_title(row)
            publish_time = await self._extract_publish_time(row)
            read_count, comment_count, like_count = '0', '0', '0'

            # 格式化发布时间为 YYYY-MM-DD
            if publish_time:
                publish_time = publish_time.split()[0] if ' ' in publish_time else publish_time[:10]

            # 使用GUI标准字段格式
            article_data = {
                'publish_time': publish_time,
                'title': title,
                'platform': '微博',
                'url': '',
                'exposure': '/',
                'read': read_count,
                'recommend': '/',
                'comment': comment_count,
                'like': like_count,
                'forward': '/',
                'collect': '/',
            }

            return article_data

        except Exception as e:
            logger.debug(f"提取文章数据失败: {e}")
            return None

    async def _extract_url(self, row) -> str:
        """提取文章URL - 从详情页提取"""
        return ''

    async def _extract_publish_time(self, row) -> str:
        """提取发布时间"""
        try:
            text = await row.inner_text()
            match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', text)
            if match:
                return match.group(1)
        except:
            pass
        return ""

    async def _extract_counts(self, row) -> Tuple[str, str, str]:
        """提取阅读数、评论数、点赞数 - 从列表页提取 (备用)"""
        return '0', '0', '0'

    async def _extract_counts_fallback(self, row, existing_counts: list) -> Tuple[str, str, str]:
        """备用数据提取方法"""
        try:
            read_count = str(existing_counts[0]) if len(existing_counts) > 0 else "0"
            comment_count = str(existing_counts[1]) if len(existing_counts) > 1 else "0"
            like_count = str(existing_counts[2]) if len(existing_counts) > 2 else "0"

            if read_count != "0" and comment_count != "0" and like_count != "0":
                return read_count, comment_count, like_count

            text = await row.inner_text()
            text = re.sub(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', '', text)
            numbers = re.findall(r'(\d+)', text)
            valid_numbers = [n for n in numbers if len(n) <= 10]

            if not valid_numbers:
                return read_count, comment_count, like_count

            if len(valid_numbers) >= 3:
                return valid_numbers[-3], valid_numbers[-2], valid_numbers[-1]
            elif len(valid_numbers) == 2:
                return valid_numbers[-2], valid_numbers[-1], "0"
            elif len(valid_numbers) == 1:
                return valid_numbers[-1], "0", "0"

            return read_count, comment_count, like_count

        except Exception as e:
            logger.debug(f"备用提取失败: {e}")
            return "0", "0", "0"


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

            # 使用GUI标准字段定义
            fieldnames = [
                'publish_time', 'title', 'platform', 'url',
                'exposure', 'read', 'recommend', 'comment', 'like', 'forward', 'collect'
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
                login_failed_callback("weibo")
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
            data['platform'] = "微博"
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
get_platform_registry().register("weibo", None, "微博", is_stable=True)

