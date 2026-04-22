"""
===============================================================
曝光量静态配置加载器
负责从 YAML 配置文件加载各平台曝光量（粉丝量）数据
===============================================================
"""

import yaml
from pathlib import Path
from typing import Dict, Optional
from loguru import logger

from utils.path_helper import BUILTIN_ROOT, CONFIG_DIR


class ExposureLoader:
    """
    曝光量配置加载器（单例模式）
    
    从 config/exposure.yaml 加载各平台的静态曝光量配置，
    在采集时直接注入到数据中，不进行页面提取。
    """
    
    _instance: Optional['ExposureLoader'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'ExposureLoader':
        """实现单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        """初始化加载器，首次调用时加载配置文件"""
        if ExposureLoader._initialized:
            return
        
        self._config: Dict[str, Optional[int]] = {}
        self._config_path: Path = self._get_config_path()
        self._load_config()
        
        ExposureLoader._initialized = True
    
    def _get_config_path(self) -> Path:
        """
        外置 config/exposure.yaml 优先（CONFIG_DIR），不存在时再尝试内置副本。
        """
        external = CONFIG_DIR / "exposure.yaml"
        if external.exists():
            logger.debug(f"找到曝光量配置文件: {external}")
            return external
        builtin = BUILTIN_ROOT / "config" / "exposure.yaml"
        if builtin.exists():
            logger.debug(f"使用内置曝光量配置: {builtin}")
            return builtin
        return external
    
    def _load_config(self) -> None:
        """加载曝光量配置文件"""
        try:
            if not self._config_path.exists():
                logger.warning(f"⚠️ 曝光量配置文件不存在: {self._config_path}")
                self._config = {}
                return

            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data is None:
                    logger.warning("⚠️ 曝光量配置文件为空")
                    self._config = {}
                    return

            # 提取曝光量配置（直接读取根级别配置）
            # 过滤掉 None 值和注释键，只保留平台标识和数值
            self._config = {}
            for key, value in data.items():
                if key is not None and not key.startswith('#') and not key.startswith('//'):
                    self._config[key] = value

            logger.success(f"✅ 曝光量配置加载成功，共 {len(self._config)} 个平台")
            logger.info(f"📋 已配置平台: {list(self._config.keys())}")
            
        except yaml.YAMLError as e:
            logger.error(f"❌ 曝光量配置文件解析失败: {e}")
            self._config = {}
        except Exception as e:
            logger.error(f"❌ 加载曝光量配置时发生错误: {e}")
            self._config = {}
    
    def get_exposure(self, platform_id: str) -> str:
        """
        获取指定平台的曝光量
        
        Args:
            platform_id: 平台标识符（如 "wechat"）
        
        Returns:
            str: 曝光量数字字符串（如 "870000"）；
                 若值为 null 或不存在，返回 "/"
        
        Example:
            >>> loader = ExposureLoader()
            >>> loader.get_exposure("wechat")
            '870000'
            >>> loader.get_exposure("zaker")
            '440000'
            >>> loader.get_exposure("non_existent")
            '/'
        """
        if not platform_id:
            logger.warning("⚠️ get_exposure 被调用时 platform_id 为空")
            return "/"
        
        value = self._config.get(platform_id)
        
        # 值为 None（YAML null）或不存在时返回 "/"
        if value is None:
            logger.debug(f"平台 {platform_id} 的曝光量未配置或为 null，返回 '/'")
            return "/"
        
        # 返回数字字符串
        logger.debug(f"平台 {platform_id} 的曝光量: {value}")
        return str(value)
    
    def reload(self) -> None:
        """重新加载配置文件（支持热更新）"""
        logger.info("🔄 重新加载曝光量配置...")
        self._load_config()
    
    def get_all_exposures(self) -> Dict[str, str]:
        """
        获取所有平台的曝光量配置
        
        Returns:
            Dict[str, str]: 平台标识 -> 曝光量字符串（null 值转为 "/"）
        """
        return {
            platform: (str(value) if value is not None else "/")
            for platform, value in self._config.items()
        }


# 模块级函数，便于直接调用
def get_exposure(platform_id: str) -> str:
    """
    获取指定平台的曝光量（便捷函数）
    
    Args:
        platform_id: 平台标识符
    
    Returns:
        str: 曝光量字符串或 "/"
    """
    return ExposureLoader().get_exposure(platform_id)
