"""
EchoServe V0.2.0 — 对话管理插件

核心编排逻辑：
    接收用户消息 → 检索知识库 → 注入上下文 → 调用 LLM → 返回回复

管理：
- 多轮对话历史（按 session_id 隔离）
- 会话超时清理
- RAG 流程编排
- 回复质量追踪

V0.1.6 变更：
- 引入 SessionStore 抽象层，Redis 可用时持久化到 Redis（多实例共享 + 重启恢复）
- Redis 不可用时自动回退到内存 OrderedDict（与旧版行为一致）
"""
from __future__ import annotations

import time
import logging
from typing import Any
from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber
from .session_store import (
    SessionStore,
    MemorySessionStore,
    RedisSessionStore,
    create_session_store,
)
# M-2: Prompt 注入防护
from .prompt_guard import detect_injection, sanitize_input, filter_output

logger = logging.getLogger("echoserve.chat")


class ChatPlugin(BaizePlugin):
    """对话管理插件"""

    plugin_id = "core.chat"
    plugin_name = "对话管理器"
    plugin_version = "0.1.6"
    dependencies = ["core.llm", "core.knowledge", "core.retriever"]

    def __init__(self):
        self.max_history: int = 20          # 每个会话最多保留的消息数
        self.session_timeout: int = 1800    # 会话超时（秒），默认 30 分钟
        self.max_context_docs: int = 5      # RAG 最大注入文档数
        self._max_sessions: int = 1000      # 最多并发会话数（内存模式）
        self._store: (SessionStore | None) = None

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化 — 创建 SessionStore（优先 Redis，降级内存）"""
        settings = ctx.settings

        # 从配置读取 Redis 参数
        redis_cfg = getattr(settings, "redis", None)
        redis_url = None
        redis_prefix = "echoseve:session:"
        redis_ttl = self.session_timeout

        if redis_cfg:
            redis_url = redis_cfg.url
            redis_prefix = redis_cfg.key_prefix
            redis_ttl = redis_cfg.session_ttl

        self._store = create_session_store(
            redis_url=redis_url,
            key_prefix=redis_prefix,
            session_ttl=redis_ttl,
            max_sessions=self._max_sessions,
        )

        # 如果是 Redis 存储，尝试连接
        if isinstance(self._store, RedisSessionStore):
            connected = await self._store._connect()
            if connected:
                logger.info(f"[{self.plugin_id}] Using Redis session store")
            else:
                # Redis 连不上，降级到内存
                logger.warning(
                    f"[{self.plugin_id}] Redis unavailable, "
                    f"falling back to memory session store"
                )
                await self._store.close()
                self._store = MemorySessionStore(max_sessions=self._max_sessions)
        else:
            logger.info(f"[{self.plugin_id}] Using memory session store")

        self.provide("chat_manager", self)
        logger.info(f"[{self.plugin_id}] Initialized "
                     f"(max_history={self.max_history}, timeout={self.session_timeout}s)")

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        """启动定时清理任务"""
        import asyncio
        self._fiber = fiber
        task = asyncio.create_task(self._cleanup_loop())
        fiber.add_task(task)
        logger.info(f"[{self.plugin_id}] Cleanup task started")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """释放会话存储资源"""
        if self._store:
            await self._store.close()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 核心对话 API ──────────────────────────────────────

    async def chat(
        self,
        session_id: str,
        user_message: str,
        use_rag: bool = True,
        user_id: str = "anonymous",
        channel: str = "web",
    ) -> dict[str, Any]:
        """
        处理一轮对话（非流式）。

        Args:
            session_id: 会话 ID
            user_message: 用户消息
            use_rag: 是否使用 RAG 检索增强

        Returns:
            {
                "session_id": str,
                "reply": str,
                "retrieved_docs": [...],  # 检索到的文档（调试用）
                "tokens": {...},          # token 用量
            }
        """
        # 获取或创建会话历史（通过 SessionStore）
        history = await self._get_or_create_session(session_id)

        # M-2: Prompt 注入防护 — 清洗输入 + 检测注入
        user_message = sanitize_input(user_message)
        injection = detect_injection(user_message)
        if injection:
            logger.warning(f"[{self.plugin_id}] Prompt injection blocked: {injection[:80]}")
            return {
                "session_id": session_id,
                "reply": "我无法处理此类请求，如需帮助请联系人工客服",
                "retrieved_docs": [],
                "tokens": {},
            }

        # 记录请求开始时间（用于延迟统计）
        start_time = time.time()

        # 更新会话活跃时间
        await self._store.update_timestamp(session_id)

        # RAG 检索
        retrieved_docs = []
        if use_rag:
            retrieved_docs = await self._retrieve(user_message)

        # 构建消息列表
        messages = list(history) + [{"role": "user", "content": user_message}]

        # 调用 LLM（使用 context-aware 方法，避免并发覆盖共享 system_prompt）
        llm = self.inject("llm")
        if retrieved_docs:
            reply = await llm.chat_with_context(messages, retrieved_docs)
        else:
            reply = await llm.chat(messages)

        # M-2: 过滤 LLM 输出中的敏感信息
        reply = filter_output(reply)

        # 更新历史
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        self._trim_history(history)
        await self._store.save_session(session_id, history)

        # 发布事件
        self.publish("chat.completed", {
            "session_id": session_id,
            "message": user_message,
            "reply": reply[:200],
            "docs_count": len(retrieved_docs),
        })

        # 记录审计日志
        audit = self.inject("audit_logger")
        if audit:
            try:
                source_ids = [
                    d.get("id", "") for d in retrieved_docs[:self.max_context_docs]
                ]
                audit.log_sync(
                    action="chat_query",
                    user_id=user_id,
                    query=user_message,
                    response_summary=reply[:500],
                    sources=source_ids,
                    latency_ms=int((time.time() - start_time) * 1000),
                    channel=channel,
                )
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Audit log failed: {e}")

        return {
            "session_id": session_id,
            "reply": reply,
            "retrieved_docs": retrieved_docs[:self.max_context_docs],
            "tokens": {},
        }

    async def chat_stream(
        self,
        session_id: str,
        user_message: str,
        use_rag: bool = True,
        user_id: str = "anonymous",
        channel: str = "web",
    ):
        """
        流式对话（异步生成器）。

        Yields:
            每个 token 的文本片段
        """
        history = await self._get_or_create_session(session_id)
        start_time = time.time()
        await self._store.update_timestamp(session_id)

        # M-2: Prompt 注入防护 — 清洗输入 + 检测注入
        user_message = sanitize_input(user_message)
        injection = detect_injection(user_message)
        if injection:
            logger.warning(f"[{self.plugin_id}] Prompt injection blocked (stream): {injection[:80]}")
            yield "我无法处理此类请求，如需帮助请联系人工客服"
            return

        # RAG
        retrieved_docs = []
        if use_rag:
            retrieved_docs = await self._retrieve(user_message)

        # 构建消息
        messages = list(history) + [{"role": "user", "content": user_message}]

        # 流式调用（使用 context-aware 方法，避免并发覆盖共享 system_prompt）
        llm = self.inject("llm")
        full_reply = []
        if retrieved_docs:
            async for chunk in llm.chat_stream_with_context(messages, retrieved_docs):
                full_reply.append(chunk)
                yield chunk
        else:
            async for chunk in llm.chat_stream(messages):
                full_reply.append(chunk)
                yield chunk

        # 更新历史
        reply_text = "".join(full_reply)
        # M-2: 过滤 LLM 输出中的敏感信息
        reply_text = filter_output(reply_text)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply_text})
        self._trim_history(history)
        await self._store.save_session(session_id, history)

        self.publish("chat.completed", {
            "session_id": session_id,
            "stream": True,
            "docs_count": len(retrieved_docs),
        })

        # 记录审计日志（流式）
        audit = self.inject("audit_logger")
        if audit:
            try:
                source_ids = [
                    d.get("id", "") for d in retrieved_docs[:self.max_context_docs]
                ]
                audit.log_sync(
                    action="chat_query_stream",
                    user_id=user_id,
                    query=user_message,
                    response_summary=reply_text[:500],
                    sources=source_ids,
                    latency_ms=int((time.time() - start_time) * 1000),
                    channel=channel,
                )
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Audit log failed: {e}")

    # ─── 会话管理 ──────────────────────────────────────────

    async def _get_or_create_session(self, session_id: str) -> list[dict[str, str]]:
        """获取或创建会话历史（通过 SessionStore）"""
        history = await self._store.load_session(session_id)
        return history if history is not None else []

    def _trim_history(self, history: list[dict[str, str]]):
        """截断过长的历史"""
        if len(history) > self.max_history:
            # 保留最新的 max_history 条
            del history[:len(history) - self.max_history]

    async def clear_session(self, session_id: str) -> bool:
        """清除指定会话"""
        return await self._store.delete_session(session_id)

    async def get_session_history(self, session_id: str) -> list[dict[str, str]]:
        """获取会话历史"""
        return await self._store.load_session(session_id)

    async def list_sessions(self) -> list[str]:
        """列出所有活跃会话 ID"""
        return await self._store.list_sessions()

    async def _cleanup_loop(self):
        """定时清理过期会话"""
        import asyncio
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.plugin_id}] Cleanup error: {e}")

    async def _cleanup_expired(self):
        """清理超时会话（通过 SessionStore 统一清理）"""
        cleaned = await self._store.cleanup_expired(self.session_timeout)
        if cleaned > 0:
            logger.info(f"[{self.plugin_id}] Cleaned {cleaned} expired sessions")

    # ─── 内部方法 ──────────────────────────────────────────

    async def _retrieve(self, query: str) -> list[dict[str, Any]]:
        """执行知识库检索"""
        retriever = self.inject("retriever")
        if not retriever:
            return []
        return await retriever.retrieve(query, top_k=self.max_context_docs)
