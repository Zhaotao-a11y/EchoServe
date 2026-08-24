"""
EchoServe V0.1.0 — Fiber

插件生命周期管理器。
每个插件运行在一个 Fiber 中，状态机：
    UNLOADED → LOADED → INITIALIZED → STARTED → STOPPED → DESTROYED

Fiber 负责：
- 按依赖顺序加载插件
- 管理插件状态转换
- 在状态转换时调用插件的生命周期钩子
- 处理插件故障隔离（一个插件崩溃不影响其他）
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Dict, List, Optional

# 延迟导入，避免循环依赖
# BaizeContext 和 BaizePlugin 在类型注解中以字符串形式引用
logger = logging.getLogger("echoseve.fiber")


class FiberState(Enum):
    UNLOADED = "unloaded"
    LOADED = "loaded"
    INITIALIZED = "initialized"
    STARTED = "started"
    STOPPED = "stopped"
    DESTROYED = "destroyed"
    ERROR = "error"


class Fiber:
    """
    单个插件的运行载体。

    每个 Fiber 包裹一个 BaizePlugin，管理其完整生命周期。
    """

    def __init__(self, plugin, ctx):  # plugin: BaizePlugin, ctx: BaizeContext 延迟引用
        self.plugin = plugin
        self.ctx = ctx
        self.state = FiberState.UNLOADED
        self.error: Optional[Exception] = None
        self._tasks: List[asyncio.Task] = []

    async def load(self):
        """加载插件（注册到 Context）"""
        if self.state != FiberState.UNLOADED:
            return
        try:
            self.plugin._ctx = self.ctx
            self.state = FiberState.LOADED
            logger.info(f"[Fiber] Loaded: {self.plugin.plugin_id}")
        except Exception as e:
            self.state = FiberState.ERROR
            self.error = e
            logger.error(f"[Fiber] Load failed: {self.plugin.plugin_id} -> {e}")

    async def init(self):
        """初始化插件"""
        if self.state != FiberState.LOADED:
            return
        try:
            await self.plugin._init(self.ctx, self)
            self.state = FiberState.INITIALIZED
            logger.info(f"[Fiber] Initialized: {self.plugin.plugin_id}")
        except Exception as e:
            self.state = FiberState.ERROR
            self.error = e
            logger.error(f"[Fiber] Init failed: {self.plugin.plugin_id} -> {e}")
            raise

    async def start(self):
        """启动插件"""
        if self.state != FiberState.INITIALIZED:
            return
        try:
            await self.plugin._start(self.ctx, self)
            self.state = FiberState.STARTED
            logger.info(f"[Fiber] Started: {self.plugin.plugin_id}")
        except Exception as e:
            self.state = FiberState.ERROR
            self.error = e
            logger.error(f"[Fiber] Start failed: {self.plugin.plugin_id} -> {e}")
            raise

    async def stop(self):
        """停止插件"""
        if self.state not in (FiberState.STARTED, FiberState.ERROR):
            return
        try:
            await self.plugin._stop(self.ctx, self)
            # 取消所有关联任务
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            self._tasks.clear()
            self.state = FiberState.STOPPED
            logger.info(f"[Fiber] Stopped: {self.plugin.plugin_id}")
        except Exception as e:
            logger.error(f"[Fiber] Stop failed: {self.plugin.plugin_id} -> {e}")

    async def destroy(self):
        """销毁插件，释放资源"""
        try:
            await self.plugin._destroy(self.ctx, self)
            self.state = FiberState.DESTROYED
            logger.info(f"[Fiber] Destroyed: {self.plugin.plugin_id}")
        except Exception as e:
            logger.error(f"[Fiber] Destroy failed: {self.plugin.plugin_id} -> {e}")

    def add_task(self, coro_or_task) -> asyncio.Task:
        """创建一个与 Fiber 关联的后台任务"""
        if isinstance(coro_or_task, asyncio.Task):
            task = coro_or_task
        elif asyncio.iscoroutine(coro_or_task):
            task = asyncio.create_task(coro_or_task)
        else:
            raise TypeError(f"Expected coroutine or Task, got {type(coro_or_task)}")
        self._tasks.append(task)
        return task

    @property
    def is_healthy(self) -> bool:
        return self.state == FiberState.STARTED and self.error is None


class FiberManager:
    """
    管理所有 Fiber 的调度器。

    职责：
    - 按依赖顺序初始化和启动插件
    - 处理插件间依赖解析
    - 批量启停
    - 健康检查
    """

    def __init__(self, ctx):  # ctx: BaizeContext 延迟引用
        self.ctx = ctx
        self._fibers: Dict[str, Fiber] = {}
        self._dependency_graph: Dict[str, List[str]] = {}

    def register(self, plugin: BaizePlugin) -> Fiber:
        """注册一个插件，创建对应的 Fiber"""
        fiber = Fiber(plugin, self.ctx)
        self._fibers[plugin.plugin_id] = fiber
        self._dependency_graph[plugin.plugin_id] = list(plugin.dependencies)
        logger.info(f"[FiberManager] Registered: {plugin.plugin_id} "
                    f"(deps: {plugin.dependencies})")
        return fiber

    def _resolve_order(self) -> List[str]:
        """
        拓扑排序，按依赖顺序返回插件 ID 列表。
        使用 Kahn 算法检测循环依赖。
        """
        in_degree: Dict[str, int] = {pid: 0 for pid in self._fibers}
        graph: Dict[str, List[str]] = {pid: [] for pid in self._fibers}

        for pid, deps in self._dependency_graph.items():
            for dep in deps:
                if dep in graph:
                    graph[dep].append(pid)
                    in_degree[pid] += 1
                else:
                    logger.warning(f"[FiberManager] Plugin '{pid}' depends on "
                                   f"'{dep}' which is not registered")

        # Kahn 算法
        queue = [pid for pid, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self._fibers):
            raise RuntimeError("[FiberManager] Circular dependency detected!")

        return order

    async def start_all(self):
        """按依赖顺序启动所有插件"""
        order = self._resolve_order()
        logger.info(f"[FiberManager] Start order: {' -> '.join(order)}")

        for pid in order:
            fiber = self._fibers[pid]
            await fiber.load()
            await fiber.init()
            await fiber.start()

        await self.ctx.start()
        logger.info("[FiberManager] All plugins started")

    async def stop_all(self):
        """逆序停止所有插件"""
        order = self._resolve_order()
        for pid in reversed(order):
            fiber = self._fibers[pid]
            await fiber.stop()

        await self.ctx.stop()
        logger.info("[FiberManager] All plugins stopped")

    async def destroy_all(self):
        """销毁所有插件"""
        for fiber in self._fibers.values():
            await fiber.destroy()
        self.ctx.destroy()
        logger.info("[FiberManager] All plugins destroyed")

    def get_fiber(self, plugin_id: str) -> Optional[Fiber]:
        return self._fibers.get(plugin_id)

    def health_check(self) -> Dict[str, str]:
        """返回所有插件的健康状态"""
        return {
            pid: fiber.state.value
            for pid, fiber in self._fibers.items()
        }
