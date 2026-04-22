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
            logger.success(f"配置文件加载成功: {platform}")
        except Exception as e:
            logger.critical(f"配置初始化失败: {e}")
            raise

    def _load_config(self) -> Dict:
        """加载平台配置文件"""
        config_path = PLATFORMS_DIR / f"{self.platform}.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件 {config_path} 不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            if config is None:
                raise ValueError(f"配置文件为空: {config_path}")

        # 处理动态路径
        if "cookie_file" in config:
            config["cookie_file"] = config["cookie_file"].replace(
                "{{cookie_dir}}", config.get("cookie_dir", str(COOKIES_DIR))
            ).replace("{{platform}}", self.platform)

        return config

    def _validate_config(self):
        """配置项完整性检查"""
        required_sections = {
            'article_list': ['article_link_selector'],
        }

        missing_fields = []
        for section, fields in required_sections.items():
            if section not in self.config:
                logger.warning(f"配置段缺失: {section}")
                continue
            for field in fields:
                if field not in self.config[section]:
                    missing_fields.append(f"{section}.{field}")

        if missing_fields:
            logger.warning(f"配置项缺失: {', '.join(missing_fields)}")

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
    str(log_dir / "qq_crawler_{time:YYYY-MM-DD}.log"),
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

PLATFORM_NAME = "qq"
config = ConfigLoader(PLATFORM_NAME)
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")
ARTICLE_LIST_URL = config.get("article_list_url", "")
# USERNAME = config.get("username", "")
# PASSWORD = config.get("password", "")

COOKIE_DIR = COOKIES_DIR
COOKIE_FILE = COOKIE_DIR / f"{PLATFORM_NAME}.json"

DEFAULT_TIMEOUT = config.get("default_timeout", 30000)
ELEMENT_TIMEOUT = config.get("element_timeout", 8000)
ELEMENT_SHORT_TIMEOUT = config.get("element_short_timeout", 5000)

MAX_RETRIES = config.get("max_retries", 2)
RETRY_DELAY_MIN = config.get("retry_delay_min", 3000)
RETRY_DELAY_MAX = config.get("retry_delay_max", 5000)

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
            self.config.get("retry_delay_min", 3000),
            self.config.get("retry_delay_max", 5000)
        )


# ============================================================
# 3. 登录模块 (已修复cookie全链路问题)
# ============================================================

class LoginManager:
    """管理登录流程：微信扫码登录、登录态验证、cookie复用与保存"""

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
        【已修复】多层级登录态校验，杜绝假登录
        1. 基础URL校验
        2. 页面核心元素校验（确认后台功能可访问）
        3. 登录过期弹窗校验
        """
        try:
            current_url = page.url
            logger.debug(f"当前URL: {current_url}")
            
            # 第一层：URL基础校验
            if "/main" not in current_url:
                if "userAuth" in current_url or "login" in current_url.lower():
                    logger.info("当前在登录页面，登录态无效")
                return False
            
            # 第二层：校验页面是否有登录过期/重新登录提示
            expired_tips = ["登录已过期", "请重新登录", "扫码登录", "微信登录"]
            for tip in expired_tips:
                if await page.get_by_text(tip).count() > 0:
                    logger.warning(f"检测到登录过期提示: {tip}，登录态无效")
                    return False
            
            # 第三层：校验后台核心元素是否存在（确认登录后可正常访问功能）
            core_elements = ["文章", "内容管理", "数据统计", "创作"]
            element_exists = False
            for element in core_elements:
                if await page.get_by_text(element, exact=True).count() > 0:
                    element_exists = True
                    break
            if not element_exists:
                logger.warning("未检测到后台核心功能元素，登录态无效")
                return False

            logger.success("登录态校验通过，cookie有效")
            return True

        except Exception as e:
            logger.warning(f"验证登录态异常: {e}")
            return False

    async def perform_login(self, page, context) -> bool:
        """
        【已修复】微信扫码登录优化
        1. 无头模式拦截提示
        2. 二维码过期自动刷新
        3. 登录成功后等待页面稳定
        4. 更清晰的日志提示
        """
        try:
            # 拦截无头模式：无头模式下无法扫码，直接报错提示
            is_headless = await page.evaluate("() => window.navigator.webdriver && !window.chrome")
            if is_headless or context.browser.is_connected() and context.browser.contexts[0].pages[0].is_closed() is False:
                # 双重校验无头模式
                if await page.evaluate("() => document.visibilityState === 'hidden'"):
                    logger.error("=" * 50)
                    logger.error("无头模式下无法进行微信扫码登录！")
                    logger.error("请设置 headless=False 后重新运行")
                    logger.error("=" * 50)
                    return False

            # 导航到登录页面
            await page.goto(self.login_url, timeout=DEFAULT_TIMEOUT)
            await page.wait_for_load_state("networkidle")
            
            logger.info("=" * 50)
            logger.info("🔑 企鹅号微信扫码登录")
            logger.info("请在弹出的浏览器中，使用微信扫描二维码完成登录")
            logger.info("二维码2分钟自动刷新，最长等待5分钟")
            logger.info("=" * 50)
            
            # 登录等待配置
            max_wait_time = 300  # 总超时5分钟
            qr_refresh_interval = 120  # 二维码2分钟刷新一次
            start_time = asyncio.get_event_loop().time()
            last_refresh_time = start_time
            
            while True:
                current_time = asyncio.get_event_loop().time()
                elapsed = current_time - start_time
                
                # 总超时判断
                if elapsed > max_wait_time:
                    logger.error("登录超时（5分钟），请重新运行程序")
                    return False
                
                # 二维码过期自动刷新
                if current_time - last_refresh_time > qr_refresh_interval:
                    logger.info("二维码已过期，自动刷新页面...")
                    await page.reload(wait_until="networkidle")
                    last_refresh_time = current_time
                
                # 检查是否登录成功
                if await self.is_logged_in(page):
                    logger.success("✅ 微信扫码登录成功！")
                    # 【关键修复】登录成功后等待页面完全稳定，确保所有cookie写入完成
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(3000)
                    return True
                
                # 每5秒输出一次等待状态
                if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                    logger.info(f"等待扫码登录中...已等待 {int(elapsed)} 秒")
                
                await page.wait_for_timeout(1000)

        except Exception as e:
            logger.error(f"登录过程异常: {e}")
            return False

    async def save_storage_state(self, context, filepath=None) -> None:
        """
        【已修复】cookie保存优化
        1. 先备份原有有效cookie，避免写入异常导致文件丢失
        2. 异常捕获，避免保存失败导致程序崩溃
        """
        if filepath is None:
            COOKIE_DIR.mkdir(exist_ok=True)
            filepath = COOKIE_FILE

        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            storage_state = await context.storage_state()
            
            # 备份原有cookie文件（如果存在）
            backup_file = f"{filepath}.bak"
            if os.path.exists(filepath):
                if os.path.getsize(filepath) > 0:
                    os.replace(filepath, backup_file)
            
            # 写入新的cookie文件
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(storage_state, f, ensure_ascii=False, indent=2)
            
            # 校验写入是否成功
            if os.path.getsize(filepath) == 0:
                logger.error("cookie文件写入为空，恢复备份文件")
                if os.path.exists(backup_file):
                    os.replace(backup_file, filepath)
                return False
            
            logger.success(f"✅ 登录态已保存到 {filepath}")
            return True

        except Exception as e:
            logger.error(f"保存登录态失败: {e}")
            return False

    def load_storage_state(self, filepath=None) -> Optional[str]:
        """
        【已修复】cookie加载校验
        1. 校验文件是否存在、非空、json格式合法
        2. 非法文件自动忽略，不影响程序启动
        """
        if filepath is None:
            filepath = COOKIE_FILE
        
        # 校验文件是否存在
        if not os.path.exists(filepath):
            logger.info("未找到cookie文件，需重新登录")
            return None
        
        # 校验文件是否为空
        if os.path.getsize(filepath) == 0:
            logger.warning("cookie文件为空，忽略加载")
            return None
        
        # 校验json格式是否合法
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                json.load(f)
            logger.info("cookie文件校验通过，准备加载")
            return filepath
        except json.JSONDecodeError:
            logger.error("cookie文件格式损坏，忽略加载")
            return None

    async def ensure_login(self, page, context, headless: bool = True) -> bool:
        """
        确保登录状态，流程优化

        Args:
            page: Playwright page 对象
            context: Playwright browser context
            headless: 是否使用无头模式
                - True (GUI自动采集模式): Cookie失效时直接返回False，由调度器加入手动登录队列
                - False (单机运行/手动登录模式): 保持原逻辑，执行perform_login扫码登录
        """
        # 先导航到主页，触发cookie生效
        logger.info(f"导航到主页校验登录态: {self.main_url}")
        await page.goto(self.main_url, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(2000)

        # 校验当前登录态
        if await self.is_logged_in(page):
            logger.info("✅ Cookie有效，自动登录成功")
            return True

        # Cookie无效/不存在
        logger.warning("Cookie无效或不存在")

        # 仅针对 GUI自动采集场景(headless=True且为三个特定平台): 直接返回False，不执行扫码登录
        # 单机运行或手动登录模式(headless=False): 保持原逻辑，执行perform_login扫码登录
        if headless:
            logger.warning("GUI自动采集模式：Cookie失效，停止自动登录流程，等待手动登录")
            return False

        # 单机运行或手动模式：执行扫码登录（保持原逻辑）
        logger.info("启动微信扫码登录流程...")
        login_success = await self.perform_login(page, context)
        if login_success:
            await self.save_storage_state(context)
        return login_success
# ============================================================
# 4. 导航模块
# ============================================================

class NavigationManager:
    """管理页面导航：跳转到文章管理页、点击标签、筛选状态"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到文章列表页并筛选已发布文章
        流程：
        1. 跳转到文章管理页面
        2. 点击"文章"标签
        3. 选择"已发布"状态
        """
        try:
            # 1. 导航到文章管理页面
            article_list_url = self.config.get("article_list_url", "")
            logger.info(f"导航到文章管理页面: {article_list_url}")
            
            await page.goto(article_list_url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT)
            await page.wait_for_timeout(2000)

            # 2. 点击"文章"标签
            logger.info("点击「文章」标签...")
            try:
                # 使用精确文本匹配
                article_tab = page.get_by_text("文章", exact=True)
                if await article_tab.count() > 0:
                    await article_tab.click()
                    await page.wait_for_timeout(1000)
                    logger.success("已点击「文章」标签")
                else:
                    logger.warning("未找到「文章」标签，可能已在正确页面")
            except Exception as e:
                logger.warning(f"点击「文章」标签失败: {e}")

            # 3. 选择"已发布"状态
            logger.info("选择「已发布」状态...")
            try:
                # 使用radio角色选择器
                published_radio = page.get_by_role("radio", name="已发布")
                if await published_radio.count() > 0:
                    # 先检查是否已选中
                    is_checked = await published_radio.is_checked()
                    if not is_checked:
                        await published_radio.click()
                        await page.wait_for_timeout(1000)
                        logger.success("已选择「已发布」状态")
                    else:
                        logger.info("「已发布」状态已选中")
                else:
                    logger.warning("未找到「已发布」状态选项")
            except Exception as e:
                logger.warning(f"选择「已发布」状态失败: {e}")

            # 等待文章列表加载
            await page.wait_for_timeout(2000)
            logger.success("导航到文章列表页完成")
            return True

        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False


# ============================================================
# 5. 数据提取模块 (已修复)
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

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略：自上而下遍历匹配（滑动式列表，最多60篇）
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []
        max_articles = 60
        article_count = 0
        seen_urls = set()  # 用于去重

        logger.info(f"开始提取文章，目标: {len(remaining_titles)} 篇，滑动式列表最多{max_articles}篇")

        while article_count < max_articles and remaining_titles:
            # 提取当前可见区域的全部文章
            articles = await self._extract_page_articles(page, seen_urls)

            if not articles:
                logger.warning("当前页面无新文章数据")
                break

            for article in articles:
                if article_count >= max_articles:
                    break

                # 记录已处理的URL
                seen_urls.add(article.get('url', ''))
                article_count += 1

                if not remaining_titles:
                    break

                # 匹配标题（三级包含匹配）
                current_title = article.get('title', '')

                # 输出完整的标题和待匹配列表，方便调试
                logger.debug(f"当前提取标题: {current_title}")
                logger.debug(f"待匹配列表: {remaining_titles}")

                if self.title_matcher.match(current_title, remaining_titles):
                    extracted_articles.append(article)
                    
                    # 【已修复】找出并移除对应的目标标题
                    matched_target = None
                    for target in remaining_titles:
                        # 使用同样的包含逻辑来确定移除哪一个
                        if target.strip() in current_title.strip():
                            matched_target = target
                            break
                    
                    if matched_target:
                        remaining_titles.remove(matched_target)
                        logger.info(f"✓ 匹配成功: {current_title[:80]}")
                        logger.info(f"  进度: {len(extracted_articles)}/{len(target_titles)}, 剩余: {len(remaining_titles)}")
                else:
                    # 输出未匹配的详细信息
                    logger.info(f"✗ 未匹配: {current_title[:80]}")
                    if len(remaining_titles) <= 2:
                        for i, target in enumerate(remaining_titles):
                            logger.info(f"  目标{i+1}: {target}")

            # 如果还有待匹配目标，尝试滚动加载更多
            if remaining_titles and article_count < max_articles:
                has_more = await self._scroll_to_load_more(page)
                if not has_more:
                    logger.info("已滚动到底部，无法加载更多")
                    break
                await page.wait_for_timeout(2000)

        logger.info(f"提取完成，匹配成功: {len(extracted_articles)} 篇，剩余未匹配: {len(remaining_titles)}")
        return remaining_titles, extracted_articles

    async def _extract_page_articles(self, page, seen_urls: set) -> List[Dict]:
        """
        提取当前页面的所有文章数据
        使用 a[href*="page.om.qq.com"] 选择器定位文章链接
        """
        articles = []

        try:
            # 查找所有文章链接（企鹅号文章URL包含page.om.qq.com）
            all_links = await page.locator('a[href*="page.om.qq.com"]').all()
            logger.debug(f"找到 {len(all_links)} 个文章链接")

            for link in all_links:
                try:
                    href = await link.get_attribute('href')
                    if not href or href in seen_urls:
                        continue

                    # 获取完整标题文本（优先使用title属性，其次使用innerText）
                    full_title = await link.get_attribute('title')
                    if full_title:
                        title = full_title.strip()
                    else:
                        # 如果没有title属性，尝试data-title或其他属性
                        data_title = await link.get_attribute('data-title')
                        if data_title:
                            title = data_title.strip()
                        else:
                            title = await link.inner_text()
                            title = title.strip()

                    if len(title) < 5:  # 过滤非标题链接
                        continue

                    # 获取文章行的父容器（用于提取其他字段）
                    article_row = link.locator('xpath=../../..')
                    row_text = ""
                    try:
                        if await article_row.count() > 0:
                            row_text = await article_row.inner_text()
                    except:
                        pass

                    # 提取发布时间 (格式: 2026-03-25 16:10:53 或 2026/3/31 14:56)
                    # 统一转换为 YYYY/MM/DD 格式（删除时间部分，月份和日期补零）
                    publish_time = ""
                    time_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', row_text)
                    if time_match:
                        year, month, day = time_match.groups()
                        publish_time = f"{year}/{int(month):02d}/{int(day):02d}"

                    # 提取阅读数 - 尝试多种模式
                    read_count = "0"
                    read_patterns = [
                        r'阅读[：:\s]*([\d,]+)',
                        r'阅读\s*([\d,]+)',
                        r'👀\s*([\d,]+)',
                        r'eye\s*icon\s*([\d,]+)'
                    ]
                    for pattern in read_patterns:
                        read_match = re.search(pattern, row_text)
                        if read_match:
                            read_count = read_match.group(1).replace(',', '')
                            break

                    # 提取评论数 - 尝试多种模式
                    comment_count = "0"
                    comment_patterns = [
                        r'评论[：:\s]*([\d,]+)',
                        r'评论\s*([\d,]+)',
                        r'💬\s*([\d,]+)',
                        r'comment\s*icon\s*([\d,]+)'
                    ]
                    for pattern in comment_patterns:
                        comment_match = re.search(pattern, row_text)
                        if comment_match:
                            comment_count = comment_match.group(1).replace(',', '')
                            break

                    # 构建文章数据（符合标准字段定义）
                    article = {
                        'title': title,
                        'url': href,
                        'publish_time': publish_time,
                        'read': read_count,
                        'comment': comment_count,
                        'platform': '极客公园企鹅号',
                        'exposure': '/',
                        'recommend': '/',
                        'like': '/',
                        'forward': '/',
                        'collect': '/'
                    }

                    articles.append(article)
                    logger.info(f"提取文章: {title[:80]}... 阅读:{read_count} 评论:{comment_count}")

                except Exception as e:
                    logger.debug(f"解析链接失败: {e}")
                    continue

            logger.info(f"本次提取到 {len(articles)} 篇新文章")

        except Exception as e:
            logger.warning(f"提取页面文章失败: {e}")

        return articles

    async def _scroll_to_load_more(self, page) -> bool:
        """
        滚动页面加载更多内容
        使用整页滚动方式
        """
        try:
            # 获取当前页面高度
            before_scroll = await page.evaluate("document.body.scrollHeight")
            
            # 滚动到底部
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            
            # 检查是否加载了新内容
            after_scroll = await page.evaluate("document.body.scrollHeight")
            
            if after_scroll > before_scroll:
                logger.debug(f"滚动加载成功: {before_scroll} -> {after_scroll}")
                return True
            else:
                # 尝试再次滚动确认
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                final_scroll = await page.evaluate("document.body.scrollHeight")
                return final_scroll > before_scroll

        except Exception as e:
            logger.warning(f"滚动失败: {e}")
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
            logger.warning("没有数据需要导出")
            return False

        try:
            if not filename:
                filename = self.generate_filename()

            filepath = self.output_dir / filename
            logger.info(f"开始导出数据到 {filepath}...")

            # 标准字段定义
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

            logger.success(f"数据已成功导出到 {filepath}")
            return True

        except Exception as e:
            logger.error(f"数据导出失败: {e}")
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
            logger.info("加载已保存的Cookie")

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
        login_success = await login_mgr.ensure_login(page, context, headless=headless)
        logger.info(f"QQ号登录结果: login_success={login_success}")
        if not login_success:
            logger.error(f"{PLATFORM_NAME} 登录失败")
            # 通知登录失败，让调度器加入手动登录队列
            if login_failed_callback:
                logger.info("调用 login_failed_callback('qq')")
                login_failed_callback("qq")
            return [], targets

        # 导航到文章列表
        if not await nav_mgr.navigate_to_article_list(page):
            logger.error(f"{PLATFORM_NAME} 导航失败")
            return [], targets

        # 提取文章数据
        remaining, extracted = await article_extractor.extract_articles(page, targets)
        remaining_targets = remaining

        # 处理结果（确保使用正确的平台名称）
        for data in extracted:
            # 数据已经在_extract_page_articles中设置了完整字段
            # 这里只做确认，不覆盖
            if 'platform' not in data:
                data['platform'] = '极客公园企鹅号'
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
        logger.error(f"{PLATFORM_NAME} 采集异常: {e}")
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

        if not await login_mgr.ensure_login(page, context, headless=False):
            logger.error("登录失败")
            return

        if not await nav_mgr.navigate_to_article_list(page):
            logger.error("导航失败")
            return

        remaining, extracted = await article_extractor.extract_articles(page, DEBUG_TARGETS)

        if extracted:
            exporter = CSVExporter()
            await exporter.save_articles(extracted)

        logger.info("按回车键关闭浏览器...")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await login_mgr.save_storage_state(context)

    except Exception as e:
        logger.error(f"程序执行异常: {e}", exc_info=e)
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
get_platform_registry().register("qq", None, "企鹅号", is_stable=True)


if __name__ == "__main__":
    asyncio.run(main())