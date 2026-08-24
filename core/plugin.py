"""
EchoServe V0.1.0 — BaizePlugin 基类

所有插件的抽象基类。
每个插件有 5 个生命周期钩子：
    on_load  → on_init → on_start → on_stop → on_destroy

插件通过 ctx.provide(key, service) 注册服务，
其他插件通过 ctx.inject(key) 获取依赖。

延迟导入 Fiber 类型以避免循环依赖。
"""
from __future__ import annotations

import logging
from typing import Any, List, Callable

logger = logging.getLogger("echoseve.plugin")


class BaizePlugin:
    """
    插件基类。子类必须设置 plugin_id 和 plugin_name。

    生命周期：
        UNLOADED → LOADED → INITIALIZED → STARTED → STOPPED → DESTROYED

    子类可重写以下钩子（均为 async）：
        on_load(ctx, fiber)    — 注册服务
        on_init(ctx, fiber)    — 初始化资源
        on_start(ctx, fiber)   — 启动后台任务
        on_stop(ctx, fiber)    — 停止任务
        on_destroy(ctx, fiber)  — 释放资源
    """

    plugin_id: str = ""
    plugin_name: str = ""
    plugin_version: str = "0.1.0"
    dependencies: List[str] = []

    # ─── 生命周期（由 Fiber 调用）──────────────────

    async def _load(self, ctx, fiber):
        """Fiber 调用：加载阶段"""
        self._ctx = ctx
        await self.on_load(ctx, fiber)

    async def _init(self, ctx, fiber):
        """Fiber 调用：初始化阶段"""
        self._ctx = ctx
        await self.on_init(ctx, fiber)

    async def _start(self, ctx, fiber):
        """Fiber 调用：启动阶段"""
        self._ctx = ctx
        await self.on_start(ctx, fiber)

    async def _stop(self, ctx, fiber):
        """Fiber 调用：停止阶段"""
        await self.on_stop(ctx, fiber)

    async def _destroy(self, ctx, fiber):
        """Fiber 调用：销毁阶段"""
        await self.on_destroy(ctx, fiber)

    # ─── 子类可重写的钩子（默认空实现）──────

    async def on_load(self, ctx, fiber):
        """加载时调用。在此注册服务。"""

    async def on_init(self, ctx, fiber):
        """初始化时调用。创建资源、连接外部服务。"""

    async def on_start(self, ctx, fiber):
        """启动时调用。启动后台任务、定时器等。"""

    async def on_stop(self, ctx, fiber):
        """停止时调用。取消任务、关闭连接。"""

    async def on_destroy(self, ctx, fiber):
        """销毁时调用。释放所有资源。"""

    # ─── 便捷方法 ──────────────────────────────────

    def provide(self, key: str, service: Any):
        """向 Context 注册一个服务"""
        self._ctx.provide(key, service)

    def inject(self, key: str, default: Any = None) -> Any:
        """从 Context 获取一个服务"""
        if self._ctx is None:
            return default
        return self._ctx.inject(key, default)

    def has(self, key: str) -> bool:
        """检查 Context 中是否存在某个服务"""
        return self._ctx.has(key)

    def publish(self, event_name: str, data: Any = None):
        """发布事件到事件总线"""
        bus = self._ctx.inject("event_bus", None)
        if bus:
            bus.publish(event_name, data)

    def subscribe(self, event_name: str, handler: Callable):
        """订阅事件"""
        bus = self._ctx.inject("event_bus", None)
        if bus:
            bus.subscribe(event_name, handler)

    # ─── 延迟导入 ──────────────────────────────────

    def _get_fiber_type(self):
        """延迟导入 Fiber 类型（避免循环依赖）"""
        from .fiber import Fiber
        return Fiber
