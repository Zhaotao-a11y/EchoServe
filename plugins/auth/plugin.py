"""
EchoServe V0.1.0 — 用户认证插件

功能：
  - JWT 签发 / 验证 / 刷新
  - bcrypt 密码哈希（cost=12）
  - 登录失败限流（5次/30分钟锁定）
  - API Key 管理（创建 / 吊销 / 限流）
  - 用户注册 / 登录 / 角色管理

V0.1.7 变更：
  - 引入 UserStore 抽象层，PostgreSQL 可用时持久化到 PG（多实例共享 + 事务一致性）
  - PG 不可用时自动回退到 JSON 文件（与旧版行为一致）
"""
from __future__ import annotations

import uuid
import time
import os
import asyncio
import secrets
import logging
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber
from .user_store import (
    UserStore,
    JSONUserStore,
    PostgresUserStore,
    create_user_store,
)

logger = logging.getLogger("echoserve.auth")

# ─── 角色权限定义 ────────────────────────────────────────────

ROLE_PERMISSIONS = {
    "super_admin": {
        "desc": "超级管理员",
        "perms": ["*"],  # 全部权限
    },
    "admin": {
        "desc": "管理员",
        "perms": [
            "kb.read", "kb.write", "kb.delete",
            "user.read", "user.write",
            "model.read", "model.write",
            "audit.read",
            "system.read",
            "system.write",
        ],
    },
    "editor": {
        "desc": "编辑者",
        "perms": ["kb.read", "kb.write", "chat.read"],
    },
    "user": {
        "desc": "普通用户",
        "perms": ["kb.read", "chat.read"],
    },
    "readonly": {
        "desc": "只读用户",
        "perms": ["kb.read", "chat.read"],
    },
    "api": {
        "desc": "API用户",
        "perms": ["chat.read", "kb.read"],
    },
}


class AuthPlugin(BaizePlugin):
    """用户认证插件"""

    plugin_id = "security.auth"
    plugin_name = "认证插件"
    plugin_version = "0.1.7"
    dependencies = []

    def __init__(self):
        self._users: dict[str, dict[str, Any]] = {}
        self._api_keys: dict[str, dict[str, Any]] = {}
        self._login_attempts: dict[str, list[float]] = {}
        self._storage_path: Path | None = None
        self._api_key_path: Path | None = None
        self._jwt_secret: str | None = None
        self._token_expire_minutes: int = 480
        self._store: UserStore | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # ─── 生命周期 ──────────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        settings = ctx.settings
        self._jwt_secret = settings.security.jwt_secret
        self._token_expire_minutes = settings.security.token_expire_minutes

        data_dir = Path(settings.root_dir) / "data" / "auth"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._storage_path = data_dir / "users.json"
        self._api_key_path = data_dir / "api_keys.json"

        # V0.1.7: 优先使用 PostgreSQL，降级到 JSON 文件
        pg_cfg = getattr(settings, "postgres", None)
        if pg_cfg:
            self._store = create_user_store(
                pg_config={
                    "host": pg_cfg.host,
                    "port": pg_cfg.port,
                    "database": pg_cfg.database,
                    "user": pg_cfg.user,
                    "password": pg_cfg.password,
                },
                storage_path=self._storage_path,
                api_key_path=self._api_key_path,
            )
            # 如果是 PG 存储，尝试连接
            if isinstance(self._store, PostgresUserStore):
                connected = await self._store._connect()
                if connected:
                    logger.info(f"[{self.plugin_id}] Using PostgreSQL user store")
                else:
                    # PG 连不上，降级到 JSON
                    logger.warning(
                        f"[{self.plugin_id}] PostgreSQL unavailable, "
                        f"falling back to JSON file storage"
                    )
                    await self._store.close()
                    self._store = JSONUserStore(
                        self._storage_path, self._api_key_path
                    )
        else:
            self._store = JSONUserStore(self._storage_path, self._api_key_path)
            logger.info(f"[{self.plugin_id}] Using JSON file user store")

        await self._load_from_store()
        await self._load_api_keys_from_store()

        # 确保至少存在一个超级管理员
        if not any(u["role"] == "super_admin" for u in self._users.values()):
            admin_id = str(uuid.uuid4())
            admin_password = os.getenv("ECHOSEVE_ADMIN_PASSWORD", "")
            if not admin_password:
                admin_password = secrets.token_urlsafe(16)
                logger.warning(
                    f"[{self.plugin_id}] ECHOSEVE_ADMIN_PASSWORD not set. "
                    f"Generated random admin password: {admin_password}"
                )
            admin_user = {
                "user_id": admin_id,
                "username": "admin",
                "password_hash": bcrypt.hashpw(
                    admin_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
                ).decode("utf-8"),
                "role": "super_admin",
                "department": "system",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login": None,
                "enabled": True,
            }
            self._users[admin_id] = admin_user
            await self._save_to_store()
            logger.warning(
                f"[{self.plugin_id}] Default admin created: "
                f"username=admin (CHANGE IMMEDIATELY via /api/auth/change-password)"
            )

        # 注册服务
        self.provide("auth_service", self)

        logger.info(
            f"[{self.plugin_id}] Initialized "
            f"({len(self._users)} users, {len(self._api_keys)} API keys)"
        )

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        await self._save_to_store()
        await self._save_api_keys_to_store()
        if self._store:
            await self._store.close()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 密码管理 ──────────────────────────────────────────────

    def hash_password(self, password: str) -> str:
        """bcrypt 哈希密码"""
        return bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")

    def verify_password(self, password: str, password_hash: str) -> bool:
        """验证密码"""
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"), password_hash.encode("utf-8")
            )
        except Exception as e:
            logger.warning(f"Password verification error: {e}")
            return False

    # ─── 用户注册 / 登录 ───────────────────────────────────────

    async def register(
        self,
        username: str,
        password: str,
        role: str = "user",
        department: str = "default",
    ) -> dict[str, Any]:
        """注册新用户"""
        if len(password) < 8:
            raise ValueError("密码长度至少8位")
        if not any(c.isupper() for c in password) or \
           not any(c.islower() for c in password) or \
           not any(c.isdigit() for c in password) or \
           not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            raise ValueError("密码必须包含大小写字母、数字和特殊字符")

        # 检查重名 + 创建 + 持久化（原子操作，防止并发写入丢失）
        async with self._lock:
            for u in self._users.values():
                if u["username"] == username:
                    raise ValueError("用户名已存在")

            if role not in ROLE_PERMISSIONS:
                raise ValueError(f"无效角色: {role}")

            user_id = str(uuid.uuid4())
            user = {
                "user_id": user_id,
                "username": username,
                "password_hash": self.hash_password(password),
                "role": role,
                "department": department,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login": None,
                "enabled": True,
            }
            self._users[user_id] = user
            await self._save_to_store()

        logger.info(f"[{self.plugin_id}] User registered: {username} ({role})")
        return {"user_id": user_id, "username": username, "role": role}

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """用户登录，返回 JWT Token"""
        # 检查登录限流
        if self._is_locked_out(username):
            raise PermissionError(
                f"账户已锁定，请{self._lockout_remaining(username)}秒后重试"
            )

        # 查找用户（先查内存）
        user = None
        for u in self._users.values():
            if u["username"] == username:
                user = u
                break

        # 验证密码
        if user and self.verify_password(password, user["password_hash"]):
            pass  # 内存验证通过
        else:
            # 内存验证失败，可能是磁盘上的用户数据已更新
            # 重新加载后再次验证（支持外部修改密码无需重启）
            await self._load_from_store()
            user = None
            for u in self._users.values():
                if u["username"] == username:
                    user = u
                    break
            if not user or not self.verify_password(password, user["password_hash"]):
                self._record_failed_attempt(username)
                raise PermissionError("用户名或密码错误")

        if not user.get("enabled", True):
            raise PermissionError("账户已禁用")

        # 登录成功，清除失败记录
        self._login_attempts.pop(username, None)

        # 更新最后登录时间
        user["last_login"] = datetime.now(timezone.utc).isoformat()
        await self._save_to_store()

        # 签发 JWT
        token = self._issue_jwt(user)

        logger.info(f"[{self.plugin_id}] Login success: {username}")
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": self._token_expire_minutes * 60,
            "user_id": user["user_id"],
            "username": user["username"],
            "role": user["role"],
        }

    def _issue_jwt(self, user: dict[str, Any]) -> str:
        """签发 JWT（内部方法）"""
        if not self._jwt_secret:
            raise RuntimeError("JWT secret not configured")
        payload = {
            "sub": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=self._token_expire_minutes),
        }
        return jwt.encode(payload, self._jwt_secret, algorithm="HS256")

    def issue_token(self, user: dict[str, Any]) -> str:
        """签发 JWT 的公共接口，供外部模块调用。"""
        return self._issue_jwt(user)

    def verify_token(self, token: str) -> dict[str, Any]:
        """验证 JWT，返回 payload"""
        if not self._jwt_secret:
            raise RuntimeError("JWT secret not configured")
        try:
            payload = jwt.decode(token, self._jwt_secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            raise PermissionError("Token 已过期")
        except jwt.InvalidTokenError:
            raise PermissionError("Token 无效")

    # ─── 登录限流 ──────────────────────────────────────────────

    def _record_failed_attempt(self, username: str):
        now = time.time()
        if username not in self._login_attempts:
            self._login_attempts[username] = []
        self._login_attempts[username].append(now)
        # 只保留最近30分钟
        cutoff = now - 1800
        self._login_attempts[username] = [
            t for t in self._login_attempts[username] if t > cutoff
        ]
        logger.warning(
            f"[{self.plugin_id}] Failed login: {username} "
            f"({len(self._login_attempts[username])}/5)"
        )

    def _is_locked_out(self, username: str) -> bool:
        if username not in self._login_attempts:
            return False
        recent = self._login_attempts[username]
        cutoff = time.time() - 1800  # 30分钟窗口
        recent = [t for t in recent if t > cutoff]
        self._login_attempts[username] = recent
        return len(recent) >= 5

    def _lockout_remaining(self, username: str) -> int:
        if username not in self._login_attempts:
            return 0
        oldest = min(self._login_attempts[username])
        remaining = int(1800 - (time.time() - oldest))
        return max(0, remaining)

    # ─── API Key 管理 ──────────────────────────────────────────

    async def create_api_key(
        self, user_id: str, name: str = "default"
    ) -> dict[str, Any]:
        """
        为指定用户创建 API Key。
        支持通过 user_id（UUID）或 username 查找。
        """
        # 先按 UUID 查找
        if user_id in self._users:
            target_id = user_id
        else:
            # 按 username 查找
            target_id = None
            for uid, u in self._users.items():
                if u["username"] == user_id:
                    target_id = uid
                    break
            if not target_id:
                raise ValueError("用户不存在")

        key = f"oz_{uuid.uuid4().hex}_{uuid.uuid4().hex[:8]}"
        key_id = str(uuid.uuid4())
        api_key = {
            "key_id": key_id,
            "key": key,
            "user_id": target_id,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_used": None,
            "enabled": True,
            "rate_limit": 100,  # 每分钟请求数
        }
        async with self._lock:
            self._api_keys[key_id] = api_key
            await self._save_api_keys_to_store()

        logger.info(f"[{self.plugin_id}] API key created for user {target_id}")
        return {"key_id": key_id, "key": key, "name": name}

    async def revoke_api_key(self, key_id: str) -> bool:
        """吊销 API Key"""
        async with self._lock:
            if key_id in self._api_keys:
                self._api_keys[key_id]["enabled"] = False
                await self._save_api_keys_to_store()
                logger.info(f"[{self.plugin_id}] API key revoked: {key_id}")
                return True
        return False

    def verify_api_key(self, key: str) -> dict[str, Any] | None:
        """验证 API Key"""
        for ak in self._api_keys.values():
            if ak["key"] == key and ak.get("enabled", True):
                ak["last_used"] = datetime.now(timezone.utc).isoformat()
                return ak
        return None

    def list_api_keys(self, user_id: str) -> list[dict[str, Any]]:
        """列出用户的 API Keys"""
        return [
            {
                "key_id": ak["key_id"],
                "name": ak["name"],
                "created_at": ak["created_at"],
                "last_used": ak["last_used"],
                "enabled": ak["enabled"],
            }
            for ak in self._api_keys.values()
            if ak["user_id"] == user_id
        ]

    # ─── 用户管理 ──────────────────────────────────────────────

    def list_users(self) -> list[dict[str, Any]]:
        """列出所有用户（不含密码哈希）"""
        return [
            {
                "user_id": u["user_id"],
                "username": u["username"],
                "role": u["role"],
                "department": u.get("department", ""),
                "created_at": u["created_at"],
                "last_login": u.get("last_login"),
                "enabled": u.get("enabled", True),
            }
            for u in self._users.values()
        ]

    async def update_user_role(self, user_id: str, new_role: str) -> bool:
        """修改用户角色"""
        if new_role not in ROLE_PERMISSIONS:
            raise ValueError(f"无效角色: {new_role}")
        async with self._lock:
            if user_id in self._users:
                self._users[user_id]["role"] = new_role
                await self._save_to_store()
                logger.info(f"[{self.plugin_id}] Role updated: {user_id} -> {new_role}")
                return True
        return False

    async def delete_user(self, user_id: str) -> bool:
        """删除用户"""
        async with self._lock:
            if user_id in self._users:
                username = self._users[user_id]["username"]
                del self._users[user_id]
                await self._save_to_store()
                logger.info(f"[{self.plugin_id}] User deleted: {username}")
                return True
        return False

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """获取用户信息"""
        u = self._users.get(user_id)
        if not u:
            return None
        return {
            "user_id": u["user_id"],
            "username": u["username"],
            "role": u["role"],
            "department": u.get("department", ""),
            "created_at": u["created_at"],
            "last_login": u.get("last_login"),
            "enabled": u.get("enabled", True),
        }

    def check_permission(self, user_id: str, permission: str) -> bool:
        """检查用户是否拥有指定权限"""
        u = self._users.get(user_id)
        if not u or not u.get("enabled", True):
            return False
        role = u["role"]
        perms = ROLE_PERMISSIONS.get(role, {}).get("perms", [])
        return "*" in perms or permission in perms

    # ─── 公共用户操作接口（供 EnterpriseAuthPlugin 等外部调用） ──

    def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        """按用户名查找用户（返回完整用户字典，含内部字段）。"""
        for u in self._users.values():
            if u["username"] == username:
                return u
        return None

    async def upsert_user(self, user_data: dict[str, Any]) -> dict[str, Any]:
        """创建或更新用户。如果 user_id 已存在则更新，否则创建新用户。"""
        async with self._lock:
            user_id = user_data.get("user_id")
            if user_id and user_id in self._users:
                # 更新现有用户
                self._users[user_id].update(user_data)
                return self._users[user_id]
            else:
                # 创建新用户
                if not user_id:
                    user_id = str(uuid.uuid4())
                    user_data["user_id"] = user_id
                self._users[user_id] = user_data
                return user_data

    async def persist(self) -> None:
        """持久化用户数据到存储层。"""
        await self._save_to_store()

    # ─── 持久化（V0.1.7: 通过 UserStore 抽象层） ────────────────

    async def _save_to_store(self):
        """通过 UserStore 保存用户数据"""
        if not self._store:
            return
        try:
            await self._store.save_users(self._users)
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Failed to save users: {e}")

    async def _load_from_store(self):
        """通过 UserStore 加载用户数据"""
        if not self._store:
            return
        try:
            self._users = await self._store.load_users()
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Failed to load users: {e}")
            self._users = {}

    async def _save_api_keys_to_store(self):
        """通过 UserStore 保存 API Key 数据"""
        if not self._store:
            return
        try:
            await self._store.save_api_keys(self._api_keys)
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Failed to save API keys: {e}")

    async def _load_api_keys_from_store(self):
        """通过 UserStore 加载 API Key 数据"""
        if not self._store:
            return
        try:
            self._api_keys = await self._store.load_api_keys()
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Failed to load API keys: {e}")
            self._api_keys = {}
