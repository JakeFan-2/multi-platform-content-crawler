"""
===============================================================
统一数据模型模块
实现12平台标准字段映射与按标题汇总规则
===============================================================
"""

import csv
from typing import List, Dict, Optional, Union
from pathlib import Path
from datetime import datetime
from loguru import logger

from utils.path_helper import DATA_DIR


# 标准字段定义（英文键名）- 按用户指定顺序
STANDARD_FIELDS = [
    "publish_time",    # 发布日期
    "title",           # 文章标题
    "platform",        # 发布平台
    "url",             # 发布链接/URL
    "exposure",        # 曝光量
    "read",            # 阅读量
    "recommend",       # 推荐量
    "comment",         # 评论量
    "like",            # 点赞量
    "forward",         # 转发量
    "collect"          # 收藏量
]

# 标准字段中文映射
FIELD_NAME_MAP = {
    "publish_time": "发布日期",
    "title": "文章标题",
    "platform": "发布平台",
    "url": "发布链接",
    "exposure": "曝光量",
    "read": "阅读量",
    "recommend": "推荐",
    "comment": "评论量",
    "like": "点赞量",
    "forward": "转发量",
    "collect": "收藏量"
}

# 平台标识到中文名映射
PLATFORM_NAME_MAP = {
    "weibo": "微博",
    "toutiao": "头条号",
    "zhihu": "知乎号",
    "baijiahao": "百家号",
    "netease": "网易号",
    "qq": "企鹅号",
    "yidian": "一点资讯号",
    "xueqiu": "雪球号",
    "zaker": "ZAKER号",
}


class DataModel:
    """
    统一数据模型
    负责数据标准化、汇总、导出
    """

    def __init__(self, output_dir: Union[str, Path, None] = None):
        self.output_dir = Path(output_dir) if output_dir is not None else DATA_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def normalize_data(self, data: Dict) -> Dict:
        """
        数据归一化：填充缺失字段为 '/'

        Args:
            data: 原始数据字典

        Returns:
            归一化后的数据字典
        """
        normalized = {}
        for field in STANDARD_FIELDS:
            value = data.get(field, "/")

            # 处理None、空字符串等情况
            if value is None or value == "" or value == "null":
                value = "/"

            normalized[field] = value

        # 确保platform字段有值
        if normalized.get("platform") == "/":
            normalized["platform"] = data.get("platform_name", "/")

        return normalized

    def normalize_batch(self, data_list: List[Dict]) -> List[Dict]:
        """
        批量归一化数据

        Args:
            data_list: 原始数据列表

        Returns:
            归一化后的数据列表
        """
        return [self.normalize_data(data) for data in data_list]

    def group_by_title(self, data_list: List[Dict]) -> Dict[str, List[Dict]]:
        """
        按标题分组数据

        Args:
            data_list: 数据列表

        Returns:
            按标题分组的字典
        """
        grouped = {}
        for data in data_list:
            title = data.get("title", "/")
            if title not in grouped:
                grouped[title] = []
            grouped[title].append(data)
        return grouped

    def sort_by_title(self, data_list: List[Dict]) -> List[Dict]:
        """
        按标题排序（保持平台顺序）

        Args:
            data_list: 数据列表

        Returns:
            排序后的数据列表
        """
        # 先按标题排序，相同标题内按平台顺序
        return sorted(data_list, key=lambda x: (x.get("title", ""), x.get("platform", "")))

    def export_to_csv(
        self,
        data_list: List[Dict],
        filename: Optional[str] = None,
        use_bom: bool = True
    ) -> str:
        """
        导出数据到CSV文件

        Args:
            data_list: 数据列表
            filename: 文件名（可选，默认按时间戳生成）
            use_bom: 是否使用UTF-8 BOM

        Returns:
            导出文件的完整路径
        """
        if not data_list:
            logger.warning("没有数据需要导出")
            return ""

        # 归一化数据
        normalized_data = self.normalize_batch(data_list)

        # 排序
        sorted_data = self.sort_by_title(normalized_data)

        # 生成文件名
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"articles_{timestamp}.csv"

        filepath = self.output_dir / filename

        # 写入CSV
        encoding = "utf-8-sig" if use_bom else "utf-8"
        with open(filepath, "w", encoding=encoding, newline="") as f:
            # 使用中文列头
            chinese_fields = [FIELD_NAME_MAP.get(f, f) for f in STANDARD_FIELDS]
            writer = csv.DictWriter(f, fieldnames=STANDARD_FIELDS, extrasaction='ignore')

            # 写入中文列头
            f.write(",".join(chinese_fields) + "\n")

            # 写入数据
            for data in sorted_data:
                row = {}
                for field in STANDARD_FIELDS:
                    row[field] = str(data.get(field, "/"))
                writer.writerow(row)

        logger.info(f"数据已导出到: {filepath}")
        return str(filepath)

    def export_unmatched(
        self,
        unmatched_data: List[tuple],
        filename: str = "unmatched.csv"
    ) -> str:
        """
        导出未匹配数据

        Args:
            unmatched_data: 未匹配数据列表 [(platform, title), ...]
            filename: 文件名

        Returns:
            导出文件的完整路径
        """
        if not unmatched_data:
            logger.info("没有未匹配数据")
            return ""

        filepath = self.output_dir / filename

        encoding = "utf-8-sig"
        with open(filepath, "w", encoding=encoding, newline="") as f:
            f.write("平台,未匹配标题\n")
            for platform, title in unmatched_data:
                platform_name = PLATFORM_NAME_MAP.get(platform, platform)
                # 转义引号
                title = title.replace('"', '""')
                f.write(f'"{platform_name}","{title}"\n')

        logger.info(f"未匹配数据已导出到: {filepath}")
        return str(filepath)

    def merge_platform_data(self, platform_data: Dict[str, List[Dict]]) -> List[Dict]:
        """
        合并多个平台的数据

        Args:
            platform_data: 平台数据字典 {platform: [data1, data2, ...]}

        Returns:
            合并后的数据列表
        """
        merged = []
        for platform, data_list in platform_data.items():
            for data in data_list:
                data_copy = data.copy()
                data_copy["platform"] = platform
                merged.append(data_copy)
        return merged


class DataAggregator:
    """
    数据聚合器
    负责按标题汇总、按平台展示未匹配数据
    """

    def __init__(self):
        self.all_data: List[Dict] = []
        self.unmatched_by_platform: Dict[str, List[str]] = {}

    def add_data(self, platform: str, data: List[Dict]):
        """
        添加平台数据

        Args:
            platform: 平台标识
            data: 数据列表
        """
        for item in data:
            item["platform"] = platform
        self.all_data.extend(data)

    def add_unmatched(self, platform: str, titles: List[str]):
        """
        添加未匹配标题

        Args:
            platform: 平台标识
            titles: 未匹配标题列表
        """
        if platform not in self.unmatched_by_platform:
            self.unmatched_by_platform[platform] = []
        self.unmatched_by_platform[platform].extend(titles)

    def get_sorted_data(self) -> List[Dict]:
        """
        获取排序后的数据（按标题分组）

        Returns:
            排序后的数据列表
        """
        dm = DataModel()
        return dm.sort_by_title(dm.normalize_batch(self.all_data))

    def get_unmatched_list(self) -> List[tuple]:
        """
        获取未匹配数据列表（按平台顺序）

        Returns:
            [(platform, title), ...]
        """
        result = []
        # 按平台顺序添加
        for platform in PLATFORM_NAME_MAP.keys():
            if platform in self.unmatched_by_platform:
                for title in self.unmatched_by_platform[platform]:
                    result.append((platform, title))
        return result

    def clear(self):
        """清空所有数据"""
        self.all_data.clear()
        self.unmatched_by_platform.clear()
