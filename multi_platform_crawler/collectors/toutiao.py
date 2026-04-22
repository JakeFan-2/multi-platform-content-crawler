# ========== 独立调试支持（不影响主程序）==========
import sys
import os
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ========== 结束 ==========

# ========== 调试用目标文章列表（仅独立运行时生效，可设置5篇）==========
DEBUG_TARGETS = [
    "在黑客松上，开发者们下注鸿蒙",
    "越来越多的人，已经把小红书玩成了 AI 孵化器",
]
# ========== 结束 ==========

"""
===============================================================
头条号采集器
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
from typing import Callable, Type, Tuple, Optional, Any, List, Dict, Mapping
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

# 控制台输出 - 使用ASCII兼容格式避免Windows编码问题
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

PLATFORM_NAME = "toutiao"
config = ConfigLoader(PLATFORM_NAME)
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
DATA_PAGE_URL = config.get("data_page_url", "")
# USERNAME 和 PASSWORD 已从 .env 文件通过 get_platform_credentials 加载
# USERNAME = config.get("username", "")
# PASSWORD = config.get("password", "")

# 路径配置
COOKIE_DIR = COOKIES_DIR
COOKIE_FILE = COOKIE_DIR / f"{PLATFORM_NAME}.json"

# 超时配置
DEFAULT_TIMEOUT = config.get("default_timeout", 30000)
ELEMENT_TIMEOUT = config.get("element_timeout", 10000)
ELEMENT_SHORT_TIMEOUT = config.get("element_short_timeout", 5000)

# 重试配置
MAX_RETRIES = config.get("max_retries", 2)
RETRY_DELAY_MIN = config.get("retry_delay_min", 3000)
RETRY_DELAY_MAX = config.get("retry_delay_max", 5000)

# 用户代理
USER_AGENTS: List[str] = config.get("user_agents", [])
TYPING_DELAY_MIN = config.get("typing_delay_min", 80)
TYPING_DELAY_MAX = config.get("typing_delay_max", 180)

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
                self.config.get("typing_delay_min", 80),
                self.config.get("typing_delay_max", 180)
            )
            await locator.type(char, delay=delay)

    async def human_typing_selector(self, locator, text: str) -> None:
        await locator.clear()
        chunk_size = 3
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            delay = random.randint(
                self.config.get("typing_delay_min", 80),
                self.config.get("typing_delay_max", 180)
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
            # 访问主页或数据页面检查登录态
            check_url = self.config.get("data_page_url", self.main_url)
            await page.goto(check_url, wait_until="networkidle",
                          timeout=self.config.get("default_timeout", 30000))

            # 检查是否被重定向到登录页
            if "login" in page.url.lower() or "auth" in page.url.lower():
                return False

            # 检查登录后特有的元素
            verify_selectors = self.config.get("verify_logged_in_selectors", [])
            for selector in verify_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    return True

            # 检查URL是否包含用户特有的路径
            if "profile" in page.url or "mp" in page.url:
                return True

            return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        执行自动登录流程
        头条号登录可能需要滑动验证和验证码，这里提供两种登录方式
        """
        try:
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            await page.wait_for_load_state("networkidle")

            # 优先尝试账密登录
            login_success = await self._try_password_login(page)
            if login_success:
                return True

            # 如果账密登录失败，尝试手机验证码登录
            logger.info("账密登录失败，尝试手机验证码登录...")
            login_success = await self._try_phone_login(page)
            return login_success

        except Exception as e:
            logger.error(f"登录过程异常: {e}")
            return False

    async def _try_password_login(self, page) -> bool:
        """尝试账密登录"""
        try:
            # 检查是否需要切换到账密登录
            switch_selectors = self.config.get("login.switch_to_password_selectors", [])
            for selector in switch_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    await locator.click()
                    await page.wait_for_load_state("networkidle")
                    break

            # 勾选协议
            agreement_selectors = self.config.get("login.agreement_checkbox_selectors", [])
            for selector in agreement_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    if not await locator.is_checked():
                        await locator.click()
                    break

            # 输入用户名
            username_selectors = self.config.get("login.username_selectors", [])
            username_filled = False
            for selector in username_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.username)
                    else:
                        await locator.fill(self.username)
                    username_filled = True
                    break

            if not username_filled:
                logger.warning("未找到用户名输入框")
                return False

            await page.wait_for_timeout(500)

            # 输入密码
            password_selectors = self.config.get("login.password_selectors", [])
            password_filled = False
            for selector in password_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.password)
                    else:
                        await locator.fill(self.password)
                    password_filled = True
                    break

            if not password_filled:
                logger.warning("未找到密码输入框")
                return False

            # 点击登录按钮
            login_button_selectors = self.config.get("login.login_button_selectors", [])
            for selector in login_button_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    await locator.click()
                    break

            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            # 检查是否需要验证码
            if "验证码" in await page.content():
                logger.warning("账密登录需要验证码，请使用Cookie登录或手动登录后保存Cookie")
                return False

            # 验证登录
            return await self.is_logged_in(page)

        except Exception as e:
            logger.warning(f"账密登录异常: {e}")
            return False

    async def _try_phone_login(self, page) -> bool:
        """尝试手机验证码登录"""
        try:
            # 切换到手机登录
            phone_tab_selectors = [
                {"role": "button", "name": "手机登录"},
                {"role": "tab", "name": "手机登录"}
            ]
            for selector in phone_tab_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    await locator.click()
                    await page.wait_for_load_state("networkidle")
                    break

            # 勾选协议
            agreement_selectors = self.config.get("login.agreement_checkbox_selectors", [])
            for selector in agreement_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    if not await locator.is_checked():
                        await locator.click()
                    break

            # 输入手机号
            phone_selectors = self.config.get("login.phone_selectors", [])
            phone_filled = False
            for selector in phone_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    if self.config.get("enable_human_typing"):
                        await self.anti_spider.human_typing_selector(locator, self.username)
                    else:
                        await locator.fill(self.username)
                    phone_filled = True
                    break

            if not phone_filled:
                logger.warning("未找到手机号输入框")
                return False

            # 点击获取验证码
            get_code_selectors = self.config.get("login.get_code_selectors", [])
            for selector in get_code_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    await locator.click()
                    break

            logger.warning("手机验证码登录需要手动输入验证码，请使用Cookie登录或手动登录后保存Cookie")
            return False

        except Exception as e:
            logger.warning(f"手机登录异常: {e}")
            return False

    def _get_locator(self, page, selector: Dict) -> Optional[Any]:
        """多策略获取元素定位器"""
        try:
            if "role" in selector:
                name = selector.get("name", "")
                exact = selector.get("exact", False)
                if exact:
                    return page.get_by_role(selector["role"], name=name, exact=True)
                else:
                    return page.get_by_role(selector["role"], name=name)
            elif "css" in selector:
                return page.locator(selector["css"])
            elif "id" in selector:
                return page.locator(f"#{selector['id']}")
            elif "xpath" in selector:
                return page.locator(selector["xpath"])
            elif "text" in selector:
                return page.get_by_text(selector["text"], exact=False)
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

        logger.info("🔐 需要执行登录...")
        login_success = await self.perform_login(page, context)
        if login_success:
            await self.save_storage_state(context)
        else:
            logger.warning("⚠️ 自动登录失败，请手动登录后程序将自动保存Cookie")
            # 等待用户手动登录
            logger.info("等待用户手动登录...(按回车键继续)")
            try:
                await asyncio.get_event_loop().run_in_executor(None, input)
            except:
                await asyncio.sleep(30)  # 等待30秒让用户手动登录

            # 再次检查登录态
            if await self.is_logged_in(page):
                await self.save_storage_state(context)
                return True

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
            if data_page_url:
                await page.goto(data_page_url, wait_until="networkidle",
                              timeout=self.config.get("default_timeout", 30000))
                logger.info(f"✅ 已导航到数据后台页面: {data_page_url}")
            else:
                # 默认导航到主页
                main_url = self.config.get("main_url", "")
                await page.goto(main_url, wait_until="networkidle",
                              timeout=self.config.get("default_timeout", 30000))
                logger.info(f"✅ 已导航到主页: {main_url}")

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
                    logger.info(f"[头条] 关键词全部匹配成功: '{target}' -> {current_title[:30]}...")
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

    def _pagination_max_pages(self) -> int:
        """优先 article_list.pagination.max_pages，兼容根级 pagination.max_pages。"""
        v = self.config.get("article_list.pagination.max_pages")
        if v is None:
            v = self.config.get("pagination.max_pages", 6)
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 6

    @staticmethod
    def _normalize_list_title(title: str) -> str:
        if not title:
            return ""
        s = title.replace("\r", " ").replace("\n", " ")
        return " ".join(s.split()).strip()

    def _row_matches_target(
        self,
        extracted_title: str,
        target: str,
        keywords_map: Optional[Mapping[str, List[str]]],
    ) -> bool:
        """
        单行标题是否命中某一检索目标。
        keywords_map 存在且含该 target（一般为 search_term）时：按方案做关键词子串匹配；
        否则走 TitleMatcher（含清洗/多词 AND）。
        """
        if not extracted_title or not target:
            return False
        if keywords_map is not None and target in keywords_map:
            kws = keywords_map[target]
            et = self._normalize_list_title(extracted_title)
            return any((kw or "").strip() in et for kw in kws if (kw or "").strip())
        return self.title_matcher.match(extracted_title, [target])

    async def extract_articles(
        self,
        page,
        target_titles: List[str],
        keywords_map: Optional[Mapping[str, List[str]]] = None,
    ) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略：自上而下遍历匹配，根据策略文档总则第4条限制遍历范围
        头条号特殊逻辑：匹配成功后需要进入详情页提取完整数据
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []

        # 根据配置获取最大采集数量
        max_articles = self.config.get("max_articles", 60)
        max_pages = self._pagination_max_pages()

        logger.info(
            f"开始提取文章，目标: {len(target_titles)} 篇，限制: {max_articles} 篇，max_pages={max_pages}"
        )

        try:
            # 等待文章列表加载
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            # 翻页继续采集：article_list.pagination.max_pages 为硬上限，无下一页则安全退出
            current_page = 1
            while len(extracted_articles) < max_articles and remaining_titles:
                if current_page > max_pages:
                    logger.info(f"已达翻页上限 max_pages={max_pages}，停止采集")
                    break

                logger.info(f"🔄 开始处理第 {current_page} 页，待匹配: {len(remaining_titles)} 篇")

                page_articles = await self._extract_page_articles(
                    page, remaining_titles, current_page, keywords_map=keywords_map
                )
                extracted_articles.extend(page_articles)

                for article in page_articles:
                    title = article.get("title", "")
                    if not title:
                        continue
                    remaining_titles = [
                        t
                        for t in remaining_titles
                        if not self._row_matches_target(title, t, keywords_map)
                    ]

                logger.info(
                    f"第 {current_page} 页完成，已提取: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}"
                )

                if not remaining_titles:
                    logger.info("✅ 所有目标文章已匹配，停止翻页")
                    break

                if current_page >= max_pages:
                    logger.info(f"已达 max_pages={max_pages}，不再翻页")
                    break

                next_page = current_page + 1
                logger.info(f"🔄 准备翻到第 {next_page} 页")
                has_next = await self._go_to_next_page(page, next_page)
                if not has_next:
                    logger.warning(f"⚠️ 翻到第 {next_page} 页失败或无下一页，停止翻页")
                    break
                current_page += 1
                logger.info(f"✅ 已翻到第 {current_page} 页")
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)

            logger.info(f"📊 翻页结束，共处理 {current_page} 页，提取 {len(extracted_articles)} 篇")

        except Exception as e:
            logger.error(f"提取文章异常: {e}")

        logger.info(f"📊 提取完成，共提取: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles

    async def _extract_page_articles(
        self,
        page,
        target_titles: List[str],
        current_page: int = 1,
        keywords_map: Optional[Mapping[str, List[str]]] = None,
    ) -> List[Dict]:
        """
        提取单页文章数据
        头条号特殊逻辑：匹配成功后进入详情页提取完整数据
        """
        articles = []

        try:
            # 等待页面加载
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(3000)

            # 尝试多种策略提取文章
            rows = []

            # 策略1: 新页面结构 - 使用配置化的选择器定位文章容器
            try:
                # 从配置文件获取文章容器选择器
                row_selectors = self.config.get('article_list', {}).get('row_selectors', [])

                logger.info(f"🔍 row_selectors配置: {row_selectors}")

                # 使用配置的选择器查找文章容器
                for selector in row_selectors:
                    try:
                        # 支持 css 和 xpath 两种格式
                        selector_value = selector.get('css', selector.get('xpath', ''))
                        if selector_value:
                            logger.info(f"🔄 尝试选择器: {selector_value}")
                            locator = page.locator(selector_value)
                            found_rows = await locator.all()
                            if found_rows and len(found_rows) > 0:
                                rows = found_rows
                                logger.info(f"📊 策略1（配置化选择器）：使用 '{selector_value}' 找到 {len(rows)} 个文章容器")
                                break
                            else:
                                logger.info(f"选择器 '{selector_value}' 未找到元素")
                    except Exception as e:
                        logger.debug(f"选择器 '{selector}' 执行失败: {e}")
                        continue
            except Exception as e:
                logger.debug(f"策略1执行失败: {e}")

            # 策略2: 标准表格行（头条号作品数据表格）
            if not rows:
                table_rows = await page.locator("table tbody tr").all()
                if not table_rows:
                    table_rows = await page.locator("table tr").all()
                if table_rows:
                    # 过滤掉表头（表头通常包含"作品"、"展现量"等文字）
                    data_rows = []
                    for row in table_rows:
                        try:
                            row_text = await row.inner_text()
                            # 如果行中包含"作品"或"展现量"，说明是表头，跳过
                            if "作品" in row_text and "展现量" in row_text:
                                continue
                            # 如果行中有"查看详情"按钮，说明是数据行
                            if "查看详情" in row_text:
                                data_rows.append(row)
                        except:
                            continue

                    if data_rows:
                        rows = data_rows
                        logger.info(f"📊 策略2：从标准表格中找到 {len(data_rows)} 行数据")

                        # 调试：输出第一行的HTML结构
                        try:
                            first_row_html = await rows[0].evaluate_handle("el => el.outerHTML")
                            html_content = await first_row_html.json_value()
                            logger.debug(f"📌 第一行HTML (前500字符): {html_content[:500]}")
                        except Exception as e:
                            logger.debug(f"📌 无法获取第一行HTML: {e}")

            # 策略3: 列表项
            if not rows:
                list_items = await page.locator(".article-item, [class*='article-item'], li[data-item-id]").all()
                if list_items:
                    rows = list_items
                    logger.info(f"📊 策略3：从列表中找到 {len(list_items)} 项")

            # 策略4: 通用容器
            if not rows:
                generic_items = await page.locator(".item, [class*='Item'], .row").all()
                if generic_items:
                    rows = generic_items
                    logger.info(f"📊 策略4：从通用容器中找到 {len(generic_items)} 项")

            if not rows:
                logger.warning("⚠️ 未找到文章列表元素")

                # 尝试直接从页面提取文本
                try:
                    page_text = await page.evaluate("() => document.body.innerText")
                    logger.info(f"📌 页面文本长度: {len(page_text)} 字符")
                    if len(page_text) < 5000:
                        logger.info(f"📌 页面文本 (前1000字符): {page_text[:1000]}")
                except Exception as e:
                    logger.debug(f"📌 无法获取页面文本: {e}")

            # 额外等待，确保DOM完全渲染
            logger.info(f"⏳ 等待 {len(rows)} 行数据完全渲染...")
            await page.wait_for_timeout(2000)

            # 调试：输出页面上的所有链接文本
            try:
                all_links = await page.locator("a").all()
                logger.info(f"📌 页面上共有 {len(all_links)} 个链接")
                link_texts = []
                for link in all_links[:20]:  # 只输出前20个
                    try:
                        text = await link.inner_text()
                        href = await link.get_attribute("href")
                        # 不过滤长度，看看到底有什么
                        if text and text.strip():
                            link_texts.append(f"{text.strip()[:40]}")
                        else:
                            link_texts.append(f"[空链接] {href[:30] if href else 'N/A'}")
                    except Exception as e:
                        logger.debug(f"获取链接文本失败: {e}")
                        continue

            # 检查是否有目标标题出现在页面上（简化版）
                if target_titles:
                    logger.info(f"🔍 待匹配目标: {[t[:30] for t in target_titles]}")
            except Exception as e:
                logger.debug(f"📌 无法获取链接: {e}")


            # 遍历每一行
            logger.info(f"🔄 开始遍历 {len(rows)} 行数据，待匹配: {len(target_titles)} 篇")
            for idx, row in enumerate(rows):
                try:
                    # 提取标题、URL、发布时间
                    title = ""
                    url = ""
                    publish_time = ""

                    # 从配置文件获取字段选择器（直接从 article_list 获取）
                    article_list_config = self.config.get('article_list', {})

                    # 提取标题和URL
                    title_selector = article_list_config.get('title', '')
                    url_selector = article_list_config.get('url', '')

                    logger.info(f"🔍 使用的选择器 - title: '{title_selector}', url: '{url_selector}'")

                    if title_selector and url_selector:
                        try:
                            # 提取标题 - .first 是属性不是协程，不需要 await
                            title_element = row.locator(title_selector).first
                            raw_title = await title_element.inner_text()
                            title = self._normalize_list_title(raw_title or "")
                            logger.info(f"📌 提取到的标题: {title[:50] if title else '空'}")

                            # 提取URL
                            url_element = row.locator(url_selector).first
                            url = await url_element.get_attribute("href")
                            url = url if url else ""
                            logger.info(f"📌 提取到的URL: {url[:50] if url else '空'}")
                        except Exception as e:
                            logger.debug(f"提取标题/URL失败: {e}")

                    # 提取发布时间
                    time_selector = article_list_config.get('publish_time', '')
                    if time_selector:
                        try:
                            # .first 是属性不是协程，不需要 await
                            time_element = row.locator(time_selector).first
                            publish_time = await time_element.inner_text()
                            publish_time = publish_time.strip() if publish_time else ""
                            # 格式转换: "03-26 16:54" -> "2026-04-01"
                            if publish_time:
                                current_year = datetime.now().year
                                # 删除尾部的时间部分（如 " 16:54"）
                                if ' ' in publish_time:
                                    publish_time = publish_time.split()[0]
                                # 拼接年份: "03-26" -> "2026-03-26"
                                if '-' in publish_time and len(publish_time.split('-')) == 2:
                                    publish_time = f"{current_year}-{publish_time}"
                        except Exception as e:
                            logger.debug(f"提取发布时间失败: {e}")

                    if not title:
                        logger.warning(f"⚠️ 行 [{idx+1}] 未找到标题，选择器: '{title_selector}'")
                        continue

                    logger.info(f"📌 第[{idx+1}]行标题: {title[:50]}")

                    # 如果有待匹配标题，进行匹配
                    if target_titles:
                        if keywords_map:
                            match_result = any(
                                self._row_matches_target(title, t, keywords_map)
                                for t in target_titles
                            )
                        else:
                            match_result = self.title_matcher.match(title, target_titles)
                        if not match_result:
                            logger.debug(f"⚠️ 文章 [{idx+1}] 不匹配: {title[:40]}")
                            continue
                        logger.info(f"✅ 匹配成功 [{idx+1}]: {title[:50]}")

                    # 提取数据统计（从配置化的字段选择器中）
                    # 注意：曝光量(exposure)改为静态配置，不在此处提取
                    read_count = ""
                    like_count = ""
                    comment_count = ""

                    try:
                        # 从配置文件获取字段选择器（直接从 article_list 获取）
                        article_list_config = self.config.get('article_list', {})

                        # 提取阅读量
                        read_selector = article_list_config.get('read_count', '')
                        if read_selector:
                            try:
                                read_element = row.locator(read_selector).first
                                read_text = await read_element.inner_text()
                                match = re.search(r'(\d+\.?\d*万?)', read_text)
                                if match:
                                    read_count = match.group(1)
                            except Exception as e:
                                logger.debug(f"提取阅读量失败: {e}")

                        # 提取点赞量
                        like_selector = article_list_config.get('like_count', '')
                        if like_selector:
                            try:
                                like_element = row.locator(like_selector).first
                                like_text = await like_element.inner_text()
                                match = re.search(r'(\d+\.?\d*万?)', like_text)
                                if match:
                                    like_count = match.group(1)
                            except Exception as e:
                                logger.debug(f"提取点赞量失败: {e}")

                        # 提取评论量
                        comment_selector = article_list_config.get('comment_count', '')
                        if comment_selector:
                            try:
                                comment_element = row.locator(comment_selector).first
                                comment_text = await comment_element.inner_text()
                                match = re.search(r'(\d+\.?\d*万?)', comment_text)
                                if match:
                                    comment_count = match.group(1)
                            except Exception as e:
                                logger.debug(f"提取评论量失败: {e}")

                    except Exception as e:
                        logger.debug(f"提取数据统计失败: {e}")

                    # 构建文章数据
                    article = {
                        'publish_time': publish_time,
                        'title': title,
                        'platform': "极客公园头条号",
                        'url': url,
                        'exposure': '/',  # 曝光量由 ExposureLoader 静态配置注入，不在采集器中提取
                        'read': read_count if read_count else '/',
                        'recommend': '/',
                        'comment': comment_count if comment_count else '/',
                        'like': like_count if like_count else '/',
                        'forward': '/',  # 列表页无此数据
                        'collect': '/',  # 列表页无此数据
                    }

                    articles.append(article)
                    logger.info(f"✅ 成功提取: {title[:30]} 阅读:{read_count} 点赞:{like_count} 评论:{comment_count}")

                except Exception as e:
                    logger.debug(f"提取单篇文章失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"提取页面文章失败: {e}")

        return articles

    async def _extract_cell_text(self, row, field_type: str) -> str:
        """
        从表格行中提取指定字段的文本
        头条号表格列顺序：作品(标题+时间), 展现量, 阅读量, 点击率, 阅读时长, 点赞量, 评论量, 收益, 铁粉展现量, 铁粉阅读量, 操作
        """
        try:
            # 头条号表格列索引（从0开始）
            # 0: 作品（标题+时间）
            # 1: 展现量
            # 2: 阅读量
            # 3: 点击率
            # 4: 阅读时长
            # 5: 点赞量
            # 6: 评论量
            # 7: 收益
            # 8: 铁粉展现量
            # 9: 铁粉阅读量
            # 10: 操作

            cell_index_map = {
                "exposure": 1,       # 展现量
                "read_count": 2,     # 阅读量
                "like_count": 5,     # 点赞量
                "comment_count": 6,  # 评论量
                "share_count": -1,  # 分享/转发（表格中无此列）
                "collect_count": -1, # 收藏（表格中无此列）
                "publish_time": 0,   # 发布时间（在标题单元格中）
            }

            cell_index = cell_index_map.get(field_type, -1)
            if cell_index == -1:
                return ""

            # 获取所有cell
            cells = await row.locator("td").all()
            if cell_index < len(cells):
                cell_text = await cells[cell_index].inner_text()
                return cell_text.strip()

        except Exception as e:
            logger.debug(f"📌 提取字段 {field_type} 失败: {e}")

        return ""

    async def _click_and_extract_detail(self, page, row, title: str, url: str) -> Optional[Dict]:
        """
        点击文章进入详情页，并提取完整数据
        头条号：点击"查看详情"按钮进入详情页
        """
        try:
            # 头条号使用"查看详情"按钮进入详情页
            detail_button_selectors = [
                "button:has-text('查看详情')",
                "td:last-child button",
                "button",
            ]

            detail_button = None
            for selector in detail_button_selectors:
                try:
                    # 如果选择器包含"查看详情"文本
                    if "has-text" in selector:
                        elem = row.locator(selector).first
                    else:
                        # 尝试找到最后一个单元格中的按钮
                        elem = row.locator("td:last-child button").first
                        if await elem.count() == 0:
                            elem = row.locator("button").first

                    if await elem.count() > 0:
                        # 检查按钮文本
                        button_text = await elem.inner_text()
                        if "查看详情" in button_text:
                            detail_button = elem
                            break
                except Exception:
                    continue

            if not detail_button:
                logger.warning(f"⚠️ 未找到查看详情按钮: {title[:30]}")
                return None

            # 点击进入详情页
            logger.info(f"🔗 点击查看详情: {title[:30]}...")
            await detail_button.click()

            # 等待详情页加载
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(3000)

            # 提取详情页数据
            detail_data = await self._extract_detail_data(page, title, url)

            # 返回列表页
            await self._back_to_list(page)

            return detail_data

        except Exception as e:
            logger.error(f"❌ 进入详情页提取数据失败: {e}")
            # 尝试返回列表页
            await self._back_to_list(page)
            return None

    async def _extract_detail_data(self, page, title: str, url: str) -> Dict:
        """
        从详情页提取完整数据字段
        """
        try:
            # 展现量
            exposure = await self._extract_detail_field(page, "exposure")

            # 阅读量
            read_count = await self._extract_detail_field(page, "read")

            # 评论数
            comment_count = await self._extract_detail_field(page, "comment")

            # 点赞数
            like_count = await self._extract_detail_field(page, "like")

            # 转发/分享数
            forward_count = await self._extract_detail_field(page, "forward")

            # 收藏数
            collect_count = await self._extract_detail_field(page, "collect")

            # 发布日期
            publish_time = await self._extract_detail_field(page, "publish_time")

            # 格式化发布时间为 YYYY-MM-DD
            if publish_time:
                publish_time = publish_time.split()[0] if ' ' in publish_time else publish_time[:10]

            # 获取当前URL作为文章链接
            article_url = page.url if page.url else url

            # 构建完整数据
            article = {
                'publish_time': publish_time if publish_time else '/',
                'title': title,
                'platform': "极客公园头条号",
                'url': article_url,
                'exposure': exposure if exposure else '/',
                'read': read_count if read_count else '/',
                'recommend': '/',
                'comment': comment_count if comment_count else '/',
                'like': like_count if like_count else '/',
                'forward': forward_count if forward_count else '/',
                'collect': collect_count if collect_count else '/',
            }

            logger.info(f"📊 详情页数据: 展现:{exposure} 阅读:{read_count} 评论:{comment_count} 点赞:{like_count} 转发:{forward_count} 收藏:{collect_count}")

            return article

        except Exception as e:
            logger.error(f"❌ 提取详情页数据失败: {e}")
            # 返回基本信息
            return {
                'publish_time': '/',
                'title': title,
                'platform': "极客公园头条号",
                'url': url,
                'exposure': '/',
                'read': '/',
                'recommend': '/',
                'comment': '/',
                'like': '/',
                'forward': '/',
                'collect': '/',
            }

    async def _extract_detail_field(self, page, field_type: str) -> str:
        """从详情页提取指定字段"""
        try:
            field_selectors = self.config.get("article_detail.field_selectors", {})

            if field_type == "exposure":
                selectors = [
                    field_selectors.get("exposure", ""),
                    field_selectors.get("exposure_alt", ""),
                    field_selectors.get("exposure_xpath", "")
                ]
            elif field_type == "read":
                selectors = [
                    field_selectors.get("read", ""),
                    field_selectors.get("read_alt", ""),
                    field_selectors.get("read_xpath", "")
                ]
            elif field_type == "comment":
                selectors = [
                    field_selectors.get("comment", ""),
                    field_selectors.get("comment_alt", ""),
                    field_selectors.get("comment_xpath", "")
                ]
            elif field_type == "like":
                selectors = [
                    field_selectors.get("like", ""),
                    field_selectors.get("like_alt", ""),
                    field_selectors.get("like_xpath", "")
                ]
            elif field_type == "forward":
                selectors = [
                    field_selectors.get("forward", ""),
                    field_selectors.get("forward_alt", ""),
                    field_selectors.get("forward_alt2", ""),
                    field_selectors.get("forward_xpath", "")
                ]
            elif field_type == "collect":
                selectors = [
                    field_selectors.get("collect", ""),
                    field_selectors.get("collect_alt", ""),
                    field_selectors.get("collect_xpath", "")
                ]
            elif field_type == "publish_time":
                selectors = [
                    field_selectors.get("publish_time", ""),
                    field_selectors.get("publish_time_alt", ""),
                    field_selectors.get("publish_time_xpath", "")
                ]
            else:
                selectors = []

            for selector in selectors:
                if not selector:
                    continue
                try:
                    if selector.startswith("//"):
                        # XPath 选择器
                        elem = page.locator(selector).first
                    else:
                        # CSS 选择器
                        elem = page.locator(selector).first

                    if await elem.count() > 0:
                        text = await elem.inner_text()
                        if text and text.strip():
                            # 提取数字（过滤掉单位等）
                            text = text.strip()
                            return text
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"📌 提取字段 {field_type} 失败: {e}")

        return ""

    async def _back_to_list(self, page) -> bool:
        """
        从详情页返回列表页
        """
        try:
            # 尝试多种方式返回列表页

            # 方式1: 点击返回按钮
            back_selectors = self.config.get("article_detail.back_to_list_selectors", [])
            for selector in back_selectors:
                locator = self._get_locator(page, selector)
                if locator and await locator.count() > 0:
                    await locator.click()
                    logger.info("✅ 点击返回按钮回到列表页")
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(2000)
                    return True

            # 方式2: 使用浏览器后退
            await page.go_back()
            logger.info("✅ 使用浏览器后退回到列表页")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            return True

        except Exception as e:
            logger.error(f"❌ 返回列表页失败: {e}")
            return False

    async def _go_to_next_page(self, page, target_page: int) -> bool:
        """翻到指定页码"""
        try:
            # 根据目标页码动态生成XPath选择器
            target_selector = f"//li[text()='{target_page}']"
            logger.info(f"🔄 尝试翻到第 {target_page} 页，选择器: {target_selector}")

            # 尝试使用XPath定位目标页码按钮
            try:
                locator = page.locator(target_selector)
                count = await locator.count()

                if count > 0:
                    logger.info(f"✅ 找到第 {target_page} 页按钮")
                    await locator.click()
                    logger.info(f"✅ 已点击第 {target_page} 页按钮")
                    return True
                else:
                    logger.warning(f"⚠️ 未找到第 {target_page} 页按钮")
            except Exception as e:
                logger.debug(f"使用XPath翻页失败: {e}")

            # 如果XPath失败，尝试使用配置文件中的选择器
            next_selectors = self.config.get("pagination.next_page_selectors", [])

            # 只尝试与目标页码匹配的选择器
            for idx, selector in enumerate(next_selectors):
                # 检查选择器是否包含目标页码
                if f"'{target_page}'" in selector:
                    locator = self._get_locator(page, selector)
                    if locator and await locator.count() > 0:
                        logger.info(f"✅ 找到翻页元素 (选择器 {idx+1}): {selector}")
                        await locator.click()
                        logger.info("✅ 已点击下一页按钮")
                        return True
                    else:
                        logger.debug(f"⚠️ 选择器 {idx+1} 未找到元素: {selector}")

            # 尝试点击加载更多按钮
            load_more_selectors = self.config.get("pagination.load_more_selectors", [])

            if load_more_selectors:
                logger.info(f"🔄 尝试点击加载更多按钮")
                for selector in load_more_selectors:
                    locator = self._get_locator(page, selector)
                    if locator and await locator.count() > 0:
                        await locator.click()
                        logger.info("✅ 已点击加载更多按钮")
                        return True

        except Exception as e:
            logger.error(f"❌ 翻页失败: {e}")

        logger.warning("⚠️ 未找到翻页元素，翻页失败")
        return False

    def _get_locator(self, page, selector: Dict) -> Optional[Any]:
        """多策略获取元素定位器"""
        try:
            if "role" in selector:
                return page.get_by_role(selector["role"], name=selector.get("name", ""))
            elif "css" in selector:
                return page.locator(selector["css"])
            elif "xpath" in selector:
                return page.locator(selector["xpath"])
            elif "text" in selector:
                return page.get_by_text(selector["text"], exact=False)
        except Exception:
            pass
        return None


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
    login_failed_callback=None,
    keywords_map: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List[Dict], List[str]]:
    """
    GUI调用接口 - 供CrawlThread调用

    Args:
        targets: 待匹配的目标标题列表（最多5个）
        result_callback: 单条数据回调函数
        unmatched_callback: 未匹配目标回调函数
        headless: 是否使用无头模式，默认True
        login_failed_callback: 登录失败回调函数，接收平台ID参数
        keywords_map: 检索词 -> 关键词列表；超长标题由调度器传 search_term 与关键词，用于列表页子串匹配

    Returns:
        (success_data, unmatched): 成功数据列表和未匹配目标列表
    """
    targets = targets[:5]  # 限制最多5个目标
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
                login_failed_callback("toutiao")
            return [], targets

        # 导航到文章列表
        if not await nav_mgr.navigate_to_article_list(page):
            logger.error(f"❌ {PLATFORM_NAME} 导航失败")
            return [], targets

        # 提取文章数据
        remaining, extracted = await article_extractor.extract_articles(
            page, targets, keywords_map=keywords_map
        )
        remaining_targets = remaining

        # 处理结果
        for data in extracted:
            data['platform'] = "极客公园头条号"
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

        remaining, extracted = await article_extractor.extract_articles(
            page, DEBUG_TARGETS, keywords_map=None
        )

        if extracted:
            exporter = CSVExporter()
            await exporter.save_articles(extracted)

        logger.info("📌 按回车键关闭浏览器...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except:
            await asyncio.sleep(60)

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
get_platform_registry().register("toutiao", None, "头条号", is_stable=True)

