"""
===============================================================
飞书多维表格导出模块
Feishu Bitable Exporter
===============================================================

功能：
- 将采集数据批量导入飞书多维表格
- 字段映射（英文→中文）
- tenant_access_token 缓存
- 分批上传（500条/次）
- tenacity 重试机制
===============================================================
"""

import os
import time
import asyncio
import aiohttp
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from typing import Dict, List, Optional, Any
from datetime import datetime


# ============================================================
# 字段映射表：英文 → 飞书中文字段
# ============================================================

FEISHU_FIELD_MAP = {
    "publish_time": "发布日期",
    "title": "题目",
    "platform": "发布平台",
    "url": "发布链接",
    "exposure": "曝光量",
    "read": "阅读",
    "recommend": "推荐",
    "comment": "评论",
    "like": "点赞",
    "forward": "转发",
    "collect": "收藏"
}

# 发布平台单选选项（必须严格匹配）
PLATFORM_OPTIONS = [
    "微信",
    "微博",
    "头条号",
    "知乎",
    "百家号",
    "网易号",
    "企鹅号",
    "一点资讯",
    "雪球",
    "ZAKER"
]


def _parse_date_to_timestamp(date_str: str) -> int:
    """
    将日期字符串转换为毫秒级时间戳
    
    支持格式：YYYY/MM/DD 或 YYYY-MM-DD
    
    Args:
        date_str: 日期字符串，如 "2026/03/30" 或 "2026-03-30"
    
    Returns:
        毫秒级时间戳
    """
    if not date_str or date_str == "/":
        return 0
    
    # 尝试解析日期
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            # 转换为毫秒级时间戳
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    
    # 无法解析，返回0
    logger.warning(f"无法解析日期: {date_str}")
    return 0


def _match_platform_option(platform_name: str) -> str:
    """
    匹配平台名称到飞书单选选项
    
    Args:
        platform_name: 平台名称
    
    Returns:
        匹配的单选选项，如不匹配则返回原值
    """
    if not platform_name or platform_name == "/":
        return ""
    
    # 精确匹配
    if platform_name in PLATFORM_OPTIONS:
        return platform_name
    
    # 尝试模糊匹配（去除空格等）
    platform_clean = platform_name.strip()
    for option in PLATFORM_OPTIONS:
        if platform_clean in option or option in platform_clean:
            return option
    
    logger.warning(f"平台名称未匹配到单选选项: {platform_name}")
    return platform_name


def _format_url_field(url: str, title: str = "") -> dict:
    """
    将URL转换为飞书超链接字段格式
    
    Args:
        url: 链接URL
        title: 显示文本（可选）
    
    Returns:
        {"text": "显示文本", "link": "URL"} 或 None
    """
    if not url or url == "/":
        return None
    
    return {
        "text": title if title else url,
        "link": url
    }


def convert_to_feishu_format(standard_data: dict) -> dict:
    """
    将内部标准字典转换为飞书API所需的格式（中文字段名 + 类型适配）
    
    飞书字段类型要求：
    - 发布日期：日期类型 → 毫秒级时间戳
    - 发布平台：单选 → 字符串（必须匹配选项）
    - 发布链接：超链接 → {"text": "显示文本", "link": "URL"}
    - 其他字段：文本 → 字符串
    
    Args:
        standard_data: 标准字段字典，如 {"publish_time": "2026/03/30", "title": "..."}
    
    Returns:
        飞书格式字典
    """
    result = {}
    
    for key, value in standard_data.items():
        if key not in FEISHU_FIELD_MAP:
            continue
        
        chinese_key = FEISHU_FIELD_MAP[key]
        
        # 调试：检查字段名是否有异常字符
        logger.debug(f"字段映射: {key} -> {repr(chinese_key)}")
        
        # 处理 None 或空值（跳过不传）
        if value is None or value == "":
            continue
        
        # 根据字段类型进行特殊处理
        if key == "publish_time":
            # 日期字段：转换为毫秒级时间戳
            if value == "/":
                continue  # 日期字段不能传"/"，跳过
            timestamp = _parse_date_to_timestamp(str(value))
            if timestamp > 0:
                result[chinese_key] = timestamp
                logger.debug(f"日期转换: {value} -> {timestamp}")
            else:
                logger.warning(f"日期解析失败，跳过: {value}")
        
        elif key == "platform":
            # 单选字段：匹配单选选项
            if value == "/":
                continue  # 单选字段不能传"/"，跳过
            matched = _match_platform_option(str(value))
            if matched:
                result[chinese_key] = matched
                logger.debug(f"平台匹配: {value} -> {matched}")
            else:
                logger.warning(f"平台未匹配到单选选项，跳过: {value}")
        
        elif key == "url":
            # 超链接字段：转换为 {text, link} 格式
            if value == "/":
                continue  # 超链接字段不能传"/"，跳过
            title = standard_data.get("title", "")
            url_field = _format_url_field(str(value), str(title) if title else "")
            if url_field:
                result[chinese_key] = url_field
                logger.debug(f"URL转换: {value} -> {url_field}")
            else:
                logger.warning(f"URL格式无效，跳过: {value}")
        
        else:
            # 其他文本字段：直接使用字符串，"/" 也传入
            result[chinese_key] = str(value)
            # 特别记录曝光量字段的值，便于调试
            if key == "exposure":
                logger.info(f"📊 曝光量字段转换: {key} = {value} -> {chinese_key} = {str(value)}")

    logger.info(f"转换后数据: {result}")
    return result

class FeishuExporter:
    """飞书多维表格导出器"""
    
    def __init__(self):
        """初始化，从环境变量读取凭证"""
        self.app_id = os.getenv("FEISHU_APP_ID", "")
        self.app_secret = os.getenv("FEISHU_APP_SECRET", "")
        self.app_token = os.getenv("FEISHU_APP_TOKEN", "")  # 多维表格文档 token
        self.table_id = os.getenv("FEISHU_TABLE_ID", "")    # 表格 ID
        
        # Token 缓存
        self._token_cache: Dict[str, Any] = {"token": None, "expires_at": 0}
        
        # 验证配置
        if not all([self.app_id, self.app_secret, self.app_token, self.table_id]):
            logger.warning("飞书配置不完整，请检查环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_APP_TOKEN, FEISHU_TABLE_ID")
    
    def _normalize_data(self, data: list) -> list:
        """
        数据归一化：字段映射 + 填充缺失字段为 '/' + 注入静态曝光量

        Args:
            data: 原始数据列表

        Returns:
            归一化后的数据列表
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

        # 标准字段列表（需要导入飞书的字段）
        STANDARD_FIELDS = [
            "publish_time", "title", "platform", "url",
            "exposure", "read", "recommend", "comment",
            "like", "forward", "collect"
        ]

        # 导入曝光量加载器
        from utils.exposure_loader import ExposureLoader
        exposure_loader = ExposureLoader()

        normalized_list = []
        for row in data:
            # 先进行字段名映射
            mapped_data = {}
            for key, value in row.items():
                new_key = FIELD_MAPPING.get(key, key)
                mapped_data[new_key] = value

            # 再填充缺失字段
            normalized = {}
            for field in STANDARD_FIELDS:
                if field in mapped_data and mapped_data[field] not in [None, "", "null"]:
                    normalized[field] = mapped_data[field]
                else:
                    normalized[field] = "/"

            # 注入静态曝光量（覆盖原有值）- 双重保险
            platform_id = row.get("platform_id") or row.get("platform", "").lower().replace("号", "").strip()
            if platform_id and platform_id != "/":
                exposure_value = exposure_loader.get_exposure(platform_id)
                if exposure_value != "/":
                    normalized["exposure"] = exposure_value
                    logger.debug(f"[FeishuExporter] 平台 {platform_id} 曝光量注入: {exposure_value}")
                else:
                    logger.warning(f"[FeishuExporter] 平台 {platform_id} 未找到曝光量配置，使用 '/'")

            normalized_list.append(normalized)

        return normalized_list
    
    async def _get_tenant_access_token(self) -> str:
        """
        获取 tenant_access_token，带缓存（2小时有效期）
        
        Returns:
            tenant_access_token 字符串
        
        Raises:
            Exception: 获取失败时抛出异常
        """
        # 检查缓存是否有效
        if self._token_cache["token"] and time.time() < self._token_cache["expires_at"]:
            logger.debug("使用缓存的 tenant_access_token")
            return self._token_cache["token"]
        
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        
        logger.info("正在获取飞书 tenant_access_token...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=30) as resp:
                data = await resp.json()
                
                if data.get("code") != 0:
                    error_msg = data.get("msg", "未知错误")
                    raise Exception(f"获取飞书 token 失败: {error_msg} (code: {data.get('code')})")
                
                token = data.get("tenant_access_token")
                if not token:
                    raise Exception("飞书响应中未找到 tenant_access_token")
                
                # 缓存 token，提前60秒过期
                expires_in = data.get("expire", 7200) - 60
                self._token_cache = {
                    "token": token,
                    "expires_at": time.time() + expires_in
                }
                
                logger.success(f"成功获取 tenant_access_token，有效期 {expires_in} 秒")
                return token
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def _batch_add_records(self, records: list) -> int:
        """
        批量添加记录到多维表格（单次最多500条）
        
        Args:
            records: 飞书格式的记录列表，每条为 {"字段名": "值"} 格式
        
        Returns:
            飞书 API 确认新建的记录条数（与 data.records 长度一致）
        
        Raises:
            Exception: API错误时抛出异常
        """
        token = await self._get_tenant_access_token()
        
        # 飞书官方 API 格式
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/batch_create"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # 飞书要求每条记录格式: {"fields": {"列名": "值"}}
        payload = {
            "records": [{"fields": rec} for rec in records]
        }
        
        logger.debug(f"正在批量添加 {len(records)} 条记录到飞书...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"飞书 batch_create HTTP {resp.status}，请检查网络与 open.feishu.cn 可达性。"
                        f" 响应片段: {body[:800]}"
                    )
                result = await resp.json()
                
                if result.get("code") != 0:
                    error_code = result.get("code")
                    error_msg = result.get("msg", "未知错误")
                    
                    # 针对常见错误码给出友好提示
                    if error_code == 1254060:
                        # 字段类型转换失败
                        logger.error("=" * 60)
                        logger.error("飞书字段类型错误 (TextFieldConvFail)")
                        logger.error("请检查飞书表格中的字段类型设置：")
                        logger.error("  - 发布日期: 应设为「日期」类型")
                        logger.error("  - 发布平台: 应设为「单选」类型")
                        logger.error("  - 发布链接: 应设为「超链接」类型")
                        logger.error("  - 其他字段: 应设为「文本」类型")
                        logger.error("=" * 60)
                        logger.error(f"发送的字段名: {list(records[0].keys()) if records else '无数据'}")
                        logger.error(f"发送的数据: {records[0] if records else '无数据'}")
                        raise Exception(f"字段类型不匹配，请检查飞书表格字段类型设置 (原始错误: {error_msg})")
                    elif error_code == 1254045:
                        logger.error("=" * 60)
                        logger.error("飞书字段名称不存在 (字段ID不存在)")
                        logger.error("请检查飞书表格中的字段名是否与以下名称完全一致（注意空格）：")
                        expected_fields = list(FEISHU_FIELD_MAP.values())
                        logger.error(f"  期望字段名: {expected_fields}")
                        logger.error(f"  发送的字段名: {list(records[0].keys()) if records else '无数据'}")
                        logger.error("=" * 60)
                        raise Exception(f"字段名称不存在，请检查飞书表格列名是否匹配 (原始错误: {error_msg})")
                    else:
                        raise Exception(f"飞书 API 错误: {error_msg} (code: {error_code})")
                
                data = result.get("data") or {}
                records_out = data.get("records") or []
                n_in, n_out = len(records), len(records_out)
                if n_in > 0 and n_out == 0:
                    raise Exception(
                        "飞书 API 返回 code=0 但 data.records 为空。"
                        " 常见原因：目标数据表为「从其它数据源同步」不可写入；"
                        " 或 app_token / table_id 与当前打开的表格不一致。"
                        f" 完整响应: {result}"
                    )
                if n_out != n_in:
                    raise Exception(
                        f"飞书 batch_create 返回记录数 ({n_out}) 与请求 ({n_in}) 不一致，"
                        f"可能部分字段被拒写或权限截断。响应: {result}"
                    )
                first_id = ""
                if records_out:
                    first_id = records_out[0].get("record_id") or records_out[0].get("id") or ""
                logger.info(
                    f"飞书已确认新建 {n_out} 条记录"
                    + (f"，首条 record_id={first_id}" if first_id else "")
                )
                return n_out
    
    async def export_data(self, data: list) -> dict:
        """
        主入口：导出数据到飞书
        
        Args:
            data: 标准字段字典列表，如 [{"publish_time": "2026/03/30", "title": "...", ...}, ...]
        
        Returns:
            {"success": bool, "total": int, "message": str}
        """
        if not data:
            return {"success": False, "message": "无数据可导入"}
        
        # 验证配置
        if not all([self.app_id, self.app_secret, self.app_token, self.table_id]):
            return {"success": False, "message": "飞书配置不完整，请检查环境变量"}
        
        # 数据归一化：确保所有字段都存在，缺失字段填充为 "/"
        normalized_data = self._normalize_data(data)
        
        logger.info(f"开始导出 {len(normalized_data)} 条记录到飞书多维表格...")
        
        # 打印字段名映射（调试用）
        logger.info(f"字段映射: {list(FEISHU_FIELD_MAP.values())}")
        
        # 调试：打印第一条原始数据
        if normalized_data:
            logger.info(f"第一条归一化数据: {normalized_data[0]}")
        
        try:
            # 1. 转换每条记录为飞书格式
            feishu_records = [convert_to_feishu_format(row) for row in normalized_data]
            
            # 调试：打印第一条转换后数据
            if feishu_records:
                logger.debug(f"第一条转换后数据: {feishu_records[0]}")
            
            # 2. 分批处理（飞书单次最多500条）
            batch_size = 500
            total = len(feishu_records)
            success_count = 0
            
            for i in range(0, total, batch_size):
                batch = feishu_records[i:i + batch_size]
                created = await self._batch_add_records(batch)
                success_count += created
                logger.info(f"飞书导入进度: {success_count}/{total}")
            
            logger.success(f"飞书导入完成: 成功导入 {total} 条记录")
            return {
                "success": True,
                "total": total,
                "message": f"成功导入 {total} 条记录到飞书多维表格"
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"飞书导入失败: {error_msg}")
            
            # 检测网络相关错误，给出友好提示
            network_keywords = ["getaddrinfo failed", "Connection refused", "timed out", 
                               "Network is unreachable", "SSL", "certificate", "connect"]
            is_network_error = any(kw.lower() in error_msg.lower() for kw in network_keywords)
            
            if is_network_error:
                friendly_msg = "网络连接失败，请检查网络连接后重试"
                logger.warning(f"网络错误: {error_msg}")
                logger.info("提示: 请确认网络可以访问 open.feishu.cn，然后重新点击「导入飞书」按钮")
                return {"success": False, "message": friendly_msg}
            
            return {"success": False, "message": error_msg}


# ============================================================
# 测试入口
# ============================================================

async def test_export():
    """测试导出功能"""
    # 测试数据
    test_data = [
        {
            "publish_time": "2026/03/30",
            "title": "测试文章标题1",
            "platform": "微信",
            "url": "https://example.com/1",
            "exposure": "1000",
            "read": "500",
            "recommend": "/",
            "comment": "10",
            "like": "50",
            "forward": "5",
            "collect": "3"
        },
        {
            "publish_time": "2026/03/31",
            "title": "测试文章标题2",
            "platform": "微博",
            "url": "https://example.com/2",
            "exposure": "2000",
            "read": "800",
            "recommend": "/",
            "comment": "20",
            "like": "100",
            "forward": "10",
            "collect": "5"
        }
    ]
    
    exporter = FeishuExporter()
    result = await exporter.export_data(test_data)
    print(f"导出结果: {result}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    from utils.path_helper import ENV_PATH

    load_dotenv(ENV_PATH)
    
    asyncio.run(test_export())
