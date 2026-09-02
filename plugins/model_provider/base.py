# -*- coding: utf-8 -*-
"""
EchoServe — Unified Model Provider Module (Phase 1.3)

A unified multi-model access management layer, implementing intelligent routing, Fallback chains, and cost tracking.
Replaces the simple backend switching logic of LLMPlugin, supporting 8+ providers.

Design Principles:
    - Unified Chat API: All models expose the same openai-compatible chat() interface
    - Configurable: Provider list is read from YAML config files
    - Extensible: New providers only require inheriting BaseProvider
    - Observable: Automatically records costs and latency
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger("echoserve.model_provider")


# ─── Data Models ──────────────────────────────────────

@dataclass
class ModelConfig:
    """Configuration for a single model"""
    id: str                       # Unique model ID (e.g., gpt-4o)
    display_name: str             # Display name (e.g., GPT-4o)
    provider_name: str            # Belongs to which provider (e.g., openai)
    context_window: int = 128000
    max_tokens: int = 4096
    supports_vision: bool = False
    supports_tools: bool = False
    supports_streaming: bool = True
    cost_per_1k_input: float = 0.0   # USD
    cost_per_1k_output: float = 0.0  # USD
    # Quality score (0.0-1.0), used for routing priority
    quality_score: float = 0.8


@dataclass
class ProviderConfig:
    """Configuration for a single Provider"""
    name: str                     # Provider ID (e.g., openai)
    display_name: str             # Display name
    base_url: str                 # API Base URL
    api_key_env: str              # Environment variable name for API Key
    api_key: str = ""             # Actual API Key (read from env)
    timeout: float = 60.0
    max_retries: int = 2
    models: list[ModelConfig] = field(default_factory=list)
    enabled: bool = True
    # Load weight (0.0-1.0), used for load balancing
    weight: float = 1.0


@dataclass
class ChatRequest:
    """Unified Chat Request"""
    messages: list[dict[str, str]]
    model: str = "default"        # Can specify a specific model
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    # Routing hints
    requires_vision: bool = False
    requires_tools: bool = False
    preferred_provider: str = ""  # Priority to use a specific provider
    budget_tier: str = "standard" # budget / standard / premium
    complexity_score: float = 0.5 # 0.0-1.0, question complexity


@dataclass
class ChatResponse:
    """Unified Chat Response"""
    content: str
    model: str                    # Actually used model ID
    provider: str                 # Actually used provider ID
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    """Routing Decision Result"""
    provider_name: str
    model_id: str
    strategy: str                 # Strategy used (cost / capability / fallback / direct)
    reason: str                   # Reason for routing


# ─── Abstract Base Class ──────────────────────────────

class BaseProvider(ABC):
    """
    Provider Base Class.

n    All LLM Providers (OpenAI, Claude, Ollama, etc.) inherit from this class.
    """

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.name = config.name
        self.display_name = config.display_name
        self.is_available = False  # Will be updated after health check

    @abstractmethod
    async def health_check(self) -> bool:
        """Health check, returns whether the service is available"""
        pass

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Synchronously call the model"""
        pass

    @abstractmethod
    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream call"""
        pass

    def get_model(self, model_id: str) -> ModelConfig | None:
        """Get model configuration"""
        for m in self.config.models:
            if m.id == model_id:
                return m
        return None

    def estimate_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost (USD)"""
        model = self.get_model(model_id)
        if not model:
            return 0.0
        input_cost = (input_tokens / 1000) * model.cost_per_1k_input
        output_cost = (output_tokens / 1000) * model.cost_per_1k_output
        return round(input_cost + output_cost, 6)


# ─── Cost Tracker ──────────────────────────────────────

class CostTracker:
    """
    Cost tracker, recording usage and costs for each model.

    Supports:
    - Real-time cost statistics
    - Usage quota alerts
    - Cost analysis reports
    """

    def __init__(self):
        self._stats: dict[str, dict[str, Any]] = {}  # model_id -> stats
        self._daily_budget: float = 100.0  # Default daily budget $100
        self._alerts_sent: set[str] = set()

    def record_usage(
        self,
        model_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        latency_ms: float,
    ):
        """Record a single call"""
        if model_id not in self._stats:
            self._stats[model_id] = {
                "provider": provider,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_cost": 0.0,
                "total_latency_ms": 0.0,
                "errors": 0,
            }

        s = self._stats[model_id]
        s["calls"] += 1
        s["input_tokens"] += input_tokens
        s["output_tokens"] += output_tokens
        s["total_cost"] += cost
        s["total_latency_ms"] += latency_ms

        # Check budget alert
        daily_cost = sum(st["total_cost"] for st in self._stats.values())
        if daily_cost > self._daily_budget and "budget" not in self._alerts_sent:
            logger.warning(f"[CostTracker] Daily budget exceeded: ${daily_cost:.2f} > ${self._daily_budget:.2f}")
            self._alerts_sent.add("budget")

    def get_stats(self, model_id: str | None = None) -> dict[str, Any]:
        """Get statistics"""
        if model_id:
            return self._stats.get(model_id, {})
        return {
            "models": self._stats,
            "total_cost": sum(s["total_cost"] for s in self._stats.values()),
            "total_calls": sum(s["calls"] for s in self._stats.values()),
        }

    def set_budget(self, budget_usd: float):
        """Set daily budget"""
        self._daily_budget = budget_usd
        self._alerts_sent.discard("budget")

    def reset_daily(self):
        """Reset daily statistics"""
        self._stats.clear()
        self._alerts_sent.clear()


# ─── Intelligent Router ──────────────────────────────

class SmartRouter:
    """
    Intelligent Router, responsible for selecting the most suitable Provider and model.

    Routing Strategies:
    1. Capability matching (vision, tools, context length)
    2. Cost optimization (select cheaper models based on complexity)
    3. Budget constraints (automatically downgrade when budget is exceeded)
    4. Load balancing (distribute based on weight)
    5. User-specified priority
    6. Fallback chain
    """

    def __init__(self, registry: "ModelRegistry", cost_tracker: CostTracker):
        self.registry = registry
        self.cost_tracker = cost_tracker

    def select(self, request: ChatRequest) -> RoutingDecision:
        """Select the best provider and model"""
        # 1. If user specifies a provider, prioritize it
        if request.preferred_provider:
            provider = self.registry.get_provider(request.preferred_provider)
            if provider and provider.is_available and provider.config.enabled:
                model = self._select_model_from_provider(provider, request)
                if model:
                    return RoutingDecision(
                        provider_name=provider.name,
                        model_id=model.id,
                        strategy="direct",
                        reason=f"User specified provider: {request.preferred_provider}",
                    )

        # 2. Get all available providers
        available = [
            p for p in self.registry.list_providers()
            if p.is_available and p.config.enabled
        ]
        if not available:
            # Fallback: Try unavailable ones
            available = [p for p in self.registry.list_providers() if p.config.enabled]
            if not available:
                raise RuntimeError("No providers available")

        # 3. Filter by capability
        candidates = []
        for provider in available:
            for model in provider.config.models:
                # Check vision capability
                if request.requires_vision and not model.supports_vision:
                    continue
                # Check tool capability
                if request.requires_tools and not model.supports_tools:
                    continue
                # Check context length
                estimated_input = sum(len(m.get("content", "")) for m in request.messages)
                estimated_tokens = estimated_input // 4  # Rough estimate
                if estimated_tokens > model.context_window * 0.8:
                    continue

                candidates.append((provider, model))

        if not candidates:
            # No capability match, force the first available
            provider = available[0]
            model = provider.config.models[0] if provider.config.models else None
            if model:
                return RoutingDecision(
                    provider_name=provider.name,
                    model_id=model.id,
                    strategy="forced_fallback",
                    reason="No capability match, forced fallback",
                )
            raise RuntimeError("No models available in any provider")

        # 4. Cost optimization strategy
        if request.budget_tier == "budget":
            # Select the cheapest model
            best = min(candidates, key=lambda x: x[1].cost_per_1k_input + x[1].cost_per_1k_output)
            return RoutingDecision(
                provider_name=best[0].name,
                model_id=best[1].id,
                strategy="cost_min",
                reason=f"Budget tier selected cheapest model: {best[1].display_name}",
            )

        if request.budget_tier == "premium":
            # Select the model with the highest quality score
            best = max(candidates, key=lambda x: x[1].quality_score)
            return RoutingDecision(
                provider_name=best[0].name,
                model_id=best[1].id,
                strategy="quality_max",
                reason=f"Premium tier selected highest quality: {best[1].display_name}",
            )

        # 5. Standard tier: Balanced cost-performance
        # Complexity score determines model grade
        if request.complexity_score < 0.3:
            # Simple question, use cheap model
            cheap_candidates = [c for c in candidates if c[1].cost_per_1k_input < 0.001]
            if cheap_candidates:
                best = max(cheap_candidates, key=lambda x: x[1].quality_score)
                return RoutingDecision(
                    provider_name=best[0].name,
                    model_id=best[1].id,
                    strategy="cost_aware",
                    reason=f"Simple question ({request.complexity_score:.2f}), use cost-effective model",
                )

        # 6. Default: Select by comprehensive score (quality * weight)
        scored = []
        for provider, model in candidates:
            score = model.quality_score * provider.config.weight
            # If daily budget is exceeded, reduce score for expensive models
            daily_cost = self.cost_tracker.get_stats().get("total_cost", 0)
            if daily_cost > self.cost_tracker._daily_budget * 0.8:
                cost_penalty = 1.0 - (model.cost_per_1k_input * 100)
                score *= max(cost_penalty, 0.1)
            scored.append((provider, model, score))

        best = max(scored, key=lambda x: x[2])
        return RoutingDecision(
            provider_name=best[0].name,
            model_id=best[1].id,
            strategy="balanced",
            reason=f"Balanced routing: quality={best[1].quality_score}, weight={best[0].config.weight}",
        )

    def _select_model_from_provider(
        self, provider: BaseProvider, request: ChatRequest
    ) -> ModelConfig | None:
        """Select the best model from a specific provider"""
        models = [m for m in provider.config.models]
        if not models:
            return None

        # Filter by capability
        valid = []
        for m in models:
            if request.requires_vision and not m.supports_vision:
                continue
            if request.requires_tools and not m.supports_tools:
                continue
            valid.append(m)

        if not valid:
            return models[0]  # Forced fallback

        # Select by complexity
        if request.complexity_score < 0.3:
            cheap = [m for m in valid if m.cost_per_1k_input < 0.001]
            if cheap:
                return max(cheap, key=lambda x: x.quality_score)

        return max(valid, key=lambda x: x.quality_score)

    def select_fallback(
        self, request: ChatRequest, exclude_provider: str
    ) -> RoutingDecision | None:
        """Select a Fallback provider (excluding the failed one)"""
        available = [
            p for p in self.registry.list_providers()
            if p.is_available and p.config.enabled and p.name != exclude_provider
        ]
        if not available:
            return None

        # Prioritize the cheapest available
        for provider in available:
            models = sorted(provider.config.models, key=lambda m: m.cost_per_1k_input)
            for model in models:
                if request.requires_vision and not model.supports_vision:
                    continue
                if request.requires_tools and not model.supports_tools:
                    continue
                return RoutingDecision(
                    provider_name=provider.name,
                    model_id=model.id,
                    strategy="fallback",
                    reason=f"Fallback from {exclude_provider} to {provider.name}",
                )

        return None


# ─── Model Registry ──────────────────────────────────

class ModelRegistry:
    """
    Model registry, managing all LLM Providers.

    Responsible for:
    - Registering/unregistering providers
    - Health check polling
    - Unified call entry
    """

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}
        self._cost_tracker = CostTracker()
        self._router = SmartRouter(self, self._cost_tracker)
        self._fallback_chain: list[str] = []  # Ordered list of fallback provider names

    def register(self, provider: BaseProvider) -> None:
        """Register a provider"""
        self._providers[provider.name] = provider
        logger.info(f"[ModelRegistry] Registered provider: {provider.name} ({provider.display_name})")

    def unregister(self, name: str) -> None:
        """Unregister a provider"""
        if name in self._providers:
            del self._providers[name]
            logger.info(f"[ModelRegistry] Unregistered provider: {name}")

    def get_provider(self, name: str) -> BaseProvider | None:
        """Get a provider by name"""
        return self._providers.get(name)

    def list_providers(self) -> list[BaseProvider]:
        """List all registered providers"""
        return list(self._providers.values())

    def list_models(self) -> list[ModelConfig]:
        """List all models from all providers"""
        models = []
        for p in self._providers.values():
            models.extend(p.config.models)
        return models

    async def health_check_all(self) -> dict[str, bool]:
        """Health check all providers"""
        results = {}
        for name, provider in self._providers.items():
            try:
                healthy = await provider.health_check()
                provider.is_available = healthy
                results[name] = healthy
            except Exception as e:
                provider.is_available = False
                results[name] = False
                logger.warning(f"[ModelRegistry] Health check failed for {name}: {e}")
        return results

    # ─── Unified Call Entry ──────────────────────────────

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """
        Unified chat entry point.

        Workflow:
        1. Route to select a provider and model
        2. Execute the call
        3. Record cost and latency
        4. On failure, trigger Fallback
        """
        decision = self._router.select(request)
        provider = self._providers.get(decision.provider_name)

        if not provider:
            raise RuntimeError(f"Selected provider not found: {decision.provider_name}")

        start_time = time.time()
        try:
            response = await provider.chat(request)
            latency_ms = (time.time() - start_time) * 1000

            # Record cost
            cost = provider.estimate_cost(
                response.model,
                response.tokens_input,
                response.tokens_output,
            )
            self._cost_tracker.record_usage(
                model_id=response.model,
                provider=provider.name,
                input_tokens=response.tokens_input,
                output_tokens=response.tokens_output,
                cost=cost,
                latency_ms=latency_ms,
            )

            # Enrich response
            response.provider = provider.name
            response.latency_ms = round(latency_ms, 2)
            response.cost_usd = cost
            return response

        except Exception as e:
            logger.error(f"[ModelRegistry] Chat failed with {provider.name}: {e}")
            # Trigger Fallback
            fallback = self._router.select_fallback(request, exclude_provider=provider.name)
            if fallback:
                logger.info(f"[ModelRegistry] Falling back to {fallback.provider_name}")
                fallback_provider = self._providers.get(fallback.provider_name)
                if fallback_provider:
                    request.preferred_provider = fallback.provider_name
                    return await self.chat(request)

            raise RuntimeError(f"All providers failed. Last error: {e}")

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream chat entry point"""
        decision = self._router.select(request)
        provider = self._providers.get(decision.provider_name)

        if not provider:
            raise RuntimeError(f"Selected provider not found: {decision.provider_name}")

        try:
            async for chunk in provider.chat_stream(request):
                yield chunk
        except Exception as e:
            logger.error(f"[ModelRegistry] Stream failed with {provider.name}: {e}")
            # Streaming does not support fallback (interruption midway), directly raise error
            raise

    # ─── Statistics ──────────────────────────────────────

    def get_cost_stats(self) -> dict[str, Any]:
        """Get cost statistics"""
        return self._cost_tracker.get_stats()

    def set_daily_budget(self, budget_usd: float):
        """Set daily budget"""
        self._cost_tracker.set_budget(budget_usd)
