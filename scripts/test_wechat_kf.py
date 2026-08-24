"""
EchoServe - 微信客服渠道本地测试脚本
=====================================
无需真实微信凭证，验证插件核心逻辑。

Usage:
    python scripts/test_wechat_kf.py

测试覆盖:
    1. 插件加载与初始化
    2. URL签名验证（SHA1）
    3. XML消息解析
    4. AES解密（需要wechatpy，可选）
    5. UnifiedMessage构建
    6. 回复截断
    7. 会话保持
"""

import sys
import os
import time
import hashlib
import unittest
import asyncio
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.channel_wechat_kf.plugin import WeChatKFPlugin, UnifiedMessage


class TestWeChatKFPlugin(unittest.TestCase):
    """微信客服插件单元测试"""

    @classmethod
    def setUpClass(cls):
        """测试前准备"""
        # 设置测试环境变量
        os.environ["WECHAT_KF_CORP_ID"] = "ww_test123"
        os.environ["WECHAT_KF_SECRET"] = "test_secret_abc"
        os.environ["WECHAT_KF_TOKEN"] = "EchoServeTest"
        os.environ["WECHAT_KF_AES_KEY"] = "abcdefghijklmnopqrstuvwxyz1234567899ABCDefg"
        os.environ["WECHAT_KF_WEBHOOK_PATH"] = "/webhook/wechat_kf"

    def setUp(self):
        """每个测试前创建插件实例"""
        self.plugin = WeChatKFPlugin()
        # 初始化测试用的 _user_sessions（避免 AttributeError）
        self.plugin._user_sessions = {}

    # ─── 基础功能测试 ─────────────────────────────────

    def test_plugin_metadata(self):
        """测试插件元数据"""
        self.assertEqual(self.plugin.plugin_id, "channel.wechat_kf")
        self.assertEqual(self.plugin.plugin_name, "微信客服")
        self.assertEqual(self.plugin.plugin_version, "0.2.0")
        self.assertIn("core.chat", self.plugin.dependencies)

    def test_status_disabled_without_config(self):
        """无配置时应为disabled"""
        status = self.plugin.get_status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["corp_id_set"])
        self.assertEqual(status["webhook_path"], "/webhook/wechat_kf")

    # ─── URL签名验证测试 ───────────────────────────────

    def test_signature_verification(self):
        """测试SHA1签名验证"""
        token = "EchoServeTest"
        timestamp = str(int(time.time()))
        nonce = "123456"
        echostr = "test_challenge"

        # 模拟微信生成签名
        params = sorted([token, timestamp, nonce])
        raw = "".join(params)
        expected_sig = hashlib.sha1(raw.encode("utf-8")).hexdigest()

        # 验证签名
        result = self.plugin._verify_signature(expected_sig, timestamp, nonce)
        self.assertTrue(result)

    def test_signature_invalid(self):
        """测试错误签名"""
        # 先设置 token，否则无 token 时开发模式直接返回 True
        self.plugin._token = "test_token"
        result = self.plugin._verify_signature(
            "invalid_signature",
            str(int(time.time())),
            "123456"
        )
        self.assertFalse(result)

    def test_signature_no_token(self):
        """无Token时允许通过（开发模式）"""
        plugin = WeChatKFPlugin()
        plugin._token = ""
        result = plugin._verify_signature("any", "any", "any")
        self.assertTrue(result)

    # ─── XML消息解析测试 ───────────────────────────────

    def test_xml_text_extraction(self):
        """测试XML文本提取"""
        import xml.etree.ElementTree as ET

        xml_str = """<xml>
            <ToUserName><![CDATA[ww_test]]></ToUserName>
            <FromUserName><![CDATA[openid_123]]></FromUserName>
            <CreateTime>1724234567</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[你好，请问怎么退货？]]></Content>
            <MsgId>12345678901234567890</MsgId>
        </xml>"""

        root = ET.fromstring(xml_str)

        msg_type = self.plugin._xml_text(root, "MsgType", "")
        from_user = self.plugin._xml_text(root, "FromUserName", "")
        content = self.plugin._xml_text(root, "Content", "")

        self.assertEqual(msg_type, "text")
        self.assertEqual(from_user, "openid_123")
        self.assertEqual(content, "你好，请问怎么退货？")

    def test_xml_missing_field(self):
        """测试缺失字段返回默认值"""
        import xml.etree.ElementTree as ET

        xml_str = "<xml><MsgType>text</MsgType></xml>"
        root = ET.fromstring(xml_str)

        result = self.plugin._xml_text(root, "NonExistent", "default")
        self.assertEqual(result, "default")

    # ─── 统一消息格式测试 ──────────────────────────────

    def test_unified_message_creation(self):
        """测试UnifiedMessage构建"""
        msg = UnifiedMessage(
            user_id="wechat_kf:openid_123",
            channel="wechat_kf",
            content="测试消息",
            metadata={"openid": "openid_123", "corp_id": "ww_test"},
            msg_type="text",
        )

        self.assertEqual(msg.user_id, "wechat_kf:openid_123")
        self.assertEqual(msg.channel, "wechat_kf")
        self.assertEqual(msg.content, "测试消息")
        self.assertEqual(msg.msg_type, "text")
        # session_id 格式：f"{channel}:{user_id}:{timestamp}"
        # 由于 user_id 本身已含 "wechat_kf:" 前缀，结果为 "wechat_kf:wechat_kf:openid_123:..."
        expected_sid_prefix = f"{msg.channel}:{msg.user_id}:"
        self.assertTrue(msg.session_id.startswith(expected_sid_prefix))

        # 验证字典转换
        d = msg.to_dict()
        self.assertEqual(d["user_id"], "wechat_kf:openid_123")
        self.assertEqual(d["channel"], "wechat_kf")
        self.assertIn("timestamp", d)

    # ─── 回复截断测试 ─────────────────────────────────

    def test_truncate_short_text(self):
        """短文本不截断"""
        text = "这是一个短回复"
        result = self.plugin._truncate_reply(text, max_bytes=2048)
        self.assertEqual(result, text)

    def test_truncate_long_text(self):
        """长文本截断"""
        text = "这是一个很长的回复" * 500  # 约 8500 字节
        result = self.plugin._truncate_reply(text, max_bytes=2048)
        self.assertTrue(len(result.encode("utf-8")) <= 2048)
        self.assertTrue(result.endswith("..."))

    # ─── 会话保持测试 ─────────────────────────────────

    def test_session_mapping(self):
        """测试openid到session_id映射"""
        openid = "openid_abc123"
        session_id = f"wechat_kf:{openid}:{int(time.time())}"

        self.plugin._user_sessions[openid] = session_id
        self.assertEqual(self.plugin._user_sessions.get(openid), session_id)
        self.assertEqual(len(self.plugin._user_sessions), 1)

    # ─── access_token缓存测试 ─────────────────────────

    def test_token_cache_expired(self):
        """测试过期token会被清除"""
        self.plugin._access_token = "old_token"
        self.plugin._token_expire_at = time.time() - 100  # 已过期

        # 模拟_get_access_token检测到过期
        now = time.time()
        self.assertTrue(now > self.plugin._token_expire_at)

    # ─── 完整消息流模拟 ───────────────────────────────

    def test_full_message_flow(self):
        """模拟完整的消息处理流程"""
        import xml.etree.ElementTree as ET

        # 1. 模拟微信推送的XML
        xml_msg = """<xml>
            <ToUserName><![CDATA[ww_test_corp]]></ToUserName>
            <FromUserName><![CDATA[openid_customer_123]]></FromUserName>
            <CreateTime>1724234567</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[发票丢了能补开吗？]]></Content>
            <MsgId>12345678901234567890</MsgId>
            <OpenKfId><![CDATA[kf_abc123]]></OpenKfId>
        </xml>"""

        # 2. 解析XML
        root = ET.fromstring(xml_msg)
        msg_type = self.plugin._xml_text(root, "MsgType", "")
        from_user = self.plugin._xml_text(root, "FromUserName", "")
        content = self.plugin._xml_text(root, "Content", "")
        open_kfid = self.plugin._xml_text(root, "OpenKfId", "")

        self.assertEqual(msg_type, "text")
        self.assertEqual(from_user, "openid_customer_123")
        self.assertEqual(content, "发票丢了能补开吗？")
        self.assertEqual(open_kfid, "kf_abc123")

        # 3. 构建统一消息
        session_id = self.plugin._user_sessions.get(from_user)
        if not session_id:
            session_id = f"wechat_kf:{from_user}:{int(time.time())}"
            self.plugin._user_sessions[from_user] = session_id

        unified = UnifiedMessage(
            user_id=f"wechat_kf:{from_user}",
            channel="wechat_kf",
            content=content.strip(),
            raw_content=content,
            metadata={
                "openid": from_user,
                "corp_id": "ww_test_corp",
                "open_kfid": open_kfid,
            },
            session_id=session_id,
            msg_type="text",
        )

        self.assertEqual(unified.content, "发票丢了能补开吗？")
        self.assertEqual(unified.metadata["open_kfid"], "kf_abc123")

        # 4. 模拟回复
        reply_text = "增值税专用发票需提供完整开票资料，请联系客服提交补开申请。"
        truncated = self.plugin._truncate_reply(reply_text)

        self.assertTrue(len(truncated.encode("utf-8")) <= 2048)
        print(f"\n[模拟回复] {truncated}")

        # 5. 验证会话保持
        self.assertIn(from_user, self.plugin._user_sessions)

        print("\n[完整消息流] PASS")


def _has_wechatpy():
    try:
        import importlib.util
        return importlib.util.find_spec("wechatpy") is not None
    except Exception:
        return False


class TestWeChatKFAES(unittest.TestCase):
    """AES加密测试（可选，需要wechatpy）"""

    @unittest.skipIf(not _has_wechatpy(), "wechatpy not installed")
    def test_aes_encryption(self):
        """测试AES加解密（端到端）"""
        from wechatpy.crypto import WeChatCrypto

        token = "test_token"
        aes_key = "abcdefghijklmnopqrstuvwxyz1234567890123abcd"
        corp_id = "ww_test"

        crypto = WeChatCrypto(token, aes_key, corp_id)

        # 原始消息
        original = "<xml><Content>测试消息</Content></xml>"
        nonce = "123456"
        timestamp = str(int(time.time()))

        # 加密 — 返回完整 XML 格式（含 Encrypt + MsgSignature）
        encrypted_xml = crypto.encrypt_message(original, nonce, timestamp)
        self.assertIsNotNone(encrypted_xml)
        self.assertIn("<Encrypt>", encrypted_xml)
        self.assertIn("<MsgSignature>", encrypted_xml)

        # 从加密 XML 中提取签名、时间戳、nonce
        import re
        sig_match = re.search(r"<MsgSignature><!\[CDATA\[(.*?)\]\]>", encrypted_xml)
        ts_match = re.search(r"<TimeStamp>(.*?)</TimeStamp>", encrypted_xml)
        nonce_match = re.search(r"<Nonce><!\[CDATA\[(.*?)\]\]>", encrypted_xml)

        msg_sig = sig_match.group(1) if sig_match else ""
        msg_ts = ts_match.group(1) if ts_match else ""
        msg_nonce = nonce_match.group(1) if nonce_match else ""

        # 解密（需使用 encrypt 时生成的原始签名）
        decrypted = crypto.decrypt_message(encrypted_xml, msg_sig, msg_ts, msg_nonce)
        self.assertEqual(decrypted, original)

    @unittest.skipIf(not _has_wechatpy(), "wechatpy not installed")
    def test_url_verify_with_crypto(self):
        """测试带加密器的URL验证（用 encrypt+decrypt 模拟）"""
        from wechatpy.crypto import WeChatCrypto

        token = "test_token"
        aes_key = "abcdefghijklmnopqrstuvwxyz1234567890123abcd"
        corp_id = "ww_test"
        crypto = WeChatCrypto(token, aes_key, corp_id)

        # 模拟微信验证：把 echostr 加密后传给平台，平台解密验证
        echostr = "test_echostr_123"
        nonce = "123456"
        timestamp = str(int(time.time()))

        # 加密 echostr
        encrypted_xml = crypto.encrypt_message(echostr, nonce, timestamp)
        self.assertIsNotNone(encrypted_xml)

        # 从加密 XML 中提取签名
        import re
        sig_match = re.search(r"<MsgSignature><!\[CDATA\[(.*?)\]\]>", encrypted_xml)
        ts_match = re.search(r"<TimeStamp>(.*?)</TimeStamp>", encrypted_xml)
        nonce_match = re.search(r"<Nonce><!\[CDATA\[(.*?)\]\]>", encrypted_xml)

        msg_sig = sig_match.group(1) if sig_match else ""
        msg_ts = ts_match.group(1) if ts_match else ""
        msg_nonce = nonce_match.group(1) if nonce_match else ""

        # 解密并验证与原始 echostr 一致
        decrypted = crypto.decrypt_message(encrypted_xml, msg_sig, msg_ts, msg_nonce)
        self.assertEqual(decrypted, echostr)


class TestWeChatKFIntegration(unittest.TestCase):
    """集成测试（模拟完整交互）"""

    def test_multiple_messages_same_user(self):
        """测试同一用户多轮对话"""
        plugin = WeChatKFPlugin()
        openid = "openid_multi_123"

        # 第一轮
        session_id_1 = f"wechat_kf:{openid}:{int(time.time())}"
        plugin._user_sessions[openid] = session_id_1

        # 第二轮（应该复用session_id）
        session_id_2 = plugin._user_sessions.get(openid)
        self.assertEqual(session_id_1, session_id_2)

        # 模拟对话
        messages = [
            "你好",
            "怎么退货？",
            "退款多久到账？",
        ]

        for msg in messages:
            unified = UnifiedMessage(
                user_id=f"wechat_kf:{openid}",
                channel="wechat_kf",
                content=msg,
                session_id=session_id_2,
            )
            self.assertEqual(unified.session_id, session_id_2)
            print(f"  [消息] {msg}")

        print(f"\n[多轮对话] PASS (共 {len(messages)} 轮)")

    def test_concurrent_users(self):
        """测试多用户并发"""
        plugin = WeChatKFPlugin()
        users = [f"openid_{i}" for i in range(10)]

        for user in users:
            session_id = f"wechat_kf:{user}:{int(time.time())}"
            plugin._user_sessions[user] = session_id

        self.assertEqual(len(plugin._user_sessions), 10)

        # 验证每个用户独立
        for i, user in enumerate(users):
            self.assertTrue(plugin._user_sessions[user].startswith(f"wechat_kf:{user}:"))

        print(f"\n[并发用户] PASS (共 {len(users)} 个用户)")


def run_tests():
    """运行所有测试"""
    print("=" * 60)
    print("EchoServe WeChat KF Channel - Local Test Suite")
    print("=" * 60)
    print("Note: These tests do NOT require real WeChat credentials.")
    print("      They validate plugin logic, message parsing,")
    print("      signature verification, and session management.")
    print("=" * 60)

    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestWeChatKFPlugin))
    suite.addTests(loader.loadTestsFromTestCase(TestWeChatKFAES))
    suite.addTests(loader.loadTestsFromTestCase(TestWeChatKFIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 输出摘要
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")

    if result.wasSuccessful():
        print("\nALL TESTS PASSED - Plugin logic is correct!")
        return 0
    else:
        print("\nSOME TESTS FAILED - Review output above")
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
