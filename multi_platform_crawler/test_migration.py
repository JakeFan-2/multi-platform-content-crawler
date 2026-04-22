#!/usr/bin/env python3
"""
迁移与交付验证脚本：路径常量、稳定平台外置文件配对、ZAKER 动态加载（load_collector）。
在项目根目录（与 main.py 同级）执行: python test_migration.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKIP_COLLECTOR_STEMS = frozenset({"__init__", "template_collector"})
SKIP_YAML_STEMS = frozenset({"template"})


def _line(msg: str) -> None:
    print(msg)


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def main() -> int:
    print("=" * 60)
    print(" 多平台内容采集系统 — 迁移验证（含 ZAKER）")
    print("=" * 60)

    failed = False

    # --- 路径（path_helper）---
    _line("\n【1】path_helper 路径常量（当前进程）")
    from utils.path_helper import (
        PROJECT_ROOT,
        BUILTIN_ROOT,
        COLLECTORS_DIR,
        PLATFORMS_DIR,
        CONFIG_DIR,
        COOKIES_DIR,
        DATA_DIR,
        LOGS_DIR,
        SNAPSHOTS_DIR,
        ENV_PATH,
        ICON_PATH,
        ENCRYPTION_KEY_PATH,
        ensure_runtime_dirs,
        ensure_sys_path_for_imports,
    )

    ensure_sys_path_for_imports(ROOT)

    if not getattr(sys, "frozen", False):
        if PROJECT_ROOT.resolve() != ROOT:
            _warn(
                f"当前工作目录推导的 PROJECT_ROOT={PROJECT_ROOT} 与脚本所在目录 {ROOT} 不一致；"
                "请在项目根目录执行: cd multi_platform_crawler && python test_migration.py"
            )
        else:
            _ok(f"PROJECT_ROOT 与脚本目录一致: {PROJECT_ROOT}")

    for label, p in [
        ("PROJECT_ROOT", PROJECT_ROOT),
        ("BUILTIN_ROOT", BUILTIN_ROOT),
        ("COLLECTORS_DIR", COLLECTORS_DIR),
        ("PLATFORMS_DIR", PLATFORMS_DIR),
        ("CONFIG_DIR", CONFIG_DIR),
        ("COOKIES_DIR", COOKIES_DIR),
        ("DATA_DIR", DATA_DIR),
        ("LOGS_DIR", LOGS_DIR),
        ("SNAPSHOTS_DIR", SNAPSHOTS_DIR),
        ("ENV_PATH", ENV_PATH),
        ("ICON_PATH", ICON_PATH),
        ("ENCRYPTION_KEY_PATH", ENCRYPTION_KEY_PATH),
    ]:
        _line(f"    {label} = {p}")

    try:
        ensure_runtime_dirs()
        _ok("ensure_runtime_dirs() 已执行（cookies/data/logs/snapshots）")
    except OSError as e:
        _fail(f"ensure_runtime_dirs 失败: {e}")
        failed = True

    # --- 外置文件配对（稳定平台白名单）---
    _line("\n【2】外置文件完整性（稳定平台 ↔ collectors/*.py + platforms/*.yaml）")
    from utils.platform_registry import PlatformRegistry

    stable = list(PlatformRegistry.STABLE_PLATFORMS)
    missing_py: list[str] = []
    missing_yaml: list[str] = []

    for pid in stable:
        py_path = COLLECTORS_DIR / f"{pid}.py"
        yml_path = PLATFORMS_DIR / f"{pid}.yaml"
        if not py_path.is_file():
            missing_py.append(pid)
        if not yml_path.is_file():
            missing_yaml.append(pid)

    if missing_py:
        _fail(f"缺少采集器: {', '.join(missing_py)}")
        failed = True
    else:
        _ok(f"稳定平台 {len(stable)} 个均有 collectors/{{id}}.py")

    if missing_yaml:
        _fail(f"缺少平台配置: {', '.join(missing_yaml)}")
        failed = True
    else:
        _ok(f"稳定平台 {len(stable)} 个均有 platforms/{{id}}.yaml")

    # 数量与孤儿文件提示（非致命）
    yaml_stems = {p.stem for p in PLATFORMS_DIR.glob("*.yaml") if p.stem not in SKIP_YAML_STEMS}
    collector_stems = {
        p.stem for p in COLLECTORS_DIR.glob("*.py") if p.stem not in SKIP_COLLECTOR_STEMS
    }
    extra_yaml = sorted(yaml_stems - set(stable))
    extra_py = sorted(collector_stems - set(stable))
    if extra_yaml:
        _warn(f"存在非稳定白名单的 YAML（可忽略若为历史文件）: {extra_yaml}")
    if extra_py:
        _warn(f"存在非稳定白名单的采集器 .py（可忽略若为模板/下线平台）: {extra_py}")

    _line(
        f"    统计: platforms 下有效 yaml {len(yaml_stems)} 个；"
        f"collectors 下有效 .py {len(collector_stems)} 个；稳定平台 {len(stable)} 个"
    )

    # --- ZAKER 动态加载 ---
    _line('\n【3】ZAKER — load_collector("zaker")')
    try:
        from utils.module_loader import load_collector

        obj = load_collector("zaker")
        _ok(f"加载成功，类型: {type(obj).__name__}")
    except Exception as e:
        _fail(str(e))
        failed = True

    # --- 可选文件 ---
    _line("\n【4】可选检查")
    if ENV_PATH.is_file():
        _ok(".env 存在")
    else:
        _warn(".env 不存在（迁移后需自行配置）")

    exposure = CONFIG_DIR / "exposure.yaml"
    if exposure.is_file():
        _ok("config/exposure.yaml 存在")
    else:
        _warn("config/exposure.yaml 不存在")

    print("-" * 60)
    if failed:
        print("RESULT: FAIL — 请先修复上述 [FAIL] 项")
        return 1
    print("RESULT: PASS — 可继续 check_before_pack.py / build_exe.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
