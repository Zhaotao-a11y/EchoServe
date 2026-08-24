"""
EchoServe V0.1.0 — FastAPI 依赖注入

为路由层提供 Context、ChatManager、LLM 等核心对象的快捷访问。
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import Depends, HTTPException, Header
import time

# 不再 from api.main import ctx —— 避免循环导入导致 ctx 永久为 None
# 改为运行时动态访问 api.main 模块属性
import api.main as _main
from core.context import BaizeContext
from plugins.chat.plugin import ChatPlugin
from plugins.llm.plugin import LLMPlugin

logger = logging.getLogger("echoseve.api.deps")


def _get_ctx() -> Optional[BaizeContext]:
    """运行时获取 api.main 中最新的 ctx 引用"""
    return _main.ctx


def get_context() -> BaizeContext:
    """获取全局 BaizeContext"""
    ctx = _get_ctx()
    if ctx is None:
        raise HTTPException(status_code=503, detail="System not initialized")
    return ctx


def get_chat_manager() -> ChatPlugin:
    """获取对话管理器"""
    ctx = _get_ctx()
    if ctx is None or not ctx.has("chat_manager"):
        raise HTTPException(status_code=503, detail="Chat service not ready")
    return ctx.inject("chat_manager")


def get_llm() -> LLMPlugin:
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

# ─── 认证（JWT 验证，回退到内存 token）──────────────

def verify_token(authorization: Optional[str] = Header(None)) -> str:
    """
    验证 Bearer Token（JWT 优先，回退兼容简易 token）。
    返回 user_id。
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")

    token = authorization[7:]

    # 优先用 Auth 插件验证 JWT
    ctx = _get_ctx()
    if ctx and ctx.has("auth_service"):
        auth = ctx.inject("auth_service")
        try:
            payload = auth.verify_token(token)
            return payload.get("sub", payload.get("user_id", "unknown"))
        except Exception as e:
            logger.debug(f"JWT verification failed, falling back to memory token: {e}")

    # 回退：内存简易 token
    token_data = _token_store.get(token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if time.time() > token_data["expire_at"]:
        _token_store.pop(token, None)
        raise HTTPException(status_code=401, detail="Token expired")

    return token_data["user_id"]


def create_access_token(user_id: str, expire_minutes: int = 60) -> str:
    """创建访问令牌（优先 JWT，回退简易 token）"""
    ctx = _get_ctx()
    if ctx and ctx.has("auth_service"):
        auth = ctx.inject("auth_service")
        # 构造一个最小 payload 让插件签发
        fake_user = {"user_id": user_id, "username": user_id, "role": "api"}
        try:
            return auth._issue_jwt(fake_user)
        except Exception as e:
            logger.warning(f"JWT issuance failed, falling back to memory token: {e}")

    # 回退
    import uuid
    token = str(uuid.uuid4())
    _token_store[token] = {
        "user_id": user_id,
        "expire_at": time.time() + expire_minutes * 60,
    }
    return token


# ─── 简易内存 token 存储（回退用）──────────────────────
_token_store: dict = {}


# ─── 速率限制（简易版）──────────────────────────────

_rate_limit_store: dict = {}

def rate_limit(
    user_id: str = Depends(verify_token),
    max_requests: int = 100,
    window_seconds: int = 60,
) -> str:
    """
    简易速率限制：每用户每分钟最多 N 次请求。
    """
    now = time.time()
    window_start = now - window_seconds

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
    """
    def _check(user_id: str = Depends(verify_token)) -> str:
        ctx = _get_ctx()
        if ctx and ctx.has("auth_service"):
            auth = ctx.inject("auth_service")
            user = None
            # 按 user_id 查找用户
            if hasattr(auth, "_users") and user_id in auth._users:
                user = auth._users[user_id]
            if user:
                role = user.get("role", "user")
                # super_admin 拥有所有权限
                if role == "super_admin":
                    return user_id
                # 检查角色权限映射
                role_perms = getattr(auth, "ROLE_PERMISSIONS", {})
                perms = role_perms.get(role, [])
                if permission in perms or "*" in perms:
                    return user_id
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: requires '{permission}'"
                )
        # 无 auth 服务时仅验证了 token，放行
        return user_id

    return _check