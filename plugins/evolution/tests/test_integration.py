"""
EchoServe Evolution System — Integration Test

验证插件生命周期、Context 服务注册、路由挂载、事件订阅。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter

try:
    from ..plugin import EvolutionPlugin
    from config.settings import EvolutionConfig
except ImportError:
    from plugin import EvolutionPlugin
    from config.settings import EvolutionConfig


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        yield Path(td)


@pytest.fixture
def mock_ctx(temp_dir):
    """模拟 BaizeContext"""
    ctx = MagicMock()
    ctx.settings = MagicMock()
    # 提供 evolution 配置
    config = EvolutionConfig(
        db_path=str(temp_dir / "evolution.db"),
        archive_dir=str(temp_dir / "archive"),
        fallback_dir=str(temp_dir / "fallback"),
    )
    ctx.settings.evolution = config
    ctx.root_dir = temp_dir

    # 模拟 provide / inject
    ctx._registry = {}

    def _provide(key, value):
        ctx._registry[key] = value

    def _inject(key, default=None):
        return ctx._registry.get(key, default)

    ctx.provide = _provide
    ctx.inject = _inject
    return ctx


@pytest.fixture
def mock_fiber():
    return MagicMock()


@pytest.fixture
def mock_event_bus():
    """模拟 EventBus"""
    bus = MagicMock()
    bus._handlers = {}

    def _subscribe(event, handler):
        bus._handlers.setdefault(event, []).append(handler)

    bus.subscribe = _subscribe
    return bus


class TestEvolutionPluginLifecycle:
    """插件生命周期集成测试。"""

    @pytest.mark.asyncio
    async def test_lifecycle(self, mock_ctx, mock_fiber, mock_event_bus, temp_dir):
        """完整生命周期：load → init → start → stop → destroy"""
        plugin = EvolutionPlugin()

        # 1. on_load
        await plugin.on_load(mock_ctx, mock_fiber)
        assert plugin.ctx is mock_ctx
        assert plugin.config is not None
        assert plugin.store is not None
        assert plugin.collector is not None
        assert plugin.failover is not None
        assert plugin._param_pool is not None
        assert plugin._experimenter is not None
        assert plugin._evaluator is not None

        # 验证服务已注册
        assert mock_ctx.inject("evolution") is plugin
        assert mock_ctx.inject("evolution_store") is plugin.store

        # 2. on_init
        plugin_router = APIRouter()
        mock_ctx.provide("http_router", plugin_router)
        await plugin.on_init(mock_ctx, mock_fiber)

        # 3. on_start
        mock_ctx.provide("event_bus", mock_event_bus)
        await plugin.on_start(mock_ctx, mock_fiber)

        # 验证事件订阅
        assert "chat.complete" in mock_event_bus._handlers
        assert "skill.execute" in mock_event_bus._handlers
        assert "user.feedback" in mock_event_bus._handlers
        assert "route.decision" in mock_event_bus._handlers
        assert "system.metric" in mock_event_bus._handlers

        # 4. on_stop
        await plugin.on_stop(mock_ctx, mock_fiber)
        assert plugin._evolution_task is None or plugin._evolution_task.done()

        # 5. on_destroy
        await plugin.on_destroy(mock_ctx, mock_fiber)

    @pytest.mark.asyncio
    async def test_event_handlers_no_crash(self, mock_ctx, mock_fiber, mock_event_bus, temp_dir):
        """事件处理器不应抛异常，即使事件数据为空。"""
        plugin = EvolutionPlugin()
        await plugin.on_load(mock_ctx, mock_fiber)
        await plugin.on_init(mock_ctx, mock_fiber)
        await plugin.on_start(mock_ctx, mock_fiber)

        # 测试各类空事件
        for handler_name in [
            "_on_chat_complete",
            "_on_skill_execute",
            "_on_user_feedback",
            "_on_route_decision",
            "_on_system_metric",
        ]:
            handler = getattr(plugin, handler_name)
            # 空数据不应抛异常
            try:
                handler({})
            except Exception as e:
                pytest.fail(f"{handler_name}({{}}) raised {e}")

        await plugin.on_stop(mock_ctx, mock_fiber)
        await plugin.on_destroy(mock_ctx, mock_fiber)

    @pytest.mark.asyncio
    async def test_degradation_blocks_collection(self, mock_ctx, mock_fiber, mock_event_bus, temp_dir):
        """降级到 L2 应阻止事件采集。"""
        plugin = EvolutionPlugin()
        await plugin.on_load(mock_ctx, mock_fiber)
        await plugin.on_init(mock_ctx, mock_fiber)
        await plugin.on_start(mock_ctx, mock_fiber)

        # 手动降级到 Level 2
        await plugin.failover.manual_degrade(
            plugin.failover.get_current_level().__class__.LEVEL_2,
            "test"
        )

        # 事件处理器应静默返回
        plugin._on_chat_complete({"session_id": "test"})
        # 无异常即通过

        await plugin.on_stop(mock_ctx, mock_fiber)
        await plugin.on_destroy(mock_ctx, mock_fiber)

    def test_get_status(self, mock_ctx, mock_fiber, temp_dir):
        """get_status 应返回完整状态字典。"""
        plugin = EvolutionPlugin()
        asyncio.run(plugin.on_load(mock_ctx, mock_fiber))
        status = plugin.get_status()
        assert "plugin_version" in status
        assert "degradation_level" in status
        assert "experiments" in status
        assert "templates_pending" in status
        assert "templates_active" in status

    def test_create_experiment_degraded(self, mock_ctx, mock_fiber, temp_dir):
        """降级状态下创建实验应抛异常。"""
        plugin = EvolutionPlugin()
        asyncio.run(plugin.on_load(mock_ctx, mock_fiber))
        asyncio.run(plugin.on_init(mock_ctx, mock_fiber))

        # 降级
        asyncio.run(plugin.failover.manual_degrade(
            plugin.failover.get_current_level().__class__.LEVEL_1,
            "test"
        ))

        with pytest.raises(RuntimeError, match="Cannot create experiment"):
            asyncio.run(plugin.create_experiment("top_k", [5, 10], "retrieval_hit_rate"))


class TestEvolutionPluginAPI:
    """REST API 集成测试。"""

    @pytest.mark.asyncio
    async def test_router_mounted(self, mock_ctx, mock_fiber, temp_dir):
        """验证路由正确挂载到 plugin_router。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        plugin = EvolutionPlugin()
        await plugin.on_load(mock_ctx, mock_fiber)

        app = FastAPI()
        plugin_router = APIRouter()
        mock_ctx.provide("http_router", plugin_router)
        await plugin.on_init(mock_ctx, mock_fiber)
        app.include_router(plugin_router)

        # 使用 TestClient 直接测试端点
        client = TestClient(app)
        response = client.get("/evolution/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["store_connected"] == "True"
