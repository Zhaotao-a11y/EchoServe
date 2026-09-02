# -*- coding: utf-8 -*-
"""
EchoServe — Workflow Engine Plugin

集成到 EchoServe 插件体系的 Workflow Engine。

职责：
    - 提供 workflow_service 给其他插件
    - 管理工作流定义（CRUD + 版本控制）
    - 执行工作流（由 chat 或外部触发）
    - 预置模板管理
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .models import (
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    NodeType,
    WorkflowStatus,
)
from .engine import WorkflowEngine, get_executor, DEFAULT_GLOBAL_TIMEOUT
from .storage import WorkflowStorage, get_preset_templates

logger = logging.getLogger("echoserve.workflow")


class WorkflowPlugin(BaizePlugin):
    """工作流引擎插件"""

    plugin_id = "core.workflow"
    plugin_name = "可视化工作流引擎"
    plugin_version = "1.0.0"
    dependencies = ["core.llm", "core.knowledge", "core.retriever", "service.ticket", "service.agent"]

    def __init__(self):
        self._storage: WorkflowStorage | None = None
        self._engine: WorkflowEngine | None = None
        self._services: dict[str, Any] = {}

    # ─── 生命周期 ──────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        data_dir = Path(ctx.settings.root_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        db_path = data_dir / "workflow.db"
        self._storage = WorkflowStorage(db_path)
        self._storage.connect()

        # 导入预置模板（首次运行时）
        templates = get_preset_templates()
        self._storage.import_templates(templates)

        # 初始化执行引擎（服务依赖稍后填充）
        self._engine = WorkflowEngine(
            services=self._services,
            global_timeout=DEFAULT_GLOBAL_TIMEOUT,
        )

        self.provide("workflow_service", self)
        self.provide("workflow_engine", self._engine)
        logger.info(f"[{self.plugin_id}] Initialized (db={db_path.name}, templates={len(templates)})")

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        # 从其他插件获取服务引用，填充 engine 的 services
        # 允许部分服务缺失（ graceful degradation ）
        self._services["llm"] = ctx.inject("llm", None)
        self._services["retriever"] = ctx.inject("retriever", None)
        self._services["ticket"] = ctx.inject("ticket_service", None)
        self._services["agent"] = ctx.inject("agent_service", None)
        self._services["sentiment_analyzer"] = ctx.inject("sentiment_analyzer", None)

        if self._engine:
            self._engine.services = self._services

        logger.info(f"[{self.plugin_id}] Started with services: {list(self._services.keys())}")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        if self._storage:
            self._storage.close()
            logger.info(f"[{self.plugin_id}] Storage closed")

    # ─── 公开 API ──────────────────────────────────────────

    def create_workflow(
        self,
        name: str,
        description: str = "",
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        variables: dict[str, Any] | None = None,
        created_by: str = "system",
    ) -> WorkflowDefinition:
        """创建新工作流（草稿状态）"""
        wf = WorkflowDefinition(
            workflow_id=f"WF-{uuid.uuid4().hex[:8].upper()}",
            name=name,
            description=description,
            nodes=[WorkflowNode.from_dict(n) for n in (nodes or [])],
            edges=[WorkflowEdge.from_dict(e) for e in (edges or [])],
            variables=variables or {},
            status=WorkflowStatus.DRAFT,
            created_by=created_by,
        )
        valid, errors = wf.validate()
        if not valid:
            raise ValueError(f"Workflow validation failed: {', '.join(errors)}")

        return self._storage.save(wf)

    def update_workflow(
        self,
        workflow_id: str,
        **kwargs,
    ) -> WorkflowDefinition | None:
        """更新工作流（自动版本+1）"""
        wf = self._storage.get(workflow_id)
        if not wf:
            return None

        if "name" in kwargs:
            wf.name = kwargs["name"]
        if "description" in kwargs:
            wf.description = kwargs["description"]
        if "nodes" in kwargs:
            wf.nodes = [WorkflowNode.from_dict(n) for n in kwargs["nodes"]]
        if "edges" in kwargs:
            wf.edges = [WorkflowEdge.from_dict(e) for e in kwargs["edges"]]
        if "variables" in kwargs:
            wf.variables = kwargs["variables"]
        if "status" in kwargs:
            wf.status = WorkflowStatus(kwargs["status"])

        wf.version += 1
        valid, errors = wf.validate()
        if not valid:
            raise ValueError(f"Workflow validation failed: {', '.join(errors)}")

        return self._storage.save(wf)

    def publish_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        """发布工作流（草稿 → 激活）"""
        wf = self._storage.get(workflow_id)
        if not wf:
            return None
        wf.status = WorkflowStatus.ACTIVE
        return self._storage.save(wf)

    def archive_workflow(self, workflow_id: str) -> bool:
        """归档工作流"""
        wf = self._storage.get(workflow_id)
        if not wf:
            return False
        wf.status = WorkflowStatus.ARCHIVED
        self._storage.save(wf)
        return True

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        return self._storage.get(workflow_id)

    def list_workflows(
        self,
        status: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[WorkflowDefinition], int]:
        wf_status = WorkflowStatus(status) if status else None
        return self._storage.list(status=wf_status, keyword=keyword, offset=offset, limit=limit)

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._storage.delete(workflow_id)

    def duplicate_workflow(self, workflow_id: str, new_name: str = "") -> WorkflowDefinition | None:
        """复制工作流（创建新版本）"""
        wf = self._storage.get(workflow_id)
        if not wf:
            return None

        new_wf = WorkflowDefinition(
            name=new_name or f"{wf.name} (副本)",
            description=wf.description,
            nodes=wf.nodes,
            edges=wf.edges,
            variables=wf.variables.copy(),
            status=WorkflowStatus.DRAFT,
        )
        return self._storage.save(new_wf)

    # ─── 执行 API ──────────────────────────────────────────

    async def execute_workflow(
        self,
        workflow_id: str,
        session_id: str,
        user_id: str = "",
        channel: str = "web",
        variables: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """同步执行工作流（完整执行直到结束或等待）"""
        wf = self._storage.get(workflow_id)
        if not wf:
            raise ValueError(f"Workflow not found: {workflow_id}")

        if wf.status != WorkflowStatus.ACTIVE:
            raise ValueError(f"Workflow {workflow_id} is not active (status={wf.status.value})")

        result = await self._engine.execute(
            workflow=wf,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            initial_variables=variables,
        )

        # 保存执行历史
        self._storage.save_execution(result)
        return result

    async def resume_workflow(
        self,
        execution_id: str,
        user_input: str,
    ) -> ExecutionResult:
        """从 WAITING 状态恢复工作流执行"""
        exec_record = self._storage.get_execution(execution_id)
        if not exec_record:
            raise ValueError(f"Execution not found: {execution_id}")

        if exec_record["status"] != ExecutionStatus.WAITING.value:
            raise ValueError(f"Execution {execution_id} is not in WAITING status")

        wf = self._storage.get(exec_record["workflow_id"])
        if not wf:
            raise ValueError(f"Workflow not found: {exec_record['workflow_id']}")

        # 重建执行上下文
        ctx_data = exec_record.get("context", "{}")
        if isinstance(ctx_data, str):
            import json
            ctx_data = json.loads(ctx_data)

        ctx = ExecutionContext(
            session_id=ctx_data.get("session_id", ""),
            user_id=ctx_data.get("user_id", ""),
            channel=ctx_data.get("channel", "web"),
            variables=ctx_data.get("variables", {}),
            workflow_id=ctx_data.get("workflow_id", ""),
        )
        ctx.node_results = ctx_data.get("node_results", {})
        ctx.execution_path = ctx_data.get("execution_path", [])
        ctx.current_node_id = ctx_data.get("current_node_id", "")

        result = await self._engine.resume(wf, ctx, user_input)
        self._storage.save_execution(result)
        return result

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        return self._storage.get_execution(execution_id)

    # ─── 快捷方法：触发匹配 ────────────────────────────────

    def find_matching_workflow(
        self,
        trigger_type: str,
        trigger_value: str = "",
        channel: str = "web",
    ) -> WorkflowDefinition | None:
        """根据触发条件查找匹配的活动工作流（取最近更新的）"""
        workflows, _ = self._storage.list(status=WorkflowStatus.ACTIVE, limit=1000)

        for wf in workflows:
            # 检查 TRIGGER 节点是否匹配
            for node in wf.nodes:
                if node.node_type == NodeType.TRIGGER:
                    config = node.config
                    if config.get("trigger_type") == trigger_type:
                        if trigger_value and config.get("trigger_value") == trigger_value:
                            return wf
                        if not trigger_value:
                            return wf
        return None
