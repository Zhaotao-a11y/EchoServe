# -*- coding: utf-8 -*-
"""
EchoServe Phase 2.5 — AI 工单调查 API 路由

端点:
    POST   /api/tickets/auto-create     AI 自动创建工单+调查
    POST   /api/tickets/{id}/investigate 对已有工单执行AI调查
    GET    /api/tickets/assigner/workload 坐席负载情况
    POST   /api/tickets/assigner/auto-assign 批量自动分配
    POST   /api/tickets/assigner/agents   注册坐席
    GET    /api/tickets/assigner/agents   获取坐席列表
    GET    /api/tickets/tools/stats       工具调用统计
    POST   /api/tickets/tools/execute     手动执行工具
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_token, require_permission

logger = logging.getLogger("echoserve.api.ticket_ai")

router = APIRouter()


# ─── 请求模型 ─────────────────────────────────────────

class AutoCreateRequest(BaseModel):
    user_message: str = Field(..., min_length=1, max_length=5000)
    session_id: str = ""
    customer_id: str = ""
    customer_name: str = ""
    channel: str = "web"


class InvestigateRequest(BaseModel):
    user_message: str = Field(..., min_length=1, max_length=5000)
    session_id: str = ""


class RegisterAgentRequest(BaseModel):
    agent_id: str
    name: str = ""
    skills: list[str] = Field(default_factory=list)
    level: str = "standard"  # junior/standard/senior/expert
    max_concurrent: int = 10


class ExecuteToolRequest(BaseModel):
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False  # 用于 requires_confirmation=True 的工具二次确认


# ─── 辅助函数 ─────────────────────────────────────────

def get_investigator():
    """获取 AI 工单调查管理器"""
    from api.deps import get_context
    ctx = get_context()
    manager = ctx.inject("ai_investigator", None)
    if not manager:
        raise HTTPException(status_code=503, detail="AI investigator not available")
    return manager


def get_assigner():
    """获取智能分配器"""
    from api.deps import get_context
    ctx = get_context()
    assigner = ctx.inject("smart_assigner", None)
    if not assigner:
        raise HTTPException(status_code=503, detail="Smart assigner not available")
    return assigner


def get_tool_registry():
    """获取工具注册中心"""
    from api.deps import get_context
    ctx = get_context()
    registry = ctx.inject("tool_registry", None)
    if not registry:
        raise HTTPException(status_code=503, detail="Tool registry not available")
    return registry


# ─── AI 工单调查端点 ───────────────────────────────────

@router.post("/tickets/auto-create")
async def auto_create_ticket(
    request: AutoCreateRequest,
    user_id: str = Depends(verify_token),
):
    """
    AI 自动创建工单并执行根因调查。

    流程: 意图分类 → 创建工单 → 根因调查 → RCA 报告
    """
    manager = get_investigator()
    try:
        result = await manager.auto_create_and_investigate(
            user_message=request.user_message,
            session_id=request.session_id,
            customer_id=request.customer_id,
            customer_name=request.customer_name,
            channel=request.channel,
        )
        return {
            "status": "created" if result.get("ticket") else "failed",
            "ticket": result.get("ticket"),
            "classification": result.get("classification"),
            "investigation": {
                "ticket_id": result.get("investigation", {}).get("ticket_id", ""),
                "kb_matched": result.get("investigation", {}).get("kb_matched", False),
                "history_matched": result.get("investigation", {}).get("history_matched", False),
                "rca_report": result.get("investigation", {}).get("rca_report", ""),
            },
        }
    except Exception as e:
        logger.error(f"[API] Auto-create ticket error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tickets/{ticket_id}/investigate")
async def investigate_ticket(
    ticket_id: str,
    request: InvestigateRequest,
    user_id: str = Depends(verify_token),
):
    """对已有工单执行 AI 根因调查"""
    manager = get_investigator()
    try:
        classification = manager.classifier.classify(request.user_message)
        investigation = await manager.investigator.investigate(
            ticket_id=ticket_id,
            user_message=request.user_message,
            classification=classification,
            session_id=request.session_id,
        )
        return {
            "status": "investigated",
            "ticket_id": ticket_id,
            "classification": classification,
            "kb_matched": investigation.get("kb_matched", False),
            "history_matched": investigation.get("history_matched", False),
            "rca_report": investigation.get("rca_report", ""),
            "investigation_steps": investigation.get("investigation_steps", []),
        }
    except Exception as e:
        logger.error(f"[API] Investigate ticket error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── 智能分配端点 ───────────────────────────────────────

@router.get("/tickets/assigner/workload")
async def get_workload(
    user_id: str = Depends(verify_token),
):
    """获取所有坐席负载情况"""
    assigner = get_assigner()
    return {"agents": assigner.get_agent_workload()}


@router.post("/tickets/assigner/auto-assign")
async def auto_assign(
    user_id: str = Depends(verify_token),
):
    """批量自动分配所有待分配工单"""
    assigner = get_assigner()
    results = assigner.auto_assign_pending()
    return {
        "status": "completed",
        "assigned_count": len(results),
        "assignments": results,
    }


@router.post("/tickets/assigner/agents")
async def register_agent(
    request: RegisterAgentRequest,
    user_id: str = Depends(verify_token),
):
    """注册坐席"""
    assigner = get_assigner()
    assigner.register_agent(
        agent_id=request.agent_id,
        name=request.name,
        skills=request.skills,
        level=request.level,
        max_concurrent=request.max_concurrent,
    )
    return {"status": "registered", "agent_id": request.agent_id}


@router.get("/tickets/assigner/agents")
async def list_agents(
    user_id: str = Depends(verify_token),
):
    """获取坐席列表"""
    assigner = get_assigner()
    return {"agents": assigner.get_agent_workload()}


# ─── 工具调用端点 ───────────────────────────────────────

@router.get("/tickets/tools/stats")
async def get_tool_stats(
    user_id: str = Depends(verify_token),
):
    """获取工具调用统计"""
    registry = get_tool_registry()
    return registry.get_stats()


@router.post("/tickets/tools/execute")
async def execute_tool(
    request: ExecuteToolRequest,
    user_id: str = Depends(verify_token),
):
    """手动执行工具调用"""
    registry = get_tool_registry()
    # Phase 2.6 Fix: 检查工具是否需要用户确认
    tool_def = registry.get_tool(request.tool_name)
    if tool_def and getattr(tool_def, "requires_confirmation", False):
        if not request.confirmed:
            raise HTTPException(
                status_code=400,
                detail=f"Tool '{request.tool_name}' requires confirmation. Set 'confirmed': true to proceed."
            )

    result = await registry.execute(request.tool_name, request.params)
    return result.to_dict()
