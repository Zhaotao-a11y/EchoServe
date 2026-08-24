"""
EchoServe V0.1.0 — LLM 网关插件

封装 vLLM 的 OpenAI 兼容接口。
支持：
- 同步/异步对话
- 流式输出
- 系统提示词管理
- Prefix Cache 提示（通过 system prompt 复用）

通过 Context 注册为 "llm" 服务。
"""
from __future__ import annotations

import logging
import os
from typing import List, Dict, Any, Optional, AsyncIterator
from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .client import VLLMClient
from .ollama_client import OllamaClient
from .prompts.customer_service import build_system_prompt, apply_qwen3_thinking

logger = logging.getLogger("echoseve.llm")


class LLMPlugin(BaizePlugin):
    """LLM 网关插件"""

    plugin_id = "core.llm"
    plugin_name = "LLM 推理网关"
    plugin_version = "0.1.4"
    dependencies = []  # 不依赖其他插件

    def __init__(self):
        self.client = None
        self.model_name: str = "qwen3-14b-q4"
        self.system_prompt: str = ""
        self.temperature: float = 0.7
        self.max_tokens: int = 2048
        self.top_p: float = 0.9
        self.backend_type: str = "vllm"  # "vllm" | "ollama"
        self.use_cs_prompt: bool = True   # 是否使用客服场景提示词

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化 LLM 客户端（自动探测 vLLM 或 Ollama）"""
        settings = ctx.settings
        self.model_name = settings.model.name

        # 探测后端类型：优先 vLLM，失败降级 Ollama
        vllm_url = settings.vllm.host
        ollama_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")

        if await self._try_vllm(vllm_url, settings):
            logger.info(f"[{self.plugin_id}] Backend: vLLM at {vllm_url}")
        elif await self._try_ollama(ollama_url):
            logger.info(f"[{self.plugin_id}] Backend: Ollama at {ollama_url}")
        else:
            logger.error(f"[{self.plugin_id}] No LLM backend available")
            # 标记为降级模式，后续调用会报错
            return

        # 加载客服场景系统提示词
        if self.use_cs_prompt:
            self.system_prompt = build_system_prompt()
            logger.info(f"[{self.plugin_id}] Loaded customer-service system prompt")
        else:
            self.system_prompt = self._build_legacy_system_prompt()

        # 注册服务到 Context
        self.provide("llm", self)
        logger.info(f"[{self.plugin_id}] Registered as 'llm' service")

    async def _try_vllm(self, url: str, settings) -> bool:
        """尝试连接 vLLM，成功返回 True"""
        try:
            client = VLLMClient(
                base_url=url,
                api_key=settings.vllm.api_key,
                model=settings.model.name,
            )
            ready = await client.wait_for_ready(timeout=10)
            if ready:
                self.client = client
                self.backend_type = "vllm"
                return True
            await client.close()
        except Exception as e:
            logger.debug(f"vLLM probe failed: {e}")
        return False

    async def _try_ollama(self, url: str) -> bool:
        """尝试连接 Ollama，成功返回 True"""
        try:
            model = os.getenv("OLLAMA_MODEL", self.model_name)
            client = OllamaClient(base_url=url, model=model)
            ready = await client.wait_for_ready(timeout=10)
            if ready:
                self.client = client
                self.backend_type = "ollama"
                return True
            await client.close()
        except Exception as e:
            logger.debug(f"Ollama probe failed: {e}")
        return False

    def _build_legacy_system_prompt(self) -> str:
        """保留旧版系统提示词（向后兼容）"""
        return (
            "你是一个专业的智能客服助手。"
            "请根据用户的问题，结合提供的知识库内容，给出准确、简洁、有帮助的回答。"
            "如果知识库中没有相关信息，请诚实地告知用户，不要编造答案。"
            "回答时保持友好、专业的语气。"
        )

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """关闭客户端"""
        if self.client:
            await self.client.close()
        logger.info(f"[{self.plugin_id}] Destroyed")

    def _build_system_prompt(self) -> str:
        """构建系统提示词（可通过知识库动态注入）"""
        return (
            "你是一个专业的智能客服助手。"
            "请根据用户的问题，结合提供的知识库内容，给出准确、简洁、有帮助的回答。"
            "如果知识库中没有相关信息，请诚实地告知用户，不要编造答案。"
            "回答时保持友好、专业的语气。"
        )

    def set_system_prompt(self, prompt: str):
        """动态设置系统提示词"""
        self.system_prompt = prompt
        logger.info(f"[{self.plugin_id}] System prompt updated ({len(prompt)} chars)")

    def update_system_prompt_with_context(self, retrieved_docs: List[Dict[str, Any]]):
        """
        [DEPRECATED] 将检索到的知识库内容注入系统提示词（RAG 核心步骤）。

        .. deprecated:: 0.1.4
            此方法直接修改 ``self.system_prompt`` 共享状态，在多会话并发场景下
            会导致 RAG 上下文互相覆盖。请改用 :meth:`chat_with_context` 或
            :meth:`chat_stream_with_context`，它们按调用构建系统提示词，
            不修改共享状态。
        """
        if not retrieved_docs:
            return

        self.system_prompt = self._build_system_prompt_with_context(retrieved_docs)

    @staticmethod
    def _build_system_prompt_with_context(retrieved_docs: List[Dict[str, Any]]) -> str:
        """
        根据检索到的文档构建带知识库上下文的系统提示词（纯函数，不修改共享状态）。

        Args:
            retrieved_docs: 检索到的文档列表

        Returns:
            包含知识库上下文的系统提示词
        """
        if not retrieved_docs:
            return (
                "你是一个专业的智能客服助手。"
                "请根据用户的问题，结合提供的知识库内容，给出准确、简洁、有帮助的回答。"
                "如果知识库中没有相关信息，请诚实地告知用户，不要编造答案。"
                "回答时保持友好、专业的语气。"
            )

        context_parts = []
        for i, doc in enumerate(retrieved_docs, 1):
            content = doc.get("content", "")
            source = doc.get("metadata", {}).get("source", "未知来源")
            context_parts.append(f"[参考 {i}] (来源: {source})\n{content}")

        knowledge_context = "\n\n".join(context_parts)

        return (
            "你是一个专业的智能客服助手。"
            "请根据用户的问题，结合下面提供的知识库内容，给出准确、简洁、有帮助的回答。"
            "如果知识库中没有相关信息，请诚实地告知用户，不要编造答案。"
            "回答时保持友好、专业的语气。\n\n"
            f"=== 知识库参考内容 ===\n{knowledge_context}\n=== 参考结束 ==="
        )

    async def chat_with_context(
        self,
        messages: List[Dict[str, str]],
        retrieved_docs: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_mode: bool = False,
        fast_mode: bool = False,
    ) -> str:
        """
        带知识库上下文的非流式对话（线程安全，不修改共享状态）。

        Args:
            messages: 对话消息列表
            retrieved_docs: RAG 检索到的文档列表
            temperature: 采样温度
            max_tokens: 最大输出 token 数
            thinking_mode: 是否启用 Qwen3 深度思考模式
            fast_mode: 是否启用快速响应模式

        Returns:
            模型生成的回复文本
        """
        if self.client is None:
            return "[Error: LLM backend not initialized]"

        # 使用客服场景提示词
        system_prompt = build_system_prompt(
            retrieved_docs=retrieved_docs,
            thinking_mode=thinking_mode,
            fast_mode=fast_mode,
        )
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # Qwen3 思考模式控制
        if self.backend_type in ("vllm", "ollama") and "qwen3" in self.model_name.lower():
            full_messages = apply_qwen3_thinking(full_messages, enable=thinking_mode)

        response = await self.client.chat_completion(
            messages=full_messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            top_p=self.top_p,
        )

        self.publish("llm.completed", {
            "prompt_tokens": response.get("usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.get("usage", {}).get("completion_tokens", 0),
        })

        choices = response.get("choices")
        if not choices:
            logger.error(f"LLM response missing choices: {response}")
            return "[Error: LLM returned no choices]"
        return choices[0]["message"]["content"]

    async def chat_stream_with_context(
        self,
        messages: List[Dict[str, str]],
        retrieved_docs: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        带知识库上下文的流式对话（线程安全，不修改共享状态）。

        Args:
            messages: 对话消息列表
            retrieved_docs: RAG 检索到的文档列表
            temperature: 采样温度
            max_tokens: 最大输出 token 数

        Yields:
            每个 token 的文本片段
        """
        if self.client is None:
            return

        system_prompt = build_system_prompt(retrieved_docs=retrieved_docs)
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        async for chunk in self.client.chat_completion_stream(
            messages=full_messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            top_p=self.top_p,
        ):
            yield chunk

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        非流式对话。

        Args:
            messages: [{"role": "user", "content": "..."}, ...]
            temperature: 采样温度
            max_tokens: 最大输出 token 数

        Returns:
            模型生成的回复文本
        """
        if self.client is None:
            return "[Error: LLM backend not initialized]"

        # 注入系统提示词
        full_messages = [{"role": "system", "content": self.system_prompt}] + messages

        response = await self.client.chat_completion(
            messages=full_messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            top_p=self.top_p,
        )

        # 发布事件
        self.publish("llm.completed", {
            "prompt_tokens": response.get("usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.get("usage", {}).get("completion_tokens", 0),
        })

        choices = response.get("choices")
        if not choices:
            logger.error(f"LLM response missing choices: {response}")
            return "[Error: LLM returned no choices]"
        return choices[0]["message"]["content"]

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        流式对话（逐 token 输出）。

        Usage:
            async for chunk in llm.chat_stream(messages):
                print(chunk, end="", flush=True)
        """
        if self.client is None:
            return

        full_messages = [{"role": "system", "content": self.system_prompt}] + messages

        async for chunk in self.client.chat_completion_stream(
            messages=full_messages,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            top_p=self.top_p,
        ):
            yield chunk

    async def chat_simple(self, user_message: str) -> str:
        """最简对话接口（单轮）"""
        return await self.chat([{"role": "user", "content": user_message}])

    def health_check(self) -> Dict[str, Any]:
        """检查 LLM 服务健康状态"""
        return {
            "plugin": self.plugin_id,
            "model": self.model_name,
            "backend": self.backend_type,
            "connected": self.client is not None,
            "customer_service_prompt": self.use_cs_prompt,
        }
