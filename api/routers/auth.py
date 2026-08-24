"""
EchoServe V0.1.0 — 认证 API 路由

端点：
    POST   /api/auth/register     注册
    POST   /api/auth/login        登录
    POST   /api/auth/logout       退出
    GET    /api/auth/me           获取当前用户
    GET    /api/users             用户列表（管理员）
    POST   /api/users            创建用户（管理员）
    DELETE /api/users/{id}       删除用户
    PUT    /api/users/{id}/role  修改角色
    POST   /api/auth/api-key      创建 API Key
    DELETE /api/auth/api-key/{id} 吊销 API Key
    GET    /api/auth/api-keys     列出 API Keys
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_context, verify_token
from plugins.auth.plugin import AuthPlugin, ROLE_PERMISSIONS

logger = logging.getLogger("echoseve.api.auth")

router = APIRouter()


# ─── 请求模型 ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(default="user", description="角色: super_admin/admin/editor/user/readonly")
    department: str = Field(default="default", max_length=100)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(default="user")
    department: str = Field(default="default")


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., description="新角色")


class CreateApiKeyRequest(BaseModel):
    name: str = Field(default="default", max_length=50)


# ─── 辅助函数 ─────────────────────────────────────────

def get_auth_service(ctx=Depends(get_context)) -> AuthPlugin:
    if not ctx.has("auth_service"):
        raise HTTPException(status_code=503, detail="认证服务未就绪")
    return ctx.inject("auth_service")


def require_permission(permission: str):
    """权限检查依赖"""
    def _checker(
        token_data: str = Depends(verify_token),
        auth: AuthPlugin = Depends(get_auth_service),
    ):
        if not auth.check_permission(token_data, permission):
            raise HTTPException(status_code=403, detail=f"需要权限: {permission}")
        return token_data
    return _checker


# ─── 认证端点 ────────────────────────────────────────

@router.post("/auth/register")
async def register(
    request: RegisterRequest,
    auth: AuthPlugin = Depends(get_auth_service),
):
    """用户注册"""
    try:
        result = await auth.register(
            username=request.username,
            password=request.password,
            role=request.role,
            department=request.department,
        )
        return {"status": "registered", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/login")
async def login(
    request: LoginRequest,
    auth: AuthPlugin = Depends(get_auth_service),
):
    """
    用户登录。
    返回 JWT Token + 用户信息。
    """
    try:
        result = await auth.login(request.username, request.password)
        return result
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/auth/logout")
async def logout(user_id: str = Depends(verify_token)):
    """退出登录（JWT 无状态，客户端丢弃 Token 即可）"""
    return {"status": "logged_out", "message": "请客户端清除本地 Token"}


@router.get("/auth/me")
async def get_me(
    user_id: str = Depends(verify_token),
    auth: AuthPlugin = Depends(get_auth_service),
):
    """获取当前用户信息"""
    user = auth.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


# ─── 用户管理端点（需管理员权限）──────────────────────

@router.get("/users")
async def list_users(
    auth: AuthPlugin = Depends(get_auth_service),
    _: str = Depends(require_permission("user.read")),
):
    """列出所有用户"""
    return {"total": len(auth.list_users()), "users": auth.list_users()}


@router.post("/users")
async def create_user(
    request: CreateUserRequest,
    auth: AuthPlugin = Depends(get_auth_service),
    _: str = Depends(require_permission("user.write")),
):
    """创建用户（管理员操作）"""
    try:
        result = await auth.register(
            username=request.username,
            password=request.password,
            role=request.role,
            department=request.department,
        )
        return {"status": "created", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    auth: AuthPlugin = Depends(get_auth_service),
    _: str = Depends(require_permission("user.write")),
):
    """删除用户"""
    if not await auth.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "deleted", "user_id": user_id}


@router.put("/users/{user_id}/role")
async def update_role(
    user_id: str,
    request: UpdateRoleRequest,
    auth: AuthPlugin = Depends(get_auth_service),
    _: str = Depends(require_permission("user.write")),
):
    """修改用户角色"""
    try:
        if not await auth.update_user_role(user_id, request.role):
            raise HTTPException(status_code=404, detail="用户不存在")
        return {"status": "updated", "user_id": user_id, "role": request.role}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/roles")
async def list_roles():
    """列出所有可用角色"""
    return {
        "roles": [
            {"id": k, "description": v["desc"], "permissions": v["perms"]}
            for k, v in ROLE_PERMISSIONS.items()
        ]
    }


# ─── API Key 管理端点 ──────────────────────────────────

@router.post("/auth/api-key")
async def create_api_key(
    request: CreateApiKeyRequest,
    user_id: str = Depends(verify_token),
    auth: AuthPlugin = Depends(get_auth_service),
):
    """为当前用户创建 API Key"""
    result = await auth.create_api_key(user_id, request.name)
    return {"status": "created", **result}


@router.delete("/auth/api-key/{key_id}")
async def revoke_api_key(
    key_id: str,
    user_id: str = Depends(verify_token),
    auth: AuthPlugin = Depends(get_auth_service),
):
    """吊销 API Key"""
    if not await auth.revoke_api_key(key_id):
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"status": "revoked", "key_id": key_id}


@router.get("/auth/api-keys")
async def list_api_keys(
    user_id: str = Depends(verify_token),
    auth: AuthPlugin = Depends(get_auth_service),
):
    """列出当前用户的 API Keys"""
    keys = auth.list_api_keys(user_id)
    return {"total": len(keys), "api_keys": keys}
