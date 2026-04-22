"""
===============================================================
环境变量加载工具
基于 path_helper 动态定位 .env（与 exe 同级外置配置一致）
===============================================================
"""

import os
from typing import Any

from dotenv import load_dotenv

from utils.path_helper import ENV_PATH


def load_env_file() -> None:
    """加载 PROJECT_ROOT 下的 .env（路径由 ENV_PATH 给出）。"""
    load_dotenv(ENV_PATH, override=True)


def get_env(key: str, default: str = "") -> str:
    """
    获取环境变量值
    自动加载 .env 文件（如果尚未加载）

    Args:
        key: 环境变量名
        default: 默认值

    Returns:
        环境变量值或默认值
    """
    load_env_file()
    return os.getenv(key, default)


def get_platform_credentials(platform: str) -> tuple[str, str]:
    """
    获取指定平台的账号密码（仅环境变量 / .env）

    Args:
        platform: 平台标识符（大小写不敏感，内部转大写拼键名）

    Returns:
        (username, password) 元组
    """
    username_key = f"{platform.upper()}_USERNAME"
    password_key = f"{platform.upper()}_PASSWORD"

    username = get_env(username_key, "")
    password = get_env(password_key, "")

    return username, password


def get_platform_credentials_with_fallback(platform: str, yaml_config: Any = None) -> tuple[str, str]:
    """
    环境变量优先，YAML 根级 username/password 降级。

    Args:
        platform: 平台标识
        yaml_config: 含 .get 方法的配置对象（如 ConfigLoader）或 dict；可为 None

    Returns:
        (username, password)
    """
    username, password = get_platform_credentials(platform)
    if yaml_config is None:
        return username, password
    if not username:
        username = str(yaml_config.get("username", "") or "")
    if not password:
        password = str(yaml_config.get("password", "") or "")
    return username, password
