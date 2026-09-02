# -*- coding: utf-8 -*-
"""
Workflow Engine — DAG 异步执行器

核心能力：
    - 基于 asyncio 的异步节点执行
    - 条件分支路由（支持变量比较、AI 判断、LLM 条件）
    - LOOP 循环防死循环（最大 10 次）
    - 节点级超时控制（默认 30 秒）
    - 全局执行超时（默认 120 秒）
    - 执行中断与恢复

设计原则：
    - 每个节点独立执行，错误可回退或终止
    - 执行上下文按 session 隔离
    - 超时处理统一为 ExecutionStatus.TIMEOUT
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .models import (
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)

logger = logging.getLogger("echoserve.workflow.engine")

# ─── 执行器默认常量 ───────────────────────────────────
DEFAULT_NODE_TIMEOUT = 30.0          # 单个节点超时（秒）
DEFAULT_GLOBAL_TIMEOUT = 120.0       # 全局执行超时（秒）
MAX_LOOP_ITERATIONS = 10             # LOOP 节点最大迭代次数


class NodeExecutor:
    """
    节点执行器基类。

    每个节点类型对应一个 Executor，通过 registry 注册。
    执行器接收节点配置和当前 ExecutionContext，返回执行结果 dict。
    """

    node_type: NodeType = NodeType.ASSIGN  # 占位

    async def execute(
        self,
        node: WorkflowNode,
        ctx: ExecutionContext,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        """
        执行节点逻辑。

        Args:
            node: 当前节点定义
            ctx: 执行上下文（含变量、历史结果）
            services: 外部服务映射（llm / knowledge / ticket / agent 等）

        Returns:
            dict 格式的节点输出，会被写入 ctx.node_results[node.node_id]
        """
        raise NotImplementedError


class TriggerExecutor(NodeExecutor):
    """触发器节点：验证触发条件并返回初始变量"""

    node_type = NodeType.TRIGGER

    async def execute(self, node, ctx, services):
        config = node.config
        trigger_type = config.get("trigger_type", "message")  # message / intent / webhook / schedule

        # 触发器节点通常由外部调用方预检查，这里记录触发信息
        return {
            "trigger_type": trigger_type,
            "trigger_value": config.get("trigger_value", ""),
            "variables_assigned": config.get("variables", {}),
        }


class AIExecutor(NodeExecutor):
    """AI 节点：调用 LLM 生成回复"""

    node_type = NodeType.AI

    async def execute(self, node, ctx, services):
        config = node.config
        prompt_template = config.get("prompt", "{{user_message}}")
        model = config.get("model", "default")
        temperature = config.get("temperature", 0.7)
        max_tokens = config.get("max_tokens", 1024)

        # 变量替换
        user_message = ctx.get_variable("user_message", "")
        prompt = prompt_template.replace("{{user_message}}", user_message)
        prompt = prompt.replace("{{session_id}}", ctx.session_id)

        # 注入 RAG 上下文（如果配置）
        rag_context = ""
        if config.get("use_rag", False):
            retriever = services.get("retriever")
            if retriever:
                try:
                    rag_results = await retriever.retrieve(user_message, top_k=3)
                    rag_context = "\n\n".join(
                        f"[Source {i+1}] {r.get('content', '')}"
                        for i, r in enumerate(rag_results)
                    )
                except Exception as e:
                    logger.warning(f"[Workflow-AI] RAG retrieval failed: {e}")

        if rag_context:
            prompt = f"Context:\n{rag_context}\n\nQuestion: {prompt}\nAnswer:"

        # 调用 LLM
        llm = services.get("llm")
        if not llm:
            return {"error": "LLM service not available", "response": "[系统错误：AI 服务不可用]"}

        try:
            messages = [{"role": "user", "content": prompt}]
            # 复用 EchoServe 的 LLM 接口（兼容 OpenAI format）
            response = await llm.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = response.get("content", "")
            return {"response": text, "model": model, "tokens_used": response.get("tokens_used", 0)}
        except Exception as e:
            logger.error(f"[Workflow-AI] LLM call failed: {e}")
            return {"error": str(e), "response": "[AI 生成失败，请稍后重试]"}


class ConditionExecutor(NodeExecutor):
    """条件节点：根据表达式判断分支"""

    node_type = NodeType.CONDITION

    async def execute(self, node, ctx, services):
        config = node.config
        condition_type = config.get("condition_type", "expression")  # expression / llm / sentiment

        if condition_type == "expression":
            # 变量表达式：如 "{{sentiment_score}} < -0.6"
            expression = config.get("expression", "true")
            result = self._eval_expression(expression, ctx)
            return {"matched": result, "condition": expression, "type": "expression"}

        elif condition_type == "llm":
            # LLM 判断：让 AI 判断是否符合条件
            llm = services.get("llm")
            if not llm:
                return {"matched": False, "error": "LLM unavailable"}

            judgment_prompt = config.get("prompt", "判断以下消息是否表示用户想要转人工？只回答 YES 或 NO。\n消息: {{user_message}}")
            judgment_prompt = judgment_prompt.replace("{{user_message}}", ctx.get_variable("user_message", ""))

            response = await llm.chat(
                messages=[{"role": "user", "content": judgment_prompt}],
                model=config.get("model", "default"),
                temperature=0.1,
                max_tokens=10,
            )
            text = response.get("content", "").strip().upper()
            matched = "YES" in text
            return {"matched": matched, "llm_response": text, "type": "llm"}

        elif condition_type == "sentiment":
            # 情绪判断：调用情绪分析服务
            sentiment = services.get("sentiment_analyzer")
            if not sentiment:
                return {"matched": False, "error": "Sentiment analyzer not available"}

            user_message = ctx.get_variable("user_message", "")
            result = await sentiment.analyze(user_message)
            score = result.get("score", 0)  # -1.0 ~ 1.0
            threshold = config.get("threshold", -0.6)
            matched = score < threshold  # 负面情绪匹配

            # 记录情绪分数到变量，供后续节点使用
            ctx.set_variable("sentiment_score", score)
            ctx.set_variable("sentiment_label", result.get("label", "neutral"))

            return {"matched": matched, "score": score, "threshold": threshold, "type": "sentiment"}

        return {"matched": False, "error": "Unknown condition type"}

    def _eval_expression(self, expression: str, ctx: ExecutionContext) -> bool:
        """安全地评估变量表达式"""
        # 提取 {{variable}} 并替换为实际值
        def _replacer(match):
            var_name = match.group(1).strip()
            val = ctx.get_variable(var_name, None)
            if val is None:
                # 尝试从 node_results 中获取
                for node_id, result in ctx.node_results.items():
                    if isinstance(result, dict) and var_name in result:
                        return str(result[var_name])
                return "None"
            return str(val)

        expr = re.sub(r"\{\{(.*?)\}\}", _replacer, expression)

        # 安全评估：只允许比较运算符和数值/字符串
        # 白名单字符
        safe_pattern = re.compile(r"^[\d\s\+\-\*\/\%\<\>\=\!\&\|\(\)\.a-zA-Z_\"\']+$")
        if not safe_pattern.match(expr):
            logger.warning(f"[Workflow-Condition] Expression rejected for safety: {expr}")
            return False

        try:
            # 使用 eval 但限制 builtins
            return bool(eval(expr, {"__builtins__": {}}, {}))  # type: ignore
        except Exception as e:
            logger.warning(f"[Workflow-Condition] Eval failed: {e}")
            return False


class RAGExecutor(NodeExecutor):
    """RAG 节点：知识库检索"""

    node_type = NodeType.RAG

    async def execute(self, node, ctx, services):
        config = node.config
        knowledge_base = config.get("knowledge_base", "default")
        top_k = config.get("top_k", 3)
        query = ctx.get_variable("user_message", "")

        retriever = services.get("retriever")
        if not retriever:
            return {"error": "Retriever service not available", "results": []}

        try:
            results = await retriever.retrieve(query, knowledge_base=knowledge_base, top_k=top_k)
            # 格式化结果
            formatted = []
            for i, r in enumerate(results):
                formatted.append({
                    "index": i + 1,
                    "content": r.get("content", "")[:500],
                    "source": r.get("source", "unknown"),
                    "score": r.get("score", 0),
                })

            # 将检索结果写入变量，供后续 AI 节点使用
            ctx.set_variable("rag_results", formatted)
            return {"results": formatted, "count": len(formatted), "knowledge_base": knowledge_base}
        except Exception as e:
            logger.error(f"[Workflow-RAG] Retrieval failed: {e}")
            return {"error": str(e), "results": []}


class HandoffExecutor(NodeExecutor):
    """转人工节点：触发人机转接"""

    node_type = NodeType.HANDOFF

    async def execute(self, node, ctx, services):
        config = node.config
        queue = config.get("queue", "default")
        priority = config.get("priority", "normal")  # low / normal / high / urgent
        reason = config.get("reason", "用户请求转人工")
        summary = config.get("summary", "")

        # 生成对话摘要（如果没有提供）
        if not summary:
            # 从上下文中构造简单摘要
            history = ctx.get_variable("chat_history", [])
            if history:
                last_msgs = history[-5:] if len(history) > 5 else history
                summary = " | ".join(f"{m.get('role', '?')}: {m.get('content', '')[:50]}" for m in last_msgs)

        # 调用 agent 服务进行转接
        agent_service = services.get("agent")
        if agent_service:
            try:
                result = await agent_service.handoff(
                    session_id=ctx.session_id,
                    queue=queue,
                    priority=priority,
                    reason=reason,
                    summary=summary,
                    context=ctx.to_dict(),
                )
                return {
                    "handoff_id": result.get("handoff_id", ""),
                    "queue": queue,
                    "priority": priority,
                    "estimated_wait": result.get("estimated_wait", -1),
                    "status": "queued",
                }
            except Exception as e:
                logger.error(f"[Workflow-Handoff] Handoff failed: {e}")
                return {"error": str(e), "status": "failed"}

        # agent 服务不可用，记录转接请求
        return {
            "handoff_request": {
                "session_id": ctx.session_id,
                "queue": queue,
                "priority": priority,
                "reason": reason,
                "summary": summary,
            },
            "status": "pending",
            "warning": "Agent service not available, handoff queued for later processing",
        }


class HTTPExecutor(NodeExecutor):
    """HTTP 节点：调用外部 API"""

    node_type = NodeType.HTTP

    async def execute(self, node, ctx, services):
        config = node.config
        method = config.get("method", "GET").upper()
        url = config.get("url", "")
        headers = config.get("headers", {})
        body_template = config.get("body", "")
        timeout = config.get("timeout", 10)

        if not url:
            return {"error": "URL is required", "status_code": 0}

        # 变量替换
        url = self._interpolate(url, ctx)
        body = self._interpolate(body_template, ctx)
        headers = {k: self._interpolate(v, ctx) for k, v in headers.items()}

        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                kwargs = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=timeout)}
                if method in ("POST", "PUT", "PATCH") and body:
                    kwargs["data"] = body

                async with session.request(method, url, **kwargs) as resp:
                    text = await resp.text()
                    return {
                        "status_code": resp.status,
                        "headers": dict(resp.headers),
                        "body": text[:2000],  # 限制返回大小
                        "url": url,
                    }
        except asyncio.TimeoutError:
            return {"error": f"Request timeout after {timeout}s", "status_code": 0}
        except Exception as e:
            return {"error": str(e), "status_code": 0}

    def _interpolate(self, template: str, ctx: ExecutionContext) -> str:
        """替换模板中的 {{variable}}"""
        def _replacer(match):
            var_name = match.group(1).strip()
            val = ctx.get_variable(var_name, "")
            if val is None:
                return ""
            return str(val)
        return re.sub(r"\{\{(.*?)\}\}", _replacer, template)


class TicketExecutor(NodeExecutor):
    """工单节点：自动创建工单"""

    node_type = NodeType.TICKET

    async def execute(self, node, ctx, services):
        config = node.config
        title = config.get("title", "自动创建工单")
        description = config.get("description", "")
        priority = config.get("priority", "medium")
        category = config.get("category", "general")
        assign_to = config.get("assign_to", "")

        # 变量替换
        title = self._interpolate(title, ctx)
        description = self._interpolate(description, ctx)

        ticket_service = services.get("ticket")
        if not ticket_service:
            return {"error": "Ticket service not available", "ticket_id": ""}

        try:
            ticket = ticket_service.create_ticket(
                title=title,
                description=description,
                priority=priority,
                category=category,
                session_id=ctx.session_id,
                customer_id=ctx.user_id,
                channel=ctx.channel,
                assigned_agent=assign_to,
                created_by="workflow",
            )
            return {
                "ticket_id": ticket.get("id", ""),
                "status": ticket.get("status", "open"),
                "priority": priority,
                "category": category,
            }
        except Exception as e:
            logger.error(f"[Workflow-Ticket] Create failed: {e}")
            return {"error": str(e), "ticket_id": ""}

    def _interpolate(self, template: str, ctx: ExecutionContext) -> str:
        def _replacer(match):
            var_name = match.group(1).strip()
            val = ctx.get_variable(var_name, "")
            return str(val) if val is not None else ""
        return re.sub(r"\{\{(.*?)\}\}", _replacer, template)


class WaitExecutor(NodeExecutor):
    """等待节点：等待用户输入或定时器"""

    node_type = NodeType.WAIT

    async def execute(self, node, ctx, services):
        config = node.config
        wait_type = config.get("wait_type", "user_input")  # user_input / timer
        duration = config.get("duration", 0)  # 秒（仅 timer 模式）

        if wait_type == "timer" and duration > 0:
            await asyncio.sleep(min(duration, 300))  # 最大 5 分钟
            return {"waited_seconds": duration, "type": "timer"}

        # user_input 模式：返回 WAITING 状态，由外部调用方恢复执行
        return {"type": "user_input", "status": "waiting", "message": "等待用户输入..."}


class AssignExecutor(NodeExecutor):
    """赋值节点：修改变量"""

    node_type = NodeType.ASSIGN

    async def execute(self, node, ctx, services):
        config = node.config
        assignments = config.get("assignments", {})

        for key, value in assignments.items():
            # 支持从其他节点结果引用：如 "{{node_abc.response}}"
            if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
                ref = value[2:-2].strip()
                if "." in ref:
                    node_id, attr = ref.split(".", 1)
                    node_result = ctx.node_results.get(node_id, {})
                    if isinstance(node_result, dict):
                        value = node_result.get(attr, "")
                    else:
                        value = str(node_result)
                else:
                    value = ctx.get_variable(ref, "")
            ctx.set_variable(key, value)

        return {"assigned": list(assignments.keys())}


class LoopExecutor(NodeExecutor):
    """循环节点：重复执行子流程"""

    node_type = NodeType.LOOP

    async def execute(self, node, ctx, services):
        config = node.config
        loop_count = ctx.loop_counters.get(node.node_id, 0)
        max_iterations = config.get("max_iterations", MAX_LOOP_ITERATIONS)
        condition = config.get("condition", "true")  # 循环条件表达式

        # 检查是否超过最大迭代次数
        if loop_count >= max_iterations:
            return {"status": "max_reached", "iterations": loop_count, "continue": False}

        # 评估条件
        condition_executor = ConditionExecutor()
        matched = condition_executor._eval_expression(condition, ctx)

        ctx.loop_counters[node.node_id] = loop_count + 1

        return {
            "status": "iteration",
            "iterations": loop_count + 1,
            "continue": matched,
            "condition": condition,
        }


class EndExecutor(NodeExecutor):
    """结束节点：终止工作流"""

    node_type = NodeType.END

    async def execute(self, node, ctx, services):
        config = node.config
        return {
            "status": "ended",
            "message": config.get("message", "工作流执行完毕"),
            "variables": ctx.variables,
            "execution_path": ctx.execution_path,
        }


# ─── 执行器注册表 ───────────────────────────────────

_EXECUTOR_REGISTRY: dict[NodeType, type[NodeExecutor]] = {}


def register_executor(node_type: NodeType, executor_cls: type[NodeExecutor]):
    _EXECUTOR_REGISTRY[node_type] = executor_cls


def get_executor(node_type: NodeType) -> NodeExecutor | None:
    cls = _EXECUTOR_REGISTRY.get(node_type)
    if cls:
        return cls()
    return None


# 注册所有内置执行器
register_executor(NodeType.TRIGGER, TriggerExecutor)
register_executor(NodeType.AI, AIExecutor)
register_executor(NodeType.CONDITION, ConditionExecutor)
register_executor(NodeType.RAG, RAGExecutor)
register_executor(NodeType.HANDOFF, HandoffExecutor)
register_executor(NodeType.HTTP, HTTPExecutor)
register_executor(NodeType.TICKET, TicketExecutor)
register_executor(NodeType.WAIT, WaitExecutor)
register_executor(NodeType.ASSIGN, AssignExecutor)
register_executor(NodeType.LOOP, LoopExecutor)
register_executor(NodeType.END, EndExecutor)


# ─── DAG 执行引擎 ───────────────────────────────────

class WorkflowEngine:
    """
    DAG 工作流执行引擎。

    用法：
        engine = WorkflowEngine(services={"llm": llm_client, "retriever": retriever})
        result = await engine.execute(workflow_def, session_id="sess_123")
    """

    def __init__(
        self,
        services: dict[str, Any] | None = None,
        node_timeout: float = DEFAULT_NODE_TIMEOUT,
        global_timeout: float = DEFAULT_GLOBAL_TIMEOUT,
    ):
        self.services = services or {}
        self.node_timeout = node_timeout
        self.global_timeout = global_timeout

    async def execute(
        self,
        workflow: WorkflowDefinition,
        session_id: str,
        user_id: str = "",
        channel: str = "web",
        initial_variables: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """
        执行工作流。

        从 TRIGGER 节点开始，按 DAG 拓扑顺序执行，遇到分支根据条件路由。
        """
        execution_id = f"EX-{uuid.uuid4().hex[:8].upper()}"
        ctx = ExecutionContext(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            variables=initial_variables or {},
            workflow_id=workflow.workflow_id,
        )

        # 验证工作流
        valid, errors = workflow.validate()
        if not valid:
            return ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                context=ctx,
                error=f"Workflow validation failed: {', '.join(errors)}",
            )

        # 找到 TRIGGER 节点作为起点
        triggers = [n for n in workflow.nodes if n.node_type == NodeType.TRIGGER]
        if not triggers:
            return ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                context=ctx,
                error="No TRIGGER node found",
            )

        current_node = triggers[0]
        start_time = time.time()

        try:
            while True:
                # 全局超时检查
                elapsed = time.time() - start_time
                if elapsed > self.global_timeout:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.TIMEOUT,
                        context=ctx,
                        error=f"Global timeout after {self.global_timeout}s",
                    )

                # 执行当前节点
                ctx.current_node_id = current_node.node_id
                node_result = await self._execute_node(current_node, ctx)
                ctx.set_node_result(current_node.node_id, node_result)

                # 如果节点返回 waiting 状态（如 WAIT 节点等待用户输入），暂停执行
                if node_result.get("status") == "waiting" and current_node.node_type == NodeType.WAIT:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.WAITING,
                        context=ctx,
                        outputs=node_result,
                    )

                # 如果是 END 节点，结束
                if current_node.node_type == NodeType.END:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.COMPLETED,
                        context=ctx,
                        outputs=node_result,
                    )

                # 找到下一个节点
                next_node = self._route_next(workflow, current_node, ctx)
                if next_node is None:
                    # 没有下一个节点，但还没到 END —— 异常终止
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.FAILED,
                        context=ctx,
                        error=f"No outgoing edge matched from node {current_node.node_id}",
                    )

                current_node = next_node

        except asyncio.TimeoutError:
            return ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.TIMEOUT,
                context=ctx,
                error="Execution timed out",
            )
        except Exception as e:
            logger.exception(f"[WorkflowEngine] Execution failed: {e}")
            return ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                context=ctx,
                error=str(e),
            )

    async def _execute_node(self, node: WorkflowNode, ctx: ExecutionContext) -> dict[str, Any]:
        """执行单个节点，带超时控制"""
        executor = get_executor(node.node_type)
        if not executor:
            return {"error": f"No executor for node type {node.node_type.value}"}

        try:
            return await asyncio.wait_for(
                executor.execute(node, ctx, self.services),
                timeout=self.node_timeout,
            )
        except asyncio.TimeoutError:
            return {"error": f"Node {node.node_id} timed out after {self.node_timeout}s", "status": "timeout"}

    def _route_next(
        self,
        workflow: WorkflowDefinition,
        current_node: WorkflowNode,
        ctx: ExecutionContext,
    ) -> WorkflowNode | None:
        """根据条件路由到下一个节点"""
        outgoing = workflow.get_outgoing_edges(current_node.node_id)
        if not outgoing:
            return None

        # 条件分支处理
        condition_edges = [e for e in outgoing if e.condition]
        default_edges = [e for e in outgoing if e.is_default]
        plain_edges = [e for e in outgoing if not e.condition and not e.is_default]

        # 1. 先尝试匹配条件边
        for edge in condition_edges:
            # 获取当前节点的执行结果
            node_result = ctx.node_results.get(current_node.node_id, {})
            # 如果节点结果中有 matched 字段（CONDITION 节点），用它判断
            if current_node.node_type == NodeType.CONDITION:
                matched = node_result.get("matched", False)
                # 根据边标签判断（如 "Yes" / "No"）
                if matched and edge.label in ("Yes", "True", "yes", "true", ""):
                    return workflow.get_node(edge.target)
                if not matched and edge.label in ("No", "False", "no", "false", ""):
                    return workflow.get_node(edge.target)
            else:
                # 其他节点的条件边：评估表达式
                executor = ConditionExecutor()
                if executor._eval_expression(edge.condition, ctx):
                    return workflow.get_node(edge.target)

        # 2. 条件都不匹配，走默认边
        if default_edges:
            return workflow.get_node(default_edges[0].target)

        # 3. 无条件边，按顺序走第一个
        if plain_edges:
            return workflow.get_node(plain_edges[0].target)

        # 4. 如果当前是 LOOP 节点且 continue=true，需要特殊处理回边
        if current_node.node_type == NodeType.LOOP:
            node_result = ctx.node_results.get(current_node.node_id, {})
            if node_result.get("continue", False):
                # 找到 LOOP 节点回指的边（target 是 LOOP 的子流程起点）
                # 约定：LOOP 节点的 outgoing edges 中，target 不是 END 的就是循环体
                for edge in outgoing:
                    target = workflow.get_node(edge.target)
                    if target and target.node_type != NodeType.END:
                        return target

        return None

    async def resume(
        self,
        workflow: WorkflowDefinition,
        ctx: ExecutionContext,
        user_input: str,
    ) -> ExecutionResult:
        """从 WAITING 状态恢复执行（用户输入后继续）"""
        # 更新变量
        ctx.set_variable("user_message", user_input)
        ctx.set_variable("resumed_at", datetime.now(timezone.utc).isoformat())

        # 找到上次暂停的节点，继续
        current_node = workflow.get_node(ctx.current_node_id)
        if not current_node:
            return ExecutionResult(
                execution_id=f"EX-{uuid.uuid4().hex[:8].upper()}",
                status=ExecutionStatus.FAILED,
                context=ctx,
                error=f"Cannot resume: node {ctx.current_node_id} not found",
            )

        # 继续执行流程（从当前节点的下一个节点开始）
        execution_id = f"EX-{uuid.uuid4().hex[:8].upper()}"
        start_time = time.time()

        # 设置当前节点为 WAIT 节点的下一个节点
        next_node = self._route_next(workflow, current_node, ctx)
        if not next_node:
            return ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.COMPLETED,
                context=ctx,
                outputs={"message": "No more steps after wait"},
            )

        current_node = next_node

        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed > self.global_timeout:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.TIMEOUT,
                        context=ctx,
                        error=f"Global timeout after {self.global_timeout}s",
                    )

                ctx.current_node_id = current_node.node_id
                node_result = await self._execute_node(current_node, ctx)
                ctx.set_node_result(current_node.node_id, node_result)

                if node_result.get("status") == "waiting" and current_node.node_type == NodeType.WAIT:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.WAITING,
                        context=ctx,
                        outputs=node_result,
                    )

                if current_node.node_type == NodeType.END:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.COMPLETED,
                        context=ctx,
                        outputs=node_result,
                    )

                next_node = self._route_next(workflow, current_node, ctx)
                if next_node is None:
                    return ExecutionResult(
                        execution_id=execution_id,
                        status=ExecutionStatus.FAILED,
                        context=ctx,
                        error=f"No outgoing edge matched from node {current_node.node_id}",
                    )
                current_node = next_node

        except Exception as e:
            logger.exception(f"[WorkflowEngine] Resume failed: {e}")
            return ExecutionResult(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                context=ctx,
                error=str(e),
            )
