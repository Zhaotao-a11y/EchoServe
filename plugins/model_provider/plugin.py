# -*- coding: utf-8 -*-
"""
EchoServe — Model Provider Plugin (Phase 1.3)

Multi-Model Provider management plugin.
Reads config/models.yaml, initializes all providers, and provides
unified chat/chat_stream services with intelligent routing & fallback.

Plugin ID: core.model_provider
Dependencies: none (standalone, does not depend on core.llm)
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .base import (
    ModelRegistry,
    CostTracker,
    SmartRouter,
    ChatRequest,
    ChatResponse,
    ProviderConfig,
    ModelConfig,
)
from .providers import create_provider

logger = logging.getLogger("echoserve.model_provider")


class ModelProviderPlugin(BaizePlugin):
    """
    Multi-Model Provider Management Plugin.

    Responsibilities:
    - Read config/models.yaml at startup
    - Initialize all enabled providers
    - Run health checks
    - Provide model_provider service (unified chat/stream/stats)
    - Periodic health check polling
    """

    plugin_id = "core.model_provider"
    plugin_name = "Multi-Model Provider"
    plugin_version = "0.1.0"
    dependencies = []  # Standalone, does not depend on core.llm

    def __init__(self):
        self._registry: ModelRegistry | None = None
        self._config_path: str = ""
        self._health_check_interval: float = 300
        self._health_check_task: asyncio.Task | None = None

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """Initialize: read config, create providers, register service"""
        # Locate the config file
        project_root = Path(__file__).resolve().parent.parent.parent
        self._config_path = str(project_root / "config" / "models.yaml")

        if not Path(self._config_path).exists():
            logger.warning(
                f"[{self.plugin_id}] Config file not found: {self._config_path}. "
                "Using empty provider list."
            )
            self._registry = ModelRegistry()
            self.provide("model_provider", self)
            return

        # Read YAML configuration
        with open(self._config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        # Create registry
        self._registry = ModelRegistry()

        # Set daily budget
        daily_budget = raw_config.get("daily_budget_usd", 50.0)
        self._registry.set_daily_budget(float(daily_budget))
        logger.info(f"[{self.plugin_id}] Daily budget: ${daily_budget}")

        # Health check interval
        self._health_check_interval = float(
            raw_config.get("health_check_interval", 300)
        )

        # Register providers
        providers_config = raw_config.get("providers", [])
        registered_count = 0

        for p_cfg in providers_config:
            if not p_cfg.get("enabled", True):
                logger.debug(
                    f"[{self.plugin_id}] Skipping disabled provider: {p_cfg.get('name')}"
                )
                continue

            # Resolve API key from environment
            api_key_env = p_cfg.get("api_key_env", "")
            api_key = os.getenv(api_key_env, "") if api_key_env else ""

            # Build ProviderConfig
            models = []
            for m_cfg in p_cfg.get("models", []):
                models.append(ModelConfig(
                    id=m_cfg["id"],
                    display_name=m_cfg.get("display_name", m_cfg["id"]),
                    provider_name=p_cfg["name"],
                    context_window=m_cfg.get("context_window", 32000),
                    max_tokens=m_cfg.get("max_tokens", 4096),
                    supports_vision=m_cfg.get("supports_vision", False),
                    supports_tools=m_cfg.get("supports_tools", False),
                    supports_streaming=m_cfg.get("supports_streaming", True),
                    cost_per_1k_input=m_cfg.get("cost_per_1k_input", 0.0),
                    cost_per_1k_output=m_cfg.get("cost_per_1k_output", 0.0),
                    quality_score=m_cfg.get("quality_score", 0.8),
                ))

            provider_config = ProviderConfig(
                name=p_cfg["name"],
                display_name=p_cfg.get("display_name", p_cfg["name"]),
                base_url=p_cfg.get("base_url", ""),
                api_key_env=api_key_env,
                api_key=api_key,
                timeout=float(p_cfg.get("timeout", 60)),
                max_retries=int(p_cfg.get("max_retries", 2)),
                models=models,
                enabled=True,
                weight=float(p_cfg.get("weight", 1.0)),
            )

            # Create provider instance
            provider_type = p_cfg.get("type", p_cfg["name"])
            provider = create_provider(provider_config, provider_type)

            # Health check (non-blocking, skip on failure)
            try:
                healthy = await provider.health_check()
                if healthy:
                    logger.info(
                        f"[{self.plugin_id}] Provider '{provider.name}' is healthy"
                    )
                else:
                    logger.warning(
                        f"[{self.plugin_id}] Provider '{provider.name}' health check failed, "
                        "will be marked unavailable but still registered"
                    )
            except Exception as e:
                logger.warning(
                    f"[{self.plugin_id}] Provider '{provider.name}' health check error: {e}"
                )

            self._registry.register(provider)
            registered_count += 1

        logger.info(
            f"[{self.plugin_id}] Registered {registered_count} providers, "
            f"{len(self._registry.list_models())} models total"
        )

        # Register service
        self.provide("model_provider", self)
        logger.info(f"[{self.plugin_id}] Registered as 'model_provider' service")

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        """Start: launch the health check polling task"""
        if self._health_check_interval > 0 and self._registry:
            self._health_check_task = asyncio.create_task(
                self._health_check_loop()
            )
            logger.info(
                f"[{self.plugin_id}] Health check polling started "
                f"(interval={self._health_check_interval}s)"
            )

    async def on_stop(self, ctx: BaizeContext, fiber: Fiber):
        """Stop: cancel the health check task"""
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            logger.info(f"[{self.plugin_id}] Health check polling stopped")

        # Close all providers' HTTP clients
        if self._registry:
            for provider in self._registry.list_providers():
                close_method = getattr(provider, "close", None)
                if close_method and asyncio.iscoroutinefunction(close_method):
                    try:
                        await close_method()
                    except Exception as e:
                        logger.warning(
                            f"[{self.plugin_id}] Error closing provider {provider.name}: {e}"
                        )

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """Destroy: clean up resources"""
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── Health Check Loop ──────────────────────────────

    async def _health_check_loop(self):
        """Periodic health check polling"""
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                if self._registry:
                    results = await self._registry.health_check_all()
                    healthy = sum(1 for v in results.values() if v)
                    total = len(results)
                    logger.info(
                        f"[{self.plugin_id}] Health check: {healthy}/{total} providers healthy"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.plugin_id}] Health check loop error: {e}")

    # ─── Public Service API ─────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        requires_vision: bool = False,
        requires_tools: bool = False,
        preferred_provider: str = "",
        budget_tier: str = "standard",
        complexity_score: float = 0.5,
    ) -> ChatResponse:
        """
        Unified chat call (with smart routing & fallback).

        Args:
            messages: Chat messages list (OpenAI format)
            model: Model ID, "default" for auto-routing
            temperature: Sampling temperature
            max_tokens: Max output tokens
            requires_vision: Whether vision capability is needed
            requires_tools: Whether tool calling is needed
            preferred_provider: Preferred provider name
            budget_tier: budget / standard / premium
            complexity_score: Question complexity 0.0-1.0

        Returns:
            ChatResponse
        """
        if not self._registry:
            raise RuntimeError("Model provider registry not initialized")

        request = ChatRequest(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            requires_vision=requires_vision,
            requires_tools=requires_tools,
            preferred_provider=preferred_provider,
            budget_tier=budget_tier,
            complexity_score=complexity_score,
        )

        return await self._registry.chat(request)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        preferred_provider: str = "",
    ):
        """
        Unified stream chat call.

        Yields content chunks as strings.
        """
        if not self._registry:
            raise RuntimeError("Model provider registry not initialized")

        request = ChatRequest(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            preferred_provider=preferred_provider,
        )

        async for chunk in self._registry.chat_stream(request):
            yield chunk

    # ─── Stats & Management API ───────────────────────

    def get_providers_status(self) -> list[dict[str, Any]]:
        """Get all providers' status"""
        if not self._registry:
            return []

        result = []
        for provider in self._registry.list_providers():
            result.append({
                "name": provider.name,
                "display_name": provider.display_name,
                "available": provider.is_available,
                "enabled": provider.config.enabled,
                "base_url": provider.config.base_url,
                "models": [
                    {
                        "id": m.id,
                        "display_name": m.display_name,
                        "context_window": m.context_window,
                        "supports_vision": m.supports_vision,
                        "supports_tools": m.supports_tools,
                        "cost_per_1k_input": m.cost_per_1k_input,
                        "cost_per_1k_output": m.cost_per_1k_output,
                        "quality_score": m.quality_score,
                    }
                    for m in provider.config.models
                ],
            })
        return result

    def get_cost_stats(self) -> dict[str, Any]:
        """Get cost statistics"""
        if not self._registry:
            return {}
        return self._registry.get_cost_stats()

    def get_available_models(self) -> list[dict[str, Any]]:
        """Get list of all available models"""
        if not self._registry:
            return []

        models = []
        for provider in self._registry.list_providers():
            if not provider.is_available:
                continue
            for m in provider.config.models:
                models.append({
                    "id": m.id,
                    "display_name": m.display_name,
                    "provider": provider.name,
                    "context_window": m.context_window,
                    "supports_vision": m.supports_vision,
                    "supports_tools": m.supports_tools,
                    "cost_per_1k_input": m.cost_per_1k_input,
                    "cost_per_1k_output": m.cost_per_1k_output,
                    "quality_score": m.quality_score,
                })
        return models

    async def trigger_health_check(self) -> dict[str, bool]:
        """Manually trigger health check for all providers"""
        if not self._registry:
            return {}
        return await self._registry.health_check_all()

    def set_daily_budget(self, budget_usd: float):
        """Set daily budget"""
        if self._registry:
            self._registry.set_daily_budget(budget_usd)
            logger.info(f"[{self.plugin_id}] Daily budget set to ${budget_usd}")

    def reset_daily_stats(self):
        """Reset daily statistics"""
        if self._registry:
            self._registry._cost_tracker.reset_daily()
            logger.info(f"[{self.plugin_id}] Daily stats reset")
