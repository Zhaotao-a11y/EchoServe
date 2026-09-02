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
import asyncio
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
    plugin_version = "0.2.0"
    dependencies = ["core.llm", "core.knowledge", "core.retriever"]

    def __init__(self):
        self.max_history: int = 20          # 每个会话最多保留的消息数
        self.session_timeout: int = 1800    # 会话超时（秒），默认 30 分钟
        self.max_context_docs: int = 5      # RAG 最大注入文档数
        self._max_sessions: int = 1000      # 最多并发会话数（内存模式）
        self._store: (SessionStore | None) = None
        # Phase 1.4: 工作流 & 智能转接集成
        self._workflow_service = None       # 延迟注入
        self._intelligent_handoff = None    # 延迟注入
        self._pending_workflow_executions: dict[str, str] = {}  # session_id -> execution_id
        # Phase 2.5/2.6: AI 工单调查 + 工具调用集成
        self._tool_orchestrator = None      # 延迟注入
        self._ai_investigator = None        # 延迟注入

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

        self._fiber = fiber
        task = asyncio.create_task(self._cleanup_loop())
        fiber.add_task(task)
        logger.info(f"[{self.plugin_id}] Cleanup task started")

        # Phase 1.4: 延迟注入工作流 & 智能转接服务（允许缺失）
        self._workflow_service = ctx.inject("workflow_service", None)
        if self._workflow_service:
            logger.info(f"[{self.plugin_id}] Workflow service integrated")
        self._intelligent_handoff = ctx.inject("intelligent_handoff", None)
        if self._intelligent_handoff:
            logger.info(f"[{self.plugin_id}] Intelligent handoff integrated")

        # Phase 2.5/2.6: 工具编排器 + AI 工单调查
        self._tool_orchestrator = ctx.inject("tool_orchestrator", None)
        if self._tool_orchestrator:
            logger.info(f"[{self.plugin_id}] Tool orchestrator integrated")
        self._ai_investigator = ctx.inject("ai_investigator", None)
        if self._ai_investigator:
            logger.info(f"[{self.plugin_id}] AI investigator integrated")

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

        # ─── Phase 1.4: 工作流触发集成 ────────────────────────
        # 1. 检查是否有待恢复的 WAITING 工作流
        pending_exec_id = self._pending_workflow_executions.get(session_id)
        if pending_exec_id and self._workflow_service:
            try:
                result = await self._workflow_service.resume_workflow(
                    execution_id=pending_exec_id,
                    user_input=user_message,
                )
                if result.status.value == "completed":
                    # 工作流已完成，清除待恢复记录
                    self._pending_workflow_executions.pop(session_id, None)
                    reply = result.variables.get("reply", result.variables.get("output", ""))
                    if reply:
                        history.append({"role": "user", "content": user_message})
                        history.append({"role": "assistant", "content": reply})
                        self._trim_history(history)
                        await self._store.save_session(session_id, history)
                        return {
                            "session_id": session_id,
                            "reply": reply,
                            "retrieved_docs": [],
                            "tokens": {},
                            "workflow_execution_id": result.execution_id,
                        }
                elif result.status.value == "waiting":
                    # 工作流仍在等待用户输入
                    reply = result.variables.get("reply", "请继续输入...")
                    history.append({"role": "user", "content": user_message})
                    history.append({"role": "assistant", "content": reply})
                    self._trim_history(history)
                    await self._store.save_session(session_id, history)
                    return {
                        "session_id": session_id,
                        "reply": reply,
                        "retrieved_docs": [],
                        "tokens": {},
                        "workflow_execution_id": result.execution_id,
                        "workflow_waiting": True,
                    }
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Workflow resume failed: {e}")
                self._pending_workflow_executions.pop(session_id, None)

        # 2. 关键词触发匹配活动工作流
        if self._workflow_service:
            try:
                matched_wf = self._workflow_service.find_matching_workflow(
                    trigger_type="keyword",
                    trigger_value=user_message,
                    channel=channel,
                )
                if matched_wf:
                    logger.info(
                        f"[{self.plugin_id}] Workflow triggered: {matched_wf.workflow_id} "
                        f"({matched_wf.name})"
                    )
                    result = await self._workflow_service.execute_workflow(
                        workflow_id=matched_wf.workflow_id,
                        session_id=session_id,
                        user_id=user_id,
                        channel=channel,
                        variables={"user_message": user_message},
                    )
                    reply = result.variables.get("reply", result.variables.get("output", ""))

                    # 如果工作流处于 WAITING 状态，记录待恢复 execution_id
                    if result.status.value == "waiting":
                        self._pending_workflow_executions[session_id] = result.execution_id

                    if reply:
                        history.append({"role": "user", "content": user_message})
                        history.append({"role": "assistant", "content": reply})
                        self._trim_history(history)
                        await self._store.save_session(session_id, history)

                        self.publish("chat.workflow_triggered", {
                            "session_id": session_id,
                            "workflow_id": matched_wf.workflow_id,
                            "execution_id": result.execution_id,
                        })

                        return {
                            "session_id": session_id,
                            "reply": reply,
                            "retrieved_docs": [],
                            "tokens": {},
                            "workflow_execution_id": result.execution_id,
                            "workflow_waiting": result.status.value == "waiting",
                        }
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Workflow trigger check failed: {e}")

        # RAG 检索
        retrieved_docs = []
        if use_rag:
            retrieved_docs = await self._retrieve(user_message)

        # Phase 2.4b: 溯源标注增强 — 为检索结果添加引用元数据
        if retrieved_docs:
            try:
                from plugins.knowledge.source_citation import SourceCitationManager
                citation_mgr = SourceCitationManager()
                retrieved_docs = citation_mgr.enrich_results(retrieved_docs)
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Citation enrichment failed: {e}")

        # 构建消息列表
        messages = list(history) + [{"role": "user", "content": user_message}]

        # ─── Phase 2.6: Tool Use 集成 ───────────────────────
        reply = ""  # 初始化 reply, 防止 LSP 误报
        tool_used = False
        if self._tool_orchestrator and self._should_use_tools(user_message):
            try:
                tool_result = await self._tool_orchestrator.process_with_tools(
                    user_message=user_message,
                    messages=list(history),
                )
                if tool_result.get("tool_calls"):
                    reply = tool_result["reply"]
                    tool_used = True
                    logger.info(
                        f"[{self.plugin_id}] Tool calls executed: "
                        f"rounds={tool_result.get('rounds', 0)}, "
                        f"calls={len(tool_result['tool_calls'])}"
                    )
                    self.publish("chat.tool_used", {
                        "session_id": session_id,
                        "tool_calls": tool_result["tool_calls"],
                        "tool_results": tool_result.get("tool_results", []),
                        "rounds": tool_result.get("rounds", 0),
                    })
            except Exception as e:
                logger.warning(
                    f"[{self.plugin_id}] Tool orchestration failed, "
                    f"falling back to LLM: {e}"
                )

        # 正常 LLM 路径（工具未处理时）
        if not tool_used:
            # 调用 LLM（使用 context-aware 方法，避免并发覆盖共享 system_prompt）
            llm = self.inject("llm")
            if retrieved_docs:
                reply = await llm.chat_with_context(messages, retrieved_docs)
            else:
                reply = await llm.chat(messages)

        # Phase 2.4b: 规范化引用格式（移除幽灵引用、统一格式）
        if retrieved_docs:
            try:
                from plugins.knowledge.source_citation import SourceCitationManager
                citation_mgr = SourceCitationManager()
                reply = citation_mgr.normalize_response(reply, retrieved_docs)
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Citation normalization failed: {e}")

        # M-2: 过滤 LLM 输出中的敏感信息
        reply = filter_output(reply)

        # 更新历史
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        self._trim_history(history)
        await self._store.save_session(session_id, history)

        # ─── Phase 2.5: AI 自动工单创建 ─────────────────────
        # 对话完成后，判断是否需要自动创建工单（投诉/Bug/紧急等）
        if self._ai_investigator:
            try:
                if self._ai_investigator.should_create_ticket(user_message, session_id):
                    asyncio.create_task(self._async_create_ticket(
                        user_message=user_message,
                        session_id=session_id,
                        user_id=user_id,
                        channel=channel,
                    ))
                    logger.info(
                        f"[{self.plugin_id}] AI auto-ticket triggered "
                        f"for session {session_id}"
                    )
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] AI ticket check failed: {e}")

        # ─── Phase 1.4: 智能人机转接检查 ─────────────────────
        handoff_triggered = False
        if self._intelligent_handoff:
            try:
                recent_messages = [
                    {"role": m["role"], "text": m["content"]}
                    for m in history[-6:]
                ]
                decision = self._intelligent_handoff.should_handoff(
                    session_messages=recent_messages,
                    last_message=user_message,
                )
                if decision and decision.should_handoff:
                    logger.info(
                        f"[{self.plugin_id}] Intelligent handoff triggered: "
                        f"trigger={decision.trigger}, priority={decision.priority}, "
                        f"reason={decision.reason}"
                    )
                    handoff_result = self._intelligent_handoff.create_intelligent_handoff(
                        session_id=session_id,
                        messages=history,
                        last_message=user_message,
                        channel=channel,
                    )
                    if handoff_result and handoff_result.get("handoff_required", False):
                        handoff_triggered = True
                        self.publish("chat.handoff_triggered", {
                            "session_id": session_id,
                            "handoff_id": handoff_result.get("handoff_id", ""),
                            "trigger": decision.trigger,
                            "agent_id": handoff_result.get("agent_id", ""),
                        })
                        # 追加转接提示
                        reply += "\n\n[已为您转接人工客服，请稍候...]"
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Intelligent handoff check failed: {e}")

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
            "handoff_triggered": handoff_triggered,
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

        # Phase 2.4b: 溯源标注增强
        if retrieved_docs:
            try:
                from plugins.knowledge.source_citation import SourceCitationManager
                citation_mgr = SourceCitationManager()
                retrieved_docs = citation_mgr.enrich_results(retrieved_docs)
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Citation enrichment failed (stream): {e}")

        # 构建消息
        messages = list(history) + [{"role": "user", "content": user_message}]

        # ─── Phase 2.6: Tool Use 集成（流式路径）────────────────
        tool_used = False
        reply_text = ""
        if self._tool_orchestrator and self._should_use_tools(user_message):
            try:
                tool_result = await self._tool_orchestrator.process_with_tools(
                    user_message=user_message,
                    messages=list(history),
                )
                if tool_result.get("tool_calls"):
                    reply_text = tool_result["reply"]
                    tool_used = True
                    # 模拟流式输出，保持接口一致性
                    for char in reply_text:
                        yield char
                    logger.info(
                        f"[{self.plugin_id}] Tool calls (stream): "
                        f"rounds={tool_result.get('rounds', 0)}, "
                        f"calls={len(tool_result['tool_calls'])}"
                    )
                    self.publish("chat.tool_used", {
                        "session_id": session_id,
                        "tool_calls": tool_result["tool_calls"],
                        "stream": True,
                    })
            except Exception as e:
                logger.warning(
                    f"[{self.plugin_id}] Tool orchestration failed (stream), "
                    f"falling back to LLM: {e}"
                )

        # 正常流式 LLM 路径（工具未处理时）
        full_reply = []
        if not tool_used:
            llm = self.inject("llm")
            if retrieved_docs:
                async for chunk in llm.chat_stream_with_context(messages, retrieved_docs):
                    full_reply.append(chunk)
                    yield chunk
            else:
                async for chunk in llm.chat_stream(messages):
                    full_reply.append(chunk)
                    yield chunk
            reply_text = "".join(full_reply)
        # 工具路径已在 L488 直接赋值 reply_text = tool_result["reply"]

        # 更新历史
        # Phase 2.4b: 规范化引用格式
        if retrieved_docs:
            try:
                from plugins.knowledge.source_citation import SourceCitationManager
                citation_mgr = SourceCitationManager()
                reply_text = citation_mgr.normalize_response(reply_text, retrieved_docs)
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Citation normalization failed (stream): {e}")
        # M-2: 过滤 LLM 输出中的敏感信息
        reply_text = filter_output(reply_text)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply_text})
        self._trim_history(history)
        await self._store.save_session(session_id, history)

        # ─── Phase 2.5: AI 自动工单创建（流式路径）──────────
        if self._ai_investigator:
            try:
                if self._ai_investigator.should_create_ticket(user_message, session_id):
                    asyncio.create_task(self._async_create_ticket(
                        user_message=user_message,
                        session_id=session_id,
                        user_id=user_id,
                        channel=channel,
                    ))
                    logger.info(
                        f"[{self.plugin_id}] AI auto-ticket triggered (stream) "
                        f"for session {session_id}"
                    )
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] AI ticket check failed (stream): {e}")

        # ─── Phase 2.7: 智能人机转接检查（流式路径）──────────
        # 与 chat() 非流式路径保持一致的转接检查逻辑
        handoff_triggered = False
        if self._intelligent_handoff:
            try:
                recent_messages = [
                    {"role": m["role"], "text": m["content"]}
                    for m in history[-6:]
                ]
                decision = self._intelligent_handoff.should_handoff(
                    session_messages=recent_messages,
                    last_message=user_message,
                )
                if decision and decision.should_handoff:
                    logger.info(
                        f"[{self.plugin_id}] Intelligent handoff triggered (stream): "
                        f"trigger={decision.trigger}, priority={decision.priority}, "
                        f"reason={decision.reason}"
                    )
                    handoff_result = self._intelligent_handoff.create_intelligent_handoff(
                        session_id=session_id,
                        messages=history,
                        last_message=user_message,
                        channel=channel,
                    )
                    if handoff_result and handoff_result.get("handoff_required", False):
                        handoff_triggered = True
                        self.publish("chat.handoff_triggered", {
                            "session_id": session_id,
                            "handoff_id": handoff_result.get("handoff_id", ""),
                            "trigger": decision.trigger,
                            "agent_id": handoff_result.get("agent_id", ""),
                            "stream": True,
                        })
                        # 流式追加转接提示
                        yield "\n\n[已为您转接人工客服，请稍候...]"
            except Exception as e:
                logger.warning(
                    f"[{self.plugin_id}] Intelligent handoff check failed (stream): {e}"
                )

        self.publish("chat.completed", {
            "session_id": session_id,
            "stream": True,
            "docs_count": len(retrieved_docs),
            "handoff_triggered": handoff_triggered,
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

    # ─── Phase 2.5/2.6 辅助方法 ────────────────────────────

    def _should_use_tools(self, user_message: str) -> bool:
        """检测用户消息是否包含工具调用意图。

        规则：
        - 订单查询关键词 (订单号/物流/快递/发货)
        - 库存查询关键词 (库存/有货/缺货)
        - 售后关键词 (退货/退款/换货)
        - 工具调用标记 (显式工具调用意图)
        """
        msg = user_message.lower()
        tool_keywords = [
            "订单", "物流", "快递", "发货", "查快递", "追踪",
            "库存", "有货", "缺货", "补货",
            "退货", "退款", "换货", "申请退货", "申请退款",
            "取消订单", "修改订单",
        ]
        return any(kw in msg for kw in tool_keywords)

    async def _async_create_ticket(
        self,
        user_message: str,
        session_id: str,
        user_id: str,
        channel: str,
    ):
        """后台异步创建工单并执行 AI 调查。"""
        try:
            result = await self._ai_investigator.auto_create_and_investigate(
                user_message=user_message,
                session_id=session_id,
                customer_id=user_id,
                channel=channel,
            )
            ticket = result.get("ticket")
            if ticket:
                ticket_id = ticket.get("id", "unknown")
                logger.info(
                    f"[{self.plugin_id}] Auto-ticket created: "
                    f"{ticket_id} (session={session_id})"
                )
                # 发布事件供其他模块监听
                self.publish("ticket.auto_created", {
                    "session_id": session_id,
                    "ticket_id": ticket_id,
                    "classification": result.get("classification"),
                    "investigation": result.get("investigation"),
                })
            else:
                logger.warning(
                    f"[{self.plugin_id}] Auto-ticket creation failed "
                    f"(session={session_id})"
                )
        except Exception as e:
            logger.error(f"[{self.plugin_id}] _async_create_ticket error: {e}")

    # ─── 内部方法 ──────────────────────────────────────────

    async def _retrieve(self, query: str) -> list[dict[str, Any]]:
        """执行知识库检索"""
        retriever = self.inject("retriever")
        if not retriever:
            return []
        return await retriever.retrieve(query, top_k=self.max_context_docs)
