"""
EchoServe V0.3.0 - 工单系统 API 路由

端点:
    POST   /api/tickets             创建工单
    GET    /api/tickets             查询工单列表(筛选/分页)
    GET    /api/tickets/{id}        获取工单详情
    PUT    /api/tickets/{id}        更新工单(状态/优先级/分配等)
    DELETE /api/tickets/{id}        删除工单
    POST   /api/tickets/{id}/comments  添加工单评论
    GET    /api/tickets/{id}/comments 获取工单评论
    GET    /api/tickets/stats       工单统计
    GET    /api/tickets/sla         SLA 超时工单
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_context, require_permission

logger = logging.getLogger("echoserve.api.ticket")

router = APIRouter()


def get_ticket_service(ctx=Depends(get_context)):
    if not ctx.has("ticket_service"):
        raise HTTPException(status_code=503, detail="工单服务未就绪")
    return ctx.inject("ticket_service")


# ─── 请求模型 ──────────────────────────────────────────

class TicketCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    priority: str = "medium"
    category: str = "general"
    session_id: str = ""
    customer_id: str = ""
    customer_name: str = ""
    channel: str = "web"
    assigned_agent: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class TicketUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    category: str | None = None
    assigned_agent: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None


class CommentCreate(BaseModel):
    author_id: str
    author_name: str = ""
    content: str = Field(..., min_length=1)
    is_internal: bool = False


# ─── 工单 CRUD ──────────────────────────────────────────

@router.post("/tickets")
async def create_ticket(
    body: TicketCreate,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.write")),
):
    """创建工单"""
    result = ticket.create_ticket(
        title=body.title,
        description=body.description,
        priority=body.priority,
        category=body.category,
        session_id=body.session_id,
        customer_id=body.customer_id,
        customer_name=body.customer_name,
        channel=body.channel,
        assigned_agent=body.assigned_agent,
        tags=body.tags,
        metadata=body.metadata,
    )
    return result


@router.get("/tickets")
async def list_tickets(
    status: str | None = None,
    priority: str | None = None,
    assigned_agent: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    offset: int = 0,
    limit: int = 50,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.read")),
):
    """查询工单列表"""
    return ticket.list_tickets(
        status=status,
        priority=priority,
        assigned_agent=assigned_agent,
        category=category,
        keyword=keyword,
        offset=offset,
        limit=min(limit, 200),
    )


@router.get("/tickets/stats")
async def get_stats(
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.read")),
):
    """工单统计看板"""
    return ticket.get_stats()


@router.get("/tickets/sla")
async def get_sla_breached(
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.read")),
):
    """SLA 超时工单"""
    return {"items": ticket.get_sla_breached()}


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.read")),
):
    """获取工单详情"""
    result = ticket.get_ticket(ticket_id)
    if not result:
        raise HTTPException(status_code=404, detail="工单不存在")
    return result


@router.put("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    body: TicketUpdate,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.write")),
):
    """更新工单"""
    result = ticket.update_ticket(ticket_id, **body.model_dump(exclude_none=True))
    if not result:
        raise HTTPException(status_code=404, detail="工单不存在")
    return result


@router.delete("/tickets/{ticket_id}")
async def delete_ticket(
    ticket_id: str,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.write")),
):
    """删除工单"""
    if not ticket.delete_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"deleted": True, "id": ticket_id}


# ─── 工单评论 ──────────────────────────────────────────

@router.post("/tickets/{ticket_id}/comments")
async def add_comment(
    ticket_id: str,
    body: CommentCreate,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.write")),
):
    """添加工单评论"""
    if not ticket.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket.add_comment(
        ticket_id=ticket_id,
        author_id=body.author_id,
        author_name=body.author_name,
        content=body.content,
        is_internal=body.is_internal,
    )


@router.get("/tickets/{ticket_id}/comments")
async def get_comments(
    ticket_id: str,
    ticket=Depends(get_ticket_service),
    _: str = Depends(require_permission("ticket.read")),
):
    """获取工单评论"""
    if not ticket.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"items": ticket.get_comments(ticket_id)}
