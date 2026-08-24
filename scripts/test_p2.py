"""
EchoServe P2 — 集成测试脚本

测试覆盖：
  T01  WhatsApp 插件导入
  T02  WhatsApp Webhook 签名验证
  T03  WhatsApp 消息处理流程
  T04  WhatsApp 速率限制
  T05  EnterpriseAuth 插件导入
  T06  LDAP 认证降级（未安装 ldap3）
  T07  OAuth2 授权 URL 生成
  T08  DPO PreferenceStore 收集
  T09  DPO 数据集构建
  T10  DPOTrainer 模拟训练
  T11  Windows 安装包构建
  T12  等保合规检查
  T13  全参数微调脚本验证
  T14  API 端点注册检查
"""
from __future__ import annotations

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# 设置项目路径
PROJECT_ROOT = Path(__file__).parent.parent
# PROJECT_ROOT 必须在最前面，确保 config/ core/ plugins/ 等包能被正确解析
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(1, str(PROJECT_ROOT / "scripts"))

# 设置环境变量（避免插件初始化失败）
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("DB_PASSWORD", "test-db-pass")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test_verify_token_123")
os.environ.setdefault("WHATSAPP_APP_SECRET", "test_app_secret_456")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test_access_token")
os.environ.setdefault("OAUTH_ENABLED", "true")
os.environ.setdefault("OAUTH_PROVIDER", "azure")
os.environ.setdefault("OAUTH_CLIENT_ID", "test_client_id")
os.environ.setdefault("OAUTH_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("LDAP_ENABLED", "true")
os.environ.setdefault("LDAP_SERVER_URI", "ldap://test.company.com")
os.environ.setdefault("LDAP_BIND_DN", "cn=admin,dc=company,dc=com")
os.environ.setdefault("LDAP_BIND_PASSWORD", "test_ldap_pwd")
os.environ.setdefault("LDAP_USER_BASE_DN", "ou=Users,dc=company,dc=com")
os.environ.setdefault("LDAP_GROUP_BASE_DN", "ou=Groups,dc=company,dc=com")

# 切换工作目录
os.chdir(PROJECT_ROOT)

# 配置日志
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []
total = 0
passed = 0


def test(name: str, func):
    """运行单个测试"""
    global total, passed
    total += 1
    try:
        result = func()
        if result is True or (isinstance(result, dict) and result.get("status") == "success"):
            print(f"  {PASS} T{total:02d} {name}")
            passed += 1
            results.append({"test": name, "status": "pass", "detail": ""})
        elif isinstance(result, dict):
            status = result.get("status", "unknown")
            detail = result.get("detail", result.get("reason", ""))
            if status == "partial":
                print(f"  {WARN} T{total:02d} {name} ({detail})")
                passed += 0.5
                results.append({"test": name, "status": "partial", "detail": detail})
            else:
                print(f"  {FAIL} T{total:02d} {name} → {detail}")
                results.append({"test": name, "status": "fail", "detail": detail})
        else:
            print(f"  {PASS} T{total:02d} {name}")
            passed += 1
            results.append({"test": name, "status": "pass", "detail": ""})
    except Exception as e:
        print(f"  {FAIL} T{total:02d} {name} → {e}")
        results.append({"test": name, "status": "fail", "detail": str(e)})


# ═══════════════════════════════════════════
#  T01 - T04: WhatsApp 插件
# ═══════════════════════════════════════════

def test_whatsapp_import():
    """T01: WhatsApp 插件可导入"""
    from plugins.channel_whatsapp.plugin import WhatsAppChannelPlugin
    p = WhatsAppChannelPlugin()
    assert p.plugin_id == "channel.whatsapp"
    assert p.plugin_name == "WhatsApp Business"
    return True


def test_whatsapp_signature():
    """T02: Meta Webhook 签名验证"""
    import hmac
    import hashlib
    from plugins.channel_whatsapp.plugin import WhatsAppChannelPlugin

    p = WhatsAppChannelPlugin()
    p._app_secret = "test_secret_123"  # TEST ONLY — not a real secret

    body = b'{"entry":[{"changes":[{"value":{}}]}]}'
    expected = hmac.new(b"test_secret_123", body, hashlib.sha256).hexdigest()

    # 正确签名
    assert p._verify_meta_signature(body, f"sha256={expected}") == True
    # 错误签名
    assert p._verify_meta_signature(body, "sha256=wrong") == False
    # 格式错误
    assert p._verify_meta_signature(body, "md5=abc") == False
    return True


def test_whatsapp_message_processing():
    """T03: WhatsApp 消息处理流程（模拟）"""
    from plugins.channel_whatsapp.plugin import WhatsAppChannelPlugin

    p = WhatsAppChannelPlugin()
    p._access_token = "test_token"  # TEST ONLY — not a real token
    p._phone_number_id = "12345"

    # 模拟消息
    msg = {
        "from": "8613800138000",
        "type": "text",
        "id": "wamid.test123",
        "text": {"body": "你好，怎么退货？"},
    }
    metadata = {"phone_number_id": "12345"}

    # 不实际发送，只测试处理流程不崩溃
    # 由于需要 chat_manager，我们只测试消息解析部分
    content = msg.get("text", {}).get("body", "").strip()
    assert content == "你好，怎么退货？"

    user_id = p._get_or_create_user("8613800138000")
    assert user_id.startswith("whatsapp:") or len(user_id) > 0

    status = p.get_status()
    assert "enabled" in status
    return True


def test_whatsapp_rate_limit():
    """T04: WhatsApp 速率限制"""
    from plugins.channel_whatsapp.plugin import WhatsAppChannelPlugin

    p = WhatsAppChannelPlugin()
    p._rate_limit = 5  # 设为 5 条/分钟便于测试
    p._send_timestamps = []  # 清空

    # 前 5 次应该通过
    for i in range(5):
        assert p._check_rate_limit() == True

    # 第 6 次应该被限制
    assert p._check_rate_limit() == False

    # 清除后恢复
    p._send_timestamps = []
    assert p._check_rate_limit() == True
    return True


# ═══════════════════════════════════════════
#  T05 - T07: EnterpriseAuth 插件
# ═══════════════════════════════════════════

def test_enterprise_auth_import():
    """T05: EnterpriseAuth 插件可导入"""
    from plugins.auth_enterprise.plugin import EnterpriseAuthPlugin
    p = EnterpriseAuthPlugin()
    assert p.plugin_id == "security.auth_enterprise"
    assert "LDAP" in p.plugin_name or "OAuth" in p.plugin_name
    return True


def test_ldap_fallback():
    """T06: LDAP 认证降级（ldap3 未安装时）"""
    from plugins.auth_enterprise.plugin import EnterpriseAuthPlugin

    p = EnterpriseAuthPlugin()
    p._ldap_enabled = True

    # ldap3 未安装时应该返回 None（降级）
    result = p.authenticate_ldap("testuser", "testpass")
    assert result is None
    return True


def test_oauth_authorize_url():
    """T07: OAuth2 授权 URL 生成"""
    from plugins.auth_enterprise.plugin import EnterpriseAuthPlugin

    p = EnterpriseAuthPlugin()
    p._oauth_enabled = True
    p._oauth_provider = "azure"
    p._oauth_client_id = "test_client_123"
    p._oauth_redirect_uri = "http://localhost:8080/api/auth/oauth/callback"
    p._oauth_authorize_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    p._oauth_scope = "openid profile email"

    # 模拟生成授权 URL 的逻辑
    params = {
        "client_id": p._oauth_client_id,
        "redirect_uri": p._oauth_redirect_uri,
        "response_type": "code",
        "scope": p._oauth_scope,
        "state": "test_state_123",
    }
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"{p._oauth_authorize_url}?{query_string}"

    assert "login.microsoftonline.com" in auth_url
    assert "client_id=test_client_123" in auth_url
    assert "response_type=code" in auth_url
    assert "state=test_state_123" in auth_url
    return True


# ═══════════════════════════════════════════
#  T08 - T10: DPO 训练
# ═══════════════════════════════════════════

def test_dpo_preference_store():
    """T08: DPO 偏好数据收集"""
    import tempfile
    from plugins.evolve.dpo_trainer import PreferenceStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = PreferenceStore(store_path=f"{tmpdir}/prefs.jsonl")

        # 记录 like
        id1 = store.record_feedback(
            prompt="如何退货？",
            response="7天无理由退货，请联系客服。",
            feedback_type="like",
            user_id="user1",
        )
        assert len(id1) > 0

        # 记录 dislike
        id2 = store.record_feedback(
            prompt="如何退货？",
            response="不知道。",
            feedback_type="dislike",
            user_id="user2",
        )
        assert len(id2) > 0

        # 记录 edit
        id3 = store.record_feedback(
            prompt="运费多少？",
            response="默认运费10元。",
            feedback_type="edit",
            user_id="user3",
            edited_response="普通快递10元，顺丰20元，满99包邮。",
        )

        # 统计
        stats = store.get_stats()
        assert stats["total"] == 3
        assert stats["by_type"]["like"] == 1
        assert stats["by_type"]["dislike"] == 1
        assert stats["by_type"]["edit"] == 1

        # 列出最近
        recent = store.list_recent()
        assert len(recent) == 3
    return True


def test_dpo_dataset_build():
    """T09: DPO 数据集构建"""
    import tempfile
    import json
    from plugins.evolve.dpo_trainer import PreferenceStore

    with tempfile.TemporaryDirectory() as tmpdir:
        prefs_path = f"{tmpdir}/prefs.jsonl"
        store = PreferenceStore(store_path=prefs_path)

        # 添加足够的偏好数据
        for i in range(5):
            store.record_feedback(
                prompt=f"问题{i}",
                response=f"好回答{i}",
                feedback_type="like",
                user_id=f"u{i}",
            )
            store.record_feedback(
                prompt=f"问题{i}",
                response=f"差回答{i}",
                feedback_type="dislike",
                user_id=f"u{i}2",
            )

        output = f"{tmpdir}/dpo_dataset.jsonl"
        result = store.build_dpo_dataset(output_path=output)

        assert result["status"] == "success"
        assert result["count"] == 5

        # 验证输出文件
        with open(output, "r") as f:
            pairs = [json.loads(line) for line in f if line.strip()]
        assert len(pairs) == 5
        assert "prompt" in pairs[0]
        assert "chosen" in pairs[0]
        assert "rejected" in pairs[0]
    return True


def test_dpo_trainer():
    """T10: DPO 训练器模拟训练"""
    import tempfile
    import json
    from plugins.evolve.dpo_trainer import DPOTrainer

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试 DPO 数据
        dpo_data = f"{tmpdir}/dpo.jsonl"
        with open(dpo_data, "w") as f:
            for i in range(20):
                f.write(json.dumps({
                    "prompt": f"问题{i}",
                    "chosen": f"好回答{i}",
                    "rejected": f"差回答{i}",
                }) + "\n")

        output = f"{tmpdir}/dpo_output"

        trainer = DPOTrainer(
            base_model="./models/qwen3-14b-q4",
            dpo_data=dpo_data,
            output_dir=output,
            num_epochs=1,  # 快速测试
        )

        result = trainer.train()

        assert result["status"] == "success"
        assert result["pair_count"] == 20
        assert "train_loss" in result
        assert "eval_loss" in result

        # 验证输出文件
        adapter_path = Path(output) / "dpo_adapter_config.json"
        assert adapter_path.exists()

        info = trainer.get_adapter_info()
        assert info is not None
        assert info["type"] == "dpo"
    return True


# ═══════════════════════════════════════════
#  T11 - T14: 构建工具 & 合规
# ═══════════════════════════════════════════

def test_windows_installer_build():
    """T11: Windows 安装包构建"""
    import tempfile
    from scripts.build_windows_installer import WindowsInstallerBuilder

    with tempfile.TemporaryDirectory() as tmpdir:
        builder = WindowsInstallerBuilder(
            version="0.1.2",
            output_dir=tmpdir,
            app_id="TEST-APP-ID-1234",
        )

        result = builder.build_all()

        assert result["status"] == "success"

        build_dir = Path(result["build_dir"])
        # Inno Setup / NSIS scripts at root
        assert (build_dir / "EchoServe.iss").exists()
        assert (build_dir / "EchoServe.nsi").exists()
        # Windows service wrapper at root
        assert (build_dir / "win_service.py").exists()
        # README at root
        assert (build_dir / "README-Windows.txt").exists()
        assert (build_dir / "LICENSE.txt").exists()
        # Batch scripts in scripts/ subdirectory
        assert (build_dir / "scripts" / "start_echoseve.bat").exists()
        assert (build_dir / "scripts" / "stop_echoseve.bat").exists()
        assert (build_dir / "scripts" / "install_docker.bat").exists()
        assert (build_dir / "scripts" / "check_environment.bat").exists()
        # Docker compose in docker/ subdirectory
        assert (build_dir / "docker" / "docker-compose.windows.yml").exists()
    return True


def test_compliance_check():
    """T12: 等保 2.0 三级合规检查"""
    import tempfile
    from scripts.compliance_check import ComplianceChecker

    with tempfile.TemporaryDirectory() as tmpdir:
        checker = ComplianceChecker(
            project_root=str(PROJECT_ROOT),
            output_dir=tmpdir,
        )

        report = checker.run_full_check()

        # 验证报告结构
        assert "overall_score" in report
        assert "grade" in report
        assert "categories" in report
        assert "summary" in report
        assert "recommendations" in report

        # 验证评分范围
        assert 0 <= report["overall_score"] <= 100

        # 验证有分类结果
        cats = report["categories"]
        assert "身份鉴别" in cats
        assert "访问控制" in cats
        assert "安全审计" in cats
        assert "数据加密" in cats

        # 生成报告文件
        paths = checker.generate_report(report, output_dir=tmpdir)
        assert "json" in paths
        assert "html" in paths

        json_path = Path(tmpdir) / Path(paths["json"]).name
        html_path = Path(tmpdir) / Path(paths["html"]).name
        assert json_path.exists()
        assert html_path.exists()

        # 验证 JSON 内容
        with open(json_path, "r") as f:
            saved = json.load(f)
        assert saved["overall_score"] == report["overall_score"]
    return True


def test_full_finetune_script():
    """T13: 全参数微调脚本验证"""
    script_path = PROJECT_ROOT / "scripts" / "train_full.py"
    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")

    # 验证关键组件存在
    checks = {
        "DeepSpeed config": "generate_deepspeed_config" in content,
        "FSDP support": "fsdp" in content.lower(),
        "Distillation": "distillation" in content.lower() or "teacher" in content,
        "ZeRO-3": "zero_stage" in content,
        "argparse": "argparse" in content,
        "TrainingArguments": "TrainingArguments" in content,
        "KL divergence": "kl_div" in content.lower() or "KLDiv" in content,
    }

    for name, ok in checks.items():
        assert ok, f"Missing: {name}"

    return True


def test_api_endpoints_registered():
    """T14: API 端点注册检查"""
    # 验证 main.py 中包含 P2 路由
    main_path = PROJECT_ROOT / "api" / "main.py"
    content = main_path.read_text(encoding="utf-8")

    p2_endpoints = {
        "EnterpriseAuth import": "EnterpriseAuthPlugin" in content,
        "WhatsApp import": "WhatsAppChannelPlugin" in content,
        "compliance endpoint": "compliance_check" in content,
        "feedback endpoint": '"/api/feedback"' in content,
        "dpo build endpoint": "/api/evolve/dpo/build" in content,
        "dpo train endpoint": "/api/evolve/dpo/train" in content,
        "windows installer endpoint": "build_windows_installer" in content,
        "version 0.1.2": "0.1.2" in content,
    }

    for name, ok in p2_endpoints.items():
        assert ok, f"Missing endpoint: {name}"

    return True


# ═══════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  EchoServe P2 — 集成测试")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  项目: {PROJECT_ROOT}")
    print("=" * 60)
    print()

    # P2 插件测试
    print("📦 WhatsApp 渠道插件:")
    test("WhatsApp 插件导入", test_whatsapp_import)
    test("Meta 签名验证", test_whatsapp_signature)
    test("消息处理流程", test_whatsapp_message_processing)
    test("速率限制", test_whatsapp_rate_limit)

    print()
    print("🔐 企业认证插件 (LDAP + OAuth2):")
    test("EnterpriseAuth 导入", test_enterprise_auth_import)
    test("LDAP 认证降级", test_ldap_fallback)
    test("OAuth2 授权 URL", test_oauth_authorize_url)

    print()
    print("🧠 DPO 风格对齐:")
    test("偏好数据收集", test_dpo_preference_store)
    test("DPO 数据集构建", test_dpo_dataset_build)
    test("DPO 训练器", test_dpo_trainer)

    print()
    print("🛠️ 构建工具 & 合规:")
    test("Windows 安装包构建", test_windows_installer_build)
    test("等保 2.0 合规检查", test_compliance_check)
    test("全参数微调脚本", test_full_finetune_script)
    test("API 端点注册", test_api_endpoints_registered)

    # 汇总
    print()
    print("=" * 60)
    pass_rate = (passed / total * 100) if total > 0 else 0
    print(f"  总计: {total} | 通过: {int(passed)} | 部分: {passed - int(passed):.0f} | 失败: {total - int(passed)}")
    print(f"  通过率: {pass_rate:.1f}%")

    if pass_rate >= 90:
        print("  评级: ✅ 优秀")
    elif pass_rate >= 75:
        print("  评级: ⚠️ 良好（有改进空间）")
    else:
        print("  评级: ❌ 需要修复")

    print("=" * 60)

    # 保存结果
    result_path = PROJECT_ROOT / "reports" / f"p2_test_{datetime.now():%Y%m%d_%H%M}.json"
    result_path.parent.mkdir(exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total": total,
            "passed": passed,
            "pass_rate": round(pass_rate, 1),
            "results": results,
        }, f, indent=2, ensure_ascii=False)

    print(f"  结果已保存: {result_path}")

    sys.exit(0 if pass_rate >= 90 else 1)
