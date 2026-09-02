# -*- coding: utf-8 -*-
"""
Workflow Engine — 数据模型层

定义工作流的 JSON Schema 与运行时数据结构：
    - WorkflowDefinition: 工作流定义（DAG）
    - WorkflowNode: 节点（类型 + 配置）
    - WorkflowEdge: 边（条件分支 + 默认分支）
    - ExecutionContext: 运行时执行上下文
    - ExecutionResult: 节点执行结果
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """支持的节点类型（与 ChatterMate 对齐并扩展）"""
    TRIGGER = "trigger"          # 触发器：关键词 / 意图 / Webhook / 定时器
    AI = "ai"                    # AI 回复：调用指定 LLM 生成回复
    CONDITION = "condition"      # 条件分支：变量比较 / AI 判断 / 情绪检测
    RAG = "rag"                  # 知识库检索
    HANDOFF = "handoff"          # 转人工
    HTTP = "http"                # 外部 API 调用
    TICKET = "ticket"            # 创建工单
    WAIT = "wait"                # 等待用户输入 / 定时器
    ASSIGN = "assign"            # 变量赋值 / 数据转换
    LOOP = "loop"                # 循环（最大 10 次防死循环）
    END = "end"                  # 结束


class WorkflowStatus(str, Enum):
    """工作流实例状态"""
    ACTIVE = "active"            # 已发布可用
    DRAFT = "draft"              # 草稿
    ARCHIVED = "archived"        # 已归档
    DISABLED = "disabled"        # 已禁用


class ExecutionStatus(str, Enum):
    """执行实例状态"""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    WAITING = "waiting"          # 等待用户输入


class WorkflowNode:
    """工作流节点"""

    def __init__(
        self,
        node_id: str,
        node_type: NodeType,
        config: dict[str, Any] | None = None,
        position: dict[str, float] | None = None,  # 前端画布坐标
        label: str = "",
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.config = config or {}
        self.position = position or {"x": 0, "y": 0}
        self.label = label or f"{node_type.value}_{node_id[:4]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "type": self.node_type.value,
            "config": self.config,
            "position": self.position,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowNode:
        return cls(
            node_id=data["id"],
            node_type=NodeType(data["type"]),
            config=data.get("config", {}),
            position=data.get("position", {}),
            label=data.get("label", ""),
        )


class WorkflowEdge:
    """工作流边（连接两个节点，可带条件）"""

    def __init__(
        self,
        edge_id: str,
        source: str,              # source node_id
        target: str,              # target node_id
        condition: str = "",      # 条件表达式（Python eval 或 LLM 判断）
        label: str = "",          # 显示标签（如 "Yes" / "No"）
        is_default: bool = False, # 默认分支（当其他条件不满足时走）
    ):
        self.edge_id = edge_id
        self.source = source
        self.target = target
        self.condition = condition
        self.label = label
        self.is_default = is_default

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "condition": self.condition,
            "label": self.label,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowEdge:
        return cls(
            edge_id=data["id"],
            source=data["source"],
            target=data["target"],
            condition=data.get("condition", ""),
            label=data.get("label", ""),
            is_default=data.get("is_default", False),
        )


class WorkflowDefinition:
    """工作流定义（DAG）"""

    def __init__(
        self,
        workflow_id: str = "",
        name: str = "",
        description: str = "",
        nodes: list[WorkflowNode] | None = None,
        edges: list[WorkflowEdge] | None = None,
        variables: dict[str, Any] | None = None,    # 全局变量默认值
        status: WorkflowStatus = WorkflowStatus.DRAFT,
        version: int = 1,
        created_by: str = "system",
    ):
        self.workflow_id = workflow_id or f"WF-{uuid.uuid4().hex[:8].upper()}"
        self.name = name
        self.description = description
        self.nodes = nodes or []
        self.edges = edges or []
        self.variables = variables or {}
        self.status = status
        self.version = version
        self.created_by = created_by
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "variables": self.variables,
            "status": self.status.value,
            "version": self.version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDefinition:
        wf = cls(
            workflow_id=data.get("workflow_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            nodes=[WorkflowNode.from_dict(n) for n in data.get("nodes", [])],
            edges=[WorkflowEdge.from_dict(e) for e in data.get("edges", [])],
            variables=data.get("variables", {}),
            status=WorkflowStatus(data.get("status", "draft")),
            version=data.get("version", 1),
            created_by=data.get("created_by", "system"),
        )
        wf.created_at = data.get("created_at", wf.created_at)
        wf.updated_at = data.get("updated_at", wf.updated_at)
        return wf

    def get_node(self, node_id: str) -> WorkflowNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_outgoing_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.source == node_id]

    def get_incoming_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.target == node_id]

    def validate(self) -> tuple[bool, list[str]]:
        """验证 DAG 结构合法性"""
        errors: list[str] = []

        # 1. 节点 ID 唯一性
        node_ids = [n.node_id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            errors.append("存在重复的节点 ID")

        # 2. 边引用的节点必须存在
        for e in self.edges:
            if e.source not in node_ids:
                errors.append(f"边 {e.edge_id} 引用了不存在的 source: {e.source}")
            if e.target not in node_ids:
                errors.append(f"边 {e.edge_id} 引用了不存在的 target: {e.target}")

        # 3. 有且仅有一个 END 节点
        end_nodes = [n for n in self.nodes if n.node_type == NodeType.END]
        if not end_nodes:
            errors.append("缺少 END 节点")

        # 4. 检查环（简单 DFS）
        adj = {n.node_id: [] for n in self.nodes}
        for e in self.edges:
            adj[e.source].append(e.target)

        visited = set()
        rec_stack = set()

        def _has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    if _has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.remove(node)
            return False

        for n in self.nodes:
            if n.node_id not in visited:
                if _has_cycle(n.node_id):
                    errors.append("工作流存在循环，必须是 DAG")
                    break

        # 5. TRIGGER 节点必须存在且作为起点（无入边）
        triggers = [n for n in self.nodes if n.node_type == NodeType.TRIGGER]
        if not triggers:
            errors.append("缺少 TRIGGER 节点")
        for t in triggers:
            if self.get_incoming_edges(t.node_id):
                errors.append(f"TRIGGER 节点 {t.node_id} 不能有入边")

        return not errors, errors


class ExecutionContext:
    """工作流运行时执行上下文（按 session 隔离）"""

    def __init__(
        self,
        session_id: str,
        user_id: str = "",
        channel: str = "web",
        variables: dict[str, Any] | None = None,
        workflow_id: str = "",
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.channel = channel
        self.variables = variables or {}
        self.workflow_id = workflow_id
        self.node_results: dict[str, Any] = {}  # node_id -> result
        self.execution_path: list[str] = []        # 已执行的节点顺序
        self.loop_counters: dict[str, int] = {}  # LOOP 节点计数器
        self.started_at = datetime.now(timezone.utc)
        self.current_node_id: str = ""

    def set_variable(self, key: str, value: Any):
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def set_node_result(self, node_id: str, result: Any):
        self.node_results[node_id] = result
        self.execution_path.append(node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "channel": self.channel,
            "variables": self.variables,
            "workflow_id": self.workflow_id,
            "node_results": {k: str(v) for k, v in self.node_results.items()},
            "execution_path": self.execution_path,
            "started_at": self.started_at.isoformat(),
            "current_node_id": self.current_node_id,
        }


class ExecutionResult:
    """单次工作流执行结果"""

    def __init__(
        self,
        execution_id: str,
        status: ExecutionStatus,
        context: ExecutionContext,
        outputs: dict[str, Any] | None = None,
        error: str = "",
    ):
        self.execution_id = execution_id
        self.status = status
        self.context = context
        self.outputs = outputs or {}
        self.error = error
        self.completed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status.value,
            "workflow_id": self.context.workflow_id,
            "session_id": self.context.session_id,
            "execution_path": self.context.execution_path,
            "outputs": self.outputs,
            "error": self.error,
            "started_at": self.context.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }
