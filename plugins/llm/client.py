"""
EchoServe V0.1.0 — vLLM HTTP 客户端

封装 vLLM 的 OpenAI 兼容 API。
支持同步/异步调用、流式输出、健康检查。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator
import httpx

logger = logging.getLogger("echoserve.llm.client")


class VLLMClient:
    """
    vLLM 异步客户端（OpenAI 兼容协议）。

    用法：
        client = VLLMClient(base_url="http://vllm:8000", model="qwen3-14b-q4")
        await client.wait_for_ready()
        response = await client.chat_completion(messages=[...])
        async for chunk in client.chat_completion_stream(messages=[...]):
            print(chunk)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        model: str = "qwen3-14b-q4",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client: (httpx.AsyncClient | None) = None

    async def _ensure_client(self):
        """懒加载 httpx 客户端"""
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout),
            )

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def wait_for_ready(self, timeout: int = 300, interval: int = 5) -> bool:
        """
        等待 vLLM 服务就绪（轮询 /v1/models 接口）。

        Args:
            timeout: 总超时（秒）
            interval: 轮询间隔（秒）

        Returns:
            True 表示就绪，False 表示超时
        """
        await self._ensure_client()
        elapsed = 0

        while elapsed < timeout:
            try:
                response = await self._client.get("/v1/models")
                if response.status_code == 200:
                    models = response.json().get("data", [])
                    model_names = [m.get("id", "") for m in models]
                    logger.info(f"[VLLM] Service ready. Models: {model_names}")
                    return True
            except Exception as e:
                logger.debug(f"[VLLM] Waiting for service... ({elapsed}s) - {e}")

            await asyncio.sleep(interval)
            elapsed += interval

        logger.error(f"[VLLM] Service not ready after {timeout}s")
        return False

    async def health_check(self) -> bool:
        """快速健康检查"""
        await self._ensure_client()
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
        stop: (list[str] | None) = None,
    ) -> dict[str, Any]:
        """
        非流式对话补全。

        Returns:
            OpenAI 格式的响应字典
        """
        await self._ensure_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": False,
        }
        if stop:
            payload["stop"] = stop

        response = await self._client.post("/v1/chat/completions", json=payload)

        if response.status_code != 200:
            error_msg = f"vLLM error {response.status_code}: {response.text}"
            logger.error(f"[VLLM] {error_msg}")
            raise RuntimeError(error_msg)

        return response.json()

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
    ) -> AsyncIterator[str]:
        """
        流式对话补全（逐 token 输出）。

        Yields:
            每个 delta 的文本片段
        """
        await self._ensure_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": True,
        }

        async with self._client.stream(
            "POST", "/v1/chat/completions", json=payload
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise RuntimeError(f"vLLM stream error: {error_text.decode()}")

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data = line[6:].strip()
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    logger.warning(f"[VLLM] Failed to parse chunk: {data[:100]}")
                    continue

    async def list_models(self) -> list[str]:
        """列出可用模型"""
        await self._ensure_client()
        response = await self._client.get("/v1/models")
        if response.status_code == 200:
            data = response.json()
            return [m["id"] for m in data.get("data", [])]
        return []
