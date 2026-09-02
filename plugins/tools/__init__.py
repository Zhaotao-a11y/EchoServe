# -*- coding: utf-8 -*-
"""
EchoServe Phase 2.6 — Function Calling / Tool Use 框架

功能:
  1. ToolRegistry: 工具注册中心，管理工具定义和执行
  2. ToolDefinition: 工具定义（名称、描述、参数 Schema、处理器）
  3. ToolExecutor: 工具执行器，含参数校验和超时控制
  4. LLM Tool Adapter: 将工具定义转换为 OpenAI-compatible tools 格式
  5. 预置工具: query_order, check_inventory, create_return_request

适配策略:
  - OpenAI / Claude / Qwen / DeepSeek: 原生 Function Calling
  - Ollama / 本地模型: prompt engineering 模拟 Tool Use
"""
from __future__ import annotations

import json
import logging
import asyncio
import inspect
import time
import re
from typing import Any, Callable, Awaitable

logger = logging.getLogger("echoserve.tools")

# ─── 工具定义 ──────────────────────────────────────────

class ToolDefinition:
    """
    工具定义。

    Attributes:
        name: 工具名称 (唯一标识)
        description: 工具描述 (供 LLM 理解用途)
        input_schema: JSON Schema 格式的参数定义
        handler: 异步处理函数 (params: dict) -> dict
        category: 工具分类 (order/inventory/return/system...)
        requires_confirmation: 是否需要用户确认 (如创建退货)
        timeout_sec: 执行超时时间
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | Callable[[dict[str, Any]], dict[str, Any]],
        category: str = "general",
        requires_confirmation: bool = False,
        timeout_sec: float = 10.0,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.category = category
        self.requires_confirmation = requires_confirmation
        self.timeout_sec = timeout_sec

    def to_openai_format(self) -> dict[str, Any]:
        """
        转换为 OpenAI Function Calling 格式。

        Returns:
            {
                "type": "function",
                "function": {
                    "name": str,
                    "description": str,
                    "parameters": dict,  # JSON Schema
                }
            }
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_prompt_description(self) -> str:
        """
        转换为 prompt 描述格式（用于不支持 Function Calling 的本地模型）。

        Returns:
            工具描述字符串
        """
        params_desc = json.dumps(self.input_schema, ensure_ascii=False, indent=2)
        return (
            f"工具名: {self.name}\n"
            f"描述: {self.description}\n"
            f"参数:\n{params_desc}"
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """
        校验参数是否符合 JSON Schema (简化版)。

        Returns:
            错误信息列表，空列表表示校验通过
        """
        errors: list[str] = []
        schema = self.input_schema
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # 检查必填参数
        for req in required:
            if req not in params:
                errors.append(f"缺少必填参数: {req}")

        # 检查参数类型 (简化版)
        for key, value in params.items():
            if key not in properties:
                errors.append(f"未知参数: {key}")
                continue

            expected_type = properties[key].get("type", "string")
            type_map = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            expected_py = type_map.get(expected_type)
            if expected_py and not isinstance(value, expected_py):
                errors.append(
                    f"参数 {key} 类型错误: 期望 {expected_type}, 实际 {type(value).__name__}"
                )

        return errors


# ─── 工具执行结果 ──────────────────────────────────────

class ToolResult:
    """工具执行结果"""

    def __init__(
        self,
        tool_name: str,
        success: bool,
        data: dict[str, Any] | None = None,
        error: str = "",
        execution_time_ms: int = 0,
    ):
        self.tool_name = tool_name
        self.success = success
        self.data = data or {}
        self.error = error
        self.execution_time_ms = execution_time_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }

    def to_llm_message(self) -> str:
        """转换为 LLM 可读的消息格式"""
        if self.success:
            return json.dumps(self.data, ensure_ascii=False)
        return f"Error: {self.error}"


# ─── 工具注册中心 ──────────────────────────────────────

class ToolRegistry:
    """
    工具注册中心。

    管理所有可用工具，提供:
    - 注册/注销工具
    - 获取工具定义 (OpenAI 格式)
    - 执行工具调用
    - 获取所有工具的 prompt 描述 (用于本地模型)
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._enabled: dict[str, bool] = {}
        self._call_count: dict[str, int] = {}
        self._error_count: dict[str, int] = {}

    def register(self, tool: ToolDefinition, enabled: bool = True):
        """注册工具"""
        self._tools[tool.name] = tool
        self._enabled[tool.name] = enabled
        self._call_count[tool.name] = 0
        self._error_count[tool.name] = 0
        logger.info(f"[ToolRegistry] Tool registered: {tool.name} ({tool.category})")

    def unregister(self, name: str):
        """注销工具"""
        self._tools.pop(name, None)
        self._enabled.pop(name, None)
        self._call_count.pop(name, None)
        self._error_count.pop(name, None)

    def enable(self, name: str):
        if name in self._enabled:
            self._enabled[name] = True

    def disable(self, name: str):
        if name in self._enabled:
            self._enabled[name] = False

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self, category: str | None = None) -> list[ToolDefinition]:
        """列出所有可用工具"""
        tools = []
        for name, tool in self._tools.items():
            if not self._enabled.get(name, False):
                continue
            if category and tool.category != category:
                continue
            tools.append(tool)
        return tools

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """获取所有工具的 OpenAI Function Calling 格式"""
        return [t.to_openai_format() for t in self.list_tools()]

    def get_prompt_description(self) -> str:
        """获取所有工具的 prompt 描述（用于不支持 FC 的模型）"""
        tools = self.list_tools()
        if not tools:
            return ""
        descriptions = [t.to_prompt_description() for t in tools]
        return "\n\n---\n\n".join(descriptions)

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        skip_validation: bool = False,
    ) -> ToolResult:
        """
        执行工具调用。

        Args:
            tool_name: 工具名称
            params: 调用参数
            skip_validation: 跳过参数校验

        Returns:
            ToolResult 执行结果
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool not found: {tool_name}",
            )

        if not self._enabled.get(tool_name, False):
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool disabled: {tool_name}",
            )

        # 参数校验
        if not skip_validation:
            errors = tool.validate_params(params)
            if errors:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Parameter validation failed: {'; '.join(errors)}",
                )

        start = time.time()
        self._call_count[tool_name] += 1

        try:
            # 执行 (支持同步和异步 handler)
            handler = tool.handler
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(
                    handler(params),
                    timeout=tool.timeout_sec,
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None, handler, params
                    ),
                    timeout=tool.timeout_sec,
                )

            elapsed_ms = int((time.time() - start) * 1000)

            return ToolResult(
                tool_name=tool_name,
                success=True,
                data=result if isinstance(result, dict) else {"result": str(result)},
                execution_time_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start) * 1000)
            self._error_count[tool_name] += 1
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool execution timed out ({tool.timeout_sec}s)",
                execution_time_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            self._error_count[tool_name] += 1
            logger.error(f"[ToolRegistry] Tool '{tool_name}' execution error: {e}")
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=str(e),
                execution_time_ms=elapsed_ms,
            )

    def get_stats(self) -> dict[str, Any]:
        """获取工具调用统计"""
        return {
            name: {
                "calls": self._call_count.get(name, 0),
                "errors": self._error_count.get(name, 0),
                "enabled": self._enabled.get(name, False),
            }
            for name in self._tools
        }


# ─── LLM 工具调用解析器 ─────────────────────────────────

class ToolCallParser:
    """
    解析 LLM 返回的工具调用意图。

    支持两种模式:
    1. 原生 Function Calling: LLM 返回 tool_calls 结构
    2. Prompt 模拟模式: 从 LLM 文本回复中解析 JSON 格式的工具调用

    Prompt 模拟格式:
        ```tool_call
        {"tool": "query_order", "params": {"order_id": "12345"}}
        ```
    """

    TOOL_CALL_PATTERN = r'```tool_call\s*(\{.*?\})\s*```'

    @staticmethod
    def parse_native(response: dict[str, Any]) -> list[dict[str, Any]]:
        """
        解析原生 Function Calling 响应。

        Args:
            response: LLM 响应 (OpenAI 格式)

        Returns:
            工具调用列表 [{"tool": str, "params": dict}]
        """
        tool_calls = []
        choices = response.get("choices", [])
        if not choices:
            return tool_calls

        message = choices[0].get("message", {})
        native_calls = message.get("tool_calls", [])

        for call in native_calls:
            func = call.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            try:
                params = json.loads(args_str) if isinstance(args_str, str) else args_str
                tool_calls.append({"tool": name, "params": params})
            except json.JSONDecodeError:
                logger.warning(f"[ToolCallParser] Failed to parse tool call args: {args_str}")

        return tool_calls

    @staticmethod
    def parse_prompt_simulated(text: str) -> list[dict[str, Any]]:
        """
        从 LLM 文本回复中解析模拟工具调用。

        Args:
            text: LLM 文本回复

        Returns:
            工具调用列表
        """
        tool_calls = []
        matches = re.findall(ToolCallParser.TOOL_CALL_PATTERN, text, re.DOTALL)

        for match in matches:
            try:
                parsed = json.loads(match)
                if "tool" in parsed:
                    tool_calls.append({
                        "tool": parsed["tool"],
                        "params": parsed.get("params", parsed.get("arguments", {})),
                    })
            except json.JSONDecodeError:
                logger.warning(f"[ToolCallParser] Failed to parse simulated tool call: {match[:100]}")

        return tool_calls

    @staticmethod
    def parse(text: str, native_response: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        统一解析工具调用 (先尝试原生, 再尝试模拟)。

        Args:
            text: LLM 文本回复
            native_response: 原生响应结构 (可选)

        Returns:
            工具调用列表
        """
        if native_response:
            native_calls = ToolCallParser.parse_native(native_response)
            if native_calls:
                return native_calls

        return ToolCallParser.parse_prompt_simulated(text)


# ─── 工具调用编排器 ─────────────────────────────────────

class ToolOrchestrator:
    """
    工具调用编排器。

    负责完整流程:
    1. 将工具列表注入 LLM prompt (或 tools 参数)
    2. 解析 LLM 回复中的工具调用意图
    3. 执行工具
    4. 将工具结果回传给 LLM 生成最终回复

    支持多轮工具调用 (最多 5 轮)。
    """

    MAX_TOOL_ROUNDS = 5

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._llm = None

    def set_llm(self, llm: Any):
        self._llm = llm

    async def process_with_tools(
        self,
        user_message: str,
        messages: list[dict[str, str]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        带工具调用的对话处理。

        Args:
            user_message: 用户消息
            messages: 对话历史
            context: 额外上下文

        Returns:
            {
                "reply": str,             # 最终回复
                "tool_calls": list[dict],  # 工具调用记录
                "tool_results": list[dict], # 工具执行结果
                "rounds": int,            # 调用轮数
            }
        """
        tool_call_log: list[dict[str, Any]] = []
        tool_results_log: list[dict[str, Any]] = []
        rounds = 0

        current_messages = list(messages) + [{"role": "user", "content": user_message}]

        while rounds < self.MAX_TOOL_ROUNDS:
            rounds += 1

            # 调用 LLM
            if not self._llm:
                return {
                    "reply": "[Error: LLM not available]",
                    "tool_calls": tool_call_log,
                    "tool_results": tool_results_log,
                    "rounds": rounds - 1,
                }

            # 构建系统提示词 (注入工具描述)
            tools = self.registry.list_tools()
            if not tools:
                # 无可用工具, 直接回复
                reply = await self._llm.chat(current_messages)
                return {
                    "reply": reply,
                    "tool_calls": [],
                    "tool_results": [],
                    "rounds": 0,
                }

            tool_desc = self.registry.get_prompt_description()
            system_prompt = self._build_tool_prompt(tool_desc)

            llm_messages = [{"role": "system", "content": system_prompt}] + current_messages

            reply = await self._llm.chat(llm_messages)

            # 解析工具调用
            tool_calls = ToolCallParser.parse(reply)

            if not tool_calls:
                # 无工具调用, 返回最终回复
                return {
                    "reply": reply,
                    "tool_calls": tool_call_log,
                    "tool_results": tool_results_log,
                    "rounds": rounds,
                }

            # 执行工具调用
            tool_results: list[ToolResult] = []
            for call in tool_calls:
                tool_name = call.get("tool", "")
                params = call.get("params", {})

                logger.info(
                    f"[ToolOrchestrator] Round {rounds}: "
                    f"executing tool '{tool_name}' with params={params}"
                )

                result = await self.registry.execute(tool_name, params)

                tool_call_log.append({
                    "round": rounds,
                    "tool": tool_name,
                    "params": params,
                })
                tool_results_log.append(result.to_dict())
                tool_results.append(result)

            # 将工具结果回传给 LLM
            tool_feedback = self._build_tool_feedback(tool_results)
            current_messages.append({"role": "assistant", "content": reply})
            current_messages.append({"role": "user", "content": tool_feedback})

        # 超过最大轮数
        logger.warning(f"[ToolOrchestrator] Max tool rounds ({self.MAX_TOOL_ROUNDS}) reached")

        # 最后一次调用 LLM 生成总结
        tool_summary = self._build_tool_feedback(tool_results)
        final_prompt = (
            "基于以上工具调用结果，请为用户生成一个简洁的最终回复。"
            "如果工具调用已解决问题，请总结结果；如果未能解决，请说明情况。"
        )
        current_messages.append({"role": "user", "content": final_prompt})
        final_reply = await self._llm.chat(current_messages)

        return {
            "reply": final_reply,
            "tool_calls": tool_call_log,
            "tool_results": tool_results_log,
            "rounds": rounds,
        }

    def _build_tool_prompt(self, tool_desc: str) -> str:
        """构建包含工具描述的系统提示词"""
        return (
            "你是一个专业的智能客服助手。你可以使用以下工具来帮助用户。\n\n"
            "使用工具时，请在回复中按以下格式调用：\n"
            "```tool_call\n"
            '{"tool": "工具名", "params": {"参数名": "参数值"}}\n'
            "```\n\n"
            "调用工具后，我会将工具执行结果返回给你，你可以基于结果继续回复用户。\n"
            "如果不需要调用工具，请直接回复用户的问题。\n\n"
            f"=== 可用工具 ===\n{tool_desc}\n=== 工具列表结束 ==="
        )

    def _build_tool_feedback(self, results: list[ToolResult]) -> str:
        """构建工具执行结果回传消息"""
        parts = ["工具执行结果:"]
        for r in results:
            parts.append(f"\n[{r.tool_name}] {'成功' if r.success else '失败'}")
            parts.append(f"结果: {r.to_llm_message()}")
            if r.execution_time_ms > 0:
                parts.append(f"(耗时 {r.execution_time_ms}ms)")
        return "\n".join(parts)


# ─── 预置工具 ──────────────────────────────────────────

async def _query_order(params: dict[str, Any]) -> dict[str, Any]:
    """查询订单状态（模拟实现）"""
    order_id = params.get("order_id", "")
    # 实际场景中这里会调用订单系统 API
    return {
        "order_id": order_id,
        "status": "shipped",
        "tracking_no": f"SF{order_id}CN",
        "estimated_delivery": "2026-09-02",
        "items": [
            {"name": "商品A", "qty": 2, "price": 99.00},
        ],
    }


async def _check_inventory(params: dict[str, Any]) -> dict[str, Any]:
    """查询商品库存（模拟实现）"""
    product_id = params.get("product_id", "")
    return {
        "product_id": product_id,
        "in_stock": True,
        "available_qty": 150,
        "warehouse": "北京仓",
        "restock_date": None,
    }


async def _create_return_request(params: dict[str, Any]) -> dict[str, Any]:
    """创建退货申请（模拟实现）"""
    order_id = params.get("order_id", "")
    reason = params.get("reason", "")
    return {
        "return_id": f"RT-{order_id}",
        "status": "pending_review",
        "reason": reason,
        "message": "退货申请已创建，等待审核",
    }


def create_default_tools() -> list[ToolDefinition]:
    """创建预置工具集"""
    return [
        ToolDefinition(
            name="query_order",
            description="查询订单状态和物流信息。当用户询问订单进度、物流状态、到货时间时使用。",
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单号，如 ORD-2026-001234",
                    },
                },
                "required": ["order_id"],
            },
            handler=_query_order,
            category="order",
        ),
        ToolDefinition(
            name="check_inventory",
            description="查询商品库存。当用户询问是否有货、库存数量、补货时间时使用。",
            input_schema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "商品ID",
                    },
                },
                "required": ["product_id"],
            },
            handler=_check_inventory,
            category="inventory",
        ),
        ToolDefinition(
            name="create_return_request",
            description="创建退货申请。当用户要求退货、退款时使用。需要确认后执行。",
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "要退货的订单号",
                    },
                    "reason": {
                        "type": "string",
                        "description": "退货原因",
                    },
                },
                "required": ["order_id", "reason"],
            },
            handler=_create_return_request,
            category="return",
            requires_confirmation=True,
        ),
    ]
