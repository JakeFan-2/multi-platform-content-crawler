"""
===============================================================
平台注册中心模块
全局唯一平台注册中心，管理稳定平台注册和预留扩展位
单例模式，线程安全
===============================================================
"""

import threading
from typing import Dict, Optional, List, Type, Any
from dataclasses import dataclass, field

from utils.path_helper import COLLECTORS_DIR


@dataclass
class PlatformInfo:
    """平台信息数据结构"""
    platform_id: str           # 平台标识（如 toutiao, weibo）
    display_name: str          # 显示名称（如 "头条号"）
    collector_class: Type      # 采集器类
    is_stable: bool           # 是否稳定上线
    module_name: str          # 外置采集器脚本路径（str(COLLECTORS_DIR / f"{id}.py")）


class PlatformRegistry:
    """
    平台注册中心
    全局唯一单例模式，管理所有平台的注册和查询
    """
    
    _instance: Optional['PlatformRegistry'] = None
    _lock = threading.Lock()
    
    # 稳定平台白名单（GUI仅渲染该列表内平台）
    STABLE_PLATFORMS = [
    "zaker",             # ZAKER
    "yidian",           # 一点资讯
    "weibo",            # 微博
    "baijiahao",        # 百家号
    "zhihu",            # 知乎
    "toutiao",          # 头条号
    "netease",          # 网易号
    "xueqiu",           # 雪球（新增）
    "qq",               # 企鹅号（新增）
    "wechat",           # 微信公众号（新增）
]
    
    # 未稳定平台占位（仅预留扩展位，不启用）
    UNSTABLE_PLATFORMS = [
        # "sohu",           # 搜狐（暂不启用）
    ]
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._platforms: Dict[str, PlatformInfo] = {}
        self._stable_platforms: List[str] = []
        self._initialized = True
        
        # 初始化时自动注册稳定平台占位
        self._init_unstable_placeholders()
    
    def _init_unstable_placeholders(self):
        """初始化未稳定平台的占位符"""
        for platform_id in self.UNSTABLE_PLATFORMS:
            self._platforms[platform_id] = PlatformInfo(
                platform_id=platform_id,
                display_name=self._get_default_display_name(platform_id),
                collector_class=None,
                is_stable=False,
                module_name=""
            )
    
    def _get_default_display_name(self, platform_id: str) -> str:
        """获取默认显示名称"""
        default_names = {
        "zaker": "ZAKER",
        "yidian": "一点资讯",
        "weibo": "微博",
        "baijiahao": "百家号",
        "zhihu": "知乎",
        "toutiao": "头条号",
        "netease": "网易号",
        "xueqiu": "雪球",
        "sohu": "搜狐",
        "qq": "QQ公众号",
        "wechat": "微信公众号",
    }
        return default_names.get(platform_id, platform_id)
    
    # ==================== 注册接口 ====================
    
    def register(self, platform_id: str, collector_class: Type = None, 
                 display_name: str = None, is_stable: bool = False) -> bool:
        """
        注册平台
        
        Args:
            platform_id: 平台标识
            collector_class: 采集器类（可选）
            display_name: 显示名称（可选）
            is_stable: 是否稳定平台
            
        Returns:
            注册是否成功
        """
        with self._lock:
            # 检查是否已注册
            if platform_id in self._platforms:
                existing = self._platforms[platform_id]
                # 如果已注册但不稳定，可以更新
                if not existing.is_stable and is_stable:
                    pass  # 允许升级为稳定
                elif existing.is_stable:
                    return True  # 已稳定注册，直接返回成功
                else:
                    return False  # 已存在但不稳定
            
            # 获取显示名称
            if display_name is None:
                display_name = self._get_default_display_name(platform_id)
            
            module_name = str(COLLECTORS_DIR / f"{platform_id}.py")
            
            # 创建平台信息
            self._platforms[platform_id] = PlatformInfo(
                platform_id=platform_id,
                display_name=display_name,
                collector_class=collector_class,
                is_stable=is_stable,
                module_name=module_name
            )
            
            # 如果是稳定平台，添加到稳定列表
            if is_stable and platform_id not in self._stable_platforms:
                self._stable_platforms.append(platform_id)
            
            return True
    
    def unregister(self, platform_id: str) -> bool:
        """
        注销平台
        
        Args:
            platform_id: 平台标识
            
        Returns:
            注销是否成功
        """
        with self._lock:
            if platform_id in self._platforms:
                platform = self._platforms[platform_id]
                # 仅允许注销非稳定平台
                if not platform.is_stable:
                    del self._platforms[platform_id]
                    if platform_id in self._stable_platforms:
                        self._stable_platforms.remove(platform_id)
                    return True
            return False
    
    # ==================== 查询接口 ====================
    
    def get_all_platforms(self) -> Dict[str, PlatformInfo]:
        """获取所有已注册平台"""
        return self._platforms.copy()
    
    def get_stable_platforms(self) -> List[str]:
        """获取所有稳定平台（按顺序）"""
        # 按照 STABLE_PLATFORMS 顺序返回
        result = []
        for platform_id in self.STABLE_PLATFORMS:
            if platform_id in self._platforms:
                platform = self._platforms[platform_id]
                # 只要标记为稳定就返回，不检查collector_class
                if platform.is_stable:
                    result.append(platform_id)
        return result
    
    def get_registered_count(self) -> int:
        """获取已注册的稳定平台数量（用于调试）"""
        return len([p for p in self._platforms.values() if p.is_stable])
    
    def get_all_stable_info(self) -> Dict[str, PlatformInfo]:
        """获取所有稳定平台的详细信息（按顺序）"""
        result = {}
        for platform_id in self.get_stable_platforms():
            result[platform_id] = self._platforms[platform_id]
        return result
    
    def get_platform_info(self, platform_id: str) -> Optional[PlatformInfo]:
        """获取指定平台的详细信息"""
        return self._platforms.get(platform_id)
    
    def is_registered(self, platform_id: str) -> bool:
        """检查平台是否已注册"""
        if platform_id not in self._platforms:
            return False
        platform = self._platforms[platform_id]
        return platform.is_stable
    
    def is_stable(self, platform_id: str) -> bool:
        """检查平台是否为稳定平台"""
        if platform_id not in self._platforms:
            return False
        return self._platforms[platform_id].is_stable
    
    def is_available(self, platform_id: str) -> bool:
        """检查平台是否可用（已注册且稳定）"""
        return self.is_stable(platform_id)
    
    def get_collector_class(self, platform_id: str) -> Optional[Type]:
        """获取平台对应的采集器类"""
        info = self._platforms.get(platform_id)
        return info.collector_class if info else None
    
    # ==================== 工具方法 ====================
    
    def validate_platforms(self, platform_ids: List[str]) -> List[str]:
        """
        校验平台列表，返回有效的稳定平台列表
        
        Args:
            platform_ids: 待校验的平台标识列表
            
        Returns:
            有效的稳定平台列表
        """
        valid = []
        for pid in platform_ids:
            if self.is_available(pid):
                valid.append(pid)
        return valid
    
    def get_platform_display_name(self, platform_id: str) -> str:
        """获取平台显示名称"""
        info = self._platforms.get(platform_id)
        return info.display_name if info else platform_id


# 全局单例访问函数
def get_platform_registry() -> PlatformRegistry:
    """获取平台注册中心单例"""
    return PlatformRegistry()
