"""
EchoServe V0.3.1 - 坐席管理 API 路由

端点:
    POST   /api/agents/register          注册坐席
    GET    /api/agents                    坐席列表
    GET    /api/agents/{id}               坐席详情
    PUT    /api/agents/{id}/status        更新坐席状态
    GET    /api/agents/{id}/workload      坐席工作负载
    GET    /api/agents/dashboard          坐席看板统计

    POST   /api/handoffs                  请求人机转接
    GET    /api/handoffs                  转接列表
    GET    /api/handoffs/{id}             转接详情
    POST   /api/handoffs/{id}/assign      分配坐席
    POST   /api/handoffs/{id}/complete    完成转接
    POST   /api/handoffs/{id}/cancel      取消转接
    GET    /api/handoffs/queue/status     排队状态
    POST   /api/handoffs/queue/process    处理排队队列

    POST   /api/handoffs/intelligent/analyze    智能转接决策分析
    POST   /api/handoffs/intelligent/execute    执行智能转接
    POST   /api/handoffs/intelligent/sentiment  情绪分析
    POST   /api/handoffs/intelligent/summary    对话摘要生成

    POST   /api/satisfaction              提交满意度评价
    GET    /api/satisfaction/{session_id}  查看会话评价
    GET    /api/satisfaction/stats         评价统计
"""
from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_context, require_permission, verify_token

logger = logging.getLogger("echoserve.api.agent")

router = APIRouter()


def get_agent_service(ctx=Depends(get_context)):
    if not ctx.has("agent_service"):
        raise HTTPException(status_code=503, detail="坐席服务未就绪")
    return ctx.inject("agent_service")


def get_intelligent_handoff(ctx=Depends(get_context)):
    """获取智能转接管理器"""
    manager = ctx.inject("intelligent_handoff", None)
    if not manager:
        raise HTTPException(
            status_code=503,
            detail="智能转接服务未就绪",
        )
    return manager


# ─── 请求模型 ──────────────────────────────────────────

class AgentRegister(BaseModel):
    agent_id: str
    agent_name: str
    role: str = "agent"
    max_concurrent: int = 5
    skills: list[str] = Field(default_factory=list)


class AgentStatusUpdate(BaseModel):
    status: str  # online/busy/break/offline


class HandoffRequest(BaseModel):
    session_id: str
    customer_id: str = ""
    customer_name: str = ""
    channel: str = "web"
    reason: str = ""
    priority: str = "medium"
    metadata: dict = Field(default_factory=dict)


class HandoffAssign(BaseModel):
    agent_id: str


class RatingSubmit(BaseModel):
    session_id: str
    rating: int = Field(..., ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    comment: str = ""
    agent_id: str = ""
    handoff_id: str = ""
    customer_id: str = ""
    metadata: dict = Field(default_factory=dict)


# ─── 坐席管理 ──────────────────────────────────────────

@router.post("/agents/register")
async def register_agent(
    body: AgentRegister,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """注册坐席"""
    return agent.register_agent(
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        role=body.role,
        max_concurrent=body.max_concurrent,
        skills=body.skills,
    )


@router.get("/agents")
async def list_agents(
    status: str | None = None,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """坐席列表"""
    return {"items": agent.list_agents(status=status)}


@router.get("/agents/dashboard")
async def get_dashboard(
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """坐席看板统计"""
    return agent.get_dashboard_stats()


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """坐席详情"""
    result = agent.get_agent(agent_id)
    if not result:
        raise HTTPException(status_code=404, detail="坐席不存在")
    return result


@router.put("/agents/{agent_id}/status")
async def update_agent_status(
    agent_id: str,
    body: AgentStatusUpdate,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """更新坐席状态"""
    result = agent.set_agent_status(agent_id, body.status)
    if not result:
        raise HTTPException(status_code=404, detail="坐席不存在")
    return result


@router.get("/agents/{agent_id}/workload")
async def get_workload(
    agent_id: str,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """坐席工作负载"""
    return agent.get_agent_workload(agent_id)


# ─── 人机转接 ──────────────────────────────────────────

@router.post("/handoffs")
async def request_handoff(
    body: HandoffRequest,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """请求人机转接"""
    return agent.request_handoff(
        session_id=body.session_id,
        customer_id=body.customer_id,
        customer_name=body.customer_name,
        channel=body.channel,
        reason=body.reason,
        priority=body.priority,
        metadata=body.metadata,
    )


@router.get("/handoffs")
async def list_handoffs(
    status: str | None = None,
    agent_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """转接列表"""
    return agent.list_handoffs(
        status=status,
        agent_id=agent_id,
        offset=offset,
        limit=min(limit, 200),
    )


@router.get("/handoffs/queue/status")
async def get_queue_status(
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """排队状态"""
    return agent.get_queue_status()


@router.post("/handoffs/queue/process")
async def process_queue(
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """处理排队队列(尝试分配空闲坐席)"""
    return agent.process_queue()


@router.get("/handoffs/{handoff_id}")
async def get_handoff(
    handoff_id: str,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """转接详情"""
    result = agent.get_handoff(handoff_id)
    if not result:
        raise HTTPException(status_code=404, detail="转接记录不存在")
    return result


@router.post("/handoffs/{handoff_id}/assign")
async def assign_handoff(
    handoff_id: str,
    body: HandoffAssign,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """分配坐席"""
    result = agent.assign_handoff(handoff_id, body.agent_id)
    if not result:
        raise HTTPException(status_code=404, detail="转接记录不存在或状态不允许")
    return result


@router.post("/handoffs/{handoff_id}/complete")
async def complete_handoff(
    handoff_id: str,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """完成转接"""
    result = agent.complete_handoff(handoff_id)
    if not result:
        raise HTTPException(status_code=404, detail="转接记录不存在或状态不允许")
    return result


@router.post("/handoffs/{handoff_id}/cancel")
async def cancel_handoff(
    handoff_id: str,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """取消转接"""
    result = agent.cancel_handoff(handoff_id)
    if not result:
        raise HTTPException(status_code=404, detail="转接记录不存在或状态不允许")
    return result


# ─── 满意度评价 ──────────────────────────────────────────

@router.post("/satisfaction")
async def submit_rating(
    body: RatingSubmit,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.write")),
):
    """提交满意度评价"""
    try:
        return agent.submit_rating(
            session_id=body.session_id,
            rating=body.rating,
            tags=body.tags,
            comment=body.comment,
            agent_id=body.agent_id,
            handoff_id=body.handoff_id,
            customer_id=body.customer_id,
            metadata=body.metadata,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/satisfaction/stats")
async def get_rating_stats(
    agent_id: str | None = None,
    days: int = 30,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """评价统计"""
    return agent.get_rating_stats(agent_id=agent_id, days=days)


@router.get("/satisfaction/{session_id}")
async def get_rating(
    session_id: str,
    agent=Depends(get_agent_service),
    _: str = Depends(require_permission("agent.read")),
):
    """查看会话评价"""
    result = agent.get_rating(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="该会话暂无评价")
    return result


# ─── 智能人机转接 ──────────────────────────────────────

class IntelligentAnalyzeRequest(BaseModel):
    """智能转接决策分析请求"""
    session_id: str = ""
    last_message: str = Field(..., min_length=1, max_length=5000)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    intent_confidence: float = 1.0
    explicit_request: bool = False


class IntelligentExecuteRequest(BaseModel):
    """执行智能转接请求"""
    session_id: str
    customer_id: str = ""
    customer_name: str = ""
    channel: str = "web"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    intent_confidence: float = 1.0
    last_message: str = Field(..., min_length=1, max_length=5000)
    explicit_request: bool = False
    customer_tier: str = "standard"


class SentimentAnalyzeRequest(BaseModel):
    """情绪分析请求"""
    text: str = Field(..., min_length=1, max_length=5000)


class SummaryRequest(BaseModel):
    """对话摘要请求"""
    messages: list[dict[str, Any]] = Field(..., min_length=1)
    max_length: int = 500
    include_emotion: bool = True


@router.post("/handoffs/intelligent/analyze")
async def intelligent_analyze(
    body: IntelligentAnalyzeRequest,
    manager=Depends(get_intelligent_handoff),
    user_id: str = Depends(verify_token),
):
    """
    智能转接决策分析。

    分析用户消息和对话历史，返回是否应该转人工的决策。
    不执行实际转接，仅返回决策结果供前端/调用方判断。
    """
    try:
        decision = manager.should_handoff(
            session_messages=body.messages,
            last_message=body.last_message,
            intent_confidence=body.intent_confidence,
            explicit_request=body.explicit_request,
        )
        return {
            "session_id": body.session_id,
            "decision": decision.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] Intelligent analyze error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/handoffs/intelligent/execute")
async def intelligent_execute(
    body: IntelligentExecuteRequest,
    manager=Depends(get_intelligent_handoff),
    user_id: str = Depends(verify_token),
):
    """
    执行智能转接（一站式）。

    完整流程：决策 → 对话摘要 → 智能路由 → 创建转接记录 → 分配坐席。
    """
    try:
        result = manager.create_intelligent_handoff(
            session_id=body.session_id,
            customer_id=body.customer_id,
            customer_name=body.customer_name,
            channel=body.channel,
            messages=body.messages,
            intent_confidence=body.intent_confidence,
            last_message=body.last_message,
            explicit_request=body.explicit_request,
            customer_tier=body.customer_tier,
        )
        return result
    except Exception as e:
        logger.error(f"[API] Intelligent execute error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/handoffs/intelligent/sentiment")
async def sentiment_analyze(
    body: SentimentAnalyzeRequest,
    manager=Depends(get_intelligent_handoff),
    user_id: str = Depends(verify_token),
):
    """
    情绪分析。

    分析文本情绪，返回 -1.0 ~ 1.0 的分数和标签。
    可用于前端实时展示用户情绪状态。
    """
    try:
        result = manager.sentiment_analyzer.analyze(body.text)
        return result
    except Exception as e:
        logger.error(f"[API] Sentiment analyze error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/handoffs/intelligent/summary")
async def generate_summary(
    body: SummaryRequest,
    manager=Depends(get_intelligent_handoff),
    user_id: str = Depends(verify_token),
):
    """
    生成对话摘要。

    将对话历史压缩为结构化摘要，供坐席快速了解上下文。
    """
    try:
        summary = manager.generate_summary(
            session_messages=body.messages,
            include_emotion=body.include_emotion,
        )
        # 截断到指定长度
        summary = summary[:body.max_length]
        return {
            "summary": summary,
            "message_count": len(body.messages),
        }
    except Exception as e:
        logger.error(f"[API] Summary generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
