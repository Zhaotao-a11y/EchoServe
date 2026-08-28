"""
EchoServe V0.1.0 — FastAPI 依赖注入

为路由层提供 Context、ChatManager、LLM 等核心对象的快捷访问。
"""
from __future__ import annotations

import logging
from typing import Any
from fastapi import Depends, HTTPException, Header
import time

# 不再 from api.main import ctx —— 避免循环导入导致 ctx 永久为 None
# 改为运行时动态访问 api.main 模块属性
from core.context import BaizeContext
# 改为函数内懒加载，避免顶层循环导入导致 chat/llm 模块在 main.py 初始化期间被过早加载
# from plugins.chat.plugin import ChatPlugin
# from plugins.llm.plugin import LLMPlugin

logger = logging.getLogger("echoserve.api.deps")


def _get_ctx() -> BaizeContext | None:
    """运行时获取 api.main 中最新的 ctx 引用"""
    import api.main as _main_mod
    return getattr(_main_mod, "ctx", None)


def get_context() -> BaizeContext:
    """获取全局 BaizeContext"""
    ctx = _get_ctx()
    if ctx is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    return ctx


def get_chat_manager() -> Any:
    """获取对话管理器"""
    ctx = _get_ctx()
    if ctx is None or not ctx.has("chat_manager"):
        raise HTTPException(status_code=503, detail="Chat service not ready")
    return ctx.inject("chat_manager")


def get_llm() -> Any:
    """获取 LLM 网关"""
    ctx = _get_ctx()
    if ctx is None or not ctx.has("llm"):
        raise HTTPException(status_code=503, detail="LLM service not ready")
    return ctx.inject("llm")


def get_retriever():
    """获取检索引擎"""
    ctx = _get_ctx()
    if ctx is None or not ctx.has("retriever"):
        raise HTTPException(status_code=503, detail="Retriever not ready")
    return ctx.inject("retriever")


def get_knowledge_base():
    """获取知识库"""
    ctx = _get_ctx()
    if ctx is None or not ctx.has("knowledge_base"):
        raise HTTPException(status_code=503, detail="Knowledge base not ready")
    return ctx.inject("knowledge_base")

# ─── 认证（JWT 验证）──────────────────────────────────

def verify_token(authorization: str | None = Header(None)) -> str:
    """
    验证 Bearer Token（JWT）。
    返回 user_id。
    无 auth 服务时返回 503，不回退到不安全的内存 token。
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")

    token = authorization[7:]

    ctx = _get_ctx()
    if not ctx or not ctx.has("auth_service"):
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    auth = ctx.inject("auth_service")
    try:
        payload = auth.verify_token(token)
        return payload.get("sub", payload.get("user_id", "unknown"))
    except Exception as e:
        logger.debug(f"JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def create_access_token(user_id: str, expire_minutes: int = 60) -> str:
    """创建 JWT 访问令牌。无 auth 服务时抛出 503，不回退到内存 token。"""
    ctx = _get_ctx()
    if not ctx or not ctx.has("auth_service"):
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    auth = ctx.inject("auth_service")
    fake_user = {"user_id": user_id, "username": user_id, "role": "api"}
    try:
        return auth.issue_token(fake_user)
    except Exception as e:
        logger.error(f"JWT issuance failed: {e}")
        raise HTTPException(status_code=500, detail="Token issuance failed")


# ─── 速率限制（滑动窗口 + 过期清理）────────────────────

_rate_limit_store: dict[str, list[float]] = {}
_RATE_LIMIT_MAX_USERS = 10_000  # 防止内存无上限增长


def _cleanup_rate_limit_store() -> None:
    """清理过期用户记录，防止 _rate_limit_store 无限增长。"""
    if len(_rate_limit_store) <= _RATE_LIMIT_MAX_USERS:
        return
    cutoff = time.time() - 300  # 清理 5 分钟无活动的用户
    expired = [uid for uid, ts_list in _rate_limit_store.items() if not ts_list or ts_list[-1] < cutoff]
    for uid in expired:
        _rate_limit_store.pop(uid, None)
    logger.debug(f"Rate limit store cleanup: removed {len(expired)} stale entries")


def rate_limit(
    user_id: str = Depends(verify_token),
    max_requests: int = 100,
    window_seconds: int = 60,
) -> str:
    """
    滑动窗口速率限制：每用户每窗口最多 N 次请求。
    """
    now = time.time()
    window_start = now - window_seconds

    _cleanup_rate_limit_store()

    if user_id in _rate_limit_store:
        _rate_limit_store[user_id] = [
            t for t in _rate_limit_store[user_id] if t > window_start
        ]
    else:
        _rate_limit_store[user_id] = []

    if len(_rate_limit_store[user_id]) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({max_requests}/{window_seconds}s)"
        )

    _rate_limit_store[user_id].append(now)
    return user_id


# ─── 权限检查 ──────────────────────────────────────

def require_permission(permission: str):
    """
    权限检查依赖工厂。
    用法: user_id: str = Depends(require_permission("knowledge.write"))
    无 auth 服务时安全降级（拒绝），不放行。
    """
    def _check(user_id: str = Depends(verify_token)) -> str:
        ctx = _get_ctx()
        if not ctx or not ctx.has("auth_service"):
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable, permission check denied"
            )
        auth = ctx.inject("auth_service")
        if not auth.check_permission(user_id, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: requires '{permission}'"
            )
        return user_id

    return _check