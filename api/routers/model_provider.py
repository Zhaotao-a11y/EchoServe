# -*- coding: utf-8 -*-
"""
EchoServe — Model Provider API Routes

Provides REST endpoints for:
- Listing providers and their status
- Listing available models
- Unified chat endpoint (with smart routing)
- Streaming chat endpoint (SSE)
- Cost statistics
- Health check trigger
- Budget management
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import verify_token

logger = logging.getLogger("echoserve.api.model_provider")

router = APIRouter()


# ─── Request/Response Models ──────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="Message role: user/assistant/system")
    content: str = Field(..., description="Message content")


class ChatRequestModel(BaseModel):
    messages: list[ChatMessage]
    model: str = Field(default="default", description="Model ID or 'default' for auto-routing")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    requires_vision: bool = Field(default=False)
    requires_tools: bool = Field(default=False)
    preferred_provider: str = Field(default="")
    budget_tier: str = Field(default="standard", pattern="^(budget|standard|premium)$")
    complexity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    stream: bool = Field(default=False, description="Whether to stream the response")


class StreamChatRequestModel(BaseModel):
    messages: list[ChatMessage]
    model: str = Field(default="default")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    preferred_provider: str = Field(default="")


class BudgetModel(BaseModel):
    budget_usd: float = Field(..., gt=0, description="Daily budget in USD")


# ─── Helper ────────────────────────────────────────────

def _get_plugin(request: Request):
    """Get the ModelProviderPlugin instance from app state"""
    ctx = request.app.state.ctx
    plugin = ctx.services.get("model_provider")
    if not plugin:
        raise HTTPException(status_code=503, detail="model_provider service not available")
    return plugin


# ─── Routes ────────────────────────────────────────────

@router.get("/model-providers")
async def list_providers(request: Request, _: dict = Depends(verify_token)):
    """List all providers and their status"""
    plugin = _get_plugin(request)
    return {
        "providers": plugin.get_providers_status(),
        "total": len(plugin.get_providers_status()),
    }


@router.get("/model-providers/models")
async def list_available_models(request: Request, _: dict = Depends(verify_token)):
    """List all available models across providers"""
    plugin = _get_plugin(request)
    models = plugin.get_available_models()
    return {
        "models": models,
        "total": len(models),
    }


@router.get("/model-providers/cost-stats")
async def get_cost_stats(request: Request, _: dict = Depends(verify_token)):
    """Get cost statistics"""
    plugin = _get_plugin(request)
    return plugin.get_cost_stats()


@router.post("/model-providers/health-check")
async def trigger_health_check(request: Request, _: dict = Depends(verify_token)):
    """Manually trigger health check for all providers"""
    plugin = _get_plugin(request)
    results = await plugin.trigger_health_check()
    return {
        "results": results,
        "healthy": sum(1 for v in results.values() if v),
        "total": len(results),
    }


@router.put("/model-providers/budget")
async def set_budget(
    body: BudgetModel,
    request: Request,
    _: dict = Depends(verify_token),
):
    """Set daily budget"""
    plugin = _get_plugin(request)
    plugin.set_daily_budget(body.budget_usd)
    return {"message": f"Daily budget set to ${body.budget_usd}"}


@router.post("/model-providers/chat")
async def unified_chat(
    body: ChatRequestModel,
    request: Request,
    _: dict = Depends(verify_token),
):
    """
    Unified chat endpoint with smart routing.

    The router will automatically select the best provider and model
    based on the request parameters (vision, tools, budget, complexity).

    If stream=True, returns SSE stream.
    """
    plugin = _get_plugin(request)

    if body.stream:
        return StreamingResponse(
            _stream_generator(plugin, body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming
    try:
        response = await plugin.chat(
            messages=[m.model_dump() for m in body.messages],
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            requires_vision=body.requires_vision,
            requires_tools=body.requires_tools,
            preferred_provider=body.preferred_provider,
            budget_tier=body.budget_tier,
            complexity_score=body.complexity_score,
        )
        return {
            "content": response.content,
            "model": response.model,
            "provider": response.provider,
            "tokens_input": response.tokens_input,
            "tokens_output": response.tokens_output,
            "cost_usd": response.cost_usd,
            "latency_ms": response.latency_ms,
            "finish_reason": response.finish_reason,
        }
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=502, detail=f"All providers failed: {e}")


@router.post("/model-providers/chat/stream")
async def stream_chat(
    body: StreamChatRequestModel,
    request: Request,
    _: dict = Depends(verify_token),
):
    """Streaming chat endpoint (SSE)"""
    plugin = _get_plugin(request)

    return StreamingResponse(
        _stream_generator_simple(plugin, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_generator(plugin, body: ChatRequestModel):
    """SSE stream generator for unified chat"""
    try:
        async for chunk in plugin.chat_stream(
            messages=[m.model_dump() for m in body.messages],
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            preferred_provider=body.preferred_provider,
        ):
            data = json.dumps({"content": chunk}, ensure_ascii=False)
            yield f"data: {data}\n\n"
        yield f"data: [DONE]\n\n"
    except Exception as e:
        error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
        yield f"data: {error_data}\n\n"


async def _stream_generator_simple(plugin, body: StreamChatRequestModel):
    """SSE stream generator for streaming endpoint"""
    try:
        async for chunk in plugin.chat_stream(
            messages=[m.model_dump() for m in body.messages],
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            preferred_provider=body.preferred_provider,
        ):
            data = json.dumps({"content": chunk}, ensure_ascii=False)
            yield f"data: {data}\n\n"
        yield f"data: [DONE]\n\n"
    except Exception as e:
        error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
        yield f"data: {error_data}\n\n"


@router.post("/model-providers/reset-stats")
async def reset_daily_stats(request: Request, _: dict = Depends(verify_token)):
    """Reset daily cost statistics"""
    plugin = _get_plugin(request)
    plugin.reset_daily_stats()
    return {"message": "Daily stats reset"}
