"""
===============================================================
采集调度中心模块 (CrawlScheduler)
负责平台串行调度、登录状态管理、任务流转控制
===============================================================
"""

import asyncio
import inspect
import re
from typing import List, Dict, Optional, Tuple, Callable
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from loguru import logger

# 导入工具模块
from utils.data_model import DataModel, DataAggregator, STANDARD_FIELDS
from utils.snapshot import SnapshotManager, RetryHandler, ErrorTracker
from utils.security import SecureStorage, FileMutex, SensitiveDataMasker
from utils.ops import LogManager, PlatformOrderManager, PlatformFeatureFlag, SystemHealthChecker
from utils.exposure_loader import ExposureLoader
from utils.module_loader import load_collector
from utils.path_helper import COLLECTORS_DIR, is_bundled_runtime
from utils.title_matcher import clean_for_match


# 需要关键词匹配的平台（标题>30字时）
KEYWORD_PLATFORMS = ["toutiao", "weibo"]


def _build_keywords_map(validated_targets: List[Dict]) -> Optional[Dict[str, List[str]]]:
    """
    检索词 search_term -> 关键词列表（空格拆分），供头条等采集器在超长标题模式下做子串匹配。
    """
    m: Dict[str, List[str]] = {}
    for vt in validated_targets:
        if not vt.get("use_keyword"):
            continue
        kw = (vt.get("keyword") or "").strip()
        if not kw:
            continue
        st = (vt.get("search_term") or "").strip()
        if not st:
            continue
        parts = [p for p in kw.split() if p.strip()]
        if parts:
            m[st] = parts
    return m if m else None

# ZAKER 列表标题常见前缀：全角圆点 ●（U+25CF）及紧随空白
_ZAKER_LEADING_BULLET_RE = re.compile(r"^\u25cf\s*")


def _strip_zaker_title_prefix(title: str) -> str:
    if title in (None, "", "/"):
        return title if title else "/"
    s = str(title).strip()
    if not s:
        return "/"
    s = _ZAKER_LEADING_BULLET_RE.sub("", s, count=1).lstrip()
    return s if s else "/"


def _xueqiu_resolve_user_title(crawled: str, search_to_original: Dict[str, str]) -> str:
    """用校验阶段「检索词→用户原标题」映射，将雪球截断/列表标题还原为用户输入。"""
    if not search_to_original:
        return crawled
    ct = (crawled or "").strip()
    if not ct or ct == "/":
        return crawled

    for term, orig in search_to_original.items():
        o = (orig or "").strip()
        t = (term or "").strip()
        if not o:
            continue
        if ct == t or ct == o:
            return orig
        if o.startswith(ct):
            return orig
        if ct in o or o in ct:
            return orig

    uniq = list(dict.fromkeys(v for v in search_to_original.values() if v and str(v).strip()))
    if len(uniq) == 1:
        o = uniq[0].strip()
        if o.startswith(ct) or ct in o:
            return uniq[0]
    return crawled


def _wechat_resolve_user_title(
    crawled: str, search_to_original: Dict[str, str]
) -> str:
    """
    微信公众号：列表页标题与用户检索词的匹配规则为 clean(检索词) in clean(页面标题)，
    与 collectors/wechat 及 title_matcher 一致；据此将 title 还原为用户给定原标题。
    """
    if not search_to_original:
        return crawled
    ct = (crawled or "").strip()
    if not ct or ct == "/":
        return crawled
    crawled_key = clean_for_match(ct)
    if not crawled_key:
        return _xueqiu_resolve_user_title(ct, search_to_original)
    for term, orig in search_to_original.items():
        tk = clean_for_match(term or "")
        if tk and tk in crawled_key:
            o = (orig or "").strip()
            return o if o else crawled
    return _xueqiu_resolve_user_title(ct, search_to_original)


# 标准数据字段（与 data_model.py 保持一致）
STANDARD_FIELDS = [
    "publish_time",    # 发布日期
    "title",           # 文章标题
    "platform",        # 发布平台
    "url",             # 发布链接
    "exposure",        # 曝光量
    "read",            # 阅读量
    "recommend",       # 推荐量
    "comment",         # 评论量
    "like",            # 点赞量
    "forward",         # 转发量
    "collect",         # 收藏量
    "crawl_time",      # 采集时间
]


class CrawlScheduler(QObject):
    """
    采集调度中心
    负责管理多平台串行调度、登录状态、任务流转
    """

    # 信号定义
    platform_started = Signal(str)  # 平台开始执行
    platform_finished = Signal(str, bool)  # 平台执行完成 (platform, success)
    login_required = Signal(str)  # 需要手动登录 (platform)
    login_success = Signal(str)  # 手动登录成功 (platform)
    data_ready = Signal(dict)  # 单条数据就绪
    unmatched_ready = Signal(str)  # 未匹配标题
    progress_updated = Signal(int, int, str)  # 进度更新 (current, total, platform)
    error_occurred = Signal(str)  # 错误发生
    task_finished = Signal(list, list)  # 任务全部完成 (all_data, all_unmatched)
    # 一次「手动登录采集 / 手动登录并采集」协程结束（含校验失败、提前 return），用于 GUI 恢复按钮
    manual_crawl_finished = Signal(str)

    def __init__(self, thread, parent=None):
        super().__init__(parent)
        self.thread = thread
        self._manual_crawl_lock = asyncio.Lock()
        self.platform_queue: List[str] = []
        self.current_platform: Optional[str] = None
        self.manual_login_queue: List[str] = []
        self.is_paused = False
        self.is_running = False

        # 收集的所有数据
        self.all_data: List[Dict] = []
        self.all_unmatched: List[str] = []

        # 初始化工具模块
        self.data_model = DataModel()
        self.data_aggregator = DataAggregator()
        self.snapshot_manager = SnapshotManager()
        self.retry_handler = RetryHandler()
        self.error_tracker = ErrorTracker()
        self.secure_storage = SecureStorage()
        self.platform_order = PlatformOrderManager()
        self.platform_flags = PlatformFeatureFlag()

        # 加载平台顺序和标记
        self.platform_order.load_order()
        self.platform_flags.load_flags()

        # 执行健康检查
        health_checker = SystemHealthChecker()
        if not health_checker.check_all():
            logger.warning(health_checker.get_report())

    def set_platforms(self, platforms: List[str]):
        """
        设置待采集的平台列表（FIFO顺序）

        核心修改：增加平台合法性校验，仅接受已注册稳定平台

        Args:
            platforms: 平台标识列表
        """
        # 导入注册中心
        from utils.platform_registry import get_platform_registry
        registry = get_platform_registry()

        # 校验并过滤有效平台
        valid_platforms = registry.validate_platforms(platforms)

        # 记录被过滤的平台
        filtered = set(platforms) - set(valid_platforms)
        if filtered:
            logger.warning(f"以下平台未注册或非稳定平台，已过滤: {filtered}")

        self.platform_queue = valid_platforms
        logger.info(f"平台队列已设置（已校验）: {self.platform_queue}")

    def get_next_platform(self) -> Optional[str]:
        """
        获取下一个待执行的平台

        Returns:
            平台标识或None
        """
        if self.platform_queue:
            return self.platform_queue.pop(0)
        return None

    def _sync_unmatched_to_gui(
        self,
        platform: str,
        unmatched_titles: List[str],
        title_restore_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        将采集器返回的未匹配标题同步到 GUI（经 thread.emit_unmatched → 未匹配列表与主日志）。

        采集器在登录失败、导航失败、异常等路径常只 ``return ([], remaining)`` 而不逐条调用
        ``unmatched_callback``；仅靠返回值写入 ``all_unmatched`` 时，界面与日志会缺条。
        与采集器内已对同一标题调用过 callback 的情况并存时，GUI 侧 ``add_unmatched`` 会去重。
        """
        if not unmatched_titles:
            return
        for raw in unmatched_titles:
            display = title_restore_map.get(raw, raw) if title_restore_map else raw
            self.thread.emit_unmatched(platform, display)

    async def execute_platform(
        self,
        platform: str,
        targets: List[str],
        headless: bool = True,
        title_restore_map: Optional[Dict[str, str]] = None,
        keywords_map: Optional[Dict[str, List[str]]] = None,
    ) -> Tuple[List[Dict], List[str]]:
        """
        执行单个平台的采集任务

        Args:
            platform: 平台标识
            targets: 目标标题列表
            headless: 是否使用无头模式

        Returns:
            (success_data, unmatched)
        """
        logger.info(f"开始执行平台: {platform}")
        self.current_platform = platform

        try:
            collector = load_collector(platform)
        except ImportError as e:
            logger.error(f"平台 {platform} 采集器加载失败: {e}")
            self.error_occurred.emit(f"{platform}: {e}")
            self.thread.emit_error(f"{platform}: {e}")
            return [], targets

        def result_callback(data: Dict):
            """单条数据回调 - 注入曝光量后发射"""
            normalized_data = self.normalize_data(
                data, platform, title_restore_map=title_restore_map
            )
            self.data_ready.emit(normalized_data)
            self.thread.emit_data(normalized_data)

        def unmatched_callback(title: str):
            """未匹配回调（展示用户原标题，与返回值汇总路径一致）。"""
            mapped = (
                title_restore_map.get(title, title)
                if title_restore_map
                else title
            )
            self.unmatched_ready.emit(title)
            self.thread.emit_unmatched(self.current_platform, mapped)

        def login_failed_callback(p: str):
            """登录失败回调：将平台加入手动登录队列"""
            logger.warning(f"平台 {p} 自动登录失败，加入手动登录队列")
            if p not in self.manual_login_queue:
                self.manual_login_queue.append(p)
                logger.warning(f"已将 {p} 加入手动登录队列，当前队列: {self.manual_login_queue}")
            logger.warning(f"发出 login_required 信号，平台: {p}")
            self.login_required.emit(p)
            self.thread.emit_login_required(p)

        try:
            collector.set_headless_override(headless)
            login_ok = await collector.ensure_login()
            if not login_ok:
                logger.warning(f"平台 {platform} ensure_login 未通过，加入手动登录队列")
                if platform not in self.manual_login_queue:
                    self.manual_login_queue.append(platform)
                self.login_required.emit(platform)
                self.thread.emit_login_required(platform)
                return [], targets

            crawl_kw: Dict[str, object] = dict(
                targets=targets,
                result_callback=result_callback,
                unmatched_callback=unmatched_callback,
                headless=headless,
                login_failed_callback=login_failed_callback,
            )
            if keywords_map is not None and "keywords_map" in inspect.signature(collector.crawl).parameters:
                crawl_kw["keywords_map"] = keywords_map
            success_data, unmatched = await collector.crawl(**crawl_kw)

            logger.info(f"平台 {platform} 采集完成: 成功 {len(success_data)} 条, 未匹配 {len(unmatched)} 条")
            return success_data, unmatched

        except Exception as e:
            logger.error(f"平台 {platform} 执行异常: {e}")
            self.error_occurred.emit(f"{platform}: {str(e)}")
            self.thread.emit_error(f"{platform}: {str(e)}")
            return [], targets

    async def auto_login(self, platform: str) -> bool:
        """
        执行自动登录

        Args:
            platform: 平台标识

        Returns:
            登录是否成功
        """
        logger.info(f"自动登录平台: {platform}")

        # 自动登录逻辑由采集器内部处理
        # 这里只做日志记录
        return True

    async def manual_login(self, platform: str) -> bool:
        """
        执行手动登录（启动非无头浏览器，等待用户操作）

        Args:
            platform: 平台标识

        Returns:
            登录是否成功
        """
        logger.info(f"请求手动登录平台: {platform}")

        # 发出信号：当前 GUI 仅将平台加入「4. 手动登录平台」列表，不弹阻塞对话框；
        # 若调用本方法，需另行驱动 login_success（例如用户完成有头采集后）。
        self.login_required.emit(platform)
        self.thread.emit_login_required(platform)

        # 等待登录完成（由GUI触发login_success信号）
        # 这里使用事件等待
        login_event = asyncio.Event()

        def on_login_success(p):
            if p == platform:
                login_event.set()

        # 连接一次性信号
        self.thread.login_success_signal.connect(on_login_success)

        try:
            # 等待登录完成，超时30分钟
            await asyncio.wait_for(login_event.wait(), timeout=1800)
            logger.info(f"平台 {platform} 手动登录完成")
            return True
        except asyncio.TimeoutError:
            logger.warning(f"平台 {platform} 手动登录超时")
            return False
        finally:
            # 断开信号连接
            try:
                self.thread.login_success_signal.disconnect(on_login_success)
            except:
                pass

    async def save_cookies(self, platform: str):
        """
        保存平台Cookie

        Args:
            platform: 平台标识
        """
        logger.info(f"保存平台Cookie: {platform}")
        # Cookie保存由采集器内部处理
        pass

    def validate_targets(self, targets_with_index: List[Dict], platform: str) -> Tuple[List[Dict], List[str]]:
        """
        校验目标标题，根据平台和字数决定检索方式

        核心逻辑：
        1. 超字数目标 + 微博/头条平台 → 使用专属关键词检索
        2. 超字数目标 + 其他平台 → 使用原标题检索
        3. 未超字数目标 + 所有平台 → 使用原标题检索

        兜底校验：
        - 微博/头条 + 标题>30字 + 关键词为空 → 强制抛出异常

        Args:
            targets_with_index: 目标标题列表（含索引、原标题、专属关键词）
            platform: 平台标识

        Returns:
            (校验后的目标列表, 检索用的关键词列表)
            每个目标包含: {"title": 原标题, "search_term": 检索词, "use_keyword": 是否用关键词}

        Raises:
            ValueError: 微博/头条平台标题>30字但关键词为空
        """
        # 判断是否是关键词平台（头条/微博）
        is_keyword_platform = platform in KEYWORD_PLATFORMS

        validated_targets = []
        search_keywords = []

        for target in targets_with_index:
            original_title = target.get("title", "")
            use_keyword = target.get("use_keyword", False)
            keyword = target.get("keyword", "")

            if is_keyword_platform and use_keyword and keyword:
                # 头条/微博 + 超字数 + 有专属关键词 → 使用关键词检索
                search_term = keyword
                search_keywords.append(keyword)
                logger.info(f"[{platform}] 标题超30字，使用关键词检索: '{keyword}' (原标题: '{original_title[:20]}...')")
            elif is_keyword_platform and use_keyword and not keyword:
                # 兜底校验：微博/头条 + 标题>30字 + 关键词为空 → 强制终止
                error_msg = f"[{platform}] 标题超30字必须输入关键词: '{original_title[:30]}...'"
                logger.error(error_msg)
                raise ValueError(error_msg)
            else:
                # 其他情况 → 使用原标题检索
                search_term = original_title

            validated_targets.append({
                "title": original_title,      # 原标题全程保留
                "search_term": search_term,   # 实际检索词
                "use_keyword": use_keyword and is_keyword_platform and bool(keyword)
            })

        # 去重关键词列表
        search_keywords = list(dict.fromkeys(search_keywords))

        return validated_targets, search_keywords

    def normalize_data(
        self,
        data: Dict,
        platform_id: str = None,
        *,
        title_restore_map: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """
        数据归一化：字段映射 + 填充缺失字段为 '/' + 平台标题后置处理 + 注入静态曝光量

        Args:
            data: 原始数据
            platform_id: 平台标识，用于获取静态曝光量配置
            title_restore_map: 检索词 -> 用户原标题（雪球号、微信公众号标题还原用，与 validate_targets 一致）

        Returns:
            归一化后的数据
        """
        # 旧字段名到新字段名的映射
        FIELD_MAPPING = {
            'read_count': 'read',
            'like_count': 'like',
            'comment_count': 'comment',
            'share_count': 'forward',
            'collect_count': 'collect',
            'exposure_count': 'exposure',
        }

        # 先进行字段名映射
        mapped_data = {}
        for key, value in data.items():
            new_key = FIELD_MAPPING.get(key, key)
            mapped_data[new_key] = value

        # 再填充缺失字段
        normalized = {}
        for field in STANDARD_FIELDS:
            if field in mapped_data and mapped_data[field] not in [None, "", "null"]:
                normalized[field] = mapped_data[field]
            else:
                normalized[field] = "/"

        # 雪球号：汇总/GUI/导出统一使用用户输入原标题（覆盖采集层截断标题）
        if platform_id == "xueqiu" and title_restore_map:
            normalized["title"] = _xueqiu_resolve_user_title(
                str(normalized.get("title", "/")), title_restore_map
            )

        # 微信公众号：采集层 title 多为页面文案，与方案「title=用户给定」一致，按与采集器相同的清洗匹配还原
        if platform_id == "wechat" and title_restore_map:
            normalized["title"] = _wechat_resolve_user_title(
                str(normalized.get("title", "/")), title_restore_map
            )

        # ZAKER：去掉列表标题前导「●」及紧随空白
        if platform_id == "zaker":
            normalized["title"] = _strip_zaker_title_prefix(str(normalized.get("title", "/")))

        # 注入静态曝光量（强制覆盖原有值）
        if platform_id:
            exposure_value = ExposureLoader().get_exposure(platform_id)
            old_exposure = normalized.get("exposure", "未设置")
            normalized["exposure"] = exposure_value
            logger.info(f"[曝光量注入] 平台: {platform_id} | 原值: {old_exposure} -> 新值: {exposure_value}")
        else:
            logger.warning(f"[曝光量注入] platform_id 为空，无法注入曝光量，保持原值: {normalized.get('exposure', '/')}")

        return normalized

    async def run(self, targets_with_index: List[Dict], keywords: List[str] = None):
        """
        执行完整采集流程

        Args:
            targets_with_index: 目标标题列表（含索引、原标题、专属关键词）
            keywords: 废弃参数（保留兼容性，不再使用）
        """
        # 提取纯标题列表用于日志
        pure_titles = [t.get("title", "") for t in targets_with_index]

        self.is_running = True
        self.all_data = []
        self.all_unmatched = []

        total_platforms = len(self.platform_queue)
        current_index = 0

        logger.info(f"开始执行采集任务，共 {total_platforms} 个平台，标题: {pure_titles}")
        self.thread.emit_log(f"开始采集任务: {len(pure_titles)} 个标题")

        while True:
            # 检查是否暂停
            while self.is_paused:
                await asyncio.sleep(0.5)

            # 获取下一个平台
            platform = self.get_next_platform()
            if not platform:
                break

            current_index += 1
            self.progress_updated.emit(current_index, total_platforms, platform)
            self.thread.emit_progress(current_index, total_platforms, platform)

            # 校验目标：根据平台决定检索方式
            try:
                validated_targets, search_keywords = self.validate_targets(targets_with_index, platform)
            except ValueError as e:
                # 兜底校验失败：关键词为空，跳过当前平台
                logger.error(f"平台 {platform} 校验失败: {e}")
                self.thread.emit_log(f"❌ {e}")
                self.platform_finished.emit(platform, False)
                continue

            # 提取检索词列表（传给采集器）
            search_terms = [t["search_term"] for t in validated_targets]
            search_to_original = {t["search_term"]: t["title"] for t in validated_targets}
            keywords_map = _build_keywords_map(validated_targets)

            # 发送平台开始信号
            self.platform_started.emit(platform)
            self.thread.emit_log(f"开始采集平台: {platform}")

            # 日志显示检索策略
            for t in validated_targets:
                if t["use_keyword"]:
                    self.thread.emit_log(f"[{platform}] 关键词检索: '{t['search_term']}'")
                else:
                    self.thread.emit_log(f"[{platform}] 标题检索: '{t['search_term'][:30]}...'")

            try:
                # 尝试自动登录（由采集器内部处理）
                self.thread.emit_log(f"{platform}: 自动登录...")

                # 执行采集（传入检索词列表）
                success_data, unmatched = await self.execute_platform(
                    platform,
                    search_terms,
                    title_restore_map=search_to_original,
                    keywords_map=keywords_map,
                )

                # 关键：将匹配成功的数据标题替换回用户输入的原标题
                for data in success_data:
                    # 查找匹配的原标题
                    # 先尝试精确匹配检索词
                    crawled_title = data.get("title", "")
                    original_title = None

                    # 遍历查找匹配的原标题（支持模糊匹配）
                    for t in validated_targets:
                        # 如果检索词匹配，使用原标题
                        if t["search_term"] in [data.get("title", ""), crawled_title]:
                            original_title = t["title"]
                            break
                        # 如果页面上抓取的标题与原标题匹配
                        if crawled_title and t["title"] and (
                            crawled_title in t["title"] or t["title"] in crawled_title
                        ):
                            original_title = t["title"]
                            break

                    # 强制替换为用户输入的原标题
                    if original_title:
                        data["title"] = original_title
                        logger.debug(f"标题已还原: '{crawled_title[:20]}...' -> '{original_title[:20]}...'")
                    elif platform == "wechat":
                        data["title"] = _wechat_resolve_user_title(
                            str(crawled_title or ""), search_to_original
                        )

                # 归一化数据（传入platform_id以注入静态曝光量）
                normalized_data = [
                    self.normalize_data(d, platform, title_restore_map=search_to_original)
                    for d in success_data
                ]

                # 收集数据
                self.all_data.extend(normalized_data)
                self.all_unmatched.extend(unmatched)
                self._sync_unmatched_to_gui(platform, unmatched, search_to_original)

                # 保存Cookie
                await self.save_cookies(platform)

                # 发送完成信号
                self.platform_finished.emit(platform, True)
                self.thread.emit_log(f"{platform}: 采集完成，成功 {len(success_data)} 条")

            except Exception as e:
                logger.error(f"平台 {platform} 执行异常: {e}")
                self.platform_finished.emit(platform, False)
                self.error_occurred.emit(f"{platform}: {str(e)}")
                self.thread.emit_error(f"{platform}: {str(e)}")

        # 任务全部完成
        self.is_running = False

        # 执行时间兜底逻辑（雪球号和微信公众号的时间替换）
        self._apply_time_fallback(self.all_data)

        self.thread.emit_finish(self.all_data, self.all_unmatched)
        self.task_finished.emit(self.all_data, self.all_unmatched)
        logger.info(f"采集任务完成: 共 {len(self.all_data)} 条数据, {len(self.all_unmatched)} 条未匹配")

    def pause(self):
        """暂停采集"""
        self.is_paused = True
        logger.info("采集任务已暂停")

    def resume(self):
        """恢复采集"""
        self.is_paused = False
        logger.info("采集任务已恢复")

    def stop(self):
        """停止采集"""
        self.is_running = False
        self.platform_queue.clear()
        logger.info("采集任务已停止")

    def on_manual_login_complete(self, platform: str):
        """
        手动登录完成回调（遗留 API；主流程已改为区域4【手动登录采集】直接 execute_manual_crawl）。

        Args:
            platform: 平台标识
        """
        logger.info(f"手动登录完成: {platform}")
        self.login_success.emit(platform)
        self.thread.emit_login_success(platform)

        # 从手动登录队列移除
        if platform in self.manual_login_queue:
            self.manual_login_queue.remove(platform)

    async def execute_manual_crawl(self, platform: str, targets_with_index: List[Dict]):
        """
        手动登录并采集：加载外置 collectors/<platform>.py（与自动调度同源 module_loader），
        经 execute_platform(..., headless=False) 走采集器 ensure_login + crawl，满足打包后 exe+外置目录模型，无需再启系统 Python。

        说明：各采集器文件内 if __name__ == "__main__" 的独立调试入口（如 DEBUG 列表写 CSV）
        与 GUI 主流程不同；本入口与自动任务一致，使用当前界面标题并回写结果表。

        同一事件循环上仅允许一段手动有头流程：`asyncio.Lock` + `locked()` 快速拒绝重复提交，避免多路
        execute_platform 并行导致 Playwright/浏览器资源冲突。

        Playwright 浏览器由采集器 ``crawl()`` 创建并在其 ``finally`` 中销毁；自动任务登录失败后
        必须完成 ``playwright.stop()``（见 ``utils.playwright_cleanup``），否则可能影响紧随其后的有头启动。
        """
        if self._manual_crawl_lock.locked():
            self.thread.emit_log(
                "⚠️ 已有手动采集任务进行中，请勿重复提交；当前请求已忽略。"
            )
            return [], []

        async with self._manual_crawl_lock:
            logger.info(f"开始手动登录采集: {platform}")
            # 上一段 crawl() 的 finally（含 playwright.stop）与 Chromium 进程退出可能略滞后于协程返回
            await asyncio.sleep(0.2)

            # 自动任务 run() 进行中且用户未整任务暂停时：暂停队列，避免与下一平台抢跑（与方案「暂停-执行-恢复」一致）
            resume_after = False
            if self.is_running and not self.is_paused:
                self.pause()
                resume_after = True
                self.thread.emit_log("已暂停自动采集队列，开始手动登录采集")

            try:
                # 调用关键词校验逻辑（与自动采集一致）
                try:
                    validated_targets, search_keywords = self.validate_targets(
                        targets_with_index, platform
                    )
                except ValueError as e:
                    # 兜底校验失败：关键词为空
                    logger.error(f"手动采集校验失败: {e}")
                    self.thread.emit_log(f"❌ {e}")
                    self.platform_finished.emit(platform, False)
                    return [], []

                # 提取检索词列表（传给采集器）
                search_terms = [t["search_term"] for t in validated_targets]
                search_to_original = {t["search_term"]: t["title"] for t in validated_targets}
                keywords_map = _build_keywords_map(validated_targets)

                # 日志显示检索策略
                for t in validated_targets:
                    if t["use_keyword"]:
                        self.thread.emit_log(f"[{platform}] 关键词检索: '{t['search_term']}'")
                    else:
                        self.thread.emit_log(f"[{platform}] 标题检索: '{t['search_term'][:30]}...'")

                collector_py = COLLECTORS_DIR / f"{platform}.py"
                if is_bundled_runtime():
                    self.thread.emit_log(
                        f"手动采集（有头）：打包运行，加载 exe 同级外置采集器 {collector_py}"
                    )
                else:
                    self.thread.emit_log(f"手动采集（有头）：加载外置采集器 {collector_py}")

                # 使用有头模式执行采集（浏览器会弹出供用户登录）
                success_data, unmatched = await self.execute_platform(
                    platform,
                    search_terms,
                    headless=False,
                    title_restore_map=search_to_original,
                    keywords_map=keywords_map,
                )

                # 关键：将匹配成功的数据标题替换回用户输入的原标题（与 run() 一致，含微信公众号清洗匹配兜底）
                for data in success_data:
                    crawled_title = data.get("title", "")
                    original_title = None
                    for t in validated_targets:
                        if t["search_term"] in [data.get("title", ""), crawled_title]:
                            original_title = t["title"]
                            break
                        if crawled_title and t["title"] and (
                            crawled_title in t["title"] or t["title"] in crawled_title
                        ):
                            original_title = t["title"]
                            break
                    if original_title:
                        data["title"] = original_title
                        logger.debug(
                            f"标题已还原: '{str(crawled_title)[:20]}...' -> '{str(original_title)[:20]}...'"
                        )
                    elif platform == "wechat":
                        data["title"] = _wechat_resolve_user_title(
                            str(crawled_title or ""), search_to_original
                        )

                # 归一化数据（传入platform_id以注入静态曝光量）
                normalized_data = [
                    self.normalize_data(d, platform, title_restore_map=search_to_original)
                    for d in success_data
                ]

                # 收集数据
                self.all_data.extend(normalized_data)
                self.all_unmatched.extend(unmatched)
                self._sync_unmatched_to_gui(platform, unmatched, search_to_original)

                # 发出完成信号
                self.platform_finished.emit(platform, len(success_data) > 0)
                self.thread.emit_log(f"手动采集完成: {platform}, 成功 {len(success_data)} 条")

                # 从手动登录队列移除
                if platform in self.manual_login_queue:
                    self.manual_login_queue.remove(platform)

                return success_data, unmatched
            finally:
                if resume_after:
                    self.resume()
                    self.thread.emit_log("自动采集队列已恢复")
                self.manual_crawl_finished.emit(platform)

    def _apply_time_fallback(self, all_data: list):
        """
        时间兜底逻辑：在数据汇总阶段，将雪球号和微信公众号的发布时间替换为优先级平台的发布时间

        Args:
            all_data: 所有平台汇总后的数据列表（每条数据包含 platform 字段）
        """
        # 兜底规则：目标平台 -> 优先级平台列表
        fallback_rules = {
        }

        # 构建标题索引：{标准化标题: {platform: article}}
        title_index = {}
        for article in all_data:
            title = article.get("title", "")
            platform = article.get("platform", "")
            if title and platform:
                norm_title = self._normalize_title(title)
                if norm_title not in title_index:
                    title_index[norm_title] = {}
                title_index[norm_title][platform] = article

        # 处理每个需要兜底的平台
        for target_platform, priority_list in fallback_rules.items():
            for article in all_data:
                if article.get("platform") != target_platform:
                    continue

                original_time = article.get("publish_time", "")
                # 检查原时间是否有效（微信模糊时间会被判定为无效）
                if self._is_valid_time(original_time):
                    continue

                # 在优先级平台中查找有效时间
                norm_title = self._normalize_title(article.get("title", ""))
                matched_time = None
                matched_source = None

                for src_platform in priority_list:
                    src_article = title_index.get(norm_title, {}).get(src_platform)
                    if src_article:
                        src_time = src_article.get("publish_time", "")
                        if self._is_valid_time(src_time):
                            matched_time = src_time
                            matched_source = src_platform
                            break

                # 应用替换
                if matched_time:
                    article["publish_time"] = matched_time
                    logger.info(f"[时间兜底] {target_platform} 文章《{article['title']}》发布时间从 {matched_source} 获取: {matched_time}")
                else:
                    # 无有效来源则置空字符串
                    article["publish_time"] = ""
                    logger.info(f"[时间兜底] {target_platform} 文章《{article['title']}》无有效发布时间来源，置空")

    def _is_valid_time(self, time_str: str) -> bool:
        """
        判断时间字符串是否有效

        Args:
            time_str: 时间字符串

        Returns:
            是否为有效时间
        """
        # 空值检查
        if not time_str or time_str is None:
            return False

        # 不能是占位符
        if time_str == "/" or time_str == "":
            return False

        # 不能包含模糊词
        fuzzy_keywords = ["前几天", "昨天", "前天", "今天", "刚刚", "分钟前", "小时前"]
        for keyword in fuzzy_keywords:
            if keyword in time_str:
                return False

        # 必须能够解析为日期格式（至少 YYYY-MM-DD）
        try:
            # 尝试解析多种常见日期格式
            from datetime import datetime

            # 尝试标准格式 YYYY-MM-DD
            datetime.strptime(time_str, "%Y-%m-%d")
            return True
        except ValueError:
            try:
                # 尝试其他常见格式
                datetime.strptime(time_str, "%Y/%m/%d")
                return True
            except ValueError:
                try:
                    datetime.strptime(time_str, "%Y年%m月%d日")
                    return True
                except ValueError:
                    return False

    def _normalize_title(self, title: str) -> str:
        """
        标准化标题用于匹配（去除空格、转为小写）

        Args:
            title: 原始标题

        Returns:
            标准化后的标题
        """
        if not title:
            return ""
        # 去除空格、特殊字符，转为小写
        normalized = title.lower().strip()
        # 去除多余空格
        normalized = " ".join(normalized.split())
        return normalized
