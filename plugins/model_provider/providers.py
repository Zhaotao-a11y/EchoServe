# -*- coding: utf-8 -*-
"""
EchoServe — Model Provider Concrete Implementations

Supports:
    - OpenAI-compatible (OpenAI / DeepSeek / Moonshot / Zhipu / etc.)
    - Anthropic Claude
    - Google Gemini
    - Ollama local
    - vLLM local (compatible interface with OpenAI)
    - Azure OpenAI
    - Baichuan / Yi / Qwen API
    - Custom HTTP Provider

All providers implement BaseProvider, unifying the chat() interface.
"""
from __future__ import annotations

import json
import logging
import os
import asyncio
from typing import Any, AsyncIterator

import httpx

from .base import (
    BaseProvider,
    ChatRequest,
    ChatResponse,
    ModelConfig,
    ProviderConfig,
)

logger = logging.getLogger("echoserve.model_provider.providers")


# ═══════════════════════════════════════════════════════════════════════════════
#   OpenAI-Compatible Provider (covers most API providers)
# ═══════════════════════════════════════════════════════════════════════════════

class OpenAICompatibleProvider(BaseProvider):
    """
    OpenAI-compatible API provider.

    Supports:
      - OpenAI official API
      - DeepSeek (https://api.deepseek.com)
      - Moonshot KIMI (https://api.moonshot.cn)
      - Zhipu GLM (https://open.bigmodel.cn)
      - Baichuan (https://api.baichuan-ai.com)
      - Yi / 01.AI (https://api.01.ai)
      - Local vLLM (http://localhost:8000)
      - Azure OpenAI (different endpoints)
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._base_url = config.base_url.rstrip("/")
        # v1/chat/completions endpoint
        self._chat_url = f"{self._base_url}/v1/chat/completions"
        # models list endpoint
        self._models_url = f"{self._base_url}/v1/models"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.config.timeout),
            )
        return self._client

    async def health_check(self) -> bool:
        """Health check via the GET /v1/models endpoint"""
        try:
            client = await self._get_client()
            resp = await client.get(self._models_url, timeout=10)
            self.is_available = resp.status_code == 200
            return self.is_available
        except Exception as e:
            logger.debug(f"[{self.name}] Health check failed: {e}")
            self.is_available = False
            return False

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Call the chat completions endpoint"""
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": request.model if request.model != "default" else self.config.models[0].id,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "auto"

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await client.post(self._chat_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                usage = data.get("usage", {})

                return ChatResponse(
                    content=message.get("content", ""),
                    model=data.get("model", payload["model"]),
                    provider=self.name,
                    tokens_input=usage.get("prompt_tokens", 0),
                    tokens_output=usage.get("completion_tokens", 0),
                    finish_reason=choice.get("finish_reason", "stop"),
                    tool_calls=message.get("tool_calls"),
                    raw_response=data,
                )
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429:
                    # Rate limit, exponential backoff
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error(f"[{self.name}] HTTP {e.response.status_code}: {e.response.text[:200]}")
                raise RuntimeError(f"{self.name} API error {e.response.status_code}: {e.response.text[:500]}")
            except Exception as e:
                last_error = e
                logger.error(f"[{self.name}] Attempt {attempt+1} failed: {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                raise

        raise RuntimeError(f"{self.name} all retries exhausted: {last_error}")

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream call"""
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": request.model if request.model != "default" else self.config.models[0].id,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": True,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        async with client.stream("POST", self._chat_url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunk_str = line[6:]
                    if chunk_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(chunk_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def close(self):
        """Close the HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ═══════════════════════════════════════════════════════════════════════════════
#   Anthropic Claude Provider
# ═══════════════════════════════════════════════════════════════════════════════

class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude API provider.

    API Doc: https://docs.anthropic.com/claude/reference
    Special note: Claude uses a messages field format different from OpenAI, with an additional system field.
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._base_url = config.base_url.rstrip("/") or "https://api.anthropic.com"
        self._chat_url = f"{self._base_url}/v1/messages"
        self._api_version = "2023-06-01"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "x-api-key": self.config.api_key,
                "anthropic-version": self._api_version,
                "Content-Type": "application/json",
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.config.timeout),
            )
        return self._client

    async def health_check(self) -> bool:
        """Claude does not have a models list endpoint; check with a minimal request"""
        try:
            client = await self._get_client()
            # Use a minimal message to test
            resp = await client.post(
                self._chat_url,
                json={
                    "model": self.config.models[0].id,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            self.is_available = resp.status_code in (200, 400)
            return self.is_available
        except Exception:
            self.is_available = False
            return False

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Call the Claude messages endpoint"""
        client = await self._get_client()

        # Extract system message
        system_content = ""
        messages = []
        for msg in request.messages:
            if msg["role"] == "system":
                system_content += msg["content"] + "\n"
            else:
                messages.append(msg)

        payload: dict[str, Any] = {
            "model": request.model if request.model != "default" else self.config.models[0].id,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
        }
        if system_content:
            payload["system"] = system_content.strip()

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await client.post(self._chat_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                content_blocks = data.get("content", [])
                content = ""
                for block in content_blocks:
                    if block.get("type") == "text":
                        content += block.get("text", "")

                usage = data.get("usage", {})

                return ChatResponse(
                    content=content,
                    model=data.get("model", payload["model"]),
                    provider=self.name,
                    tokens_input=usage.get("input_tokens", 0),
                    tokens_output=usage.get("output_tokens", 0),
                    finish_reason=data.get("stop_reason", "stop"),
                    raw_response=data,
                )
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Claude API error {e.response.status_code}: {e.response.text[:500]}")
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                raise

        raise RuntimeError(f"Claude all retries exhausted: {last_error}")

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream call (Claude SSE format)"""
        client = await self._get_client()

        system_content = ""
        messages = []
        for msg in request.messages:
            if msg["role"] == "system":
                system_content += msg["content"] + "\n"
            else:
                messages.append(msg)

        payload: dict[str, Any] = {
            "model": request.model if request.model != "default" else self.config.models[0].id,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
            "stream": True,
        }
        if system_content:
            payload["system"] = system_content.strip()

        async with client.stream("POST", self._chat_url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunk_str = line[6:]
                    try:
                        chunk = json.loads(chunk_str)
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                    except json.JSONDecodeError:
                        continue

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ═══════════════════════════════════════════════════════════════════════════════
#   Google Gemini Provider
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiProvider(BaseProvider):
    """
    Google Gemini API provider.

    API Doc: https://ai.google.dev/docs
    Uses a different message format and needs to convert OpenAI format to Gemini format.
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._base_url = config.base_url.rstrip("/") or "https://generativelanguage.googleapis.com"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
            )
        return self._client

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert OpenAI format to Gemini format"""
        system_instruction = ""
        contents = []
        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_instruction += text + "\n"
            else:
                gemini_role = "user" if role == "user" else "model"
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": text}],
                })
        return system_instruction.strip(), contents

    async def health_check(self) -> bool:
        """Health check via the models list endpoint"""
        try:
            client = await self._get_client()
            url = f"{self._base_url}/v1beta/models?key={self.config.api_key}"
            resp = await client.get(url, timeout=10)
            self.is_available = resp.status_code == 200
            return self.is_available
        except Exception:
            self.is_available = False
            return False

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Call the Gemini generateContent endpoint"""
        client = await self._get_client()

        model_id = request.model if request.model != "default" else self.config.models[0].id
        system_instruction, contents = self._convert_messages(request.messages)

        url = f"{self._base_url}/v1beta/models/{model_id}:generateContent?key={self.config.api_key}"

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens or 4096,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                candidates = data.get("candidates", [])
                content = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        content += part.get("text", "")

                usage = data.get("usageMetadata", {})

                return ChatResponse(
                    content=content,
                    model=model_id,
                    provider=self.name,
                    tokens_input=usage.get("promptTokenCount", 0),
                    tokens_output=usage.get("candidatesTokenCount", 0),
                    finish_reason=candidates[0].get("finishReason", "STOP") if candidates else "STOP",
                    raw_response=data,
                )
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Gemini API error {e.response.status_code}: {e.response.text[:500]}")
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                raise

        raise RuntimeError(f"Gemini all retries exhausted: {last_error}")

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream call"""
        client = await self._get_client()

        model_id = request.model if request.model != "default" else self.config.models[0].id
        system_instruction, contents = self._convert_messages(request.messages)

        url = f"{self._base_url}/v1beta/models/{model_id}:streamGenerateContent?key={self.config.api_key}&alt=sse"

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens or 4096,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunk_str = line[6:]
                    try:
                        chunk = json.loads(chunk_str)
                        candidates = chunk.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                text = part.get("text", "")
                                if text:
                                    yield text
                    except json.JSONDecodeError:
                        continue

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ═════════════22════════════════════════════════════════════════════════════════
#   Ollama Local Provider
# ═══════════════════════════════════════════════════════════════════════════════

class OllamaProvider(BaseProvider):
    """
    Ollama local model provider.

    Used for local inference, privacy protection, or reducing API costs.
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._base_url = config.base_url.rstrip("/") or "http://localhost:11434"
        self._chat_url = f"{self._base_url}/api/chat"
        self._tags_url = f"{self._base_url}/api/tags"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
            )
        return self._client

    async def health_check(self) -> bool:
        """Check via the GET /api/tags endpoint"""
        try:
            client = await self._get_client()
            resp = await client.get(self._tags_url, timeout=5)
            self.is_available = resp.status_code == 200
            return self.is_available
        except Exception:
            self.is_available = False
            return False

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Call the Ollama chat endpoint"""
        client = await self._get_client()

        model_id = request.model if request.model != "default" else self.config.models[0].id

        payload: dict[str, Any] = {
            "model": model_id,
            "messages": request.messages,
            "stream": False,
            "options": {
                "temperature": request.temperature,
            },
        }
        if request.max_tokens:
            payload["options"]["num_predict"] = request.max_tokens

        resp = await client.post(self._chat_url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        message = data.get("message", {})
        eval_count = data.get("eval_count", 0)
        prompt_eval_count = data.get("prompt_eval_count", 0)

        return ChatResponse(
            content=message.get("content", ""),
            model=data.get("model", model_id),
            provider=self.name,
            tokens_input=prompt_eval_count,
            tokens_output=eval_count,
            finish_reason="stop",
            raw_response=data,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream call (Ollama uses newline-delimited JSON)"""
        client = await self._get_client()

        model_id = request.model if request.model != "default" else self.config.models[0].id

        payload: dict[str, Any] = {
            "model": model_id,
            "messages": request.messages,
            "stream": True,
            "options": {
                "temperature": request.temperature,
            },
        }
        if request.max_tokens:
            payload["options"]["num_predict"] = request.max_tokens

        async with client.stream("POST", self._chat_url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    message = chunk.get("message", {})
                    content = message.get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ═══════════════════════════════════════════════════════════════════════════════
#   Provider Factory
# ═══════════════════════════════════════════════════════════════════════════════

# Provider type → class mapping
_PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    "openai": OpenAICompatibleProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "deepseek": OpenAICompatibleProvider,
    "moonshot": OpenAICompatibleProvider,
    "zhipu": OpenAICompatibleProvider,
    "baichuan": OpenAICompatibleProvider,
    "yi": OpenAICompatibleProvider,
    "vllm": OpenAICompatibleProvider,
    "azure": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "claude": AnthropicProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "ollama": OllamaProvider,
}


def create_provider(config: ProviderConfig, provider_type: str = "") -> BaseProvider:
    """
    Create a Provider instance based on configuration.

    Args:
        config: Provider configuration
        provider_type: Provider type, if empty uses config.name

    Returns:
        BaseProvider instance
    """
    ptype = provider_type or config.name

    # Fuzzy match
    ptype_lower = ptype.lower()
    for key, cls in _PROVIDER_CLASSES.items():
        if key in ptype_lower:
            return cls(config)

    # Default OpenAI-compatible
    logger.warning(f"Unknown provider type '{ptype}', defaulting to OpenAI-compatible")
    return OpenAICompatibleProvider(config)
