"""
EchoServe P1 — vLLM 客户端（模型热切换）

功能：
- 通过 vLLM OpenAI 兼容 API 管理模型
- 支持模型列表查询、加载、卸载
- 支持 LoRA adapter 动态挂载/卸载
- 健康检查与状态监控
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
import httpx

logger = logging.getLogger("echoserve.model.vllm")


class VLLMClient:
    """
    vLLM 推理服务客户端。

    支持：
    - 标准 OpenAI 兼容接口（/v1/chat/completions）
    - vLLM 扩展接口（/v1/models 管理）
    - LoRA adapter 热加载
    """

    def __init__(
        self,
        host: str = "http://localhost:8000",
        api_key: str = "",
        timeout: float = 30.0,
    ):
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        self._async_client = httpx.AsyncClient(timeout=timeout)
        self._current_model: str | None = None
        self._loaded_adapters: list[str] = []

    # ─── 生命周期 ──────────────────────────────────

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()
        # 异步客户端需在事件循环中关闭
        try:
            asyncio.get_running_loop()
            # 在 async 上下文中——创建 task 异步关闭（fire and forget）
            asyncio.ensure_future(self._async_client.aclose())
        except RuntimeError:
            # 不在 async 上下文——用 asyncio.run() 同步关闭
            try:
                asyncio.run(self._async_client.aclose())
            except Exception as e:
                logger.debug(f"Error closing vLLM sync client: {e}")

    async def aclose(self):
        """异步关闭"""
        await self._async_client.aclose()
        self._client.close()

    # ─── 健康检查 ──────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """检查 vLLM 服务健康状态"""
        try:
            resp = self._client.get(f"{self.host}/health")
            return {
                "healthy": resp.status_code == 200,
                "status_code": resp.status_code,
                "latency_ms": resp.elapsed.total_seconds() * 1000,
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def ahealth_check(self) -> dict[str, Any]:
        """异步健康检查"""
        try:
            resp = await self._async_client.get(f"{self.host}/health")
            return {
                "healthy": resp.status_code == 200,
                "status_code": resp.status_code,
                "latency_ms": resp.elapsed.total_seconds() * 1000,
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    # ─── 模型管理 ──────────────────────────────────

    def list_models(self) -> list[dict[str, Any]]:
        """列出当前已加载的模型"""
        try:
            resp = self._client.get(f"{self.host}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
            return []
        except Exception as e:
            logger.error(f"  获取模型列表失败: {e}")
            return []

    def get_current_model(self) -> str | None:
        """获取当前活跃模型 ID"""
        models = self.list_models()
        if models:
            self._current_model = models[0].get("id", "")
        return self._current_model

    def load_model(self, model_path: str, model_id: str | None = None) -> dict[str, Any]:
        """
        加载新模型（vLLM 的 /load_model 接口）。

        Args:
            model_path: 模型路径或名称
            model_id: 可选的模型 ID（用于多模型区分）

        Returns:
            {"status": "success"|"failed", ...}
        """
        payload = {"model": model_path}
        if model_id:
            payload["model_id"] = model_id

        try:
            logger.info(f"  正在加载模型: {model_path}")
            start = time.time()
            resp = self._client.post(
                f"{self.host}/v1/load_model",
                json=payload,
                timeout=300.0,  # 模型加载可能较慢
            )
            elapsed = time.time() - start

            if resp.status_code == 200:
                self._current_model = model_id or model_path
                logger.info(f"  模型加载成功 (耗时 {elapsed:.1f}s)")
                return {
                    "status": "success",
                    "model": self._current_model,
                    "load_time_s": round(elapsed, 1),
                }
            else:
                logger.error(f"  模型加载失败: HTTP {resp.status_code}")
                return {
                    "status": "failed",
                    "reason": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as e:
            logger.error(f"  模型加载异常: {e}")
            return {"status": "failed", "reason": str(e)}

    def unload_model(self, model_id: str | None = None) -> dict[str, Any]:
        """
        卸载模型（释放显存）。

        注意：vLLM 原生不支持动态卸载，通常需要重启服务。
        此方法通过管理接口实现（如支持）。
        """
        target = model_id or self._current_model
        if not target:
            return {"status": "failed", "reason": "没有已加载的模型"}

        try:
            resp = self._client.post(
                f"{self.host}/v1/unload_model",
                json={"model_id": target},
                timeout=60.0,
            )
            if resp.status_code == 200:
                if self._current_model == target:
                    self._current_model = None
                logger.info(f"  模型已卸载: {target}")
                return {"status": "success", "model": target}
            else:
                # vLLM 可能不支持 unload，需要重启
                return {
                    "status": "needs_restart",
                    "reason": "vLLM 不支持热卸载，需重启服务",
                    "instruction": f"docker restart vllm && sleep 10 && "
                                   f"curl -X POST {self.host}/v1/load_model "
                                   f"-H 'Content-Type: application/json' "
                                   f"-d '{{\"model\": \"{target}\"}}'",
                }
        except Exception as e:
            return {"status": "failed", "reason": str(e)}

    # ─── LoRA Adapter 管理 ────────────────────────

    def load_lora_adapter(
        self,
        adapter_path: str,
        adapter_name: str,
    ) -> dict[str, Any]:
        """
        动态加载 LoRA adapter（无需重启 vLLM）。

        vLLM 支持通过 /v1/load_lora_adapter 接口热加载。
        """
        payload = {
            "lora_name": adapter_name,
            "lora_path": adapter_path,
        }

        try:
            logger.info(f"  加载 LoRA adapter: {adapter_name} ({adapter_path})")
            resp = self._client.post(
                f"{self.host}/v1/load_lora_adapter",
                json=payload,
                timeout=120.0,
            )

            if resp.status_code == 200:
                self._loaded_adapters.append(adapter_name)
                logger.info(f"  LoRA adapter 加载成功: {adapter_name}")
                return {
                    "status": "success",
                    "adapter": adapter_name,
                    "path": adapter_path,
                }
            else:
                logger.error(f"  LoRA 加载失败: HTTP {resp.status_code}")
                return {
                    "status": "failed",
                    "reason": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as e:
            logger.error(f"  LoRA 加载异常: {e}")
            return {"status": "failed", "reason": str(e)}

    def unload_lora_adapter(self, adapter_name: str) -> dict[str, Any]:
        """卸载指定的 LoRA adapter"""
        try:
            resp = self._client.post(
                f"{self.host}/v1/unload_lora_adapter",
                json={"lora_name": adapter_name},
            )
            if resp.status_code == 200:
                if adapter_name in self._loaded_adapters:
                    self._loaded_adapters.remove(adapter_name)
                return {"status": "success", "adapter": adapter_name}
            return {"status": "failed", "reason": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "failed", "reason": str(e)}

    def list_lora_adapters(self) -> list[str]:
        """列出已加载的 LoRA adapters"""
        return list(self._loaded_adapters)

    # ─── 推理接口 ──────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        top_p: float = 0.9,
        lora_name: str | None = None,
    ) -> str:
        """
        同步对话推理。

        Args:
            messages: OpenAI 格式消息列表
            model: 模型 ID（默认使用当前模型）
            lora_name: 可选 LoRA adapter 名称

        Returns:
            生成的文本
        """
        payload = {
            "model": model or self._current_model or "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": False,
        }
        if lora_name:
            payload["lora_name"] = lora_name

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = self._client.post(
                f"{self.host}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"  推理失败: HTTP {resp.status_code} - {resp.text[:200]}")
                return ""
        except Exception as e:
            logger.error(f"  推理异常: {e}")
            return ""

    async def achat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        lora_name: str | None = None,
    ) -> str:
        """异步对话推理"""
        payload = {
            "model": model or self._current_model or "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if lora_name:
            payload["lora_name"] = lora_name

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = await self._async_client.post(
                f"{self.host}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            return ""
        except Exception as e:
            logger.error(f"  异步推理异常: {e}")
            return ""

    async def achat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        lora_name: str | None = None,
    ):
        """异步流式对话（生成器）"""
        payload = {
            "model": model or self._current_model or "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if lora_name:
            payload["lora_name"] = lora_name

        headers = {"Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with self._async_client.stream(
            "POST",
            f"{self.host}/v1/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    # ─── 状态查询 ──────────────────────────────────

    def get_server_info(self) -> dict[str, Any]:
        """获取 vLLM 服务器信息"""
        try:
            resp = self._client.get(f"{self.host}/version")
            version_info = resp.json() if resp.status_code == 200 else {}
        except Exception as e:
            logger.debug(f"Failed to get vLLM version info: {e}")
            version_info = {}

        health = self.health_check()

        return {
            "host": self.host,
            "healthy": health.get("healthy", False),
            "latency_ms": health.get("latency_ms", 0),
            "current_model": self._current_model,
            "loaded_adapters": list(self._loaded_adapters),
            "version": version_info,
        }
