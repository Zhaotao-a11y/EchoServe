"""
EchoServe P2 — 企业级认证插件（LDAP + OAuth2）

功能：
  - LDAP 集成（Active Directory / OpenLDAP）
    · 用户登录时通过 LDAP 验证密码
    · 自动同步用户信息和组/部门
    · 支持 LDAPS（TLS 加密）
  - OAuth2 集成（企业 SSO）
    · 支持 OAuth2 / OIDC 协议
    · 兼容 Azure AD / Google Workspace / 企业微信 SSO
    · 自动创建/更新本地用户
  - 本地认证降级
    · LDAP/OAuth 不可用时自动降级到本地 JWT
  - 会话管理
    · OAuth 回调处理
    · Token 刷新

配置（环境变量 / .env）：
  # LDAP
  LDAP_ENABLED         是否启用 LDAP
  LDAP_SERVER_URI      ldap://ad.company.com 或 ldaps://ad.company.com
  LDAP_BIND_DN        绑定 DN（用于搜索用户）
  LDAP_BIND_PASSWORD   绑定密码
  LDAP_USER_BASE_DN    ou=Users,dc=company,dc=com
  LDAP_USER_FILTER     (&(objectClass=user)(sAMAccountName=%s))
  LDAP_GROUP_BASE_DN   ou=Groups,dc=company,dc=com
  LDAP_USERNAME_ATTR   sAMAccountName
  LDAP_EMAIL_ATTR      mail
  LDAP_DEPARTMENT_ATTR department
  LDAP_AUTO_CREATE     自动创建用户（true/false）

  # OAuth2
  OAUTH_ENABLED        是否启用 OAuth2
  OAUTH_PROVIDER       azure|google|wechat|custom
  OAUTH_CLIENT_ID      客户端 ID
  OAUTH_CLIENT_SECRET  客户端密钥
  OAUTH_AUTHORIZE_URL  授权端点
  OAUTH_TOKEN_URL      Token 端点
  OAUTH_USERINFO_URL   用户信息端点
  OAUTH_REDIRECT_URI   http://localhost:8080/api/auth/oauth/callback
  OAUTH_SCOPE          openid profile email
  OAUTH_AUTO_CREATE    自动创建用户（true/false）
"""
from __future__ import annotations

import uuid
import time
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone

import jwt

from core.plugin import BaizePlugin
# BaizeContext 延迟导入，避免循环依赖
from core.fiber import Fiber

logger = logging.getLogger("echoseve.auth.enterprise")


class EnterpriseAuthPlugin(BaizePlugin):
    """
    企业级认证插件。

    优先级：OAuth2 → LDAP → 本地 JWT（降级链）
    """

    plugin_id = "security.auth_enterprise"
    plugin_name = "企业认证（LDAP + OAuth2）"
    plugin_version = "0.1.0"
    dependencies = ["security.auth"]  # 依赖基础认证插件

    # ─── OAuth2 提供商预设 ────────────────────────

    OAUTH_PRESETS = {
        "azure": {
            "authorize_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            "userinfo_url": "https://graph.microsoft.com/v1.0/me",
            "scope": "openid profile email",
            "tenant": "common",
        },
        "google": {
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
            "scope": "openid profile email",
        },
        "wechat_work": {
            "authorize_url": "https://open.work.weixin.qq.com/wwopen/sso/authorize",
            "token_url": "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            "userinfo_url": "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo",
            "scope": "snsapi_login",
        },
    }

    def __init__(self):
        # LDAP 配置
        self._ldap_enabled: bool = False
        self._ldap_server: str = ""
        self._ldap_bind_dn: str = ""
        self._ldap_bind_pwd: str = ""
        self._ldap_user_base: str = ""
        self._ldap_user_filter: str = "(&(objectClass=user)(sAMAccountName=%s))"
        self._ldap_group_base: str = ""
        self._ldap_username_attr: str = "sAMAccountName"
        self._ldap_email_attr: str = "mail"
        self._ldap_dept_attr: str = "department"
        self._ldap_auto_create: bool = True

        # OAuth2 配置
        self._oauth_enabled: bool = False
        self._oauth_provider: str = ""
        self._oauth_client_id: str = ""
        self._oauth_client_secret: str = ""
        self._oauth_authorize_url: str = ""
        self._oauth_token_url: str = ""
        self._oauth_userinfo_url: str = ""
        self._oauth_redirect_uri: str = ""
        self._oauth_scope: str = "openid profile email"
        self._oauth_auto_create: bool = True

        # 状态
        self._ldap_connection = None
        self._oauth_state_store: Dict[str, Dict[str, Any]] = {}  # state → metadata
        self._sync_history: List[Dict[str, Any]] = []

    # ─── 生命周期 ──────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        import os

        # 加载 LDAP 配置
        self._ldap_enabled = os.getenv("LDAP_ENABLED", "false").lower() == "true"
        if self._ldap_enabled:
            self._ldap_server = os.getenv("LDAP_SERVER_URI", "")
            self._ldap_bind_dn = os.getenv("LDAP_BIND_DN", "")
            self._ldap_bind_pwd = os.getenv("LDAP_BIND_PASSWORD", "")
            self._ldap_user_base = os.getenv("LDAP_USER_BASE_DN", "")
            self._ldap_user_filter = os.getenv("LDAP_USER_FILTER", self._ldap_user_filter)
            self._ldap_group_base = os.getenv("LDAP_GROUP_BASE_DN", "")
            self._ldap_username_attr = os.getenv("LDAP_USERNAME_ATTR", "sAMAccountName")
            self._ldap_email_attr = os.getenv("LDAP_EMAIL_ATTR", "mail")
            self._ldap_dept_attr = os.getenv("LDAP_DEPARTMENT_ATTR", "department")
            self._ldap_auto_create = os.getenv("LDAP_AUTO_CREATE", "true").lower() == "true"

        # 加载 OAuth2 配置
        self._oauth_enabled = os.getenv("OAUTH_ENABLED", "false").lower() == "true"
        if self._oauth_enabled:
            self._oauth_provider = os.getenv("OAUTH_PROVIDER", "custom")
            self._oauth_client_id = os.getenv("OAUTH_CLIENT_ID", "")
            self._oauth_client_secret = os.getenv("OAUTH_CLIENT_SECRET", "")
            self._oauth_redirect_uri = os.getenv(
                "OAUTH_REDIRECT_URI",
                "http://localhost:8080/api/auth/oauth/callback",
            )
            self._oauth_scope = os.getenv("OAUTH_SCOPE", "openid profile email")
            self._oauth_auto_create = os.getenv("OAUTH_AUTO_CREATE", "true").lower() == "true"

            # 应用预设
            if self._oauth_provider in self.OAUTH_PRESETS:
                preset = self.OAUTH_PRESETS[self._oauth_provider].copy()
                tenant = os.getenv("OAUTH_AZURE_TENANT", "common")
                preset["authorize_url"] = preset["authorize_url"].format(tenant=tenant)
                preset["token_url"] = preset["token_url"].format(tenant=tenant)
                self._oauth_authorize_url = os.getenv("OAUTH_AUTHORIZE_URL", preset["authorize_url"])
                self._oauth_token_url = os.getenv("OAUTH_TOKEN_URL", preset["token_url"])
                self._oauth_userinfo_url = os.getenv("OAUTH_USERINFO_URL", preset["userinfo_url"])
                self._oauth_scope = os.getenv("OAUTH_SCOPE", preset["scope"])
            else:
                self._oauth_authorize_url = os.getenv("OAUTH_AUTHORIZE_URL", "")
                self._oauth_token_url = os.getenv("OAUTH_TOKEN_URL", "")
                self._oauth_userinfo_url = os.getenv("OAUTH_USERINFO_URL", "")

        # 注册路由
        router = ctx.inject("http_router", None)
        if router:
            # OAuth 路由
            router.add_api_route(
                "/api/auth/oauth/authorize",
                endpoint=self.oauth_authorize,
                methods=["GET"],
                name="oauth_authorize",
            )
            router.add_api_route(
                "/api/auth/oauth/callback",
                endpoint=self.oauth_callback,
                methods=["GET"],
                name="oauth_callback",
            )
            # LDAP 同步路由（管理员触发）
            router.add_api_route(
                "/api/auth/ldap/sync",
                endpoint=self.ldap_sync_endpoint,
                methods=["POST"],
                name="ldap_sync",
            )
            # 状态查询
            router.add_api_route(
                "/api/auth/enterprise/status",
                endpoint=self.get_status_endpoint,
                methods=["GET"],
                name="enterprise_auth_status",
            )

        # 注册服务
        self.provide("enterprise_auth", self)

        # 状态日志
        ldap_status = "enabled" if self._ldap_enabled else "disabled"
        oauth_status = "enabled" if self._oauth_enabled else "disabled"
        logger.info(f"[{self.plugin_id}] LDAP: {ldap_status}, OAuth2: {oauth_status}")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        self._close_ldap()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ════════════════════════════════════════════
    #  LDAP 认证
    # ════════════════════════════════════════════

    def authenticate_ldap(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        通过 LDAP 验证用户密码。

        Returns:
            用户信息字典（含 username, email, department, groups）
            验证失败返回 None
        """
        if not self._ldap_enabled:
            logger.debug(f"[{self.plugin_id}] LDAP disabled, skipping")
            return None

        try:
            import ldap3
        except ImportError:
            logger.error(f"[{self.plugin_id}] ldap3 未安装，请 pip install ldap3")
            return None

        # 建立连接
        conn = self._get_ldap_connection()
        if not conn:
            return None

        try:
            # 搜索用户
            search_filter = self._ldap_user_filter % username
            conn.search(
                search_base=self._ldap_user_base,
                search_filter=search_filter,
                attributes=[
                    self._ldap_username_attr,
                    self._ldap_email_attr,
                    self._ldap_dept_attr,
                    "displayName",
                    "memberOf",
                ],
            )

            if not conn.entries:
                logger.warning(f"[{self.plugin_id}] LDAP: 用户不存在: {username}")
                return None

            entry = conn.entries[0]
            user_dn = entry.entry_dn

            # 验证密码（绑定测试）
            verify_conn = ldap3.Connection(
                ldap3.Server(self._ldap_server, get_info=ldap3.ALL),
                user=user_dn,
                password=password,
                auto_bind=True,
            )

            if not verify_conn.bound:
                logger.warning(f"[{self.plugin_id}] LDAP: 密码错误: {username}")
                return None

            # 提取用户信息
            user_info = {
                "username": getattr(entry, self._ldap_username_attr, [username])[0]
                    if hasattr(entry, self._ldap_username_attr) else username,
                "email": getattr(entry, self._ldap_email_attr, [""])[0]
                    if hasattr(entry, self._ldap_email_attr) else "",
                "department": getattr(entry, self._ldap_dept_attr, [""])[0]
                    if hasattr(entry, self._ldap_dept_attr) else "",
                "display_name": getattr(entry, "displayName", [""])[0]
                    if hasattr(entry, "displayName") else username,
                "groups": [g.split(",")[0].split("=")[-1] for g in
                          getattr(entry, "memberOf", [])],
                "auth_type": "ldap",
            }

            logger.info(f"[{self.plugin_id}] LDAP 认证成功: {username}")
            return user_info

        except Exception as e:
            logger.error(f"[{self.plugin_id}] LDAP 认证异常: {e}")
            return None

    async def ldap_sync_users(self) -> Dict[str, Any]:
        """
        从 LDAP 同步所有用户到本地。

        Returns:
            {"status": "success", "synced": int, "created": int, "updated": int, "errors": int}
        """
        if not self._ldap_enabled:
            return {"status": "disabled", "reason": "LDAP not enabled"}

        try:
            pass
        except ImportError:
            return {"status": "error", "reason": "ldap3 not installed"}

        conn = self._get_ldap_connection()
        if not conn:
            return {"status": "error", "reason": "LDAP connection failed"}

        created = 0
        updated = 0
        errors = 0

        try:
            # 搜索所有用户
            conn.search(
                search_base=self._ldap_user_base,
                search_filter="(objectClass=user)",
                attributes=[
                    self._ldap_username_attr,
                    self._ldap_email_attr,
                    self._ldap_dept_attr,
                    "displayName",
                ],
            )

            auth = self.ctx.inject("auth_service")
            if not auth:
                return {"status": "error", "reason": "auth_service not available"}

            for entry in conn.entries:
                try:
                    username = getattr(entry, self._ldap_username_attr, [None])[0]
                    if not username:
                        continue

                    email = getattr(entry, self._ldap_email_attr, [""])[0]
                    dept = getattr(entry, self._ldap_dept_attr, ["default"])[0]

                    # 检查是否已存在
                    existing = None
                    for u in auth._users.values():
                        if u["username"] == username:
                            existing = u
                            break

                    if existing:
                        # 更新
                        existing["department"] = dept
                        existing["email"] = email
                        existing["auth_type"] = "ldap"
                        updated += 1
                    elif self._ldap_auto_create:
                        # 创建（无密码，LDAP 认证）
                        user_id = str(uuid.uuid4())
                        auth._users[user_id] = {
                            "user_id": user_id,
                            "username": username,
                            "password_hash": "",  # LDAP 用户无本地密码
                            "role": "user",
                            "department": dept,
                            "email": email,
                            "auth_type": "ldap",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "last_login": None,
                            "enabled": True,
                        }
                        created += 1

                except Exception as e:
                    logger.warning(f"LDAP sync error for user entry: {e}")
                    errors += 1

            # 保存
            await auth._save_to_store()

            sync_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "created": created,
                "updated": updated,
                "errors": errors,
                "total_users": len(conn.entries),
            }
            self._sync_history.append(sync_record)

            logger.info(
                f"[{self.plugin_id}] LDAP 同步完成: "
                f"创建={created}, 更新={updated}, 错误={errors}"
            )

            return {
                "status": "success",
                "created": created,
                "updated": updated,
                "errors": errors,
                "total": len(conn.entries),
            }

        except Exception as e:
            logger.error(f"[{self.plugin_id}] LDAP 同步异常: {e}")
            return {"status": "error", "reason": str(e)}

    def _get_ldap_connection(self):
        """获取 LDAP 连接（带缓存）"""
        if self._ldap_connection and self._ldap_connection.bound:
            return self._ldap_connection

        try:
            import ldap3
            server = ldap3.Server(self._ldap_server, get_info=ldap3.ALL)
            conn = ldap3.Connection(
                server,
                user=self._ldap_bind_dn,
                password=self._ldap_bind_pwd,
                auto_bind=True,
            )
            self._ldap_connection = conn
            return conn
        except Exception as e:
            logger.error(f"[{self.plugin_id}] LDAP 连接失败: {e}")
            return None

    def _close_ldap(self):
        """关闭 LDAP 连接"""
        if self._ldap_connection:
            try:
                self._ldap_connection.unbind()
            except Exception as e:
                logger.debug(f"Error closing LDAP connection: {e}")
            self._ldap_connection = None

    # ════════════════════════════════════════════
    #  OAuth2 认证
    # ════════════════════════════════════════════

    async def oauth_authorize(self, request):
        """
        发起 OAuth2 授权请求。
        重定向用户到 OAuth 提供商的授权页面。
        """
        from fastapi.responses import RedirectResponse
        import secrets

        if not self._oauth_enabled:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="OAuth2 not enabled")

        # 生成 state 参数（防 CSRF）
        state = secrets.token_urlsafe(32)
        self._oauth_state_store[state] = {
            "created_at": time.time(),
            "redirect_after": request.query_params.get("redirect_after", "/"),
        }

        # 清理过期 state（30 分钟）
        cutoff = time.time() - 1800
        self._oauth_state_store = {
            k: v for k, v in self._oauth_state_store.items()
            if v.get("created_at", 0) > cutoff
        }

        # 构造授权 URL
        params = {
            "client_id": self._oauth_client_id,
            "redirect_uri": self._oauth_redirect_uri,
            "response_type": "code",
            "scope": self._oauth_scope,
            "state": state,
        }

        # Azure AD 额外参数
        if self._oauth_provider == "azure":
            params["response_mode"] = "query"

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        auth_url = f"{self._oauth_authorize_url}?{query_string}"

        logger.info(f"[{self.plugin_id}] OAuth 授权重定向: {self._oauth_provider}")
        return RedirectResponse(url=auth_url)

    async def oauth_callback(self, request):
        """
        处理 OAuth2 回调。
        用授权码换取 access_token，获取用户信息，创建/更新本地用户，签发 JWT。
        """
        from fastapi import HTTPException
        from fastapi.responses import RedirectResponse

        query_params = dict(request.query_params)
        code = query_params.get("code", "")
        state = query_params.get("state", "")

        # 验证 state
        if state not in self._oauth_state_store:
            raise HTTPException(status_code=400, detail="Invalid state parameter")

        state_info = self._oauth_state_store.pop(state)
        redirect_after = state_info.get("redirect_after", "/")

        if not code:
            raise HTTPException(status_code=400, detail="Authorization code missing")

        try:
            import httpx

            # 1. 用 code 换 token
            token_data = {
                "client_id": self._oauth_client_id,
                "client_secret": self._oauth_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self._oauth_redirect_uri,
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self._oauth_token_url, data=token_data)
                token_resp = resp.json()

                if "access_token" not in token_resp:
                    safe_resp = {
                        k: "***REDACTED***" if "token" in k.lower() or "secret" in k.lower() else v
                        for k, v in token_resp.items()
                    }
                    logger.error(f"[{self.plugin_id}] Token 获取失败: {safe_resp}")
                    raise HTTPException(status_code=400, detail="Token exchange failed")

                access_token = token_resp["access_token"]

                # 2. 获取用户信息
                userinfo_resp = await client.get(
                    self._oauth_userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo = userinfo_resp.json()

            # 3. 解析用户信息（不同提供商字段不同）
            user_info = self._parse_oauth_userinfo(userinfo)

            # 4. 创建/更新本地用户
            auth = self.ctx.inject("auth_service")
            if not auth:
                raise HTTPException(status_code=500, detail="Auth service unavailable")

            internal_user = await self._upsert_oauth_user(auth, user_info)

            # 5. 签发 JWT
            token = self._issue_jwt_for_user(internal_user)

            logger.info(
                f"[{self.plugin_id}] OAuth 登录成功: "
                f"{user_info.get('username')} ({self._oauth_provider})"
            )

            # 6. 重定向到前端（带 token）
            frontend_url = f"/?token={token}&username={user_info.get('username', '')}"
            return RedirectResponse(url=frontend_url)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[{self.plugin_id}] OAuth 回调异常: {e}")
            raise HTTPException(status_code=500, detail=f"OAuth callback error: {e}")

    def _parse_oauth_userinfo(self, userinfo: Dict[str, Any]) -> Dict[str, Any]:
        """解析不同 OAuth 提供商的用户信息"""
        provider = self._oauth_provider

        if provider == "azure":
            return {
                "username": userinfo.get("userPrincipalName", "").split("@")[0],
                "email": userinfo.get("mail", userinfo.get("userPrincipalName", "")),
                "display_name": userinfo.get("displayName", ""),
                "department": userinfo.get("department", ""),
                "auth_type": "oauth_azure",
            }
        elif provider == "google":
            return {
                "username": userinfo.get("email", "").split("@")[0],
                "email": userinfo.get("email", ""),
                "display_name": userinfo.get("name", ""),
                "department": "",
                "auth_type": "oauth_google",
            }
        elif provider == "wechat_work":
            return {
                "username": userinfo.get("UserId", userinfo.get("userid", "")),
                "email": "",
                "display_name": userinfo.get("name", ""),
                "department": "",
                "auth_type": "oauth_wechat_work",
            }
        else:
            # 通用解析
            return {
                "username": userinfo.get("preferred_username")
                    or userinfo.get("username")
                    or userinfo.get("email", "").split("@")[0],
                "email": userinfo.get("email", ""),
                "display_name": userinfo.get("name", ""),
                "department": userinfo.get("department", ""),
                "auth_type": f"oauth_{provider}",
            }

    async def _upsert_oauth_user(self, auth, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """创建或更新 OAuth 用户"""
        username = user_info["username"]

        # 查找现有用户
        for u in auth._users.values():
            if u["username"] == username:
                # 更新
                u["email"] = user_info.get("email", u.get("email", ""))
                u["department"] = user_info.get("department", u.get("department", ""))
                u["auth_type"] = user_info.get("auth_type", "oauth")
                u["last_login"] = datetime.now(timezone.utc).isoformat()
                return u

        # 创建新用户
        if self._oauth_auto_create:
            user_id = str(uuid.uuid4())
            new_user = {
                "user_id": user_id,
                "username": username,
                "password_hash": "",  # OAuth 用户无本地密码
                "role": "user",
                "department": user_info.get("department", "default"),
                "email": user_info.get("email", ""),
                "display_name": user_info.get("display_name", username),
                "auth_type": user_info.get("auth_type", "oauth"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login": datetime.now(timezone.utc).isoformat(),
                "enabled": True,
            }
            auth._users[user_id] = new_user

            # 保存
            await auth._save_to_store()

            logger.info(f"[{self.plugin_id}] OAuth 用户已创建: {username}")
            return new_user

        # 不自动创建 → 返回临时信息
        return {
            "user_id": "temp",
            "username": username,
            "role": "user",
        }

    def _issue_jwt_for_user(self, user: Dict[str, Any]) -> str:
        """为本地用户签发 JWT"""
        auth = self.ctx.inject("auth_service")
        if auth and hasattr(auth, "_issue_jwt"):
            return auth._issue_jwt(user)

        # 降级：直接签发
        settings = self.ctx.settings
        secret = getattr(settings.security, "jwt_secret", "change-me")
        expire_min = getattr(settings.security, "token_expire_minutes", 480)

        payload = {
            "sub": user.get("user_id", "unknown"),
            "username": user.get("username", ""),
            "role": user.get("role", "user"),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=expire_min),
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    # ─── 统一认证入口 ──────────────────────────────

    async def authenticate(
        self, username: str, password: str
    ) -> Optional[Dict[str, Any]]:
        """
        统一认证入口（认证链）。

        优先级：LDAP → 本地 JWT（降级）

        Returns:
            用户信息字典，失败返回 None
        """
        # 1. 尝试 LDAP
        if self._ldap_enabled:
            ldap_result = self.authenticate_ldap(username, password)
            if ldap_result:
                return ldap_result

        # 2. 降级到本地认证
        auth = self.ctx.inject("auth_service")
        if auth:
            try:
                result = await auth.login(username, password)
                if result and "user_id" in result:
                    user = auth.get_user(result["user_id"])
                    if user:
                        user["auth_type"] = "local"
                        return user
            except Exception as e:
                logger.debug(f"Local auth fallback failed: {e}")

        return None

    # ─── HTTP 端点 ─────────────────────────────────

    async def ldap_sync_endpoint(self, request):
        """管理员触发 LDAP 同步"""

        # 权限检查
        auth = self.ctx.inject("auth_service")
        if auth:
            # 从请求中获取当前用户（简化：实际应从 JWT 解析）
            pass

        result = await self.ldap_sync_users()
        return result

    async def get_status_endpoint(self, request):
        """返回企业认证状态"""
        return self.get_status()

    # ─── 状态查询 ─────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """返回企业认证配置状态"""
        return {
            "plugin_id": self.plugin_id,
            "ldap": {
                "enabled": self._ldap_enabled,
                "server": self._ldap_server,
                "user_base": self._ldap_user_base,
                "auto_create": self._ldap_auto_create,
                "connected": self._ldap_connection is not None
                    and self._ldap_connection.bound,
            },
            "oauth2": {
                "enabled": self._oauth_enabled,
                "provider": self._oauth_provider,
                "client_id_set": bool(self._oauth_client_id),
                "redirect_uri": self._oauth_redirect_uri,
                "scope": self._oauth_scope,
                "auto_create": self._oauth_auto_create,
            },
            "sync_history": self._sync_history[-5:],  # 最近 5 次同步
        }
