"""
EchoServe V0.1.0 — 配置插件

将全局配置包装为插件，使其可以被其他插件依赖。
"""
from __future__ import annotations

import logging

# 延迟导入，避免循环依赖
logger = logging.getLogger("echoseve.config")


class ConfigPlugin:  # 不继承 BaizePlugin 以避免循环导入
    """
    配置插件。

    将全局 Settings 对象注册为 "config" 服务，
    并声明 plugin_id = "core.config"，供其他插件依赖。
    """

    plugin_id = "core.config"
    plugin_name = "配置管理"
    plugin_version = "0.1.0"
    dependencies = []

    def __init__(self):
        self.settings = None
        self._ctx = None

    async def on_init(self, ctx, fiber):
        """将配置对象注册到 Context"""
        self._ctx = ctx
        self.settings = ctx.settings
        ctx.provide("config", self.settings)
        ctx.provide("settings", self.settings)
        logger.info(f"[{self.plugin_id}] 配置已注册")

    async def on_destroy(self, ctx, fiber):
        logger.info(f"[{self.plugin_id}] 已销毁")
