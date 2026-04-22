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
极客公园微信公众号采集器
基于扫码登录方式实现 - 终极反检测版本
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
import time
from typing import Callable, Type, Tuple, Optional, Any, List, Dict
from utils.title_matcher import match_any_target
from utils.env_loader import get_platform_credentials_with_fallback
from utils.path_helper import PROJECT_ROOT, PLATFORMS_DIR, COOKIES_DIR, LOGS_DIR, DATA_DIR
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Playwright, TimeoutError as PlaywrightTimeoutError
from loguru import logger


# ============================================================
# 1. 配置加载模块
# ============================================================

class ConfigLoader:
    """加载平台配置（YAML），提供点号路径访问方法"""

    def __init__(self, platform: str = "geekpark_wechat"):
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
        logger.info("开始验证配置文件...")
        required_sections = {
            'login': ['type', 'qrcode_wait_timeout', 'check_interval'],
            'verify_logged_in_selectors': [],
            'navigation': ['steps'],
            'article_list': ['list_container', 'list_row', 'field_selectors'],
            'pagination': ['type', 'max_pages'],
        }

        missing_fields = []
        for section, fields in required_sections.items():
            if section not in self.config:
                logger.warning(f"配置段缺失: {section}")
                missing_fields.append(section)
                continue
            for field in fields:
                if field not in self.config[section]:
                    missing_fields.append(f"{section}.{field}")

        if missing_fields:
            logger.warning(f"配置项缺失: {', '.join(missing_fields)}")
        else:
            logger.success("配置验证完成，所有必需字段存在")

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
    str(log_dir / "geekpark_wechat_crawler_{time:YYYY-MM-DD}.log"),
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

PLATFORM_NAME = "geekpark_wechat"
config = ConfigLoader(PLATFORM_NAME)

# 从环境变量加载账号密码（扫码登录时可能为空值）
USERNAME, PASSWORD = get_platform_credentials_with_fallback(PLATFORM_NAME, config)

# 从配置中获取常用值
MAIN_URL = config.get("main_url", "")
LOGIN_URL = config.get("login_url", "")

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
RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    PlaywrightTimeoutError,
    TimeoutError,
    ConnectionError,
    OSError,
)

# 用户代理配置
USER_AGENTS: List[str] = config.get("user_agents", [])
TYPING_DELAY_MIN = config.get("typing_delay_min", 50)
TYPING_DELAY_MAX = config.get("typing_delay_max", 150)

# 功能开关
ENABLE_STEALTH = config.get("enable_stealth", True)
ENABLE_USER_AGENT_RANDOM = config.get("enable_user_agent_random", True)

# 随机视口配置（反指纹）
VIEWPORT_SIZES = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
]

# 随机设备缩放因子（反指纹）
DEVICE_SCALES = [1, 1.25, 1.5]


def build_browser_context_args() -> Dict[str, Any]:
    """
    构建浏览器上下文参数（终极反指纹配置）
    """
    viewport = random.choice(VIEWPORT_SIZES)
    device_scale = random.choice(DEVICE_SCALES)

    # 获取UA配置（优先使用真实浏览器UA）
    user_agents = config.get("user_agents", [])
    if not user_agents:
        # 预设高可信度UA列表（与Chrome版本匹配）
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
    user_agent = random.choice(user_agents)

    # 构建上下文参数
    context_args = {
        "viewport": viewport,
        "device_scale_factor": device_scale,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "permissions": [],
        "user_agent": user_agent,
        "java_script_enabled": True,
        "bypass_csp": True,
        "accept_downloads": False,
    }

    # 如果有Cookie文件，加载它
    if COOKIE_FILE.exists():
        context_args["storage_state"] = str(COOKIE_FILE)
        logger.info("已加载现有Cookie文件")

    # 添加代理配置
    proxy_config = config.get("proxy")
    if proxy_config and proxy_config.get("enabled", False):
        proxy_server = proxy_config.get("server")
        if proxy_server:
            context_args["proxy"] = {
                "server": proxy_server
            }
            # 如果有用户名和密码
            proxy_username = proxy_config.get("username")
            proxy_password = proxy_config.get("password")
            if proxy_username and proxy_password:
                context_args["proxy"]["username"] = proxy_username
                context_args["proxy"]["password"] = proxy_password
            logger.info(f"已启用代理: {proxy_server}")

    return context_args


# 终极反指纹初始化脚本 - 在页面创建前注入
ULTIMATE_STEALTH_SCRIPT = """
(() => {
    // ========== 核心：隐藏所有自动化痕迹 ==========
    
    // 1. 彻底移除 webdriver 标志
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
        enumerable: true
    });
    
    // 从原型链上彻底删除
    if (navigator.__proto__) {
        delete navigator.__proto__.webdriver;
    }
    
    // 2. 伪造完整的 plugins 对象
    const fakePlugins = [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', version: undefined, length: 1, item: function(idx) { return this[idx]; }, namedItem: function(name) { return this[name]; }},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format', version: undefined, length: 1, item: function(idx) { return this[idx]; }, namedItem: function(name) { return this[name]; }},
        {name: 'Native Client', filename: 'internal-nacl-plugin', description: '', version: undefined, length: 2, item: function(idx) { return this[idx]; }, namedItem: function(name) { return this[name]; }},
        {name: 'Widevine Content Decryption Module', filename: 'widevinecdmadapter.dll', description: 'Widevine Content Decryption Module', version: undefined, length: 0, item: function(idx) { return this[idx]; }, namedItem: function(name) { return this[name]; }}
    ];
    
    fakePlugins.length = fakePlugins.length;
    fakePlugins.item = function(idx) { return this[idx]; };
    fakePlugins.namedItem = function(name) { return this[name]; };
    fakePlugins.refresh = function() {};
    
    Object.defineProperty(navigator, 'plugins', {
        get: () => fakePlugins,
        configurable: true,
        enumerable: true
    });
    
    // 3. 伪造 mimeTypes
    const fakeMimeTypes = [
        {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: fakePlugins[0]},
        {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: fakePlugins[1]},
        {type: 'application/x-nacl', suffixes: '', description: 'Native Client module', enabledPlugin: fakePlugins[2]},
        {type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client module', enabledPlugin: fakePlugins[2]}
    ];
    
    fakeMimeTypes.length = fakeMimeTypes.length;
    fakeMimeTypes.item = function(idx) { return this[idx]; };
    fakeMimeTypes.namedItem = function(name) { return this[name]; };
    
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => fakeMimeTypes,
        configurable: true,
        enumerable: true
    });
    
    // 4. 伪造 languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en-US', 'en'],
        configurable: true,
        enumerable: true
    });
    
    // 5. 伪造 platform
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
        configurable: true,
        enumerable: true
    });
    
    // 6. 伪造 deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true,
        enumerable: true
    });
    
    // 7. 伪造 hardwareConcurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true,
        enumerable: true
    });
    
    // 8. 伪造 maxTouchPoints
    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 0,
        configurable: true,
        enumerable: true
    });
    
    // 9. 伪造 Chrome 对象
    window.chrome = {
        runtime: {
            OnInstalledReason: {CHROME_UPDATE: "chrome_update", EXTENSION_UPDATE: "extension_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update"},
            OnRestartRequiredReason: {APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic"},
            PlatformArch: {ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64"},
            PlatformNaclArch: {ARM: "arm", MIPS: "mips", MIPS64: "mips64", MIPS64EL: "mips64el", MIPSEL: "mipsel", X86_32: "x86-32", X86_64: "x86-64"},
            PlatformOs: {ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win"},
            RequestUpdateCheckStatus: {NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available"}
        },
        app: {
            isInstalled: false,
            InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
            RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'}
        },
        csi: function() {},
        loadTimes: function() {}
    };
    
    // 10. 伪装 Notification 权限
    if (window.Notification) {
        Object.defineProperty(Notification, 'permission', {
            get: () => 'default',
            configurable: true
        });
    }
    
    // 11. 伪造 permissions API
    const originalQuery = window.navigator.permissions?.query;
    if (originalQuery) {
        window.navigator.permissions.query = function(parameters) {
            if (parameters.name === 'notifications') {
                return Promise.resolve({state: 'default', onchange: null});
            }
            if (parameters.name === 'clipboard-read' || parameters.name === 'clipboard-write') {
                return Promise.resolve({state: 'prompt', onchange: null});
            }
            return originalQuery.call(window.navigator.permissions, parameters);
        };
    }
    
    // 12. 隐藏 Playwright 特殊属性
    delete window.__playwright;
    delete window.__pw_manual;
    delete window.__pw_scripts;
    
    // 13. 伪造 WebGL 指纹
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };
    
    // 14. 伪造 Canvas 指纹（添加微小噪声）
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        if (type === 'image/png' && this.width > 100 && this.height > 100) {
            const ctx = this.getContext('2d');
            if (ctx) {
                ctx.fillStyle = `rgba(${Math.floor(Math.random()*5)}, ${Math.floor(Math.random()*5)}, ${Math.floor(Math.random()*5)}, 0.005)`;
                ctx.fillRect(0, 0, 1, 1);
            }
        }
        return originalToDataURL.call(this, type);
    };
    
    // 15. 覆盖 toString 方法隐藏痕迹
    const originalToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === window.navigator.permissions?.query) {
            return 'function query() { [native code] }';
        }
        return originalToString.call(this);
    };
    
    // 16. 伪造 battery API
    if ('getBattery' in navigator) {
        navigator.getBattery = function() {
            return Promise.resolve({
                charging: true,
                chargingTime: 0,
                dischargingTime: Infinity,
                level: 1,
                addEventListener: function() {},
                removeEventListener: function() {}
            });
        };
    }
    
    // 17. 伪造 connection API
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 10,
            saveData: false,
            addEventListener: function() {},
            removeEventListener: function() {}
        }),
        configurable: true,
        enumerable: true
    });
    
    // 18. 伪装 screen 对象
    Object.defineProperty(screen, 'availWidth', { get: () => screen.width });
    Object.defineProperty(screen, 'availHeight', { get: () => screen.height });
    Object.defineProperty(screen, 'availLeft', { get: () => 0 });
    Object.defineProperty(screen, 'availTop', { get: () => 0 });
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
    
    // 19. 移除自动化相关属性（再次确保）
    if (navigator.__proto__) {
        delete navigator.__proto__.webdriver;
    }
    
    // 20. 伪装 console.debug（某些检测会检查）
    const originalDebug = console.debug;
    console.debug = function(...args) {
        if (args[0] && typeof args[0] === 'string' && args[0].includes('playwright')) {
            return;
        }
        return originalDebug.apply(console, args);
    };
})();
"""


async def inject_stealth_scripts(context):
    """
    向浏览器上下文注入终极反指纹脚本
    必须在创建任何页面之前调用
    """
    try:
        # 通过 add_init_script 注入，每个新页面都会自动执行
        await context.add_init_script(ULTIMATE_STEALTH_SCRIPT)
        logger.success("终极反指纹脚本已注入")
    except Exception as e:
        logger.warning(f"注入反指纹脚本失败: {e}")


# ============================================================
# 2. 通用工具模块
# ============================================================

class AntiSpiderHelper:
    """反爬工具：随机UA、人类输入模拟、浏览器指纹伪装等"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def random_delay(self, min_ms: int = None, max_ms: int = None):
        """随机延迟"""
        min_delay = min_ms or self.config.get("action_delay_min", 1000)
        max_delay = max_ms or self.config.get("action_delay_max", 3000)
        delay = random.uniform(min_delay, max_delay) / 1000
        await asyncio.sleep(delay)

    async def human_like_click(self, page, locator, random_offset: bool = True):
        """人类模拟点击"""
        await self.random_delay(500, 1500)
        box = await locator.bounding_box()
        if box and random_offset:
            x = box["x"] + random.uniform(5, box["width"] - 5)
            y = box["y"] + random.uniform(5, box["height"] - 5)
            steps = random.randint(5, 10)
            await page.mouse.move(x, y, steps=steps)
        await locator.click()
        await self.random_delay(1000, 2500)

    async def human_like_scroll(self, page, distance: int = None):
        """人类模拟滚动"""
        if not distance:
            distance = random.randint(300, 800)
        steps = random.randint(5, 10)
        step_distance = distance / steps
        for _ in range(steps):
            await page.evaluate(f"window.scrollBy(0, {step_distance})")
            await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.random_delay(500, 1000)


class RetryManager:
    """重试装饰器"""

    def __init__(self, config: ConfigLoader):
        self.config = config
        self.retryable_exceptions = RETRYABLE_EXCEPTIONS

    def get_random_retry_delay(self) -> int:
        return random.randint(
            self.config.get("retry_delay_min", 3000),
            self.config.get("retry_delay_max", 5000)
        )


# ============================================================
# 3. 登录模块（扫码登录专用）
# ============================================================

class LoginManager:
    """管理扫码登录流程：Cookie加载/保存、登录态验证"""

    def __init__(self, config: ConfigLoader, anti_spider: AntiSpiderHelper, retry_manager: RetryManager):
        self.config = config
        self.anti_spider = anti_spider
        self.retry_manager = retry_manager
        self.platform = config.platform

        self.login_url = self.config.get("login_url", "")
        self.main_url = self.config.get("main_url", "")
        self.qrcode_wait_timeout = self.config.get("login.qrcode_wait_timeout", 300000)
        self.check_interval = self.config.get("login.check_interval", 15000)

    async def is_logged_in(self, page, allow_redirect: bool = True) -> bool:
        """
        验证当前登录态是否有效
        
        Args:
            page: 页面对象
            allow_redirect: 是否允许跳转到主页验证（扫码过程中应设为False）
        """
        try:
            current_url = page.url
            logger.debug(f"当前页面URL: {current_url}")
            
            # 检测1: 如果当前在扫码登录页面，直接返回False（未登录）
            if page.url == "https://mp.weixin.qq.com/" or "/loginpage" in page.url:
                try:
                    qrcode_selectors = self.config.get("login.qrcode_selectors", [])
                    for selector_config in qrcode_selectors:
                        locator = self._get_locator(page, selector_config)
                        if locator:
                            is_visible = await locator.is_visible(timeout=3000)
                            if is_visible:
                                logger.info("当前在扫码登录页面，等待扫码...")
                                return False
                except Exception:
                    pass
            
            # 检测2: 如果在Cookie失效页面，返回False
            try:
                cookie_expired_selectors = self.config.get("cookie_expired_selectors", [])
                for selector_config in cookie_expired_selectors:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            logger.info("检测到Cookie失效页面")
                            return False
            except Exception:
                pass

            # 检测3: 检查登录成功特征元素（当前页面）
            verify_selectors = self.config.get("verify_logged_in_selectors", [])
            for selector_config in verify_selectors:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        is_visible = await locator.is_visible(timeout=5000)
                        if is_visible:
                            logger.success("登录态验证通过（当前页面）")
                            return True
                except Exception:
                    continue
            
            # 检测4: 如果需要，跳转到主页验证（扫码过程不应执行）
            if allow_redirect and self.main_url not in page.url:
                logger.info(f"当前页面未检测到登录特征，尝试跳转主页验证...")
                await page.goto(self.main_url, wait_until="networkidle", timeout=self.config.get("default_timeout", 30000))
                
                # 重新检测Cookie失效
                try:
                    cookie_expired_selectors = self.config.get("cookie_expired_selectors", [])
                    for selector_config in cookie_expired_selectors:
                        locator = self._get_locator(page, selector_config)
                        if locator:
                            is_visible = await locator.is_visible(timeout=3000)
                            if is_visible:
                                logger.info("跳转到主页后检测到Cookie失效")
                                return False
                except Exception:
                    pass
                
                # 重新检测登录特征
                for selector_config in verify_selectors:
                    try:
                        locator = self._get_locator(page, selector_config)
                        if locator:
                            is_visible = await locator.is_visible(timeout=5000)
                            if is_visible:
                                logger.success("跳转到主页后验证登录态通过")
                                return True
                    except Exception:
                        continue

            logger.warning("未检测到登录成功特征，判定为未登录")
            return False

        except Exception as e:
            logger.warning(f"验证登录态失败: {e}")
            return False

    async def _capture_debug_info(self, page, stage: str):
        """捕获调试信息：截图和页面内容"""
        try:
            debug_dir = PROJECT_ROOT / "debug"
            debug_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # 截图
            screenshot_path = debug_dir / f"{stage}_{timestamp}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"调试截图已保存: {screenshot_path}")
            
            # 保存页面HTML
            html_path = debug_dir / f"{stage}_{timestamp}.html"
            html_content = await page.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"调试HTML已保存: {html_path}")
            
            # 输出页面基本信息
            logger.info(f"当前URL: {page.url}")
            logger.info(f"页面标题: {await page.title()}")
            
        except Exception as e:
            logger.debug(f"捕获调试信息失败: {e}")

    async def perform_login(self, page, context) -> bool:
        """
        执行扫码登录流程
        """
        try:
            logger.info("开始扫码登录流程...")
            
            # 使用更自然的页面加载方式
            await page.goto(self.login_url, timeout=self.config.get("default_timeout", 30000))
            
            # 等待页面完全加载（包括JS渲染）
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)  # 额外等待确保JS执行完成
            
            # 捕获调试信息
            await self._capture_debug_info(page, "login_page_loaded")
            
            # 等待二维码加载
            try:
                qrcode_selectors = self.config.get("login.qrcode_selectors", [])
                for selector in qrcode_selectors:
                    try:
                        locator = self._get_locator(page, selector)
                        if locator:
                            await locator.wait_for(state="visible", timeout=15000)
                            logger.success("二维码已加载")
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"等待二维码加载超时: {e}")

            # 提示用户扫码
            logger.info("=" * 60)
            logger.info("请使用微信扫描二维码登录公众平台")
            logger.info(f"等待时间: {self.qrcode_wait_timeout / 1000}秒")
            logger.info("=" * 60)

            # 循环检测登录态
            start_time = time.time()
            check_count = 0

            while (time.time() - start_time) * 1000 < self.qrcode_wait_timeout:
                check_count += 1
                logger.info(f"第 {check_count} 次检测登录状态...")

                # 检查页面是否已从扫码页跳转（扫码成功后页面会自动跳转）
                current_url = page.url
                logger.debug(f"当前页面URL: {current_url}")

                # 如果页面已跳转到主页或其他非登录页，说明扫码成功
                if "/cgi-bin/home" in current_url or "/cgi-bin/appmsgpublish" in current_url:
                    logger.success("检测到页面已跳转到登录后页面，扫码登录成功！")
                    await self._capture_debug_info(page, "login_success_redirect")
                    await self.save_storage_state(context)
                    return True

                # 如果仍在扫码页，检查是否已登录（Cookie立即生效的情况）
                if "/loginpage" in current_url or current_url == "https://mp.weixin.qq.com/":
                    # 扫码页等待中，检查是否有登录成功特征（可能无需跳转就已登录）
                    is_logged_in = await self._check_login_indicators(page)
                    if is_logged_in:
                        logger.success("在扫码页检测到登录特征，登录成功！")
                        await self._capture_debug_info(page, "login_success_on_qrcode_page")
                        await self.save_storage_state(context)
                        return True
                else:
                    # 页面跳转到其他URL，可能是登录成功
                    logger.info(f"页面跳转到: {current_url}，尝试验证登录状态...")
                    if await self._check_login_indicators(page):
                        logger.success("页面跳转后检测到登录特征，登录成功！")
                        await self.save_storage_state(context)
                        return True

                await asyncio.sleep(self.check_interval / 1000)

            raise TimeoutError(f"扫码登录超时（{self.qrcode_wait_timeout / 1000}秒）")

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
        logger.success(f"登录态已保存到 {filepath}")

    def load_storage_state(self, filepath=None) -> Optional[str]:
        """加载保存的浏览器状态"""
        if filepath is None:
            filepath = COOKIE_FILE
        if os.path.exists(filepath):
            return filepath
        return None

    async def is_cookie_expired_page(self, page) -> bool:
        """检测是否为Cookie失效页面"""
        try:
            heading = await page.locator('heading[level="2"]').inner_text(timeout=3000)
            if "登录超时" in heading or "请重新登录" in heading:
                return True

            page_text = await page.inner_text('body')
            if "登录超时， 请重新登录" in page_text or "请重新登录" in page_text:
                return True

            return False
        except Exception:
            return False

    async def _check_login_indicators(self, page) -> bool:
        """
        检查页面是否有登录成功的特征元素
        用于扫码后检测登录状态（不强制跳转）
        """
        try:
            # 检测登录成功特征元素
            verify_selectors = self.config.get("verify_logged_in_selectors", [])
            for selector_config in verify_selectors:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            return True
                except Exception:
                    continue

            # 检测是否已到达登录后的页面URL
            current_url = page.url
            if "/cgi-bin/home" in current_url or "/cgi-bin/appmsgpublish" in current_url:
                return True

            return False
        except Exception:
            return False

    async def click_login_on_expired_page(self, page) -> bool:
        """在Cookie失效页面上点击'登录'按钮"""
        try:
            logger.info("检测到Cookie失效页面，尝试点击登录按钮...")

            cookie_expired_login_selectors = self.config.get("cookie_invalid_check.relogin_button_selectors", [])

            for selector_config in cookie_expired_login_selectors:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        await locator.wait_for(state="visible", timeout=5000)
                        await locator.click()
                        logger.info("已点击登录按钮")
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(2)
                        return True
                except Exception as e:
                    logger.debug(f"选择器点击失败: {selector_config}, 错误: {e}")
                    continue

            # 备用方案
            try:
                login_link = page.get_by_role("link", name="登录")
                await login_link.click()
                logger.info("通过role定位已点击登录按钮")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)
                return True
            except Exception:
                pass

            logger.error("点击登录按钮失败")
            return False
        except Exception as e:
            logger.error(f"点击登录按钮异常: {e}")
            return False

    async def ensure_login(self, page, context, headless: bool = True) -> bool:
        """
        确保登录状态（严格按配置执行 - 优化版）

        Args:
            page: Playwright page 对象
            context: Playwright browser context
            headless: 是否使用无头模式
                - True (GUI自动采集模式): Cookie失效时直接返回False，由调度器加入手动登录队列
                - False (单机运行/手动登录模式): 保持原逻辑，执行perform_login扫码登录

        执行逻辑：
        1. Cookie登录，加载目标页面，进行页面状态检测
        2. 分支判断：
           - 分支1：直接进入登录后目标页面 → Cookie有效，启动采集
           - 分支2：弹出「登录超时，请重新登录」界面 → 点击「登录」按钮
        3. 点击「登录」后二次分支：
           - 情况A：跳转至扫码登录界面 → Cookie失效，重新获取
           - 情况B：跳转至目标页面 → Cookie仍有效，恢复采集
        """
        logger.info("=" * 60)
        logger.info("开始执行Cookie登录校验流程...")
        logger.info("=" * 60)

        # ========== 步骤1: 加载目标页面并检测状态 ==========
        logger.info(f"步骤1: 加载目标页面: {self.main_url}")
        try:
            await page.goto(self.main_url, wait_until="networkidle", timeout=self.config.get("default_timeout", 30000))
            await asyncio.sleep(2)  # 等待页面稳定
            logger.info(f"页面加载完成，当前URL: {page.url}")
        except Exception as e:
            logger.warning(f"加载目标页面失败: {e}")

        # ========== 步骤2: 页面状态分支判断 ==========
        logger.info("步骤2: 检测页面状态...")

        # 分支1: 检测是否已进入登录后页面（Cookie有效）
        if await self._check_logged_in_indicators(page):
            logger.success("【分支1】检测到登录成功特征，Cookie有效，直接进入采集流程")
            return True

        # 分支2: 检测是否为「登录超时，请重新登录」弹窗界面
        if await self._check_cookie_expired_page(page):
            logger.info("【分支2】检测到「登录超时，请重新登录」弹窗界面")
            logger.info("准备点击「登录」按钮...")

            # 点击「登录」按钮
            click_success = await self._click_relogin_button(page)

            if not click_success:
                logger.error("点击「登录」按钮失败")
                # 仅针对GUI自动采集场景：停止自动登录流程
                if headless:
                    logger.warning("GUI自动采集模式：Cookie失效，停止自动登录流程，等待手动登录")
                    return False
                # 单机运行/手动模式：执行扫码登录
                logger.error("尝试直接扫码登录...")
                return await self.perform_login(page, context)

            logger.info("已点击「登录」按钮，等待页面跳转...")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # ========== 步骤3: 点击后二次分支判断 ==========
            logger.info("步骤3: 检测点击「登录」后的跳转结果...")
            current_url = page.url
            logger.info(f"当前页面URL: {current_url}")

            # 情况A: 跳转至扫码登录/初始登录界面 → Cookie彻底失效
            if current_url == "https://mp.weixin.qq.com/" or "/loginpage" in current_url:
                logger.info("【情况A】已跳转至扫码登录界面，判定Cookie彻底失效")
                # 仅针对GUI自动采集场景：停止自动登录流程
                if headless:
                    logger.warning("GUI自动采集模式：Cookie失效，停止自动登录流程，等待手动登录")
                    return False
                # 单机运行/手动模式：执行扫码登录
                logger.info("执行Cookie重新获取流程（扫码登录）...")
                login_success = await self.perform_login(page, context)
                if login_success:
                    await self.save_storage_state(context)
                return login_success

            # 情况B: 直接跳转至目标页面 → Cookie仍有效
            if await self._check_logged_in_indicators(page):
                logger.success("【情况B】点击后检测到登录成功特征，Cookie仍有效")
                logger.success("恢复正常导航与数据采集流程")
                return True

            # 其他未知情况
            logger.warning("跳转结果未知，尝试检测登录状态...")
            if await self.is_logged_in(page, allow_redirect=False):
                logger.success("检测到已登录状态")
                return True

            logger.warning("未检测到登录状态")
            # 仅针对GUI自动采集场景：停止自动登录流程
            if headless:
                logger.warning("GUI自动采集模式：Cookie失效，停止自动登录流程，等待手动登录")
                return False
            # 单机运行/手动模式：执行扫码登录
            logger.warning("尝试扫码登录...")
            login_success = await self.perform_login(page, context)
            if login_success:
                await self.save_storage_state(context)
            return login_success

        # 未识别到已知页面状态，尝试通用登录检测
        logger.info("未识别到特定页面状态，执行通用登录检测...")
        if await self.is_logged_in(page, allow_redirect=True):
            logger.success("通用检测通过，已处于登录状态")
            return True

        # 最终兜底
        # 仅针对GUI自动采集场景：停止自动登录流程
        if headless:
            logger.warning("GUI自动采集模式：Cookie失效，停止自动登录流程，等待手动登录")
            return False

        # 单机运行/手动模式：执行扫码登录（保持原逻辑）
        logger.info("Cookie登录失败，执行直接扫码登录...")
        login_success = await self.perform_login(page, context)
        if login_success:
            await self.save_storage_state(context)
        return login_success

    async def _check_cookie_expired_page(self, page) -> bool:
        """
        检测是否为「登录超时，请重新登录」弹窗界面
        严格依据配置文件: cookie_invalid_check
        """
        try:
            # 使用配置中的文本特征检测
            invalid_text_indicators = self.config.get("cookie_invalid_check.invalid_text_indicators", [])
            if invalid_text_indicators:
                page_text = await page.inner_text('body', timeout=5000)
                for indicator in invalid_text_indicators:
                    if indicator in page_text:
                        logger.debug(f"检测到Cookie失效文本特征: {indicator}")
                        return True

            # 使用配置中的heading选择器检测
            invalid_heading_selectors = self.config.get("cookie_invalid_check.invalid_heading_selectors", [])
            for selector_config in invalid_heading_selectors:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            logger.debug(f"检测到Cookie失效heading选择器: {selector_config}")
                            return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug(f"检测Cookie失效页面失败: {e}")
            return False

    async def _click_relogin_button(self, page) -> bool:
        """
        点击「登录超时，请重新登录」界面上的「登录」按钮
        严格依据配置文件: cookie_invalid_check.relogin_button_selectors
        """
        try:
            relogin_button_selectors = self.config.get("cookie_invalid_check.relogin_button_selectors", [])

            for selector_config in relogin_button_selectors:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        # 等待元素可见并点击
                        await locator.wait_for(state="visible", timeout=5000)
                        await locator.click()
                        logger.info(f"已通过选择器点击「登录」按钮: {selector_config}")
                        return True
                except Exception as e:
                    logger.debug(f"选择器点击失败: {selector_config}, 错误: {e}")
                    continue

            logger.error("所有配置的「登录」按钮选择器均点击失败")
            return False
        except Exception as e:
            logger.error(f"点击「登录」按钮异常: {e}")
            return False

    async def _check_logged_in_indicators(self, page) -> bool:
        """
        检测登录成功特征（用于分支1和情况B）
        严格依据配置文件: cookie_invalid_check.logged_in_indicators 和 verify_logged_in_selectors
        """
        try:
            # 首先检查URL特征
            success_url_contains = self.config.get("login_check.success_url_contains", "/cgi-bin/home")
            if success_url_contains and success_url_contains in page.url:
                logger.debug(f"检测到登录成功URL特征: {success_url_contains}")

            # 检查登录成功元素（使用配置的反向特征）
            logged_in_indicators = self.config.get("cookie_invalid_check.logged_in_indicators", [])
            for selector_config in logged_in_indicators:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            logger.debug(f"检测到登录成功特征元素: {selector_config}")
                            return True
                except Exception:
                    continue

            # 额外检查verify_logged_in_selectors
            verify_selectors = self.config.get("verify_logged_in_selectors", [])
            for selector_config in verify_selectors:
                try:
                    locator = self._get_locator(page, selector_config)
                    if locator:
                        is_visible = await locator.is_visible(timeout=3000)
                        if is_visible:
                            logger.debug(f"检测到登录验证选择器: {selector_config}")
                            return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug(f"检测登录成功特征失败: {e}")
            return False

    async def _capture_debug_info(self, page, stage: str):
        """捕获调试信息：截图和页面内容"""
        try:
            debug_dir = PROJECT_ROOT / "debug"
            debug_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # 截图
            screenshot_path = debug_dir / f"{stage}_{timestamp}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"调试截图已保存: {screenshot_path}")
            
            # 保存页面HTML
            html_path = debug_dir / f"{stage}_{timestamp}.html"
            html_content = await page.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"调试HTML已保存: {html_path}")
            
            # 输出页面基本信息
            logger.info(f"当前URL: {page.url}")
            logger.info(f"页面标题: {await page.title()}")
            
        except Exception as e:
            logger.debug(f"捕获调试信息失败: {e}")


# ============================================================
# 4. 导航模块
# ============================================================

class NavigationManager:
    """管理页面导航"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    async def navigate_to_article_list(self, page) -> bool:
        """
        导航到文章列表页（发表记录页）
        增强调试和容错能力
        """
        try:
            navigation_steps = self.config.get("navigation.steps", [])
            data_page_url = self.config.get("data_page_url", "")

            # 首先输出当前页面状态用于调试
            logger.info(f"导航开始，当前URL: {page.url}")
            logger.info(f"当前页面标题: {await page.title()}")

            for step in navigation_steps:
                step_name = step.get("name", "")
                selectors = step.get("selectors", [])
                wait_for = step.get("wait_for", [])

                logger.info(f"执行导航步骤: {step_name}")
                logger.debug(f"可用选择器: {selectors}")

                clicked = False
                last_error = None

                for idx, selector in enumerate(selectors):
                    try:
                        locator = self._get_locator(page, selector)
                        if locator:
                            # 增加超时时间到10秒，并输出调试信息
                            logger.debug(f"尝试选择器 {idx+1}/{len(selectors)}: {selector}")
                            await locator.wait_for(state="visible", timeout=10000)
                            logger.debug(f"选择器可见，准备点击...")
                            await locator.click()
                            clicked = True
                            logger.success(f"点击成功: {step_name} (使用选择器 {idx+1})")
                            break
                        else:
                            logger.debug(f"选择器 {selector} 返回None")
                    except Exception as e:
                        last_error = e
                        logger.debug(f"选择器 {idx+1} 失败: {selector}, 错误: {e}")
                        continue

                if not clicked:
                    logger.error(f"导航步骤失败: {step_name}")
                    logger.error(f"最后错误: {last_error}")
                    # 输出页面调试信息
                    await self._debug_page_state(page, f"nav_fail_{step_name}")
                    return False

                # 等待页面加载
                logger.debug("等待页面加载...")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)  # 增加等待时间确保元素渲染

                # 如果有wait_for配置，等待特定元素出现
                if wait_for:
                    logger.debug(f"等待元素出现: {wait_for}")
                    for wait_selector in wait_for:
                        try:
                            wait_locator = self._get_locator(page, wait_selector)
                            if wait_locator:
                                await wait_locator.wait_for(state="visible", timeout=8000)
                                logger.debug(f"等待元素成功: {wait_selector}")
                                break
                        except Exception as e:
                            logger.debug(f"等待元素失败: {wait_selector}, 错误: {e}")

            # 验证是否到达目标页面
            current_url = page.url
            logger.info(f"导航后当前URL: {current_url}")

            if data_page_url and data_page_url not in current_url:
                logger.info(f"未到达目标页面，直接导航到: {data_page_url}")
                await page.goto(data_page_url, wait_until="networkidle")
                await asyncio.sleep(2)

            logger.success("导航到文章列表页完成")
            return True

        except Exception as e:
            logger.error(f"导航失败: {e}", exc_info=True)
            return False

    async def _debug_page_state(self, page, stage: str):
        """输出页面调试状态"""
        try:
            logger.info(f"=== 页面调试状态 [{stage}] ===")
            logger.info(f"URL: {page.url}")
            logger.info(f"标题: {await page.title()}")

            # 尝试查找关键元素
            key_elements = ["内容管理", "发表记录", "首页", "菜单"]
            for text in key_elements:
                try:
                    count = await page.get_by_text(text).count()
                    if count > 0:
                        logger.info(f"找到元素 '{text}': {count}个")
                except:
                    pass

            # 保存调试截图
            debug_dir = PROJECT_ROOT / "debug"
            debug_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = debug_dir / f"{stage}_{timestamp}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"调试截图已保存: {screenshot_path}")

        except Exception as e:
            logger.debug(f"调试页面状态失败: {e}")

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

    async def extract_articles(self, page, target_titles: List[str]) -> Tuple[List[str], List[Dict]]:
        """
        提取文章数据
        策略一：自上而下遍历匹配
        """
        remaining_titles = target_titles.copy()
        extracted_articles = []

        pagination_type = self.config.get("pagination.type", "button")
        max_pages = self.config.get("pagination.max_pages", 6)
        max_articles = self.config.get("pagination.max_articles", 60)

        total_targets = len(target_titles)
        logger.info(f"开始提取文章，目标: {total_targets} 篇，最大页数: {max_pages}")

        article_count = 0
        current_page = 1

        while current_page <= max_pages and remaining_titles and article_count < max_articles:
            logger.info(f"[第 {current_page} 页] 待匹配: {len(remaining_titles)}/{total_targets} 篇")

            articles = await self._extract_page_articles(page)

            if not articles:
                logger.warning("当前页面无文章数据")
                break

            page_matched = 0
            for article in articles:
                article_count += 1

                if not remaining_titles:
                    break

                if article_count > max_articles:
                    logger.info(f"已达到最大文章数限制 ({max_articles})")
                    break

                current_title = article.get('title', '')

                if self.title_matcher.match(current_title, remaining_titles):
                    extracted_articles.append(article)
                    page_matched += 1
                    # 从剩余列表中移除匹配的标题
                    remaining_titles = [t for t in remaining_titles
                                       if not self.title_matcher.match(current_title, [t])]
                    logger.info(f"  ✓ 匹配成功 [{len(extracted_articles)}/{total_targets}]: {current_title}")
                # 未匹配时不输出日志，减少冗余

            logger.info(f"[第 {current_page} 页] 浏览 {len(articles)} 篇，匹配 {page_matched} 篇，累计匹配 {len(extracted_articles)}/{total_targets} 篇")

            if remaining_titles and article_count < max_articles:
                has_next = await self._go_to_next_page(page)
                if not has_next:
                    break
                current_page += 1
                await page.wait_for_timeout(2000)

        # 汇总统计
        unmatched_count = len(remaining_titles)
        logger.info("=" * 60)
        logger.info(f"采集完成 | 总浏览: {article_count} 篇 | 匹配成功: {len(extracted_articles)} 篇 | 未匹配: {unmatched_count} 篇")
        if remaining_titles:
            logger.info(f"未匹配标题: {remaining_titles}")
        logger.info("=" * 60)
        return remaining_titles, extracted_articles

    async def _extract_page_articles(self, page) -> List[Dict]:
        """提取当前页面的所有文章数据"""
        articles = []

        try:
            iframe_selectors = self.config.get("article_list.iframe_selectors", [])
            if iframe_selectors:
                for selector in iframe_selectors:
                    try:
                        iframe_locator = page.locator(selector.get("css", "") if isinstance(selector, dict) else selector)
                        if await iframe_locator.count() > 0:
                            frame = page.frame_locator(selector.get("css", "") if isinstance(selector, dict) else selector)
                            articles = await self._extract_from_frame(frame)
                            if articles:
                                return articles
                    except Exception:
                        continue

            articles = await self._extract_from_page(page)

        except Exception as e:
            logger.warning(f"提取页面文章失败: {e}")

        return articles

    async def _extract_from_page(self, page) -> List[Dict]:
        """从主页面提取文章"""
        articles = []

        try:
            list_container = self.config.get("article_list.list_container", "")
            row_selector = self.config.get("article_list.list_row", "")

            if not list_container or not row_selector:
                logger.warning("文章列表选择器未配置")
                return articles

            # 等待列表容器可见
            try:
                await page.locator(list_container).wait_for(state="visible", timeout=10000)
                logger.debug(f"列表容器可见: {list_container}")
            except Exception as e:
                logger.warning(f"等待列表容器超时: {list_container}, 错误: {e}")
                return articles

            # 获取所有文章行
            rows = await page.locator(row_selector).all()
            logger.info(f"找到 {len(rows)} 篇文章")

            for idx, row in enumerate(rows):
                try:
                    logger.debug(f"正在提取第 {idx+1}/{len(rows)} 篇文章...")
                    article = await self._extract_single_article(row)
                    if article:
                        articles.append(article)
                        logger.info(f"第 {idx+1} 篇文章提取成功: {article.get('title', 'N/A')[:30]}...")
                    else:
                        logger.debug(f"第 {idx+1} 篇文章提取返回空")
                except Exception as e:
                    logger.debug(f"提取第 {idx+1} 篇文章失败: {e}")
                    continue

        except Exception as e:
            logger.warning(f"从页面提取文章失败: {e}")

        return articles

    async def _extract_from_frame(self, frame) -> List[Dict]:
        """从iframe中提取文章"""
        articles = []

        try:
            row_selector = self.config.get("article_list.list_row", "")
            rows = await frame.locator(row_selector).all()

            for row in rows:
                article = await self._extract_single_article(row)
                if article:
                    articles.append(article)

        except Exception as e:
            logger.warning(f"从iframe提取文章失败: {e}")

        return articles

    async def _extract_single_article(self, row) -> Optional[Dict]:
        """
        提取单篇文章数据（基于 MCP 验证的微信公众号页面结构）
        
        MCP分析的实际HTML结构：
        文章行结构（.weui-desktop-mass__overview的父元素）：
        - div (文章行容器)
          - .weui-desktop-mass__overview: 时间和状态
            - .weui-desktop-mass__time: "今天 08:16" (em元素，发布时间)
          - .weui-desktop-mass-appmsg: 文章内容
            - .weui-desktop-mass-appmsg__title: 标题链接
              - span: 标题文本
              - href: 文章URL
            - .weui-desktop-mass-media__data-list: 数据列表
              - .appmsg-view: 阅读数
              - .appmsg-like: 点赞数
              - .appmsg-share: 分享数
              - .appmsg-haokan: 推荐数
              - .appmsg-comment: 评论数
        
        注意：row传入的是.weui-desktop-mass__overview，需要从父元素或兄弟元素查找
        """
        try:
            article = {
                "platform": "极客公园微信",
                "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            # 获取文章行的父元素（包含完整的文章信息）
            # row 是 .weui-desktop-mass__overview，它的父元素包含完整文章信息
            article_row = row.locator("xpath=..")  # 父元素

            # 1. 提取标题和URL（从 .weui-desktop-mass-appmsg__title）
            try:
                # 先在父元素中查找标题链接
                title_link = article_row.locator(".weui-desktop-mass-appmsg__title").first
                if await title_link.count() > 0:
                    article["title"] = (await title_link.inner_text(timeout=3000)).strip()
                    article["url"] = await title_link.get_attribute("href", timeout=3000)
                    logger.debug(f"提取到标题: {article['title'][:30]}...")
                else:
                    # 备用：在当前元素中查找
                    title_link = row.locator(".weui-desktop-mass-appmsg__title").first
                    if await title_link.count() > 0:
                        article["title"] = (await title_link.inner_text(timeout=3000)).strip()
                        article["url"] = await title_link.get_attribute("href", timeout=3000)
                    else:
                        # 再备用：查找包含mp.weixin.qq.com/s的链接
                        title_link = article_row.locator("a[href*='mp.weixin.qq.com/s']").first
                        if await title_link.count() > 0:
                            article["title"] = (await title_link.inner_text(timeout=3000)).strip()
                            article["url"] = await title_link.get_attribute("href", timeout=3000)
            except Exception as e:
                logger.debug(f"提取标题/URL失败: {e}")
                return None

            if article.get("title"):
                article["title"] = re.sub(r"\s*原创\s*$", "", article["title"]).strip()

            if not article.get("title"):
                logger.debug("未提取到标题，跳过")
                return None

            # 2. 提取发布时间（从 .weui-desktop-mass__time 或 em）
            try:
                time_elem = row.locator(".weui-desktop-mass__time").first
                if await time_elem.count() > 0:
                    article["publish_time"] = (await time_elem.inner_text(timeout=3000)).strip()
                else:
                    # 备用：尝试em元素
                    time_elem = row.locator("em").first
                    if await time_elem.count() > 0:
                        article["publish_time"] = (await time_elem.inner_text(timeout=3000)).strip()
                    else:
                        article["publish_time"] = ""
            except Exception as e:
                logger.debug(f"提取发布时间失败: {e}")
                article["publish_time"] = ""

            # 3. 提取数据字段（从父元素中查找）
            try:
                # 阅读数
                try:
                    read_elem = article_row.locator(".appmsg-view .weui-desktop-mass-media__data__inner").first
                    if await read_elem.count() > 0:
                        article["read_count"] = (await read_elem.inner_text(timeout=2000)).strip()
                except:
                    pass
                
                # 点赞数
                try:
                    like_elem = article_row.locator(".appmsg-like .weui-desktop-mass-media__data__inner").first
                    if await like_elem.count() > 0:
                        article["like_count"] = (await like_elem.inner_text(timeout=2000)).strip()
                except:
                    pass
                
                # 分享数
                try:
                    share_elem = article_row.locator(".appmsg-share .weui-desktop-mass-media__data__inner").first
                    if await share_elem.count() > 0:
                        article["share_count"] = (await share_elem.inner_text(timeout=2000)).strip()
                except:
                    pass
                
                # 评论数
                try:
                    comment_elem = article_row.locator(".appmsg-comment .weui-desktop-mass-media__data__inner").first
                    if await comment_elem.count() > 0:
                        article["comment_count"] = (await comment_elem.inner_text(timeout=2000)).strip()
                except:
                    pass
                
                # 在看数（收藏）
                try:
                    collect_elem = article_row.locator(".appmsg-haokan .weui-desktop-mass-media__data__inner").first
                    if await collect_elem.count() > 0:
                        article["collect_count"] = (await collect_elem.inner_text(timeout=2000)).strip()
                except:
                    pass
                
                logger.debug(f"文章数据: 阅读{article.get('read_count', 'N/A')} "
                           f"点赞{article.get('like_count', 'N/A')} "
                           f"分享{article.get('share_count', 'N/A')}")
                
            except Exception as e:
                logger.debug(f"提取数据字段失败: {e}")

            # 4. 确保所有字段都存在
            for field in ["read_count", "like_count", "comment_count", "share_count", "collect_count", "url", "publish_time"]:
                if field not in article:
                    article[field] = ""

            return article

        except Exception as e:
            logger.debug(f"解析文章行失败: {e}")
            return None

    async def _go_to_next_page(self, page) -> bool:
        """翻到下一页"""
        try:
            pagination_type = self.config.get("pagination.type", "button")

            if pagination_type == "none":
                return False

            if pagination_type == "scroll":
                scroll_count = self.config.get("pagination.scroll_count", 3)
                for _ in range(scroll_count):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1000)
                return True

            if pagination_type == "button":
                button_selectors = self.config.get("pagination.button_selectors", [])
                for selector in button_selectors:
                    try:
                        # MCP验证：使用 text 选择器定位下一页按钮
                        if isinstance(selector, dict):
                            if "text" in selector:
                                locator = page.get_by_text(selector["text"])
                            elif "role" in selector:
                                locator = page.get_by_role(selector["role"], name=selector.get("name", ""))
                            elif "css" in selector:
                                locator = page.locator(selector["css"])
                            else:
                                continue
                        else:
                            locator = page.locator(selector)

                        if await locator.count() > 0:
                            # 检查是否禁用
                            try:
                                is_disabled = await locator.is_disabled(timeout=1000)
                                if is_disabled:
                                    logger.debug("下一页按钮已禁用，到达最后一页")
                                    return False
                            except:
                                pass

                            await locator.click()
                            logger.info("已点击下一页按钮")
                            await page.wait_for_load_state("networkidle")
                            await asyncio.sleep(2)
                            return True
                    except Exception as e:
                        logger.debug(f"翻页选择器失败: {selector}, 错误: {e}")
                        continue

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
            logger.warning("没有数据需要导出")
            return False

        try:
            if not filename:
                filename = self.generate_filename()

            filepath = self.output_dir / filename
            logger.info(f"开始导出数据到 {filepath}...")

            fieldnames = [
                "publish_time", "title", "platform", "url", "exposure",
                "read", "recommend", "comment", "like", "forward", "collect", "crawl_time"
            ]

            standardized_articles = []
            for article in articles:
                std_article = {
                    "publish_time": article.get("publish_time", ""),
                    "title": article.get("title", ""),
                    "platform": article.get("platform", "极客公园微信"),
                    "url": article.get("url", ""),
                    "exposure": "/",
                    "read": article.get("read_count", ""),
                    "recommend": "/",
                    "comment": article.get("comment_count", ""),
                    "like": article.get("like_count", ""),
                "forward": article.get("share_count", ""),
                    "collect": article.get("collect_count", ""),
                    "crawl_time": article.get("crawl_time", "")
                }
                standardized_articles.append(std_article)

            with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(standardized_articles)

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
        targets: 待匹配的目标标题列表
        result_callback: 单条数据回调函数
        unmatched_callback: 未匹配目标回调函数
        headless: 是否使用无头模式，默认True
        login_failed_callback: 登录失败回调函数，接收平台ID参数
    """
    targets = targets[:5]
    success_data = []
    remaining_targets = targets.copy()
    browser = None
    context = None
    p = None

    try:
        p = await async_playwright().start()
        
        # 精简但有效的浏览器参数
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--lang=zh-CN",
        ]

        browser = await p.chromium.launch(
            headless=headless,  # 使用传入的参数控制无头模式
            args=browser_args
        )

        # 构建上下文参数
        context_args = build_browser_context_args()

        # 加载Cookie
        if COOKIE_FILE.exists():
            context_args["storage_state"] = str(COOKIE_FILE)

        context = await browser.new_context(**context_args)

        # 注入终极反指纹脚本（关键！）
        await inject_stealth_scripts(context)

        page = await context.new_page()

        # 初始化管理器
        anti_spider = AntiSpiderHelper(config)
        retry_manager = RetryManager(config)
        login_mgr = LoginManager(config, anti_spider, retry_manager)
        nav_mgr = NavigationManager(config)
        article_extractor = ArticleListExtractor(config)

        # 确保登录
        login_success = await login_mgr.ensure_login(page, context, headless=headless)
        if not login_success:
            logger.error(f"{PLATFORM_NAME} 登录失败")
            # 通知登录失败，让调度器加入手动登录队列
            if login_failed_callback:
                login_failed_callback("geekpark_wechat")
            return [], targets

        # 导航到文章列表
        if not await nav_mgr.navigate_to_article_list(page):
            logger.error(f"{PLATFORM_NAME} 导航失败")
            return [], targets

        # 提取文章数据
        remaining, extracted = await article_extractor.extract_articles(page, targets)
        # 供 except 返回：避免提取成功后（如 save_storage_state）抛错时误用初始全量 targets
        remaining_targets = remaining

        # 处理结果
        for data in extracted:
            data['platform'] = "极客公园微信"
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

async def run(p: Playwright, headless: bool = False) -> None:
    """
    独立运行入口（直接运行采集器时调用）
    """
    context = None
    browser = None

    try:
        # 精简但关键的浏览器启动参数
        browser_args = [
            # 核心：禁用自动化特征检测
            '--disable-blink-features=AutomationControlled',
            # 禁用站点隔离（避免检测）
            '--disable-features=IsolateOrigins,site-per-process',
            # 禁用同源策略（某些场景需要）
            '--disable-web-security',
            # 禁用 /dev/shm 使用（Docker/容器环境）
            '--disable-dev-shm-usage',
            # 禁用沙箱（配合其他参数）
            '--no-sandbox',
            '--disable-setuid-sandbox',
            # 禁用GPU加速（避免某些指纹检测）
            '--disable-gpu',
            # 窗口大小
            '--window-size=1920,1080',
            # 语言设置
            '--lang=zh-CN',
            # 接受语言
            '--accept-lang=zh-CN,zh',
        ]

        # 非无头模式添加的参数
        if not headless:
            browser_args.extend([
                '--start-maximized',
                '--force-device-scale-factor=1',
            ])

        logger.info("正在启动浏览器（终极反检测模式）...")
        browser = await p.chromium.launch(
            headless=headless,
            args=browser_args,
        )

        # 构建上下文参数（带反指纹配置）
        context_args = build_browser_context_args()

        # 加载Cookie
        login_mgr = LoginManager(config, AntiSpiderHelper(config), RetryManager(config))
        storage_state = login_mgr.load_storage_state()
        if storage_state:
            context_args["storage_state"] = storage_state

        # 创建上下文（关键：在创建页面前注入脚本）
        context = await browser.new_context(**context_args)

        # 注入终极反指纹脚本（必须在创建任何页面前调用！）
        await inject_stealth_scripts(context)

        # 创建页面
        page = await context.new_page()

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
        await run(p, headless=False)


# ============================================================
# 平台注册
# ============================================================
from utils.platform_registry import get_platform_registry
get_platform_registry().register("geekpark_wechat", None, "微信公众号", is_stable=True)


if __name__ == "__main__":
    asyncio.run(main())
