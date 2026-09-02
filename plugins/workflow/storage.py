# -*- coding: utf-8 -*-
"""
Workflow Engine — 工作流定义持久化存储

使用 SQLite（与 Ticket 插件同构），支持：
    - 工作流 CRUD（含版本管理）
    - 执行历史记录
    - 预置模板导入
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ExecutionResult, WorkflowDefinition, WorkflowStatus

logger = logging.getLogger("echoserve.workflow.storage")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStorage:
    """工作流 SQLite 存储"""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self):
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id   TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                description   TEXT DEFAULT '',
                nodes         TEXT NOT NULL,        -- JSON
                edges         TEXT NOT NULL,        -- JSON
                variables     TEXT DEFAULT '{}',     -- JSON
                status        TEXT DEFAULT 'draft',  -- active/draft/archived/disabled
                version       INTEGER DEFAULT 1,
                created_by    TEXT DEFAULT 'system',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                is_template   INTEGER DEFAULT 0      -- 1 = 预置模板，不可删除
            );

            CREATE TABLE IF NOT EXISTS workflow_executions (
                execution_id   TEXT PRIMARY KEY,
                workflow_id    TEXT NOT NULL,
                session_id     TEXT NOT NULL,
                status         TEXT NOT NULL,        -- running/completed/failed/timeout/waiting
                context        TEXT NOT NULL,        -- JSON (ExecutionContext)
                outputs        TEXT DEFAULT '{}',     -- JSON
                error          TEXT DEFAULT '',
                started_at     TEXT NOT NULL,
                completed_at   TEXT,
                FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_wf_status    ON workflows(status);
            CREATE INDEX IF NOT EXISTS idx_wf_template  ON workflows(is_template);
            CREATE INDEX IF NOT EXISTS idx_exec_wfid    ON workflow_executions(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_exec_session ON workflow_executions(session_id);
            CREATE INDEX IF NOT EXISTS idx_exec_status  ON workflow_executions(status);
        """)
        self._conn.commit()

    # ─── Workflow CRUD ─────────────────────────────────

    def save(self, wf: WorkflowDefinition) -> WorkflowDefinition:
        """保存或更新工作流"""
        now = _now_iso()
        self._conn.execute(
            """INSERT INTO workflows
               (workflow_id, name, description, nodes, edges, variables,
                status, version, created_by, created_at, updated_at, is_template)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(workflow_id) DO UPDATE SET
               name=excluded.name, description=excluded.description,
               nodes=excluded.nodes, edges=excluded.edges,
               variables=excluded.variables, status=excluded.status,
               version=excluded.version, updated_at=excluded.updated_at""",
            (
                wf.workflow_id, wf.name, wf.description,
                json.dumps([n.to_dict() for n in wf.nodes]),
                json.dumps([e.to_dict() for e in wf.edges]),
                json.dumps(wf.variables),
                wf.status.value, wf.version, wf.created_by,
                wf.created_at, now, 0,
            ),
        )
        self._conn.commit()
        wf.updated_at = now
        return wf

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        row = self._conn.execute(
            "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_workflow(row)

    def list(
        self,
        status: WorkflowStatus | None = None,
        is_template: bool | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[WorkflowDefinition], int]:
        where = ["1=1"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status.value)
        if is_template is not None:
            where.append("is_template = ?")
            params.append(1 if is_template else 0)
        if keyword:
            where.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        sql_where = " AND ".join(where)
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM workflows WHERE {sql_where}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""SELECT * FROM workflows WHERE {sql_where}
                ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()

        return [self._row_to_workflow(r) for r in rows], total

    def delete(self, workflow_id: str) -> bool:
        """删除工作流（模板不可删）"""
        row = self._conn.execute(
            "SELECT is_template FROM workflows WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
        if not row:
            return False
        if row["is_template"] == 1:
            logger.warning(f"[WorkflowStorage] Cannot delete template {workflow_id}")
            return False

        self._conn.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
        self._conn.commit()
        return True

    # ─── 执行历史 ──────────────────────────────────────

    def save_execution(self, result: ExecutionResult) -> None:
        self._conn.execute(
            """INSERT INTO workflow_executions
               (execution_id, workflow_id, session_id, status,
                context, outputs, error, started_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(execution_id) DO UPDATE SET
               status=excluded.status, outputs=excluded.outputs,
               error=excluded.error, completed_at=excluded.completed_at""",
            (
                result.execution_id,
                result.context.workflow_id,
                result.context.session_id,
                result.status.value,
                json.dumps(result.context.to_dict()),
                json.dumps(result.outputs),
                result.error,
                result.context.started_at.isoformat(),
                result.completed_at.isoformat() if result.completed_at else None,
            ),
        )
        self._conn.commit()

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM workflow_executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # ─── 模板导入 ──────────────────────────────────────

    def import_templates(self, templates: list[WorkflowDefinition]) -> int:
        """导入预置模板（is_template=1）"""
        count = 0
        for wf in templates:
            wf.status = WorkflowStatus.ACTIVE
            now = _now_iso()
            self._conn.execute(
                """INSERT OR IGNORE INTO workflows
                   (workflow_id, name, description, nodes, edges, variables,
                    status, version, created_by, created_at, updated_at, is_template)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    wf.workflow_id, wf.name, wf.description,
                    json.dumps([n.to_dict() for n in wf.nodes]),
                    json.dumps([e.to_dict() for e in wf.edges]),
                    json.dumps(wf.variables),
                    wf.status.value, 1, "system", now, now, 1,
                ),
            )
            count += self._conn.total_changes
        self._conn.commit()
        logger.info(f"[WorkflowStorage] Imported {count} templates")
        return count

    # ─── helpers ───────────────────────────────────────

    def _row_to_workflow(self, row: sqlite3.Row) -> WorkflowDefinition:
        return WorkflowDefinition.from_dict({
            "workflow_id": row["workflow_id"],
            "name": row["name"],
            "description": row["description"],
            "nodes": json.loads(row["nodes"]),
            "edges": json.loads(row["edges"]),
            "variables": json.loads(row["variables"]),
            "status": row["status"],
            "version": row["version"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })


# ─── 预置模板定义 ────────────────────────────────────

def get_preset_templates() -> list[WorkflowDefinition]:
    """预置 5 个常用工作流模板"""
    templates: list[WorkflowDefinition] = []

    # 模板 1: FAQ 自动问答
    templates.append(WorkflowDefinition(
        workflow_id="TEMPLATE-FAQ",
        name="FAQ 自动问答",
        description="常见问题的自动知识库检索与回复",
        nodes=[
            WorkflowNode("trigger_1", NodeType.TRIGGER, {"trigger_type": "message"}),
            WorkflowNode("rag_1", NodeType.RAG, {"knowledge_base": "faq", "top_k": 3}),
            WorkflowNode("ai_1", NodeType.AI, {
                "prompt": "基于以下知识库内容回答用户问题。\n\n知识库：{{rag_results}}\n\n用户问题：{{user_message}}\n\n请给出准确、简洁的回答，并标注信息来源。",
                "model": "default",
                "temperature": 0.3,
            }),
            WorkflowNode("end_1", NodeType.END),
        ],
        edges=[
            WorkflowEdge("e1", "trigger_1", "rag_1"),
            WorkflowEdge("e2", "rag_1", "ai_1"),
            WorkflowEdge("e3", "ai_1", "end_1"),
        ],
        status=WorkflowStatus.ACTIVE,
    ))

    # 模板 2: 退款处理流程
    templates.append(WorkflowDefinition(
        workflow_id="TEMPLATE-REFUND",
        name="退款处理流程",
        description="用户退款请求的智能处理与工单创建",
        nodes=[
            WorkflowNode("trigger_1", NodeType.TRIGGER, {"trigger_type": "intent", "trigger_value": "refund"}),
            WorkflowNode("check_1", NodeType.CONDITION, {
                "condition_type": "expression",
                "expression": "{{order_id}} != ''",
            }),
            WorkflowNode("ai_1", NodeType.AI, {
                "prompt": "用户想要退款。请询问订单号（如果还没有提供的话），并说明退款政策。用户消息：{{user_message}}",
            }),
            WorkflowNode("ticket_1", NodeType.TICKET, {
                "title": "退款申请 - 订单 {{order_id}}",
                "description": "用户申请退款。对话摘要：{{user_message}}",
                "priority": "medium",
                "category": "refund",
            }),
            WorkflowNode("ai_2", NodeType.AI, {
                "prompt": "已为用户创建退款工单，工单号：{{ticket_1.ticket_id}}。请告知用户预计处理时间（1-3个工作日）。",
            }),
            WorkflowNode("handoff_1", NodeType.HANDOFF, {
                "queue": "refund",
                "priority": "normal",
                "reason": "退款需人工复核",
            }),
            WorkflowNode("end_1", NodeType.END),
        ],
        edges=[
            WorkflowEdge("e1", "trigger_1", "check_1"),
            WorkflowEdge("e2", "check_1", "ai_1", condition="", label="No", is_default=False),
            WorkflowEdge("e3", "check_1", "ticket_1", condition="", label="Yes", is_default=False),
            WorkflowEdge("e4", "ai_1", "end_1"),
            WorkflowEdge("e5", "ticket_1", "ai_2"),
            WorkflowEdge("e6", "ai_2", "handoff_1"),
            WorkflowEdge("e7", "handoff_1", "end_1"),
        ],
        status=WorkflowStatus.ACTIVE,
    ))

    # 模板 3: 技术支持分诊
    templates.append(WorkflowDefinition(
        workflow_id="TEMPLATE-TECH-SUPPORT",
        name="技术支持智能分诊",
        description="根据用户问题类型自动分配给对应技术团队",
        nodes=[
            WorkflowNode("trigger_1", NodeType.TRIGGER, {"trigger_type": "intent", "trigger_value": "technical_issue"}),
            WorkflowNode("rag_1", NodeType.RAG, {"knowledge_base": "tech_docs", "top_k": 3}),
            WorkflowNode("check_1", NodeType.CONDITION, {
                "condition_type": "expression",
                "expression": "{{rag_1.count}} > 0",
            }),
            WorkflowNode("ai_1", NodeType.AI, {
                "prompt": "根据知识库内容回答技术问题。\n知识库：{{rag_results}}\n用户问题：{{user_message}}",
            }),
            WorkflowNode("ai_2", NodeType.AI, {
                "prompt": "用户的技术问题较复杂，需要人工介入。请生成简要摘要，包含：1. 问题描述 2. 已尝试的解决方法 3. 用户环境信息",
            }),
            WorkflowNode("handoff_1", NodeType.HANDOFF, {
                "queue": "technical_support",
                "priority": "high",
                "reason": "技术问题未在知识库中找到答案",
            }),
            WorkflowNode("end_1", NodeType.END),
            WorkflowNode("end_2", NodeType.END),
        ],
        edges=[
            WorkflowEdge("e1", "trigger_1", "rag_1"),
            WorkflowEdge("e2", "rag_1", "check_1"),
            WorkflowEdge("e3", "check_1", "ai_1", condition="", label="Yes"),
            WorkflowEdge("e4", "check_1", "ai_2", condition="", label="No"),
            WorkflowEdge("e5", "ai_1", "end_1"),
            WorkflowEdge("e6", "ai_2", "handoff_1"),
            WorkflowEdge("e7", "handoff_1", "end_2"),
        ],
        status=WorkflowStatus.ACTIVE,
    ))

    # 模板 4: 投诉升级处理
    templates.append(WorkflowDefinition(
        workflow_id="TEMPLATE-COMPLAINT",
        name="投诉升级处理",
        description="检测负面情绪并自动升级处理",
        nodes=[
            WorkflowNode("trigger_1", NodeType.TRIGGER, {"trigger_type": "message"}),
            WorkflowNode("sentiment_1", NodeType.CONDITION, {
                "condition_type": "sentiment",
                "threshold": -0.5,
            }),
            WorkflowNode("ai_1", NodeType.AI, {
                "prompt": "用户情绪激动（负面情绪检测）。请先表达理解和歉意，然后询问具体问题。用户消息：{{user_message}}",
            }),
            WorkflowNode("ticket_1", NodeType.TICKET, {
                "title": "紧急投诉处理",
                "description": "用户投诉，情绪负面。消息：{{user_message}}",
                "priority": "urgent",
                "category": "complaint",
            }),
            WorkflowNode("handoff_1", NodeType.HANDOFF, {
                "queue": "complaint",
                "priority": "urgent",
                "reason": "用户投诉，情绪负面，需人工安抚",
            }),
            WorkflowNode("normal_ai", NodeType.AI, {
                "prompt": "正常回复用户问题：{{user_message}}",
            }),
            WorkflowNode("end_1", NodeType.END),
            WorkflowNode("end_2", NodeType.END),
        ],
        edges=[
            WorkflowEdge("e1", "trigger_1", "sentiment_1"),
            WorkflowEdge("e2", "sentiment_1", "ai_1", condition="", label="Yes"),
            WorkflowEdge("e3", "ai_1", "ticket_1"),
            WorkflowEdge("e4", "ticket_1", "handoff_1"),
            WorkflowEdge("e5", "handoff_1", "end_1"),
            WorkflowEdge("e6", "sentiment_1", "normal_ai", condition="", label="No", is_default=True),
            WorkflowEdge("e7", "normal_ai", "end_2"),
        ],
        status=WorkflowStatus.ACTIVE,
    ))

    # 模板 5: 产品推荐
    templates.append(WorkflowDefinition(
        workflow_id="TEMPLATE-PRODUCT-REC",
        name="智能产品推荐",
        description="根据用户需求推荐相关产品",
        nodes=[
            WorkflowNode("trigger_1", NodeType.TRIGGER, {"trigger_type": "intent", "trigger_value": "product_inquiry"}),
            WorkflowNode("http_1", NodeType.HTTP, {
                "method": "POST",
                "url": "{{api_base}}/products/search",
                "body": '{"query": "{{user_message}}"}',
            }),
            WorkflowNode("check_1", NodeType.CONDITION, {
                "condition_type": "expression",
                "expression": "{{http_1.status_code}} == 200",
            }),
            WorkflowNode("ai_1", NodeType.AI, {
                "prompt": "根据产品搜索结果向用户推荐合适的产品。\n搜索结果：{{http_1.body}}\n\n用户需求：{{user_message}}",
            }),
            WorkflowNode("ai_2", NodeType.AI, {
                "prompt": "产品搜索服务暂时不可用，请询问用户更多需求细节，稍后由人工回复。",
            }),
            WorkflowNode("end_1", NodeType.END),
            WorkflowNode("end_2", NodeType.END),
        ],
        edges=[
            WorkflowEdge("e1", "trigger_1", "http_1"),
            WorkflowEdge("e2", "http_1", "check_1"),
            WorkflowEdge("e3", "check_1", "ai_1", condition="", label="Yes"),
            WorkflowEdge("e4", "check_1", "ai_2", condition="", label="No"),
            WorkflowEdge("e5", "ai_1", "end_1"),
            WorkflowEdge("e6", "ai_2", "end_2"),
        ],
        status=WorkflowStatus.ACTIVE,
    ))

    return templates


# 修复循环引用
from .models import WorkflowNode, WorkflowEdge, NodeType
