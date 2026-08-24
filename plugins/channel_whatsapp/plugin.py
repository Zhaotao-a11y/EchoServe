"""
EchoServe P2 — WhatsApp 渠道插件

功能：
  - WhatsApp Business API 消息接收（Webhook 回调）
  - Meta Webhook 验证（GET 挑战）
  - 消息格式转换（统一消息格式 UnifiedMessage）
  - 调用 ChatPlugin 处理消息并回复
  - 用户映射（手机号 → 内部用户ID）
  - 媒体消息处理（文本/图片/文档）
  - 速率限制（Meta 限制 80 条/分钟/号码）

配置（环境变量 / .env）：
  WHATSAPP_VERIFY_TOKEN    Webhook 验证 Token
  WHATSAPP_APP_SECRET     App Secret（用于签名验证）
  WHATSAPP_PHONE_NUMBER_ID  WhatsApp Business 电话号码 ID
  WHATSAPP_ACCESS_TOKEN    Permanent Access Token
  WHATSAPP_WEBHOOK_PATH   Webhook 路径（默认 /webhook/whatsapp）
  WHATSAPP_RATE_LIMIT     每分钟最大发送数（默认 80）
"""
from __future__ import annotations

import json
import time
import hashlib
import hmac
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from core.plugin import BaizePlugin
# BaizeContext 延迟导入，避免循环依赖
from core.fiber import Fiber

logger = logging.getLogger("echoseve.channel.whatsapp")

# 复用企业微信插件的 UnifiedMessage 定义
from plugins.channel_wechat.plugin import UnifiedMessage


class WhatsAppChannelPlugin(BaizePlugin):
    """WhatsApp Business API 渠道插件"""

    plugin_id = "channel.whatsapp"
    plugin_name = "WhatsApp Business"
    plugin_version = "0.1.0"
    dependencies = ["core.chat", "security.auth"]

    # Meta 速率限制：每个电话号码每分钟最多 80 条消息
    META_RATE_LIMIT = 80
    META_RATE_WINDOW = 60  # 秒

    def __init__(self):
        self._ctx: Optional[BaizeContext] = None
        self._verify_token: str = ""
        self._app_secret: str = ""
        self._phone_number_id: str = ""
        self._access_token: str = ""
        self._webhook_path: str = "/webhook/whatsapp"
        self._rate_limit: int = 80
        # 发送计数（滑动窗口）
        self._send_timestamps: List[float] = []
        # 用户映射：phone_number → internal_user_id
        self._user_mapping: Dict[str, str] = {}
        # 媒体消息缓存
        self._media_cache: Dict[str, Dict[str, Any]] = {}

    # ─── 生命周期 ──────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        self._ctx = ctx
        import os
        self._verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        self._app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
        self._phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self._access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self._webhook_path = os.getenv("WHATSAPP_WEBHOOK_PATH", "/webhook/whatsapp")
        self._rate_limit = int(os.getenv("WHATSAPP_RATE_LIMIT", str(self.META_RATE_LIMIT)))

        # 注册路由
        try:
            router = ctx.inject("http_router", None)
        except KeyError:
            router = None

        enabled = self._is_configured()

        if router and enabled:
            router.add_api_route(
                path=self._webhook_path,
                endpoint=self.verify_webhook,
                methods=["GET"],
                name="whatsapp_verify",
            )
            router.add_api_route(
                path=self._webhook_path,
                endpoint=self.handle_webhook,
                methods=["POST"],
                name="whatsapp_webhook",
            )
            logger.info(f"[{self.plugin_id}] Webhook route: {self._webhook_path}")
        elif router:
            logger.info(f"[{self.plugin_id}] Disabled - missing credentials (set WHATSAPP_* env vars)")
        else:
            logger.info(f"[{self.plugin_id}] No http_router available")

        # 注册服务
        self.provide("whatsapp_channel", self)

        logger.info(
            f"[{self.plugin_id}] Initialized "
            f"({'enabled' if enabled else 'disabled - configure WHATSAPP_* env vars'})"
        )

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── Webhook 端点 ──────────────────────────────────

    async def verify_webhook(self, request):
        """
        Meta 首次配置 Webhook 时的验证（GET 请求）。
        验证 challenge 参数并返回明文。
        """
        from fastapi.responses import PlainTextResponse, JSONResponse

        query_params = dict(request.query_params)
        mode = query_params.get("hub.mode", "")
        token = query_params.get("hub.verify_token", "")
        challenge = query_params.get("hub.challenge", "")

        logger.info(f"[{self.plugin_id}] Webhook verification attempt")

        if mode == "subscribe" and token == self._verify_token:
            logger.info(f"[{self.plugin_id}] Webhook verified successfully")
            return PlainTextResponse(content=challenge)
        else:
            logger.warning(f"[{self.plugin_id}] Webhook verification failed")
            return JSONResponse(status_code=403, content={"detail": "Verification failed"})

    async def handle_webhook(self, request):
        """
        接收 WhatsApp 推送的消息（POST 请求）。
        验证签名 → 解析 JSON → 转换为 UnifiedMessage → 调用 ChatPlugin → 回复
        """
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse

        body = await request.body()

        # 验证 Meta 签名（生产环境必须开启）
        if self._app_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            if not self._verify_meta_signature(body, signature):
                logger.error(f"[{self.plugin_id}] Invalid Meta signature")
                raise HTTPException(status_code=403, detail="Invalid signature")

        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        logger.info(f"[{self.plugin_id}] Received webhook: {json.dumps(data)[:300]}")

        try:
            # 解析 WhatsApp Business API 格式
            entries = data.get("entry", [])
            for entry in entries:
                for change in entry.get("changes", []):
                    value = change.get("value", {})

                    # 处理消息
                    if "messages" in value:
                        for msg in value["messages"]:
                            await self._process_message(msg, value.get("metadata", {}))

                    # 处理状态更新（已送达/已读等）
                    if "statuses" in value:
                        for status in value["statuses"]:
                            self._process_status(status)

            return JSONResponse(content={"status": "ok"})

        except Exception as e:
            logger.error(f"[{self.plugin_id}] Webhook error: {e}")
            return JSONResponse(content={"status": "error", "detail": str(e)})

    # ─── 消息处理 ──────────────────────────────────────

    async def _process_message(self, msg: Dict[str, Any], metadata: Dict[str, Any]):
        """处理单条 WhatsApp 消息"""
        phone = msg.get("from", "")
        msg_type = msg.get("type", "text")
        msg_id = msg.get("id", "")

        # 获取或创建内部用户映射
        user_id = self._get_or_create_user(phone)

        # 根据消息类型提取内容
        content = ""
        metadata_extra = {}

        if msg_type == "text":
            content = msg.get("text", {}).get("body", "").strip()
        elif msg_type == "image":
            content = "[图片消息]"
            metadata_extra["media_id"] = msg.get("image", {}).get("id", "")
            metadata_extra["media_caption"] = msg.get("image", {}).get("caption", "")
        elif msg_type == "document":
            content = "[文档消息]"
            metadata_extra["media_id"] = msg.get("document", {}).get("id", "")
            metadata_extra["filename"] = msg.get("document", {}).get("filename", "")
        elif msg_type == "audio":
            content = "[语音消息]"
            metadata_extra["media_id"] = msg.get("audio", {}).get("id", "")
        elif msg_type == "video":
            content = "[视频消息]"
            metadata_extra["media_id"] = msg.get("video", {}).get("id", "")
        else:
            logger.info(f"[{self.plugin_id}] Unsupported message type: {msg_type}")
            content = f"[{msg_type} 消息]"

        if not content or content.startswith("["):
            # 非文本消息，记录但尝试用描述回复
            if not content:
                return

        # 转换为统一消息
        unified = UnifiedMessage(
            user_id=f"whatsapp:{phone}",
            channel="whatsapp",
            content=content,
            raw_content=content,
            metadata={
                "phone": phone,
                "message_id": msg_id,
                "message_type": msg_type,
                **metadata_extra,
            },
            session_id=f"whatsapp:{phone}",
        )

        # 调用对话插件处理
        chat = self.inject("chat_manager")
        if not chat:
            logger.error(f"[{self.plugin_id}] Chat manager not available")
            await self._send_reply(phone, "系统暂时不可用，请稍后再试。")
            return

        try:
            result = await chat.chat(
                session_id=unified.session_id,
                user_message=unified.content,
                use_rag=True,
            )
            reply_text = result.get("reply", "暂未找到相关信息")

            # WhatsApp 文本消息限制 4096 字符
            if len(reply_text) > 4000:
                reply_text = reply_text[:4000] + "..."

            await self._send_reply(phone, reply_text)

            # 记录审计日志
            audit = self.inject("audit_logger")
            if audit:
                try:
                    await audit.log(
                        action="whatsapp_chat",
                        user_id=unified.user_id,
                        query=unified.content,
                        response_summary=reply_text[:500],
                        latency_ms=result.get("tokens", {}).get("latency_ms", 0),
                        channel="whatsapp",
                    )
                except Exception as e:
                    logger.warning(f"[{self.plugin_id}] Audit log failed: {e}")

        except Exception as e:
            logger.error(f"[{self.plugin_id}] Chat processing error: {e}")
            await self._send_reply(phone, "抱歉，处理您的消息时出现错误，请稍后再试。")

    def _process_status(self, status: Dict[str, Any]):
        """处理消息状态更新（delivered/read/sent/failed）"""
        msg_id = status.get("id", "")
        msg_status = status.get("status", "")
        timestamp = status.get("timestamp", "")
        logger.info(f"[{self.plugin_id}] Message {msg_id} status: {msg_status} @ {timestamp}")

    # ─── 发送回复 ──────────────────────────────────────

    async def _send_reply(self, to_phone: str, text: str):
        """通过 WhatsApp Business API 发送文本消息"""
        # 速率限制检查
        if not self._check_rate_limit():
            logger.warning(f"[{self.plugin_id}] Rate limit exceeded, message queued/dropped")
            # 生产环境应加入队列延迟发送
            return

        if not self._access_token or not self._phone_number_id:
            logger.error(f"[{self.plugin_id}] WhatsApp credentials not configured")
            return

        url = f"https://graph.facebook.com/v18.0/{self._phone_number_id}/messages"

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": text},
        }

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                data = resp.json()

                if resp.status_code == 200:
                    self._record_send()
                    logger.info(f"[{self.plugin_id}] Reply sent to {to_phone}")
                else:
                    logger.error(
                        f"[{self.plugin_id}] Send failed: "
                        f"status={resp.status_code}, body={data}"
                    )
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Send error: {e}")

    def _check_rate_limit(self) -> bool:
        """检查是否超过 Meta 速率限制"""
        now = time.time()
        # 清除过期记录
        cutoff = now - self.META_RATE_WINDOW
        self._send_timestamps = [t for t in self._send_timestamps if t > cutoff]
        if len(self._send_timestamps) < self._rate_limit:
            # 记录本次发送
            self._send_timestamps.append(now)
            return True
        return False

    def _record_send(self):
        """记录一次发送"""
        self._send_timestamps.append(time.time())

    # ─── 签名验证 ──────────────────────────────────────

    def _verify_meta_signature(self, body: bytes, signature: str) -> bool:
        """验证 Meta Webhook 签名（HMAC-SHA256）"""
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(
            self._app_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        provided = signature[7:]  # 去掉 "sha256=" 前缀
        return hmac.compare_digest(expected, provided)

    # ─── 用户映射 ──────────────────────────────────────

    def _get_or_create_user(self, phone: str) -> str:
        """获取或创建手机号对应的内部用户 ID"""
        if phone in self._user_mapping:
            return self._user_mapping[phone]

        # 尝试通过认证插件查找/创建用户
        auth = self.inject("auth_service", None)
        if auth:
            # 检查是否已存在
            for u in auth._users.values():
                if u.get("phone") == phone:
                    self._user_mapping[phone] = u["user_id"]
                    return u["user_id"]

            # 创建新用户
            try:
                import uuid
                user_id = str(uuid.uuid4())
                new_user = {
                    "user_id": user_id,
                    "username": f"whatsapp_{phone}",
                    "password_hash": "",  # 社交登录无密码
                    "role": "user",
                    "department": "whatsapp",
                    "phone": phone,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_login": datetime.now(timezone.utc).isoformat(),
                    "enabled": True,
                    "auth_type": "whatsapp",
                }
                auth._users[user_id] = new_user
                self._user_mapping[phone] = user_id
                logger.info(f"[{self.plugin_id}] Created user for phone {phone}")
                return user_id
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Failed to create user: {e}")

        # 降级：使用 phone 作为 user_id
        return f"whatsapp:{phone}"

    def map_phone_user(self, phone: str, internal_user_id: str):
        """手动映射手机号到内部用户"""
        self._user_mapping[phone] = internal_user_id

    # ─── 状态查询 ──────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """返回渠道状态"""
        return {
            "plugin_id": self.plugin_id,
            "enabled": self._is_configured(),
            "phone_number_id_set": bool(self._phone_number_id),
            "access_token_set": bool(self._access_token),
            "webhook_path": self._webhook_path,
            "mapped_users": len(self._user_mapping),
            "rate_limit": {
                "limit": self._rate_limit,
                "window_seconds": self.META_RATE_WINDOW,
                "current_count": len(self._send_timestamps),
            },
            "media_cache_size": len(self._media_cache),
        }

    def _is_configured(self) -> bool:
        """检查是否所有必要配置都已设置"""
        return bool(
            self._verify_token
            and self._app_secret
            and self._phone_number_id
            and self._access_token
        )
