"""
===============================================================
安全加固模块
实现Cookie加密、敏感信息脱敏、文件互斥锁
===============================================================
"""

import os
import re
import json
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Union
from datetime import datetime
from cryptography.fernet import Fernet
from loguru import logger

from utils.path_helper import COOKIES_DIR, ENCRYPTION_KEY_PATH, ENV_PATH


class EncryptionManager:
    """
    加密管理器
    负责Cookie和敏感信息的加密存储
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, key_path: Union[str, Path, None] = None):
        if hasattr(self, '_initialized'):
            return

        self.key_path = Path(key_path) if key_path is not None else Path(ENCRYPTION_KEY_PATH)
        self._cipher: Optional[Fernet] = None
        self._init_cipher()
        self._initialized = True

    def _init_cipher(self):
        """初始化加密器"""
        try:
            # 尝试从环境变量获取密钥
            key = os.environ.get("ENCRYPTION_KEY")
            if key:
                # 使用base64编码的密钥
                import base64
                key = base64.urlsafe_b64decode(key)
            elif self.key_path.exists():
                # 从文件加载密钥
                with open(self.key_path, "rb") as f:
                    key = f.read()
            else:
                # 生成新密钥
                key = Fernet.generate_key()
                # 保存到文件
                self.key_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.key_path, "wb") as f:
                    f.write(key)
                logger.info("已生成新的加密密钥")

            self._cipher = Fernet(key)
            logger.info("加密管理器初始化完成")

        except Exception as e:
            logger.error(f"加密管理器初始化失败: {e}")
            raise

    def encrypt(self, data: str) -> str:
        """
        加密字符串

        Args:
            data: 待加密的字符串

        Returns:
            加密后的字符串（base64编码）
        """
        if not self._cipher:
            raise RuntimeError("加密器未初始化")

        encrypted = self._cipher.encrypt(data.encode("utf-8"))
        return encrypted.decode("utf-8")

    def decrypt(self, encrypted_data: str) -> str:
        """
        解密字符串

        Args:
            encrypted_data: 加密的字符串

        Returns:
            解密后的字符串
        """
        if not self._cipher:
            raise RuntimeError("加密器未初始化")

        decrypted = self._cipher.decrypt(encrypted_data.encode("utf-8"))
        return decrypted.decode("utf-8")

    def encrypt_dict(self, data: Dict) -> str:
        """
        加密字典

        Args:
            data: 待加密的字典

        Returns:
            加密后的字符串（JSON格式）
        """
        json_str = json.dumps(data, ensure_ascii=False)
        return self.encrypt(json_str)

    def decrypt_dict(self, encrypted_data: str) -> Dict:
        """
        解密字典

        Args:
            encrypted_data: 加密的字符串

        Returns:
            解密后的字典
        """
        json_str = self.decrypt(encrypted_data)
        return json.loads(json_str)


class SecureStorage:
    """
    安全存储模块
    负责Cookie的安全存储和加载
    """

    def __init__(self, storage_dir: Union[str, Path, None] = None):
        self.storage_dir = Path(storage_dir) if storage_dir is not None else COOKIES_DIR
        self.storage_dir.mkdir(exist_ok=True)
        self.encryption = EncryptionManager()

    def save_cookie(self, platform: str, cookie_data: Dict) -> bool:
        """
        保存Cookie（加密存储）

        Args:
            platform: 平台标识
            cookie_data: Cookie数据

        Returns:
            是否保存成功
        """
        try:
            filepath = self.storage_dir / f"{platform}_cookie.enc"

            # 加密并保存
            encrypted = self.encryption.encrypt_dict(cookie_data)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(encrypted)

            logger.info(f"Cookie已加密保存: {platform}")
            return True

        except Exception as e:
            logger.error(f"保存Cookie失败: {e}")
            return False

    def load_cookie(self, platform: str) -> Optional[Dict]:
        """
        加载Cookie

        Args:
            platform: 平台标识

        Returns:
            Cookie数据，如果不存在返回None
        """
        try:
            filepath = self.storage_dir / f"{platform}_cookie.enc"
            if not filepath.exists():
                return None

            with open(filepath, "r", encoding="utf-8") as f:
                encrypted = f.read()

            # 解密
            cookie_data = self.encryption.decrypt_dict(encrypted)
            logger.info(f"Cookie已加载: {platform}")
            return cookie_data

        except Exception as e:
            logger.error(f"加载Cookie失败: {e}")
            return None

    def delete_cookie(self, platform: str) -> bool:
        """
        删除Cookie

        Args:
            platform: 平台标识

        Returns:
            是否删除成功
        """
        try:
            filepath = self.storage_dir / f"{platform}_cookie.enc"
            if filepath.exists():
                filepath.unlink()
                logger.info(f"Cookie已删除: {platform}")
            return True

        except Exception as e:
            logger.error(f"删除Cookie失败: {e}")
            return False


class FileMutex:
    """
    文件互斥锁
    防止多线程文件写入冲突
    """

    _locks: Dict[str, threading.Lock] = {}
    _manager_lock = threading.Lock()

    @classmethod
    def get_lock(cls, filepath: str) -> threading.Lock:
        """
        获取文件锁

        Args:
            filepath: 文件路径

        Returns:
            互斥锁对象
        """
        with cls._manager_lock:
            if filepath not in cls._locks:
                cls._locks[filepath] = threading.Lock()
            return cls._locks[filepath]

    @classmethod
    def write_safe(cls, filepath: str, content: str, mode: str = "w", encoding: str = "utf-8"):
        """
        线程安全的文件写入

        Args:
            filepath: 文件路径
            content: 写入内容
            mode: 写入模式
            encoding: 编码
        """
        lock = cls.get_lock(filepath)
        with lock:
            with open(filepath, mode, encoding=encoding) as f:
                f.write(content)

    @classmethod
    def read_safe(cls, filepath: str, mode: str = "r", encoding: str = "utf-8") -> str:
        """
        线程安全的文件读取

        Args:
            filepath: 文件路径
            mode: 读取模式
            encoding: 编码

        Returns:
            文件内容
        """
        lock = cls.get_lock(filepath)
        with lock:
            with open(filepath, mode, encoding=encoding) as f:
                return f.read()


class SensitiveDataMasker:
    """
    敏感数据脱敏器
    负责日志中敏感信息的脱敏
    """

    # 脱敏规则
    MASK_RULES = {
        # 账号密码
        r'(password["\s:=]+)([^\s"\',]+)': r'\1******',
        r'(username["\s:=]+)([^\s"\',]+)': r'\1******',
        r'(account["\s:=]+)([^\s"\',]+)': r'\1******',
        r'(pwd["\s:=]+)([^\s"\',]+)': r'\1******',

        # Cookie
        r'(cookie["\s:=]+)([^\s"\',]+)': r'\1******',

        # Token
        r'(token["\s:=]+)([^\s"\',]+)': r'\1******',
        r'(access_token["\s:=]+)([^\s"\',]+)': r'\1******',

        # 手机号
        r'(1[3-9]\d)[\d]{4}([\d]{4})': r'\1****\2',

        # 邮箱
        r'([a-zA-Z0-9._%+-]+)@[a-zA-Z0-9.-]+(\.[a-zA-Z]{2,})': r'***@\2',
    }

    @classmethod
    def mask(cls, text: str) -> str:
        """
        脱敏处理

        Args:
            text: 原始文本

        Returns:
            脱敏后的文本
        """
        masked = text
        for pattern, replacement in cls.MASK_RULES.items():
            masked = re.sub(pattern, replacement, masked, flags=re.IGNORECASE)
        return masked


class ConfigLoader:
    """
    配置加载器
    从.env文件加载敏感配置
    """

    @staticmethod
    def load_env(env_file: Union[str, Path, None] = None) -> Dict[str, str]:
        """
        加载环境变量

        Args:
            env_file: .env 文件路径；默认使用 path_helper.ENV_PATH（exe 同级外置）

        Returns:
            环境变量字典
        """
        env_path = Path(ENV_PATH) if env_file is None else Path(env_file)
        if not env_path.exists():
            logger.warning(f".env文件不存在: {env_path}")
            return {}

        config = {}
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # 解析 KEY=VALUE 格式
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    config[key] = value

        logger.info(f"环境变量已加载: {len(config)} 项")
        return config

    @staticmethod
    def get_credential(platform: str) -> Dict[str, str]:
        """
        获取平台账号密码

        Args:
            platform: 平台标识

        Returns:
            {username, password}
        """
        # 从环境变量加载
        username = os.environ.get(f"{platform.upper()}_USERNAME", "")
        password = os.environ.get(f"{platform.upper()}_PASSWORD", "")

        # 如果环境变量没有，尝试从.env加载
        if not username or not password:
            config = ConfigLoader.load_env()
            username = config.get(f"{platform.upper()}_USERNAME", "")
            password = config.get(f"{platform.upper()}_PASSWORD", "")

        return {"username": username, "password": password}
