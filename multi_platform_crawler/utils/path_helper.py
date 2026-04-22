# utils/path_helper.py
"""
动态路径管理：区分 exe 部署目录（外置配置/数据）与 PyInstaller 内置资源解压目录。
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_bundled_runtime() -> bool:
    """是否为 PyInstaller 等打包运行（比仅判断 frozen 更稳，兼容 _MEIPASS 已注入的情形）。"""
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def get_project_root() -> Path:
    """
    项目根目录（外置文件所在目录）。
    打包环境：exe 所在目录；开发环境：本文件所在包根目录（含 main.py、collectors/ 的目录），
    避免从 IDE 或上级目录启动时 cwd 与项目根不一致导致 cookies/platforms 错位。
    """
    if is_bundled_runtime():
        return Path(sys.executable).resolve().parent
    # path_helper 位于 <项目根>/utils/path_helper.py
    return Path(__file__).resolve().parent.parent


def get_builtin_root() -> Path:
    """
    内置资源根目录（如 resources/icon.ico）。
    打包环境：PyInstaller 临时解压目录 sys._MEIPASS；开发环境与项目根一致。
    """
    if is_bundled_runtime():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is None:
            raise RuntimeError("打包环境下未找到 sys._MEIPASS，无法定位内置资源")
        return Path(meipass).resolve()
    return get_project_root()


PROJECT_ROOT = get_project_root()
BUILTIN_ROOT = get_builtin_root()

COLLECTORS_DIR = PROJECT_ROOT / "collectors"
PLATFORMS_DIR = PROJECT_ROOT / "platforms"
CONFIG_DIR = PROJECT_ROOT / "config"
COOKIES_DIR = PROJECT_ROOT / "cookies"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
SNAPSHOTS_DIR = PROJECT_ROOT / "snapshots"
ENV_PATH = PROJECT_ROOT / ".env"
ICON_PATH = BUILTIN_ROOT / "resources" / "icon.ico"
ENCRYPTION_KEY_PATH = PROJECT_ROOT / ".encryption.key"


def ensure_sys_path_for_imports(entry_dir: Path) -> None:
    """
    统一导入搜索路径，避免冻结环境下路径错位。

    - entry_dir：main 入口所在目录（开发为项目根；onefile 为 PyInstaller 解压目录 _MEIPASS）。
    - 打包时：外置 collectors/*.py 内含 ``from utils.xxx import ...``，必须先能从内置包解析 ``utils``。
      因此将 ``BUILTIN_ROOT``（_MEIPASS）置于 ``sys.path`` 最前，再将 ``PROJECT_ROOT``（exe 同级）置其后，
      避免 ``PROJECT_ROOT`` 在前的版本中误挡或未命中内置 ``utils``。
    """
    es = str(entry_dir.resolve())
    if is_bundled_runtime():
        br = str(BUILTIN_ROOT.resolve())
        pr = str(PROJECT_ROOT.resolve())
        for p in (es, pr, br):
            while p in sys.path:
                sys.path.remove(p)
        sys.path.insert(0, pr)
        sys.path.insert(0, br)
    else:
        if es not in sys.path:
            sys.path.insert(0, es)


def ensure_runtime_dirs() -> None:
    """创建运行时目录（不创建外置 collectors/platforms/config，由交付物提供）。"""
    for d in (COOKIES_DIR, DATA_DIR, LOGS_DIR, SNAPSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print("=== path_helper 自测 ===")
    print(f"frozen: {getattr(sys, 'frozen', False)}  bundled: {is_bundled_runtime()}")
    print(f"PROJECT_ROOT      = {PROJECT_ROOT}")
    print(f"BUILTIN_ROOT      = {BUILTIN_ROOT}")
    print(f"COLLECTORS_DIR    = {COLLECTORS_DIR}")
    print(f"PLATFORMS_DIR     = {PLATFORMS_DIR}")
    print(f"CONFIG_DIR        = {CONFIG_DIR}")
    print(f"COOKIES_DIR       = {COOKIES_DIR}")
    print(f"DATA_DIR          = {DATA_DIR}")
    print(f"LOGS_DIR          = {LOGS_DIR}")
    print(f"SNAPSHOTS_DIR     = {SNAPSHOTS_DIR}")
    print(f"ENV_PATH          = {ENV_PATH}")
    print(f"ICON_PATH         = {ICON_PATH} (exists={ICON_PATH.is_file()})")
    print(f"ENCRYPTION_KEY_PATH = {ENCRYPTION_KEY_PATH}")
    ensure_runtime_dirs()
    for name, p in [
        ("COOKIES_DIR", COOKIES_DIR),
        ("DATA_DIR", DATA_DIR),
        ("LOGS_DIR", LOGS_DIR),
        ("SNAPSHOTS_DIR", SNAPSHOTS_DIR),
    ]:
        ok = p.is_dir()
        print(f"ensure_runtime_dirs: {name} is_dir={ok}")
