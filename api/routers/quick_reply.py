"""
EchoServe V0.3.0 - 快捷回复 API 路由

端点:
    POST   /api/quick-replies             创建模板
    GET    /api/quick-replies             查询模板列表
    GET    /api/quick-replies/search       搜索模板
    GET    /api/quick-replies/{id}         获取模板详情
    PUT    /api/quick-replies/{id}         更新模板
    DELETE /api/quick-replies/{id}         删除模板
    POST   /api/quick-replies/{id}/render  渲染模板(替换变量)
    GET    /api/quick-replies/categories   分类列表
    GET    /api/quick-replies/stats        分类统计
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_context, require_permission

logger = logging.getLogger("echoserve.api.quick_reply")

router = APIRouter()


def get_qr_service(ctx=Depends(get_context)):
    if not ctx.has("quick_reply_service"):
        raise HTTPException(status_code=503, detail="快捷回复服务未就绪")
    return ctx.inject("quick_reply_service")


# ─── 请求模型 ──────────────────────────────────────────

class ReplyCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    category: str = "general"
    shortcut: str = ""
    variables: list[str] = Field(default_factory=list)
    sort_order: int = 0


class ReplyUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    category: str | None = None
    shortcut: str | None = None
    variables: list[str] | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ReplyRender(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)


# ─── 模板 CRUD ──────────────────────────────────────────

@router.post("/quick-replies")
async def create_reply(
    body: ReplyCreate,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.write")),
):
    """创建快捷回复模板"""
    return qr.create_reply(
        title=body.title,
        content=body.content,
        category=body.category,
        shortcut=body.shortcut,
        variables=body.variables,
        sort_order=body.sort_order,
    )


@router.get("/quick-replies")
async def list_replies(
    category: str | None = None,
    keyword: str | None = None,
    active_only: bool = True,
    offset: int = 0,
    limit: int = 50,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.read")),
):
    """查询模板列表"""
    return qr.list_replies(
        category=category,
        keyword=keyword,
        active_only=active_only,
        offset=offset,
        limit=min(limit, 200),
    )


@router.get("/quick-replies/search")
async def search_replies(
    keyword: str,
    limit: int = 10,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.read")),
):
    """模糊搜索模板"""
    return {"items": qr.search_replies(keyword=keyword, limit=min(limit, 50))}


@router.get("/quick-replies/categories")
async def list_categories(
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.read")),
):
    """分类列表"""
    return {"items": qr.list_categories()}


@router.get("/quick-replies/stats")
async def get_stats(
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.read")),
):
    """分类统计"""
    return {"items": qr.get_category_stats()}


@router.get("/quick-replies/{reply_id}")
async def get_reply(
    reply_id: str,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.read")),
):
    """获取模板详情"""
    result = qr.get_reply(reply_id)
    if not result:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@router.put("/quick-replies/{reply_id}")
async def update_reply(
    reply_id: str,
    body: ReplyUpdate,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.write")),
):
    """更新模板"""
    result = qr.update_reply(reply_id, **body.model_dump(exclude_none=True))
    if not result:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@router.delete("/quick-replies/{reply_id}")
async def delete_reply(
    reply_id: str,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.write")),
):
    """删除模板"""
    if not qr.delete_reply(reply_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"deleted": True, "id": reply_id}


@router.post("/quick-replies/{reply_id}/render")
async def render_reply(
    reply_id: str,
    body: ReplyRender,
    qr=Depends(get_qr_service),
    _: str = Depends(require_permission("kb.read")),
):
    """渲染模板(替换变量后返回最终文本)"""
    result = qr.render_reply(reply_id, body.variables)
    if not result:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"rendered_content": result}
