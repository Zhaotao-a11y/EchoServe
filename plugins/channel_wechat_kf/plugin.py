"""
EchoServe V0.2.0 — 微信客服渠道插件 (WeChat Customer Service Channel)
=====================================================================
对接微信客服（企业微信客服）消息接口，支持 CorpID + Secret 回调模式。

与公众号客服的区别：
  - 认证方式: CorpID + Secret（非 AppID + AppSecret）
  - API 域名: qyapi.weixin.qq.com（非 api.weixin.qq.com）
  - 消息格式: 企业微信 XML 格式，支持 AES 加密
  - 回复接口: /cgi-bin/kf/send_msg（需 open_kfid）

功能：
  - URL 验证（GET 回调验证，SHA1 签名）
  - 消息接收与 AES 解密（wechatpy.crypto.WeChatCrypto）
  - 消息解析（XML → UnifiedMessage）
  - 调用 ChatPlugin 处理对话（use_rag=False）
  - 通过客服消息 API 回复
  - access_token 缓存管理
  - 会话保持（openid → session_id 映射）

配置（环境变量 / .env）：
  WECHAT_KF_CORP_ID         企业微信 CorpID
  WECHAT_KF_SECRET          微信客服应用 Secret
  WECHAT_KF_TOKEN           回调 Token（消息签名验证）
  WECHAT_KF_AES_KEY         回调 EncodingAESKey（43位，用于 AES 解密）
  WECHAT_KF_WEBHOOK_PATH    Webhook 路径（默认 /webhook/wechat_kf）

API 文档：https://developer.work.weixin.qq.com/document/path/94669
"""
from __future__ import annotations

import os
import time
import hashlib
import logging
import xml.etree.ElementTree as ET
from typing import Any
from datetime import datetime, timezone

from core.plugin import BaizePlugin
from core.fiber import Fiber
from fastapi import Request

logger = logging.getLogger("echoserve.channel.wechat_kf")


class UnifiedMessage:
    """统一消息格式（跨渠道通用）"""

    def __init__(
        self,
        user_id: str,
        channel: str,
        content: str,
        raw_content: str = "",
        metadata: (dict[str, Any] | None) = None,
        session_id: (str | None) = None,
        msg_type: str = "text",
    ):
        self.user_id = user_id
        self.channel = channel
        self.content = content
        self.raw_content = raw_content or content
        self.metadata = metadata or {}
        self.session_id = session_id or f"{channel}:{user_id}:{int(time.time())}"
        self.timestamp = datetime.now(timezone.utc)
        self.msg_type = msg_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "channel": self.channel,
            "content": self.content,
            "raw_content": self.raw_content,
            "metadata": self.metadata,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "msg_type": self.msg_type,
        }


class WeChatKFPlugin(BaizePlugin):
    """微信客服渠道插件（企业微信客服，CorpID + Secret 模式）"""

    plugin_id = "channel.wechat_kf"
    plugin_name = "微信客服"
    plugin_version = "0.2.0"
    dependencies = ["core.chat", "security.auth"]

    def __init__(self):
        self._corp_id: str = ""
        self._secret: str = ""
        self._token: str = ""           # 回调 Token（明文签名验证）
        self._aes_key: str = ""         # 回调 EncodingAESKey（43位，用于 AES 解密）
        self._access_token: (str | None) = None
        self._token_expire_at: float = 0
        self._webhook_path: str = "/webhook/wechat_kf"
        self._max_msg_len: int = 2048   # 客服消息文本长度限制（字节）
        self._user_sessions: dict[str, str] = {}   # openid -> session_id 映射
        self._crypto: Any = None        # WeChatCrypto 实例（延迟初始化）

    # ─── 生命周期 ──────────────────────────────────────

    async def on_init(self, ctx, fiber):
        # 从环境变量读取配置
        self._corp_id = os.getenv("WECHAT_KF_CORP_ID", "")
        self._secret = os.getenv("WECHAT_KF_SECRET", "")
        self._token = os.getenv("WECHAT_KF_TOKEN", "")
        self._aes_key = os.getenv("WECHAT_KF_AES_KEY", "")
        self._webhook_path = os.getenv("WECHAT_KF_WEBHOOK_PATH", "/webhook/wechat_kf")

        # 初始化 AES 解密器（如果配置了 AESKey）
        if self._aes_key and self._corp_id:
            try:
                from wechatpy.crypto import WeChatCrypto
                self._crypto = WeChatCrypto(self._token, self._aes_key, self._corp_id)
                logger.info(f"[{self.plugin_id}] AES crypto initialized")
            except Exception as e:
                logger.warning(f"[{self.plugin_id}] Failed to init AES crypto: {e}")
                self._crypto = None

        # 注册路由
        try:
            router = ctx.inject("http_router", None)
        except KeyError:
            router = None

        if router:
            router.add_api_route(
                path=self._webhook_path,
                endpoint=self.handle_webhook,
                methods=["POST"],
                name="wechat_kf_webhook",
            )
            router.add_api_route(
                path=self._webhook_path,
                endpoint=self.verify_url,
                methods=["GET"],
                name="wechat_kf_verify",
            )
            logger.info(f"[{self.plugin_id}] Webhook route: {self._webhook_path}")
        else:
            logger.info(f"[{self.plugin_id}] No http_router available, webhook not registered")

        # 注册服务
        self.provide("wechat_kf_channel", self)

        # 状态
        enabled = bool(self._corp_id and self._secret)
        logger.info(
            f"[{self.plugin_id}] Initialized "
            f"({'enabled' if enabled else 'disabled - missing credentials'})"
        )

    async def on_destroy(self, ctx, fiber):
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 入口1: 回调 URL 验证 ─────────────────────────────

    async def verify_url(self, request: Request):
        """
        微信客服回调 URL 验证（GET 请求）。
        微信会发送 msg_signature, timestamp, nonce, echostr，
        验证签名并解密 echostr 后返回明文。
        """
        from fastapi.responses import PlainTextResponse

        query_params = dict(request.query_params)
        msg_signature = query_params.get("msg_signature", "")
        timestamp = query_params.get("timestamp", "")
        nonce = query_params.get("nonce", "")
        echostr = query_params.get("echostr", "")

        logger.info(
            f"[{self.plugin_id}] URL verification attempt | "
            f"msg_signature={msg_signature[:20]}... timestamp={timestamp} nonce={nonce} "
            f"echostr_len={len(echostr)} has_aes={bool(self._crypto)}"
        )

        if not echostr:
            logger.warning(f"[{self.plugin_id}] URL verification failed: no echostr")
            return PlainTextResponse(content="", status_code=400)

        # 如果有 AES 配置，使用 wechatpy 验证签名并解密 echostr
        if self._crypto:
            try:
                # 微信 URL 验证的 echostr 是加密字符串，需用 _check_signature
                # 验证签名并解密后返回明文（与公众号/企业微信模式一致）
                from wechatpy.crypto import PrpCrypto
                decrypted = self._crypto._check_signature(
                    msg_signature, timestamp, nonce, echostr, PrpCrypto
                )
                logger.info(
                    f"[{self.plugin_id}] URL verification passed (AES decrypted) | "
                    f"decrypted_len={len(decrypted)}"
                )
                return PlainTextResponse(content=decrypted)
            except Exception as e:
                logger.warning(
                    f"[{self.plugin_id}] URL verification failed: {type(e).__name__}: {e}"
                )
                return PlainTextResponse(content="", status_code=403)
        else:
            # 无 AES 时仅做 SHA1 签名验证（兼容明文模式）
            if self._verify_signature(msg_signature, timestamp, nonce, echostr):
                logger.info(f"[{self.plugin_id}] URL verification passed (plain mode)")
                return PlainTextResponse(content=echostr)
            else:
                logger.warning(f"[{self.plugin_id}] URL verification failed")
                return PlainTextResponse(content="", status_code=403)

    # ─── 入口2: 接收客服消息 ──────────────────────────────

    async def handle_webhook(self, request: Request):
        """
        接收微信客服推送的消息（POST 请求）。
        消息为 XML 格式，可能经过 AES 加密。

        处理流程：
          1. 校验签名
          2. 如有加密则 AES 解密
          3. 解析 XML，提取 openid + content + open_kfid
          4. 转换为 UnifiedMessage
          5. 调用 ChatPlugin 生成回复（use_rag=False）
          6. 通过客服 API 发送回复
        """
        from fastapi.responses import PlainTextResponse

        query_params = dict(request.query_params)
        msg_signature = query_params.get("msg_signature", "")
        timestamp = query_params.get("timestamp", "")
        nonce = query_params.get("nonce", "")

        body = await request.body()
        body_text = body.decode("utf-8")
        logger.info(f"[{self.plugin_id}] Received webhook: {body_text[:300]}")

        try:
            # 如有 AES 配置，先解密
            if self._crypto:
                try:
                    body_text = self._crypto.decrypt_message(body_text, msg_signature, timestamp, nonce)
                    logger.info(f"[{self.plugin_id}] Message decrypted successfully")
                except Exception as e:
                    logger.error(f"[{self.plugin_id}] AES decrypt failed: {e}")
                    return PlainTextResponse(content="success")
            else:
                # 明文模式：仅校验签名
                if not self._verify_signature(msg_signature, timestamp, nonce):
                    logger.warning(f"[{self.plugin_id}] Invalid signature on webhook")
                    return PlainTextResponse(content="", status_code=403)

            # 解析 XML
            root = ET.fromstring(body_text)
            msg_type_node = root.find("MsgType")
            msg_type = msg_type_node.text if msg_type_node is not None else "text"

            from_user_node = root.find("FromUserName")
            to_user_node = root.find("ToUserName")
            from_user = from_user_node.text if from_user_node is not None else ""
            to_user = to_user_node.text if to_user_node is not None else ""

            # 提取 open_kfid（微信客服特有字段）
            kf_id_node = root.find("KfAccount") or root.find("KfId") or root.find("OpenKfId")
            open_kfid = kf_id_node.text if kf_id_node is not None else ""

            # 消息内容
            content = ""
            if msg_type == "text":
                content_node = root.find("Content")
                content = content_node.text if content_node is not None else ""
            elif msg_type == "image":
                content = "[用户发送了图片]"
                logger.info(f"[{self.plugin_id}] Image message from {from_user}")
            elif msg_type == "voice":
                content = "[用户发送了语音]"
                recognition_node = root.find("Recognition")
                if recognition_node is not None and recognition_node.text:
                    content = recognition_node.text
                logger.info(f"[{self.plugin_id}] Voice message from {from_user}: {content}")
            elif msg_type == "event":
                event_node = root.find("Event")
                event = event_node.text if event_node is not None else ""
                if event == "subscribe":
                    content = "[用户关注]"
                elif event == "unsubscribe":
                    content = "[用户取消关注]"
                elif event == "enter_session":
                    content = "[用户进入客服会话]"
                else:
                    content = f"[事件: {event}]"
                logger.info(f"[{self.plugin_id}] Event from {from_user}: {event}")

            # 忽略纯事件（不生成回复），但进入会话可发欢迎语
            if msg_type == "event":
                if content == "[用户进入客服会话]":
                    welcome_msg = "您好！我是智能客服助手，请输入您的问题，我会尽力为您解答。"
                    await self._send_kf_message(from_user, welcome_msg, open_kfid=open_kfid)
                return PlainTextResponse(content="success")

            # 构建统一消息，复用 session_id 以保持上下文
            session_id = self._user_sessions.get(from_user)
            if not session_id:
                session_id = f"wechat_kf:{from_user}:{int(time.time())}"
                self._user_sessions[from_user] = session_id

            unified = UnifiedMessage(
                user_id=f"wechat_kf:{from_user}",
                channel="wechat_kf",
                content=content.strip(),
                raw_content=content,
                metadata={
                    "openid": from_user,
                    "corp_id": to_user,
                    "open_kfid": open_kfid,
                    "msg_type": msg_type,
                },
                session_id=session_id,
                msg_type=msg_type,
            )

            # 调用 ChatPlugin 处理（use_rag=False，由 LoRA 内化知识）
            chat = self.inject("chat_manager")
            if not chat:
                logger.error(f"[{self.plugin_id}] Chat manager not available")
                await self._send_kf_message(from_user, "系统暂时不可用，请稍后重试。", open_kfid=open_kfid)
                return PlainTextResponse(content="success")

            result = await chat.chat(
                session_id=unified.session_id,
                user_message=unified.content,
                use_rag=False,
                user_id=unified.user_id,
                channel="wechat_kf",
            )

            reply_text = result.get("reply", "暂未找到相关信息")

            # 截断超长回复
            reply_text = self._truncate_reply(reply_text)

            # 发送客服消息回复
            await self._send_kf_message(from_user, reply_text, open_kfid=open_kfid)

            # 记录审计日志
            audit = self.inject("audit_logger")
            if audit:
                try:
                    await audit.log(
                        action="wechat_kf_chat",
                        user_id=unified.user_id,
                        query=unified.content,
                        response_summary=reply_text[:500],
                        latency_ms=result.get("tokens", {}).get("latency_ms", 0),
                        channel="wechat_kf",
                    )
                except Exception as e:
                    logger.warning(f"[{self.plugin_id}] Audit log failed: {e}")

            return PlainTextResponse(content="success")

        except ET.ParseError as e:
            logger.error(f"[{self.plugin_id}] XML parse error: {e}")
            return PlainTextResponse(content="success")
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Webhook error: {e}", exc_info=True)
            return PlainTextResponse(content="success")

    # ─── 发送客服消息 ──────────────────────────────────

    async def _send_kf_message(self, openid: str, text: str, open_kfid: str = "") -> bool:
        """
        通过微信客服 API 发送文本回复。
        API: POST https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token=xxx
        """
        access_token = await self._get_access_token()
        if not access_token:
            logger.error(f"[{self.plugin_id}] No access token, cannot reply")
            return False

        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={access_token}"

        payload: dict[str, Any] = {
            "touser": openid,
            "msgtype": "text",
            "text": {"content": text},
        }
        if open_kfid:
            payload["open_kfid"] = open_kfid

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                errcode = data.get("errcode", 0)
                if errcode == 0:
                    logger.info(f"[{self.plugin_id}] Reply sent to {openid}")
                    return True
                elif errcode == 40001:
                    logger.warning(f"[{self.plugin_id}] Token expired, clearing cache")
                    self._access_token = None
                    return False
                elif errcode == 40003:
                    logger.warning(f"[{self.plugin_id}] Invalid openid: {openid}")
                    return False
                elif errcode == 95011:
                    logger.warning(f"[{self.plugin_id}] User not in active session (over 48h)")
                    # 清除会话映射
                    if openid in self._user_sessions:
                        del self._user_sessions[openid]
                    return False
                else:
                    logger.error(
                        f"[{self.plugin_id}] Send failed: "
                        f"errcode={errcode}, errmsg={data.get('errmsg')}"
                    )
                    return False
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Send error: {e}")
            return False

    # ─── Access Token 管理 ─────────────────────────────

    async def _get_access_token(self) -> (str | None):
        """
        获取企业微信 access_token（带缓存）。
        API: GET https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=xxx&corpsecret=xxx
        """
        now = time.time()

        # 检查缓存（提前 60 秒刷新）
        if self._access_token and now < self._token_expire_at - 60:
            return self._access_token

        if not self._corp_id or not self._secret:
            logger.warning(f"[{self.plugin_id}] CorpID/Secret not configured")
            return None

        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
            f"?corpid={self._corp_id}&corpsecret={self._secret}"
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                data = resp.json()

                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expire_at = now + data.get("expires_in", 7200)
                    logger.info(f"[{self.plugin_id}] Access token refreshed")
                    return self._access_token
                else:
                    logger.error(f"[{self.plugin_id}] Token error: {data}")
                    return None
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Token request error: {e}")
            return None

    # ─── 签名验证 ──────────────────────────────────────

    def _verify_signature(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str = ""
    ) -> bool:
        """
        验证微信客服回调签名（SHA1）。
        将 token、timestamp、nonce、[echostr] 排序后拼接做 SHA1。
        """
        if not self._token:
            logger.warning(f"[{self.plugin_id}] No token configured, skipping verify")
            return True  # 开发环境可跳过

        params = [self._token, timestamp, nonce]
        if echostr:
            params.append(echostr)
        params.sort()
        raw = "".join(params)
        # NOTE: SHA1 用于企业微信/微信客服 Webhook 签名验证，
        # 这是微信 API 协议强制要求的算法，非本系统自主选择。
        sha = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return sha == msg_signature

    # ─── 工具方法 ──────────────────────────────────────

    def _xml_text(self, root, tag: str, default: str = "") -> str:
        """从 XML ElementTree root 中提取指定标签的文本内容。"""
        node = root.find(tag)
        return node.text if node is not None and node.text else default

    def _truncate_reply(self, text: str, max_bytes: int = 2000) -> str:
        """截断回复文本至微信客服消息限制内"""
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        truncated = text[:600]
        while len(truncated.encode("utf-8")) > max_bytes:
            truncated = truncated[:-1]
        return truncated + "..."

    # ─── 多客服转接 ──────────────────────────────────────

    async def _transfer_to_human(self, openid: str, open_kfid: str = "") -> bool:
        """
        将对话转接至人工客服。
        """
        access_token = await self._get_access_token()
        if not access_token:
            return False

        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={access_token}"
        payload: dict[str, Any] = {
            "touser": openid,
            "msgtype": "transfer_customer_service",
        }
        if open_kfid:
            payload["open_kfid"] = open_kfid

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    logger.info(f"[{self.plugin_id}] Transferred to human agent: {openid}")
                    if openid in self._user_sessions:
                        del self._user_sessions[openid]
                    return True
                else:
                    logger.error(f"[{self.plugin_id}] Transfer failed: {data}")
                    return False
        except Exception as e:
            logger.error(f"[{self.plugin_id}] Transfer error: {e}")
            return False

    # ─── 状态查询 ──────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """返回渠道状态"""
        return {
            "plugin_id": self.plugin_id,
            "enabled": bool(self._corp_id and self._secret),
            "corp_id_set": bool(self._corp_id),
            "webhook_path": self._webhook_path,
            "active_sessions": len(self._user_sessions),
            "token_cached": bool(self._access_token),
            "token_expires_in": max(0, int(self._token_expire_at - time.time())),
            "aes_enabled": bool(self._crypto),
        }
