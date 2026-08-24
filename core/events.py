"""
EchoServe V0.1.0 — 事件总线

轻量级发布-订阅模式，支持同步和异步事件分发。
插件间通过事件解耦通信，避免直接依赖。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("echoseve.events")


class EventBus:
    """
    事件总线，管理事件的发布与订阅。

    用法：
        bus = EventBus()
        bus.subscribe("retrieval.done", handler)
        bus.publish("retrieval.done", {"docs": [...]})

    事件命名约定：使用 "." 分隔的层级命名
    如：retrieval.done, llm.stream, chat.started, plugin.loaded
    """

    def __init__(self):
        self._sync_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._async_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._wildcard_handlers: List[Callable] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """设置事件循环（由 Fiber 在 start 阶段注入）"""
        self._loop = loop

    def subscribe(self, event_name: str, handler: Callable):
        """订阅事件（自动判断同步/异步）"""
        if asyncio.iscoroutinefunction(handler):
            self._async_handlers[event_name].append(handler)
        else:
            self._sync_handlers[event_name].append(handler)
        logger.debug(f"[EventBus] Subscribed: {event_name} -> {handler.__name__}")

    def subscribe_wildcard(self, handler: Callable):
        """订阅所有事件（用于审计日志等场景）"""
        self._wildcard_handlers.append(handler)

    def unsubscribe(self, event_name: str, handler: Callable):
        """取消订阅"""
        if handler in self._sync_handlers.get(event_name, []):
            self._sync_handlers[event_name].remove(handler)
        if handler in self._async_handlers.get(event_name, []):
            self._async_handlers[event_name].remove(handler)

    def publish(self, event_name: str, data: Any = None):
        """发布事件（同步方法，非阻塞）"""
        # 同步处理器
        for handler in self._sync_handlers.get(event_name, []):
            try:
                handler(data)
            except Exception as e:
                logger.error(f"[EventBus] Sync handler error: {e}")

        # 异步处理器（调度到事件循环）
        async_handlers = self._async_handlers.get(event_name, [])
        if async_handlers and self._loop and not self._loop.is_closed():
            for handler in async_handlers:
                self._loop.create_task(self._safe_async(handler, data))

        # 通配符处理器
        for handler in self._wildcard_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    if self._loop and not self._loop.is_closed():
                        self._loop.create_task(self._safe_async(handler, {"event": event_name, "data": data}))
                else:
                    handler(event_name, data)
            except Exception as e:
                logger.error(f"[EventBus] Wildcard handler error: {e}")

    async def publish_async(self, event_name: str, data: Any = None):
        """发布事件（异步方法，等待所有处理器完成）"""
        # 同步处理器
        for handler in self._sync_handlers.get(event_name, []):
            try:
                handler(data)
            except Exception as e:
                logger.error(f"[EventBus] Sync handler error: {e}")

        # 异步处理器
        tasks = []
        for handler in self._async_handlers.get(event_name, []):
            tasks.append(self._safe_async(handler, data))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_async(self, handler: Callable, data: Any):
        try:
            await handler(data)
        except Exception as e:
            logger.error(f"[EventBus] Async handler error: {e}")

    def clear(self):
        """清空所有订阅（用于测试）"""
        self._sync_handlers.clear()
        self._async_handlers.clear()
        self._wildcard_handlers.clear()
