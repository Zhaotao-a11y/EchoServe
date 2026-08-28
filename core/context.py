"""
EchoServe V0.1.0 — BaizeContext（共享上下文容器）

核心运行时对象。所有插件通过它：
    - 注册服务（provide）
    - 获取依赖（inject）
    - 发布/订阅事件
    - 访问全局配置

设计原则：
    - 所有服务注册都附带可逆的清理操作（effect）
    - 销毁时按 LIFO 顺序执行清理，精确恢复状态
    - 借鉴 Cordis 的"时空可组合性"理念
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

# 延迟导入 plugin，避免循环依赖（plugin.py → context → plugin）
from .events import EventBus
from config.settings import settings as default_settings

def _get_fiber_classes():
    from .fiber import Fiber, FiberManager
    return Fiber, FiberManager

def _get_plugin_class():
    try:
        from .plugin import BaizePlugin
        return BaizePlugin
    except (ImportError, RuntimeError):
        # 模块循环导入中，返回 None，调用方需处理
        return None

logger = logging.getLogger("echoserve.context")

# Sentinel to distinguish "no default passed" vs "default=None"
_MISSING = object()


class BaizeContext:
    """
    共享上下文容器。

    生命周期：
        创建 → 插件注册 → 启动 → 运行 → 停止 → 销毁

    用法：
        ctx = BaizeContext()
        ctx.provide("llm", llm_client)
        llm = ctx.inject("llm")
        ctx.publish("event.name", data)
    """

    def __init__(self, settings=None):
        self.settings = settings or default_settings
        self._services: dict[str, Any] = {}
        self._effects: list[Callable] = []
        self._event_bus: (EventBus | None) = None
        self._plugins: dict[str, BaizePlugin] = {}
        self._root_dir: Path = Path(__file__).resolve().parent.parent
        self.logger = logging.getLogger("echoseve")

        # 初始化事件总线
        self._event_bus = EventBus()
        self._services["event_bus"] = self._event_bus

        # 注入配置
        self._services["settings"] = self.settings

        # 状态
        self._started = False

    # ─── 服务注册/查找 ──────────────────────────

    def provide(self, key: str, service: Any, cleanup: (Callable | None) = None):
        """
        注册一个服务到 Context。

        Args:
            key: 服务唯一标识
            service: 服务对象
            cleanup: 可选的清理函数（销毁时调用）
        """
        self._services[key] = service
        if cleanup:
            self._effects.append(cleanup)
        else:
            # 默认清理：从 services 中移除
            self._effects.append(lambda: self._services.pop(key, None))
        logger.debug(f"[Context] Provided: {key}")

    def inject(self, key: str, default: Any = _MISSING) -> Any:
        """
        从 Context 获取服务。

        Args:
            key: 服务键名
            default: 当服务不存在时返回的默认值（不传则抛 KeyError）

        Raises:
            KeyError: 服务不存在且未提供 default
        """
        if key not in self._services:
            if default is not _MISSING:
                return default
            raise KeyError(f"Service '{key}' not found in context")
        return self._services[key]

    def has(self, key: str) -> bool:
        """检查服务是否存在"""
        return key in self._services

    def remove(self, key: str) -> bool:
        """移除一个服务"""
        if key in self._services:
            del self._services[key]
            return True
        return False

    # ─── 插件管理 ──────────────────────────────

    def register_plugin(self, plugin):
        """注册一个插件实例"""
        self._plugins[plugin.plugin_id] = plugin
        logger.debug(f"[Context] Plugin registered: {plugin.plugin_id}")

    def list_plugins(self):
        """列出所有已注册插件"""
        return dict(self._plugins)

    def get_plugin(self, plugin_id: str):
        """获取指定插件"""
        return self._plugins.get(plugin_id)

    # ─── 事件总线代理 ──────────────────────────

    def publish(self, event_name: str, data: Any = None):
        """发布事件"""
        if self._event_bus:
            self._event_bus.publish(event_name, data)

    def subscribe(self, event_name: str, handler: Callable):
        """订阅事件"""
        if self._event_bus:
            self._event_bus.subscribe(event_name, handler)

    def subscribe_wildcard(self, handler: Callable):
        """订阅所有事件"""
        if self._event_bus:
            self._event_bus.subscribe_wildcard(handler)

    # ─── 生命周期 ──────────────────────────────

    async def start(self):
        """启动 Context（启动事件总线）"""
        if self._started:
            return
        self._started = True
        logger.info("[Context] Started")

    async def stop(self):
        """停止 Context"""
        if not self._started:
            return
        self._started = False
        logger.info("[Context] Stopped")

    def destroy(self):
        """
        销毁 Context，按 LIFO 顺序执行所有清理操作。
        精确恢复初始状态。
        """
        logger.info(f"[Context] Destroying ({len(self._effects)} effects)...")
        while self._effects:
            cleanup = self._effects.pop()
            try:
                cleanup()
            except Exception as e:
                logger.error(f"[Context] Cleanup error: {e}")

        self._services.clear()
        self._plugins.clear()

        if self._event_bus:
            self._event_bus.clear()

        logger.info("[Context] Destroyed")

    # ─── 配置访问 ──────────────────────────────

    @property
    def root_dir(self) -> Path:
        """项目根目录"""
        return self._root_dir

    # ─── 日志配置 ──────────────────────────────

    def _setup_logging(self):
        """初始化日志配置"""
        log_cfg = self.settings.log
        level = getattr(logging, log_cfg.level.upper(), logging.INFO)

        # 确保日志目录存在
        from pathlib import Path
        log_path = Path(log_cfg.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 避免重复添加 handler
        if hasattr(self.logger, '_echoseve_configured'):
            return
        self.logger.setLevel(level)

        # 文件处理器
        file_handler = logging.FileHandler(log_cfg.file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s - %(message)s"
        ))
        self.logger.addHandler(file_handler)

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter(
            "%(levelname)s %(name)s - %(message)s"
        ))
        self.logger.addHandler(console_handler)
        self.logger._echoseve_configured = True

    # ─── 工厂方法 ──────────────────────────────

    def create_fiber(self, plugin: BaizePlugin):
        """为插件创建 Fiber（延迟导入，避免循环依赖）"""
        Fiber, _ = _get_fiber_classes()
        return Fiber(plugin, self)

    # ─── 调试辅助 ──────────────────────────────

    def debug_info(self) -> dict[str, Any]:
        """返回 Context 当前状态（调试用）"""
        return {
            "services": list(self._services.keys()),
            "plugins": list(self._plugins.keys()),
            "started": self._started,
            "effects_count": len(self._effects),
            "root_dir": str(self._root_dir),
        }
