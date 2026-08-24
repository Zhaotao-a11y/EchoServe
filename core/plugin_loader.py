"""
EchoServe V0.1.0 — 插件加载器

支持两种模式：
1. 自动发现：扫描 plugins/ 目录，自动导入并注册
2. 手动注册：显式指定插件列表（推荐用于生产环境）

用法：
    loader = PluginLoader(ctx, fiber_manager)
    loader.auto_discover("plugins")
    loader.load_all()
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import List, Type

from .context import BaizeContext
from .fiber import FiberManager
from .plugin import BaizePlugin

logger = logging.getLogger("echoseve.loader")


class PluginLoader:
    """插件发现与加载器"""

    def __init__(self, ctx: BaizeContext, fiber_manager: FiberManager):
        self.ctx = ctx
        self.fiber_manager = fiber_manager
        self._plugin_classes: List[Type[BaizePlugin]] = []

    def register(self, plugin_cls: Type[BaizePlugin]):
        """手动注册一个插件类

        特例：ConfigPlugin 不继承 BaizePlugin（避免循环导入），
        但必须具有 plugin_id / dependencies / on_init / on_destroy 接口。
        """
        if not issubclass(plugin_cls, BaizePlugin):
            # 检查是否为 ConfigPlugin（特殊豁免）
            if plugin_cls.__name__ == "ConfigPlugin":
                pass  # 允许通过
            else:
                raise TypeError(f"{plugin_cls} must inherit from BaizePlugin")

        if not plugin_cls.plugin_id:
            raise ValueError(f"{plugin_cls.__name__} must set plugin_id")

        self._plugin_classes.append(plugin_cls)
        logger.info(f"[Loader] Registered: {plugin_cls.plugin_id}")

    def auto_discover(self, package_name: str = "plugins"):
        """
        自动发现指定包下的所有插件模块。
        约定：每个插件模块必须包含至少一个 BaizePlugin 子类，
        且该子类设置了 plugin_id。
        """
        logger.info(f"[Loader] Auto-discovering plugins in '{package_name}'")

        try:
            package = importlib.import_module(package_name)
        except ImportError:
            logger.warning(f"[Loader] Package '{package_name}' not found")
            return

        package_path = Path(package.__file__).parent if package.__file__ else None

        if not package_path:
            return

        # 遍历子模块
        for module_info in pkgutil.iter_modules([str(package_path)]):
            if module_info.name.startswith("_"):
                continue

            module_name = f"{package_name}.{module_info.name}"
            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.error(f"[Loader] Failed to import {module_name}: {e}")
                continue

            # 查找 BaizePlugin 子类
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if (issubclass(obj, BaizePlugin) and
                        obj is not BaizePlugin and
                        obj.plugin_id):
                    self.register(obj)

    def load_all(self):
        """实例化所有已注册的插件类，注册到 FiberManager 和 Context"""
        for plugin_cls in self._plugin_classes:
            try:
                plugin_instance = plugin_cls()
                self.fiber_manager.register(plugin_instance)
                # 同时注册到 Context 的插件列表
                self.ctx.register_plugin(plugin_instance)
            except Exception as e:
                logger.error(f"[Loader] Failed to instantiate {plugin_cls.__name__}: {e}")
                raise

        logger.info(f"[Loader] Total plugins loaded: {len(self._plugin_classes)}")

    def get_plugin_ids(self) -> List[str]:
        """返回所有已注册插件的 ID"""
        return [cls.plugin_id for cls in self._plugin_classes]
