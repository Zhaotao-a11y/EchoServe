"""
EchoServe V0.1.0 — 配置插件

将全局配置包装为插件，使其可以被其他插件依赖。
继承 BaizePlugin，使用延迟导入避免循环依赖。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("echoseve.config")

# 延迟导入 BaizePlugin，避免循环依赖
from core.plugin import BaizePlugin  # noqa: E402


class ConfigPlugin(BaizePlugin):
    """
    配置插件。

    将全局 Settings 对象注册为 "config" 服务，
    并声明 plugin_id = "core.config"，供其他插件依赖。

    继承 BaizePlugin，但所有依赖（settings）均在 on_init 中延迟导入，
    避免模块加载时的循环依赖。
    """

    plugin_id = "core.config"
    plugin_name = "配置管理"
    plugin_version = "0.1.0"
    dependencies = []

    def __init__(self):
        self.settings = None
        self._ctx = None

    async def on_init(self, ctx: "BaizeContext", fiber: "Fiber"):
        """将配置对象注册到 Context"""
        self._ctx = ctx
        # 延迟导入，避免循环依赖
        from config.settings import settings
        self.settings = settings
        ctx.provide("config", self.settings)
        ctx.provide("settings", self.settings)
        logger.info(f"[{self.plugin_id}] 配置已注册")

    async def on_destroy(self, ctx: "BaizeContext", fiber: "Fiber"):
        logger.info(f"[{self.plugin_id}] 已销毁")
