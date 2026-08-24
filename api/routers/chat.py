"""
EchoServe V0.1.0 — 对话 API 路由

端点：
    POST /api/chat          非流式对话
    POST /api/chat/stream   流式对话
    GET  /api/chat/sessions 列出会话
    DELETE /api/chat/session/{id} 清除会话
"""
from __future__ import annotations

import uuid
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_chat_manager, verify_token, rate_limit
from plugins.chat.plugin import ChatPlugin

logger = logging.getLogger("echoseve.api.chat")

router = APIRouter()


# ─── 请求/响应模型 ─────────────────────────────────────────

class ChatRequest(BaseModel):
    """对话请求"""
    session_id: Optional[str] = Field(
        default=None,
        description="会话 ID，不传则自动创建"
    )
    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    use_rag: bool = Field(default=False, description="是否启用 RAG 检索增强")


class ChatResponse(BaseModel):
    """对话响应"""
    session_id: str
    reply: str
    retrieved_docs: List[Dict[str, Any]] = []
    tokens: Dict[str, Any] = {}


class StreamChatRequest(BaseModel):
    """流式对话请求"""
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=4000)
    use_rag: bool = Field(default=True)


# ─── 端点实现 ───────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    chat_manager: ChatPlugin = Depends(get_chat_manager),
    user_id: str = Depends(rate_limit),
):
    """
    非流式对话接口。

    请求体：
    ```json
    {
        "session_id": "optional-existing-session",
        "message": "你们的退货政策是什么？",
        "use_rag": true
    }
    ```

    响应：
    ```json
    {
        "session_id": "abc-123",
        "reply": "根据我们的退货政策...",
        "retrieved_docs": [...],
        "tokens": {}
    }
    ```
    """
    # 自动生成 session_id
    session_id = request.session_id or str(uuid.uuid4())

    try:
        result = await chat_manager.chat(
            session_id=session_id,
            user_message=request.message,
            use_rag=request.use_rag,
        )
        return ChatResponse(**result)
    except Exception as e:
        logger.error(f"[API] Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(
    request: StreamChatRequest,
    chat_manager: ChatPlugin = Depends(get_chat_manager),
    user_id: str = Depends(rate_limit),
):
    """
    流式对话接口（SSE 风格，逐 token 输出）。

    使用 text/event-stream 返回：
    ```
    data: {"delta": "根据"}
    data: {"delta": "我们"}
    data: {"delta": "的"}
    ...
    data: [DONE]
    ```
    """
    from fastapi.responses import StreamingResponse
    import json

    session_id = request.session_id or str(uuid.uuid4())

    async def generate():
        try:
            async for chunk in chat_manager.chat_stream(
                session_id=session_id,
                user_message=request.message,
                use_rag=request.use_rag,
            ):
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"[API] Stream error: {e}")
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session_id,
        },
    )


@router.get("/chat/sessions")
async def list_sessions(
    chat_manager: ChatPlugin = Depends(get_chat_manager),
    user_id: str = Depends(verify_token),
):
    """列出所有活跃会话"""
    sessions = await chat_manager.list_sessions()
    return {
        "total": len(sessions),
        "sessions": sessions,
    }


@router.delete("/chat/session/{session_id}")
async def clear_session(
    session_id: str,
    chat_manager: ChatPlugin = Depends(get_chat_manager),
    user_id: str = Depends(verify_token),
):
    """清除指定会话"""
    success = await chat_manager.clear_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "cleared", "session_id": session_id}


@router.get("/chat/session/{session_id}/history")
async def get_session_history(
    session_id: str,
    chat_manager: ChatPlugin = Depends(get_chat_manager),
    user_id: str = Depends(verify_token),
):
    """获取会话历史"""
    history = await chat_manager.get_session_history(session_id)
    return {
        "session_id": session_id,
        "messages": history,
    }
