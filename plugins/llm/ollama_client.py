"""
EchoServe V0.1.0 — Ollama 客户端适配器
兼容 vLLM 的 OpenAI 接口，提供本地化 7-8B 模型快速部署能力。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, AsyncIterator

import httpx

logger = logging.getLogger("echoseve.llm.ollama")


class OllamaClient:
    """
    Ollama API 客户端，接口兼容 VLLMClient。

    支持：
    - 同步/异步对话
    - 流式输出
    - 服务就绪轮询
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """懒加载 httpx 异步客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def wait_for_ready(self, timeout: int = 300, interval: int = 5) -> bool:
        """等待 Ollama 服务就绪（轮询 /api/tags 接口）。"""
        await self._ensure_client()
        elapsed = 0

        while elapsed < timeout:
            try:
                response = await self._client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    model_names = [m.get("name", "") for m in models]
                    logger.info(f"[Ollama] Service ready. Models: {model_names}")
                    return True
            except Exception as e:
                logger.debug(f"[Ollama] Waiting for service... ({elapsed}s) - {e}")

            await asyncio.sleep(interval)
            elapsed += interval

        logger.error(f"[Ollama] Service not ready after {timeout}s")
        return False

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        非流式对话，返回 OpenAI 兼容格式。
        """
        client = await self._ensure_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
            "stream": False,
        }
        if stop:
            payload["options"]["stop"] = stop

        response = await client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama chat failed: {response.status_code} - {response.text}"
            )

        data = response.json()
        content = data.get("message", {}).get("content", "")

        # 转换为 OpenAI 兼容格式
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": (
                    data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
                ),
            },
        }

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
    ) -> AsyncIterator[str]:
        """
        流式对话，逐块 yield token 文本。
        """
        client = await self._ensure_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
            "stream": True,
        }

        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json=payload,
        ) as response:
            if response.status_code != 200:
                text = await response.aread()
                raise RuntimeError(
                    f"Ollama stream failed: {response.status_code} - {text}"
                )

            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue

    async def close(self) -> None:
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """快速健康检查"""
        try:
            client = await self._ensure_client()
            response = await client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        """获取可用模型列表"""
        try:
            client = await self._ensure_client()
            response = await client.get(f"{self.base_url}/api/tags")
            data = response.json()
            return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            return []
