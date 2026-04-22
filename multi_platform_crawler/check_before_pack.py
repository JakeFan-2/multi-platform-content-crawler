#!/usr/bin/env python3
"""
打包前检查：目录与文件、可选模板生成、load_collector(zaker) 冒烟验证。
在项目根目录（与 main.py 同级）执行: python check_before_pack.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 避免 Windows 控制台 GBK 下 loguru/emoji 写入失败
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.path_helper import ensure_sys_path_for_imports

ensure_sys_path_for_imports(ROOT)

DEFAULT_EXPOSURE = """# 多平台内容采集系统 - 曝光量静态配置（打包前检查生成的默认模板，请按实际调整）
# 平台标识: 数值（null 表示无数据）
wechat: null
weibo: null
toutiao: null
zhihu: null
baijiahao: null
netease: null
qq: null
yidian: null
xueqiu: null
zaker: null
"""

ENV_TEMPLATE = """# 多平台内容采集系统 — 环境变量（打包前检查生成的模板，请填写真实值）
# 飞书多维表格（导入功能，见 utils/feishu_exporter.py）
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_APP_TOKEN=
FEISHU_TABLE_ID=

# 平台账号示例（与 migrate_env.py / 各平台 YAML 约定一致，按需增删）
# ZAKER_USERNAME=
# ZAKER_PASSWORD=
"""


def _ok(msg: str) -> None:
    print(f"[PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def main() -> int:
    failed = False

    collectors = ROOT / "collectors"
    py_files = list(collectors.glob("*.py")) if collectors.is_dir() else []
    if collectors.is_dir() and len(py_files) >= 1:
        _ok(f"collectors/ 存在且含 {len(py_files)} 个 .py")
    else:
        _fail("collectors/ 不存在或没有 .py 文件")
        failed = True

    platforms = ROOT / "platforms"
    yaml_files = list(platforms.glob("*.yaml")) if platforms.is_dir() else []
    if platforms.is_dir() and len(yaml_files) >= 1:
        _ok(f"platforms/ 存在且含 {len(yaml_files)} 个 .yaml")
    else:
        _fail("platforms/ 不存在或没有 .yaml 文件")
        failed = True

    config_dir = ROOT / "config"
    exposure_path = config_dir / "exposure.yaml"
    if exposure_path.is_file():
        _ok(f"config/exposure.yaml 已存在: {exposure_path}")
    else:
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            exposure_path.write_text(DEFAULT_EXPOSURE, encoding="utf-8")
            _ok(f"已创建默认 config/exposure.yaml: {exposure_path}")
        except OSError as e:
            _fail(f"无法创建 config/exposure.yaml: {e}")
            failed = True

    icon_path = ROOT / "resources" / "icon.ico"
    if icon_path.is_file():
        _ok(f"resources/icon.ico 存在: {icon_path}")
    else:
        _warn(f"未找到 resources/icon.ico（PyInstaller --icon 可能失败，请补充图标）: {icon_path}")

    env_path = ROOT / ".env"
    if env_path.is_file():
        _ok(f".env 已存在: {env_path}")
    else:
        try:
            env_path.write_text(ENV_TEMPLATE, encoding="utf-8")
            _ok(f"已创建 .env 模板: {env_path}")
        except OSError as e:
            _fail(f"无法创建 .env: {e}")
            failed = True

    try:
        from utils.module_loader import load_collector

        collector = load_collector("zaker")
        _ok(f"load_collector(\"zaker\") 成功，实例类型: {type(collector).__name__}")
    except Exception as e:
        _fail(f"load_collector(\"zaker\") 失败: {e}")
        failed = True

    print("-" * 50)
    if failed:
        print("RESULT: FAIL（存在 [FAIL] 项，请先修复后再打包）")
        return 1
    print("RESULT: PASS（存在 [WARN] 时仍可通过，但建议处理警告项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
