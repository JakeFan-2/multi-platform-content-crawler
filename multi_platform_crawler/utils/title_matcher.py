# ========== 标题匹配工具模块 ==========
# 仅使用 Python 内置 re 库 + 原生字符串包含判断
# 核心逻辑：目标 in 抓取（统一清洗后子串包含）
# ===================================================
import re
from typing import List
from loguru import logger

# ====================== 统一匹配清洗正则 ======================
# 保留：中文、英文、数字；删除：所有其他字符（标点、空格、符号等）
PATTERN_CLEAN = re.compile(r'[^\u4e00-\u9fa5a-zA-Z0-9]+')

# ====================== 展示轻清洗正则（不参与匹配）======================
PATTERN_STRIP = re.compile(r'^\s+|\s+$')      # 去除首尾空格
PATTERN_SPACE = re.compile(r'\s+')            # 合并中间多余空格为单个空格
PATTERN_ELLIPSIS = re.compile(r'[.…]+$')      # 去除末尾英文句点或中文省略号


def clean_for_match(title: str) -> str:
    """
    【匹配专用】统一清洗：目标标题和抓取标题都使用此函数
    规则：
      1. 删除所有标点、空格、特殊符号
      2. 英文转小写
      3. 仅保留中文、英文字母、数字
    """
    if not title:
        return ""
    cleaned = PATTERN_CLEAN.sub('', title.strip())
    return cleaned.lower()


# 兼容别名
clean_target = clean_for_match


def clean_for_display(title: str) -> str:
    """
    【展示专用】轻清洗：仅用于日志输出、CSV存储，不参与匹配
    规则：
      1. 去除首尾空格
      2. 合并中间多余空格为单个空格
      3. 去除末尾省略号
    """
    if not title:
        return ""
    title = PATTERN_STRIP.sub('', title)
    title = PATTERN_SPACE.sub(' ', title)
    title = PATTERN_ELLIPSIS.sub('', title)
    return title


def match_title(target_title: str, crawled_title: str) -> bool:
    """
    单个标题匹配（统一清洗后子串包含）
    返回 True 表示匹配成功
    """
    target = clean_for_match(target_title)
    crawled = clean_for_match(crawled_title)
    # 空标题保护：如果清洗后目标为空（如纯符号标题），直接返回 False
    if not target:
        return False
    return target in crawled


def match_any_target(target_titles: List[str], crawled_title: str) -> bool:
    """
    批量匹配：判断 crawled_title 是否匹配 targets 中的任意一个
    用于采集器中遍历待匹配列表
    匹配规则：目标标题 in 抓取标题
    """
    crawled = clean_for_match(crawled_title)
    for target in target_titles:
        t = clean_for_match(target)
        if t and t in crawled:
            return True
    return False


def match_crawled_in_target(target_titles: List[str], crawled_title: str) -> bool:
    """
    批量匹配：判断 crawled_title 是否被 targets 中的任意一个包含
    用于雪球等抓取标题被截断的平台
    匹配规则：抓取标题 in 目标标题（截断标题匹配完整标题）
    """
    crawled = clean_for_match(crawled_title)
    if not crawled:
        return False
    for target in target_titles:
        t = clean_for_match(target)
        if t and crawled in t:
            return True
    return False
