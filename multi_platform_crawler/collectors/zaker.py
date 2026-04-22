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

# ZAKER采集器
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
from playwright_stealth import Stealth  # 仅导入 Stealth 类
from loguru import logger  # loguru日志

# ==================== 1. 配置加载模块 ====================
class ConfigLoader:
    """加载平台配置（YAML），提供点号路径访问方法"""

    def __init__(self, platform: str = "zaker"):
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

        # 处理动态路径（如cookie_file）
        if "cookie_file" in config:
            config["cookie_file"] = config["cookie_file"].replace(
                "{{cookie_dir}}", config.get("cookie_dir", str(COOKIES_DIR))
            ).replace(
                "{{platform}}", self.platform
            )

        return config

    def _validate_config(self):
        """配置项完整性检查"""
        logger.info("🔍 开始验证配置文件...")
        required_sections = {
            'login': ['username_selectors', 'password_selectors', 'login_button_selectors'],
            'navigation': ['content_management_selectors', 'geek_park_selectors'],
            'article_list': ['iframe_selectors', 'table_selectors', 'row_selectors', 'field_selectors']
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
                    # 处理列表中的字典（如选择器列表）
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

# ==================== 日志配置 ====================
logger.remove()  # 移除默认控制台输出，避免重复
log_dir = LOGS_DIR
log_dir.mkdir(exist_ok=True)  # 自动创建logs目录

# 1. 控制台输出（简洁格式，去除emoji避免Windows GBK编码问题）
logger.add(
    sys.stdout,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
    level="INFO",
    colorize=False  # 禁用颜色以避免编码问题
)

# 2. 文件输出（异步+轮转+压缩）
logger.add(
    str(log_dir / "zaker_crawler_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="7 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{line} | {message}",
    level="DEBUG",
    enqueue=True
)

# ==================== 平台配置 ====================
# 初始化配置加载器
config = ConfigLoader("zaker")

# 从配置中获取常用值
PLATFORM_NAME = config.get("platform")
COOKIE_DIR = COOKIES_DIR
COOKIE_FILE = COOKIE_DIR / "zaker.json"
MAIN_URL = config.get("main_url")
LOGIN_URL = config.get("login_url")
ZAKER_USERNAME, ZAKER_PASSWORD = get_platform_credentials_with_fallback(
    PLATFORM_NAME or "zaker", config
)

# 时间配置
DEFAULT_TIMEOUT = config.get("default_timeout")
ELEMENT_TIMEOUT = config.get("element_timeout")
ELEMENT_SHORT_TIMEOUT = config.get("element_short_timeout")
ELEMENT_FALLBACK_TIMEOUT = config.get("element_fallback_timeout")

# 重试配置
MAX_RETRIES = config.get("max_retries")
RETRY_DELAY_MIN = config.get("retry_delay_min")
RETRY_DELAY_MAX = config.get("retry_delay_max")
RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    PlaywrightTimeoutError,
    TimeoutError,
    ConnectionError,
    OSError,
)

# 用户代理配置
USER_AGENTS: List[str] = config.get("user_agents")
TYPING_DELAY_MIN = config.get("typing_delay_min")
TYPING_DELAY_MAX = config.get("typing_delay_max")

# 功能开关
ENABLE_STEALTH = config.get("enable_stealth")
ENABLE_USER_AGENT_RANDOM = config.get("enable_user_agent_random")
ENABLE_HUMAN_TYPING = config.get("enable_human_typing")


# ==================== 2. 通用工具模块 ====================
class AntiSpiderHelper:
    """反爬工具：随机UA、人类输入模拟等"""
    
    def __init__(self, config: ConfigLoader):
        self.config = config
    
    def get_random_user_agent(self) -> str:
        """获取随机用户代理"""
        user_agents = self.config.get("user_agents")
        return random.choice(user_agents)
    
    async def human_typing(self, page, selector: str, text: str) -> None:
        """人类模拟输入（通过选择器）"""
        locator = page.locator(selector)
        await locator.clear()
        for char in text:
            delay = random.randint(
                self.config.get("typing_delay_min"),
                self.config.get("typing_delay_max")
            )
            await locator.type(char, delay=delay)
    
    async def human_typing_selector(self, locator, text: str) -> None:
        """人类模拟输入（通过定位器）- 优化版：每次输入2-3个字符"""
        await locator.clear()
        # 每次输入2-3个字符，加快速度
        chunk_size = 3
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i+chunk_size]
            delay = random.randint(
                self.config.get("typing_delay_min"),
                self.config.get("typing_delay_max")
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
        """获取随机重试延迟时间"""
        return random.randint(
            self.config.get("retry_delay_min"),
            self.config.get("retry_delay_max")
        )
    
    def async_retry_on_failure(
        self,
        max_retries: Optional[int] = None,
        delay: Optional[int] = None,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        backoff_factor: float = 1.0,
        on_retry: Optional[Callable] = None,
        random_delay: bool = False
    ):
        """异步重试装饰器"""
        if max_retries is None:
            max_retries = self.config.get("max_retries")
        if retryable_exceptions is None:
            retryable_exceptions = self.retryable_exceptions
        
        def decorator(func: Callable):
            async def wrapper(*args, **kwargs) -> Any:
                last_exception = None
                for attempt in range(1, max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        is_retryable = isinstance(e, retryable_exceptions)

                        if is_retryable and attempt < max_retries:
                            logger.warning(f"⚠️ 第 {attempt} 次尝试失败（可重试）: {type(e).__name__}: {e}")
                            current_delay = self.get_random_retry_delay() if random_delay else (
                                delay or self.get_random_retry_delay())
                            logger.info(f"⏳ {current_delay}ms 后重试...")

                            retry_success = True
                            if on_retry:
                                try:
                                    page_arg = args[0] if args else kwargs.get("page")
                                    if not page_arg:
                                        raise ValueError("无法获取page参数")
                                    await on_retry(page_arg)
                                except Exception as cb_error:
                                    logger.error(f"❌ 重试回调执行失败: {cb_error}")
                                    retry_success = False

                            if not retry_success:
                                logger.error(f"❌ 重试回调失败，终止重试")
                                raise e

                            await asyncio.sleep(current_delay / 1000)
                        elif is_retryable and attempt >= max_retries:
                            logger.error(f"❌ 所有 {max_retries} 次尝试均失败")
                            raise e
                        else:
                            logger.error(f"❌ 不可重试的异常: {type(e).__name__}: {e}", exc_info=e)
                            raise e
                raise last_exception

            return wrapper

        return decorator


# ==================== 3. 登录模块 ====================
class LoginManager:
    """管理登录流程：Cookie加载/保存、登录态验证、自动登录、手动兜底"""
    
    def __init__(self, config: ConfigLoader, anti_spider: AntiSpiderHelper, retry_manager: RetryManager):
        self.config = config
        self.anti_spider = anti_spider
        self.retry_manager = retry_manager
        self.platform = config.platform  # 添加platform属性

        # 从环境变量获取账号密码（已从 .env 文件加载到全局变量 ZAKER_USERNAME/ZAKER_PASSWORD）
        self.username = ZAKER_USERNAME
        self.password = ZAKER_PASSWORD
        self.login_url = self.config.get("login_url")
        self.main_url = self.config.get("main_url")
        self.cookie_file = self.config.get("cookie_file")
        
    async def reset_login_page(self, page) -> None:
        """重置登录页面"""
        try:
            await page.goto(self.login_url, timeout=self.config.get("default_timeout"))
            await page.wait_for_load_state("networkidle", timeout=self.config.get("default_timeout"))
            logger.info("🔄 登录页面已重置")
        except Exception as e:
            raise RuntimeError(f"重置登录页面失败: {e}") from e
    
    async def is_logged_in(self, page) -> bool:
        """验证当前登录态是否有效"""
        try:
            logger.info(f"🔍 验证登录态: 访问 {self.main_url}")
            await page.goto(self.main_url, wait_until="networkidle", timeout=self.config.get("default_timeout"))
            logger.info(f"🔍 当前URL: {page.url}")

            if "/login/" in page.url or "/login/index" in page.url:
                logger.error("❌ Cookie已失效，页面重定向到登录页")
                return False

            logger.info("✅ 未重定向到登录页，检查'内容管理'链接...")
            content_management = page.get_by_role("link", name="内容管理")
            is_visible = await content_management.is_visible(timeout=self.config.get("element_timeout"))
            logger.info(f"🔍 '内容管理'链接可见性: {is_visible}")

            if is_visible:
                logger.success("✅ Cookie登录验证通过")
            else:
                logger.error("❌ '内容管理'链接不可见，Cookie可能失效")

            return is_visible
        except PlaywrightTimeoutError:
            logger.warning("⚠️ 验证登录态超时")
            raise TimeoutError("验证登录态超时") from None
        except Exception as e:
            logger.warning(f"⚠️ 验证登录态失败: {e}", exc_info=e)
            raise RuntimeError(f"验证登录态失败: {e}") from e
    
    async def perform_login(self, page, context) -> bool:
        """执行完整的登录流程"""
        logger.info("🔐 开始执行登录流程...")
        try:
            # 1. 导航到登录页面
            await page.goto(self.login_url, timeout=self.config.get("default_timeout"))
            await page.wait_for_load_state("networkidle", timeout=self.config.get("default_timeout"))

            # 从配置中获取选择器列表
            username_selectors = self.config.get("login.username_selectors")
            password_selectors = self.config.get("login.password_selectors")
            login_button_selectors = self.config.get("login.login_button_selectors")

            # 2. 填充用户名（多策略定位 + 快速失败）
            logger.info("📝 开始填充用户名...")
            username_filled = False
            fast_timeout = 3000  # 快速检查超时3秒
            for selector in username_selectors:
                strategy_name = ""
                locator = None
                try:
                    if "role" in selector:
                        strategy_name = f"语义化({selector['role']}, {selector['name']})"
                        locator = page.get_by_role(selector["role"], name=selector["name"])
                    elif "css" in selector:
                        strategy_name = f"CSS({selector['css']})"
                        locator = page.locator(selector["css"])
                    elif "id" in selector:
                        strategy_name = f"ID({selector['id']})"
                        locator = page.locator(f"#{selector['id']}")
                    elif "xpath" in selector:
                        strategy_name = f"XPath({selector['xpath']})"
                        locator = page.locator(selector["xpath"])
                    else:
                        continue

                    # 【优化】快速检查元素是否存在（3秒超时）
                    await locator.wait_for(state="visible", timeout=fast_timeout)

                    # 使用人类模拟或直接填充
                    if self.config.get("enable_human_typing"):
                        logger.info(f"⌨️ 使用{strategy_name}填充用户名...")
                        await self.anti_spider.human_typing_selector(locator, self.username)
                    else:
                        logger.info(f"✏️ 使用{strategy_name}填充用户名...")
                        await locator.fill(self.username)

                    username_filled = True
                    logger.success(f"✅ 用户名填充成功（{strategy_name}）")
                    break
                except Exception as e:
                    logger.debug(f"✗ {strategy_name}不可用")
                    continue

            if not username_filled:
                raise RuntimeError("❌ 无法定位用户名输入框")

            await page.wait_for_timeout(500)  # 模拟人类操作间隔

            # 3. 填充密码（多策略定位 + 快速失败）
            logger.info("📝 开始填充密码...")
            password_filled = False
            for selector in password_selectors:
                strategy_name = ""
                locator = None
                try:
                    if "role" in selector:
                        strategy_name = f"语义化({selector['role']}, {selector['name']})"
                        locator = page.get_by_role(selector["role"], name=selector["name"])
                    elif "css" in selector:
                        strategy_name = f"CSS({selector['css']})"
                        locator = page.locator(selector["css"])
                    elif "id" in selector:
                        strategy_name = f"ID({selector['id']})"
                        locator = page.locator(f"#{selector['id']}")
                    elif "xpath" in selector:
                        strategy_name = f"XPath({selector['xpath']})"
                        locator = page.locator(selector["xpath"])
                    else:
                        continue

                    # 【优化】快速检查元素是否存在（3秒超时）
                    await locator.wait_for(state="visible", timeout=fast_timeout)

                    # 使用人类模拟或直接填充
                    if self.config.get("enable_human_typing"):
                        logger.info(f"⌨️ 使用{strategy_name}填充密码...")
                        await self.anti_spider.human_typing_selector(locator, self.password)
                    else:
                        logger.info(f"✏️ 使用{strategy_name}填充密码...")
                        await locator.fill(self.password)

                    password_filled = True
                    logger.success(f"✅ 密码填充成功（{strategy_name}）")
                    break
                except Exception as e:
                    logger.debug(f"✗ {strategy_name}不可用")
                    continue

            if not password_filled:
                raise RuntimeError("❌ 无法定位密码输入框")

            await page.wait_for_timeout(500)  # 模拟人类操作间隔

            # 4. 点击登录按钮
            logger.info("🖱️ 点击登录按钮...")
            login_clicked = False
            for selector in login_button_selectors:
                strategy_name = ""
                try:
                    if "role" in selector:
                        strategy_name = f"语义化({selector['role']}, {selector['name']})"
                        await page.get_by_role(selector["role"], name=selector["name"]).click()
                    elif "css" in selector:
                        strategy_name = f"CSS({selector['css']})"
                        await page.click(selector["css"])
                    elif "id" in selector:
                        strategy_name = f"ID({selector['id']})"
                        await page.click(f"#{selector['id']}")
                    elif "xpath" in selector:
                        strategy_name = f"XPath({selector['xpath']})"
                        await page.click(selector["xpath"])
                    else:
                        continue

                    login_clicked = True
                    logger.success(f"✅ 登录按钮点击成功（{strategy_name}）")
                    break
                except Exception as e:
                    logger.warning(f"⚠️ {strategy_name}点击失败: {e}")
                    continue

            if not login_clicked:
                raise RuntimeError("❌ 无法定位或点击登录按钮")

            # 5. 等待登录结果并验证
            logger.info("⏳ 等待登录结果...")
            await page.wait_for_load_state("networkidle", timeout=self.config.get("default_timeout"))

            # 检查是否跳转到主页或登录成功页面
            current_url = page.url
            logger.info(f"🔍 登录后URL: {current_url}")

            # 验证登录是否成功
            if await self.is_logged_in(page):
                logger.success("✅ 登录成功")
                # 保存登录状态
                await self.save_storage_state(context)
                return True
            else:
                logger.error("❌ 登录验证失败")
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程中发生异常: {e}", exc_info=e)
            # 保存失败截图用于调试
            try:
                screenshot_path = LOGS_DIR / f"zaker_login_error_{datetime.now():%Y%m%d_%H%M%S}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                logger.info(f"📸 登录失败截图已保存: {screenshot_path}")
            except:
                pass
            return False

    async def save_storage_state(self, context, filepath=None) -> None:
        """保存浏览器状态到文件"""
        if filepath is None:
            cookie_dir = COOKIES_DIR
            cookie_dir.mkdir(parents=True, exist_ok=True)
            filepath = cookie_dir / f"{self.platform}.json"

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        storage_state = await context.storage_state()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(storage_state, f, ensure_ascii=False, indent=2)
        logger.success(f"✅ 登录态已保存到 {filepath}")

    def load_storage_state(self, filepath=None) -> Optional[str]:
        """加载保存的浏览器状态"""
        if filepath is None:
            cookie_dir = COOKIES_DIR
            filepath = cookie_dir / f"{self.platform}.json"

        if os.path.exists(filepath):
            logger.info(f"✅ 找到保存的登录态文件: {filepath}")
            return filepath
        logger.warning(f"⚠️ 未找到保存的登录态文件: {filepath}")
        return None

    async def ensure_login(self, page, context) -> bool:
        """确保登录状态（自动尝试Cookie登录或账号登录）"""
        logger.info("🔐 开始验证/确保登录状态")

        # 1. 尝试Cookie登录
        if await self.is_logged_in(page):
            logger.success("✅ 通过Cookie验证登录成功")
            return True

        # 2. 执行账号密码登录
        logger.info("🔐 需要执行账号密码登录...")
        try:
            login_success = await self.perform_login(page, context)
            if login_success:
                logger.success("✅ 账号密码登录成功")
                return True
            logger.error("❌ 账号密码登录失败")
            return False
        except Exception as e:
            logger.error(f"❌ 登录过程中发生异常: {e}", exc_info=e)
            return False


# ==================== CSV导出模块 ====================
class CSVExporter:
    """数据导出模块：将采集的数据导出为CSV格式"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.output_dir = DATA_DIR
        self.output_dir.mkdir(exist_ok=True)

    def generate_filename(self, prefix: str = "articles") -> str:
        """生成带时间戳的文件名"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{timestamp}.csv"

    async def save_articles(self, articles: List[Dict[str, Any]], filename: Optional[str] = None) -> bool:
        """
        将文章数据保存为CSV文件
        :param articles: 文章数据列表
        :param filename: 自定义文件名（可选）
        :return: 是否成功保存
        """
        if not articles:
            logger.warning("⚠️ 没有数据需要导出")
            return False

        try:
            # 生成文件名
            if not filename:
                filename = self.generate_filename()

            filepath = self.output_dir / filename
            logger.info(f"📄 开始导出数据到 {filepath}...")

            # 确定CSV字段顺序（标准9字段，所有平台必须一致）
            fieldnames = [
                "platform", "title", "url", "publish_time",
                "read_count", "like_count", "comment_count",
                "share_count", "crawl_time"
            ]

            # 写入CSV文件
            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(articles)

            logger.success(f"✅ 数据已成功导出到 {filepath}")
            logger.info(f"📊 共导出 {len(articles)} 条记录")
            return True

        except Exception as e:
            logger.error(f"❌ 数据导出失败: {e}", exc_info=e)
            return False


# ==================== 4. 导航管理模块 ====================
class NavigationManager:
    """管理侧边栏导航：内容管理 → 极客公园"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        侧边栏导航：检测侧边栏元素 → 点击内容管理 → 点击极客公园
        第一步：登录后检测侧边栏核心元素（优先校验「内容管理」）
        第二步：点击「内容管理」后，精准检测其子菜单（极客公园），检测成功后直接点击「极客公园」
        """
        try:
            logger.info("🧭 开始侧边栏导航...")

            # 从配置获取超时设置
            timeout = self.config.get("element_short_timeout")
            fallback_timeout = self.config.get("element_fallback_timeout")

            # 从配置获取选择器
            content_selectors = self.config.get("navigation.content_management_selectors")
            geek_park_selectors = self.config.get("navigation.geek_park_selectors")

            # ===== 第一步：检测侧边栏核心元素（优先「内容管理」）=====
            logger.info("📋 第一步：检测侧边栏核心元素...")

            content_locator = None
            for selector in content_selectors:
                try:
                    if "id" in selector:
                        content_locator = page.locator(f"#{selector['id']}")
                        await content_locator.wait_for(state="visible", timeout=timeout)
                        logger.success(f"✅ 侧边栏核心元素「内容管理」检测成功（ID定位: {selector['id']}）")
                        break
                    elif "role" in selector:
                        content_locator = page.get_by_role(selector["role"], name=selector["name"])
                        await content_locator.wait_for(state="visible", timeout=timeout)
                        logger.success(f"✅ 侧边栏核心元素「内容管理」检测成功（语义化定位）")
                        break
                    elif "css" in selector:
                        content_locator = page.locator(selector["css"])
                        await content_locator.wait_for(state="visible", timeout=timeout)
                        logger.success(f"✅ 侧边栏核心元素「内容管理」检测成功（CSS定位）")
                        break
                except Exception as e:
                    logger.warning(f"⚠️ 定位失败: {selector} - {e}")
                    continue

            if not content_locator:
                raise RuntimeError("❌ 侧边栏核心元素检测失败")

            # ===== 第二步：点击「内容管理」并检测子菜单「极客公园」=====
            logger.info("📋 第二步：点击「内容管理」并检测子菜单...")

            # 点击「内容管理」展开子菜单
            await content_locator.click()
            logger.success("✅ 已点击「内容管理」一级菜单")

            # 检测子菜单「极客公园」
            submenu_locator = None
            for selector in geek_park_selectors:
                try:
                    if "css" in selector:
                        submenu_locator = page.locator(selector["css"])
                        await submenu_locator.wait_for(state="visible", timeout=timeout)
                        logger.success(f"✅ 子菜单「极客公园」检测成功（CSS定位）")
                        break
                    elif "role" in selector:
                        submenu_locator = page.get_by_role(selector["role"], name=selector["name"])
                        await submenu_locator.wait_for(state="visible", timeout=timeout)
                        logger.success(f"✅ 子菜单「极客公园」检测成功（语义化定位）")
                        break
                    elif "xpath" in selector:
                        submenu_locator = page.locator(selector["xpath"])
                        await submenu_locator.wait_for(state="visible", timeout=timeout)
                        logger.success(f"✅ 子菜单「极客公园」检测成功（XPath定位）")
                        break
                except Exception as e:
                    logger.warning(f"⚠️ 子菜单定位失败: {selector} - {e}")
                    continue

            if not submenu_locator:
                raise RuntimeError("❌ 子菜单「极客公园」检测失败")

            # 点击「极客公园」进入文章列表
            await submenu_locator.click()
            logger.success("✅ 已点击「极客公园」二级菜单")

            # 等待页面网络请求完成
            logger.info("⏳ 等待页面网络请求完成...")
            await page.wait_for_load_state("networkidle", timeout=self.config.get("element_timeout"))

            # ===== 第三步：等待文章列表表格完全渲染 =====
            logger.info("📋 第三步：等待文章列表加载...")

            # 从配置获取iframe选择器
            iframe_selectors = self.config.get("article_list.iframe_selectors")
            table_selectors = self.config.get("article_list.table_selectors")

            frame_locator = None
            for iframe_selector in iframe_selectors:
                try:
                    if "id" in iframe_selector:
                        iframe_element = page.locator(f"#{iframe_selector['id']}")
                        await iframe_element.wait_for(state="attached", timeout=self.config.get("element_timeout"))
                        frame_locator = page.frame_locator(f"#{iframe_selector['id']}")
                        logger.success(f"✅ iframe元素已定位（ID: {iframe_selector['id']}）")
                        break
                    elif "css" in iframe_selector:
                        iframe_element = page.locator(iframe_selector["css"])
                        await iframe_element.wait_for(state="attached", timeout=self.config.get("element_timeout"))
                        frame_locator = page.frame_locator(iframe_selector["css"])
                        logger.success(f"✅ iframe元素已定位（CSS）")
                        break
                except Exception as e:
                    logger.warning(f"⚠️ iframe定位失败: {iframe_selector} - {e}")
                    continue

            if not frame_locator:
                raise RuntimeError("❌ 无法定位文章列表iframe")

            # 等待表格加载
            try:
                table_locator = frame_locator.locator(table_selectors[0]["css"]).first
                await table_locator.wait_for(state="visible", timeout=10000)
                logger.success("✅ 成功进入iframe，文章列表表格已加载")
            except Exception as e:
                logger.warning(f"⚠️ 表格加载失败: {e}")
                screenshot_path = LOGS_DIR / f"zaker_table_error_{datetime.now():%Y%m%d_%H%M%S}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                logger.info(f"📸 表格加载失败截图已保存: {screenshot_path}")
                raise RuntimeError("❌ 文章列表加载失败")

            # 额外等待：确保表格行完全渲染
            await asyncio.sleep(1)
            logger.success("✅ 侧边栏导航完成，已进入「极客公园」文章列表")
            return True

        except Exception as e:
            logger.error(f"❌ 导航过程失败: {e}", exc_info=e)
            return False


# ==================== 5. 标题匹配模块 ====================
class TitleMatcher:
    """
    标题匹配器（仅使用 re 库 + 原生字符串包含）
    严格遵循 COLLECTOR_STRATEGY.md 规则：
    - 匹配成功后立即从待匹配列表移除
    - 匹配和移除使用一致的包含逻辑
    """

    def match(self, current_title: str, target_titles: List[str]) -> bool:
        """
        判断当前文章标题是否匹配目标标题列表中的任意一个
        匹配规则：目标标题 in 抓取标题（清洗后子串包含）
        """
        return match_any_target(target_titles, current_title)

    def get_cleaned_targets(self, target_titles: List[str]) -> List[str]:
        """获取清理后的目标标题列表"""
        from utils.title_matcher import clean_target
        return [clean_target(t) for t in target_titles]

    def remove_matched(self, remaining_titles: List[str], matched_title: str) -> List[str]:
        """
        从剩余标题列表中移除已匹配的标题
        【关键修复】使用与match方法一致的包含匹配逻辑
        规则：目标 in matched_title（清洗后子串包含）
        """
        from utils.title_matcher import clean_for_match

        # 清洗抓取的标题
        cleaned_matched = clean_for_match(matched_title)

        # 遍历待匹配列表，找到第一个被包含的目标标题并移除
        for i, target in enumerate(remaining_titles):
            cleaned_target = clean_for_match(target)
            # 【修复】使用与match一致的包含判断：目标 in 抓取
            if cleaned_target and cleaned_target in cleaned_matched:
                removed = remaining_titles.pop(i)
                logger.success(f"  ✅ 已从目标列表移除: {removed}")
                break

        return remaining_titles


# ==================== 6. 文章列表提取模块 ====================
class ArticleListExtractor:
    """文章列表提取模块：定位文章列表、提取数据"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.title_matcher = TitleMatcher()

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        批量匹配待提取文章、提取数据并移除已匹配项
        支持翻页遍历（最多3页，约60篇）
        :param page: Playwright Page 对象
        :param target_titles: 待匹配目标文章标题列表（长度≤5）
        :return: (剩余未匹配的目标文章标题列表, 提取到的文章数据列表)
        """
        logger.info(f"📋 开始批量提取文章，目标文章：{target_titles}")

        # 创建副本列表用于操作，避免修改原列表
        remaining_titles = target_titles.copy()
        extracted_articles = []

        # 限制待匹配文章列表数量≤5篇
        if len(remaining_titles) > 5:
            logger.warning(f"⚠️ 待匹配文章列表数量超过5篇上限，自动截断为前5篇")
            remaining_titles = remaining_titles[:5]

        # 获取分页配置
        pagination_config = self.config.get("pagination", {})
        max_pages = pagination_config.get("max_pages", 3)

        # 翻页遍历
        for current_page in range(1, max_pages + 1):
            logger.info(f"🔄 开始第 {current_page}/{max_pages} 页...")

            # 第一步：定位并遍历文章列表
            logger.info("🔍 第一步：定位文章列表...")

            # 切换到文章列表iframe上下文
            frame_locator = await self._locate_iframe(page)
            if not frame_locator:
                logger.error("❌ 无法定位文章列表iframe")
                break

            # 定位文章行
            row_locator = await self._locate_rows(frame_locator)
            if not row_locator:
                logger.error("❌ 无法定位文章列表")
                break

            # 第二步：遍历匹配并提取数据
            logger.info("🔍 第二步：遍历匹配文章...")

            total_rows = await row_locator.count()
            logger.info(f"📄 共找到 {total_rows} 篇文章（当前页面）")
            logger.info(f"📋 待匹配目标: {remaining_titles}")

            # 遍历提取
            for idx in range(total_rows):
                if len(remaining_titles) == 0:
                    logger.info("📌 待匹配文章列表已为空，遍历结束")
                    break

                try:
                    current_row = row_locator.nth(idx)
                    article_data = await self._extract_single_article(current_row, remaining_titles)

                    if article_data:
                        extracted_articles.append(article_data)
                        remaining_titles = self.title_matcher.remove_matched(
                            remaining_titles, article_data["title"]
                        )
                except Exception as e:
                    logger.warning(f"⚠️ 第{idx+1}篇文章处理失败: {e}")
                    continue

            # 检查是否已匹配所有目标
            if len(remaining_titles) == 0:
                logger.success("✅ 所有目标文章已匹配完成")
                break

            # 如果不是最后一页，尝试翻页
            if current_page < max_pages:
                logger.info(f"📄 第 {current_page} 页遍历完成，尝试翻页...")
                has_next = await self._go_to_next_page(page)
                if not has_next:
                    logger.info("ℹ️ 已到达最后一页")
                    break
                await asyncio.sleep(1)  # 等待页面加载完成
            else:
                logger.info("ℹ️ 已达到最大页数限制")

        # 返回结果
        if len(remaining_titles) > 0:
            logger.error(f"❌ 爬取程序终止，剩余未匹配文章列表：{remaining_titles}")
        else:
            logger.success(f"✅ 爬取程序终止，待匹配文章列表已全部匹配完成")

        logger.info(f"📊 批量提取完成，剩余未匹配文章：{remaining_titles}")
        logger.info(f"📊 共提取文章数据：{len(extracted_articles)} 条")
        return remaining_titles, extracted_articles

    async def _go_to_next_page(self, page) -> bool:
        """翻到下一页"""
        try:
            pagination_config = self.config.get("pagination", {})
            next_selectors = pagination_config.get("next_button_selectors", [])

            for selector in next_selectors:
                try:
                    if "css" in selector:
                        next_btn = page.locator(selector["css"])
                    elif "xpath" in selector:
                        next_btn = page.locator(selector["xpath"])
                    else:
                        continue

                    # 检查按钮是否可见且可用
                    if await next_btn.is_visible(timeout=2000):
                        # 检查按钮是否被禁用
                        is_disabled = await next_btn.get_attribute("disabled")
                        if is_disabled:
                            logger.info("ℹ️ 下一页按钮已禁用")
                            return False

                        await next_btn.click()
                        await page.wait_for_load_state("networkidle", timeout=5000)
                        logger.success("✅ 翻页成功")
                        return True
                except Exception as e:
                    logger.warning(f"⚠️ 翻页失败: {e}")
                    continue

            logger.warning("⚠️ 未找到下一页按钮")
            return False

        except Exception as e:
            logger.error(f"❌ 翻页异常: {e}")
            return False

    async def _locate_iframe(self, page):
        """定位文章列表iframe"""
        # 从配置获取iframe选择器
        iframe_selectors = self.config.get("article_list.iframe_selectors")

        for selector in iframe_selectors:
            try:
                if "id" in selector:
                    frame_locator = page.frame_locator(f"#{selector['id']}")
                elif "css" in selector:
                    frame_locator = page.frame_locator(selector["css"])
                else:
                    continue

                await asyncio.sleep(0.5)
                logger.info(f"🔗 定位到文章列表iframe（{selector}），切换上下文")
                return frame_locator
            except Exception as e:
                logger.warning(f"⚠️ iframe定位失败: {e}")
                continue

        return None

    async def _locate_rows(self, frame_locator):
        """定位文章列表行"""
        # 从配置获取选择器
        table_selectors = self.config.get("article_list.table_selectors")
        row_selectors = self.config.get("article_list.row_selectors")

        logger.info("🔄 等待表格内容加载...")

        # 检查页面中的表格数量
        table_count = await frame_locator.locator('.datagrid-btable').count()
        logger.debug(f"🔍 页面中共有 {table_count} 个 datagrid-btable")

        # 遍历所有表格，找出包含数据的那个
        for i in range(table_count):
            table = frame_locator.locator('.datagrid-btable').nth(i)
            html = await table.evaluate('el => el.outerHTML')

            has_title = 'field="title"' in html
            has_data = 'datagrid-row' in html
            logger.debug(f"  表格{i}: 长度={len(html)}, has_title={has_title}, has_datagrid_row={has_data}")

            if has_title:
                logger.success(f"  ✅ 找到包含title的表格（索引{i}）")

                # 定位该表格的行
                for row_selector in row_selectors:
                    try:
                        row_locator = table.locator(row_selector["css"])
                        count = await row_locator.count()
                        if count > 0:
                            logger.debug(f"  ✅ 该表格有 {count} 行")
                            return row_locator
                    except Exception as e:
                        logger.warning(f"  ⚠️ 行定位失败: {e}")
                        continue

        # 兜底方案
        logger.warning("⚠️ 使用兜底方案...")
        for i in range(table_count):
            table = frame_locator.locator('.datagrid-btable').nth(i)
            row_locator = table.locator('tr.datagrid-row')
            count = await row_locator.count()
            if count > 0:
                logger.success(f"✅ 兜底找到表格{i}，共{count}行")
                return row_locator

        return None

    async def _extract_single_article(self, current_row, remaining_titles: List[str]) -> Optional[Dict]:
        """提取单篇文章数据"""
        # 1. 提取标题
        try:
            title_cell = current_row.locator('td[field="title"]')
            raw_title = await title_cell.text_content()
            current_title = raw_title.lstrip() if raw_title else ""
            logger.debug(f"🔍 第N篇标题: {current_title[:30]}...")
        except Exception as e:
            logger.warning(f"⚠️ 标题提取失败: {e}")
            return None

        # 2. 匹配检查
        if not self.title_matcher.match(current_title, remaining_titles):
            logger.debug(f"  ⏭️ 跳过（不在目标列表中）")
            return None

        logger.success(f"🎯 匹配到目标文章：{current_title}")

        # 3. 提取数据（标准9字段，数据字典统一使用publish_time）
        article_data = {
            "platform": "极客公园ZAKER号",
            "title": current_title,
            "url": "",
            "publish_time": "",  # 统一使用 publish_time（选择器 td[field="addtime"] 保持不变）
            "read_count": 0,
            "like_count": "/",    # ZAKER平台不提供此字段
            "comment_count": "/", # ZAKER平台不提供此字段
            "share_count": "/",   # ZAKER平台不提供此字段
            "crawl_time": ""      # GUI调用时填充
        }

        # 提取发布日期（选择器使用 addtime 保持不变）
        try:
            date_locator = current_row.locator('td[field="addtime"]')
            date_text = await date_locator.text_content()
            article_data["publish_time"] = date_text.strip()[:10] if date_text else "/"
        except Exception as e:
            logger.warning(f"  ⚠️ 发布时间提取失败: {e}")
            article_data["publish_time"] = "/"

        # 提取阅读数
        try:
            count_locator = current_row.locator('td[field="beautify_pv"]')
            count_text = await count_locator.text_content()
            article_data["read_count"] = int(count_text.strip()) if count_text and count_text.strip().isdigit() else 0
        except Exception as e:
            logger.warning(f"  ⚠️ 阅读数提取失败: {e}")

        # 提取URL
        try:
            view_btn = current_row.locator('td[field="action"] a:has-text("查看")')
            onclick_text = await view_btn.get_attribute("onclick")
            if onclick_text:
                match = re.search(r"viewArticle\(\s*'([a-f0-9]+)'\s*,", onclick_text)
                if match:
                    article_id = match.group(1)
                    article_data["url"] = f"https://app.myzaker.com/article/{article_id}"
                else:
                    logger.warning(f"  ⚠️ 未提取到文章ID")
        except Exception as e:
            logger.warning(f"  ⚠️ URL提取失败: {e}")

        # 打印提取到的文章数据
        logger.info(f"  📰 标题: {article_data['title']}")
        logger.info(f"  📅 日期: {article_data['publish_time']}")
        logger.info(f"  👀 阅读: {article_data['read_count']}")
        logger.info(f"  🔗 URL: {article_data['url']}")

        return article_data


# ==================== 主逻辑函数 ====================
async def run(p: Playwright, headless: bool = False) -> None:
    """独立运行入口

    Args:
        p: Playwright 对象
        headless: 是否使用无头模式，默认False（显示浏览器）
    """
    context = None
    browser = None
    try:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--start-maximized"]
        )

        # 初始化工具类
        anti_spider = AntiSpiderHelper(config)
        retry_manager = RetryManager(config)

        # 创建核心管理器实例
        login_mgr = LoginManager(config, anti_spider, retry_manager)
        nav_mgr = NavigationManager(config)
        csv_exporter = CSVExporter(config)
        article_extractor = ArticleListExtractor(config)

        context_args = {"viewport": None}
        if ENABLE_USER_AGENT_RANDOM:
            user_agent = anti_spider.get_random_user_agent()
            context_args["user_agent"] = user_agent
            logger.info(f"🌐 使用随机User-Agent: {user_agent[:50]}...")

        # 检查是否有保存的登录态
        storage_state = login_mgr.load_storage_state()
        if storage_state:
            context_args["storage_state"] = storage_state

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        # 核心修复：手动给 page 应用反检测（适配 v2.0.2）
        if ENABLE_STEALTH:
            logger.info("🔒 启用 playwright-stealth 隐藏浏览器指纹...")
            stealth = Stealth()
            await stealth.apply_stealth_async(page)  # 注意：是给 page 应用，不是 context

        # 使用LoginManager确保登录状态
        if storage_state:
            logger.info(f"🍪 检测到保存的登录态: {COOKIE_FILE}")
            logger.info("🔍 验证登录态是否有效...")

        # 统一使用ensure_login方法处理所有登录场景
        login_success = await login_mgr.ensure_login(page, context)
        if not login_success:
            logger.error("❌ 登录失败，程序终止")
            if os.path.exists(COOKIE_FILE):
                os.remove(COOKIE_FILE)
                logger.info("🗑️ 已删除失效的登录态文件")
            return

        # ===== 侧边栏导航：内容管理 → 极客公园 =====
        if not await nav_mgr.navigate_to_article_list(page):
            logger.error("❌ 导航失败，程序终止")
            return

        # 执行采集（使用调试目标列表）
        remaining, extracted_articles = await article_extractor.extract_articles(page, DEBUG_TARGETS)

        # 导出采集的数据到CSV
        if extracted_articles:
            # 填充 crawl_time 字段
            for article in extracted_articles:
                article["crawl_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            logger.info("\n" + "="*50)
            logger.info("📊 数据采集完成！")
            logger.info("="*50)
            logger.info(f"📋 共采集文章: {len(extracted_articles)} 篇")
            for i, article in enumerate(extracted_articles, 1):
                logger.info(f"   {i}. {article['title'][:40]}...")
                logger.info(f"      📅 {article['publish_time']} | 👀 {article['read_count']}")

            logger.info("\n📤 正在导出到CSV文件...")
            export_success = await csv_exporter.save_articles(extracted_articles)

            if export_success:
                logger.info("\n" + "="*50)
                logger.info("✅ CSV导出成功！")
                logger.info("="*50)
                logger.info(f"📂 文件位置: data/")
                logger.info(f"📝 文件名格式: articles_YYYYMMDD_HHMMSS.csv")
                logger.info("💡 可直接在Excel中打开查看")
            else:
                logger.error("❌ CSV导出失败，请检查日志")
        else:
            logger.warning("⚠️ 没有采集到数据，跳过导出")

        logger.info("\n📌 按回车键关闭浏览器并保存登录态...")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await login_mgr.save_storage_state(context)

    except Exception as e:
        logger.error(f"❌ 程序执行异常: {e}", exc_info=e)
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()


# ==================== 入口函数（彻底放弃 Stealth 上下文管理器） ====================
async def main():
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 直接使用原生 Playwright，不使用 Stealth 的 use_async
    async with async_playwright() as p:
        await run(p)


if __name__ == "__main__":
    asyncio.run(main())


# ==================== GUI调用接口 ====================
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
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    # 限制目标数量
    targets = targets[:5]

    success_data = []
    remaining_targets = targets.copy()
    browser = None
    context = None
    p = None

    try:
        # 启动浏览器
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=headless)

        # 设置上下文参数
        context_args = {"viewport": None}

        # 检查并加载Cookie
        cookie_file = COOKIES_DIR / "zaker.json"

        if cookie_file.exists():
            context_args["storage_state"] = str(cookie_file)

        # 随机UA
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
            logger.error("❌ ZAKER登录失败")
            # 通知登录失败，让调度器加入手动登录队列
            if login_failed_callback:
                login_failed_callback("zaker")
            return [], targets

        # 导航到文章列表
        if not await nav_mgr.navigate_to_article_list(page):
            logger.error("❌ ZAKER导航失败")
            return [], targets

        # 提取文章数据
        remaining, extracted = await article_extractor.extract_articles(page, targets)
        remaining_targets = remaining

        # 处理结果
        for data in extracted:
            # 添加平台和时间戳
            data['platform'] = "极客公园ZAKER号"
            data['crawl_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            success_data.append(data)

            # 回调
            if result_callback:
                result_callback(data)

        # 未匹配
        for target in remaining:
            if unmatched_callback:
                unmatched_callback(target)

        # 保存登录态
        await login_mgr.save_storage_state(context)

        return success_data, remaining

    except Exception as e:
        logger.error(f"❌ ZAKER采集异常: {e}")
        return success_data, remaining_targets

    finally:
        from utils.playwright_cleanup import shutdown_chromium_session

        await shutdown_chromium_session(
            playwright=p, context=context, browser=browser, log_label="zaker"
        )


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("zaker", None, "ZAKER", is_stable=True)

