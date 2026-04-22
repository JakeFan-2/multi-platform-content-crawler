# utils/module_loader.py
"""
外置 collectors/ 目录动态加载采集器模块。
不修改采集器内部业务逻辑，仅提供运行时按平台标识加载并实例化。
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.path_helper import COLLECTORS_DIR


def _collector_class_name(platform_name: str) -> str:
    """toutiao -> ToutiaoCollector；geekpark_wechat -> GeekparkWechatCollector"""
    parts = platform_name.split("_")
    return "".join(p.capitalize() for p in parts) + "Collector"


class _LegacyCrawlCollectorAdapter:
    """
    兼容当前各采集器：仅暴露模块级 async def crawl(...)，无 *Collector 类时由加载器返回此适配器，
    以统一满足调度端的 set_headless_override → ensure_login → crawl 调用顺序。
    """

    def __init__(self, module: Any) -> None:
        self._module = module
        self._headless_override: Optional[bool] = None

    def set_headless_override(self, headless: bool) -> None:
        self._headless_override = headless

    async def ensure_login(self) -> bool:
        # 登录校验在现有各平台 crawl() 内部完成
        return True

    async def crawl(
        self,
        targets: List[str],
        result_callback=None,
        unmatched_callback=None,
        headless: bool = True,
        login_failed_callback=None,
        keywords_map: Optional[Dict[str, List[str]]] = None,
    ) -> Tuple[List[Dict], List[str]]:
        h = self._headless_override if self._headless_override is not None else headless
        kwargs: Dict[str, Any] = dict(
            targets=targets,
            result_callback=result_callback,
            unmatched_callback=unmatched_callback,
            headless=h,
            login_failed_callback=login_failed_callback,
        )
        if keywords_map is not None and "keywords_map" in inspect.signature(self._module.crawl).parameters:
            kwargs["keywords_map"] = keywords_map
        return await self._module.crawl(**kwargs)


def load_collector(platform_name: str) -> Any:
    """
    从 COLLECTORS_DIR / {platform_name}.py 动态加载并返回采集器实例。

    优先实例化 {Platform}Collector；若不存在则回退为包装模块级 crawl 的适配器。

    Raises:
        ImportError: 搜狐号已下线、文件不存在、规格无效、执行失败、缺少入口或实例化失败。
    """
    if platform_name == "sohu":
        raise ImportError("搜狐号已下线，禁止加载")

    if not platform_name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", platform_name):
        raise ImportError(f"非法的平台标识: {platform_name!r}")

    collector_path: Path = COLLECTORS_DIR / f"{platform_name}.py"
    if not collector_path.is_file():
        raise ImportError(f"采集器文件不存在: {collector_path}")

    class_name = _collector_class_name(platform_name)
    mod_name = f"_dyn_collector_{platform_name}"

    try:
        spec = importlib.util.spec_from_file_location(mod_name, collector_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法创建采集器加载规格: {collector_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    except ImportError:
        raise
    except Exception as e:
        raise ImportError(f"加载采集器模块失败 ({platform_name}): {e}") from e

    cls = getattr(module, class_name, None)
    if cls is not None and isinstance(cls, type):
        try:
            return cls()
        except Exception as e:
            raise ImportError(f"实例化 {class_name} 失败: {e}") from e

    crawl_fn = getattr(module, "crawl", None)
    if callable(crawl_fn):
        return _LegacyCrawlCollectorAdapter(module)

    raise ImportError(
        f"模块 {collector_path} 中既无类 {class_name}，也无可调用的 crawl()，无法作为采集器加载"
    )


# def reload_collector(platform_name: str) -> Any:
#     """可选：从磁盘重新加载并返回新实例。需先从 sys.modules 移除 _dyn_collector_{name} 再调用 load_collector。"""
#     raise NotImplementedError
