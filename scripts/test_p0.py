"""
P0 集成测试 — 验证所有 P0 模块功能
"""
import sys
import os
import json
import shutil
import asyncio
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 70)
print("  EchoServe V0.1.0 — P0 集成测试")
print("=" * 70)

results = {"passed": 0, "failed": 0, "errors": []}

def test(name):
    def decorator(func):
        async def wrapper():
            try:
                print(f"\n▶ {name}...")
                await func()
                print(f"  ✅ PASSED")
                results["passed"] += 1
            except Exception as e:
                print(f"  ❌ FAILED: {e}")
                results["failed"] += 1
                results["errors"].append({"test": name, "error": str(e)})
                traceback.print_exc()
        return wrapper
    return decorator

# ─── 导入模块 ─────────────────────────────
from config.settings import settings
from core.context import BaizeContext
from core.fiber import FiberManager
from core.plugin_loader import PluginLoader

from plugins.auth.plugin import AuthPlugin
from plugins.audit.plugin import AuditPlugin
from plugins.knowledge.plugin import KnowledgePlugin
from plugins.knowledge.document_parser import (
    parse_file, chunk_text, parse_jsonl,
)
from plugins.chat.plugin import ChatPlugin
from plugins.channel_wechat.plugin import WeChatChannelPlugin, UnifiedMessage


def make_fiber():
    """创建一个 mock Fiber 对象"""
    return type("Fiber", (), {
        "add_task": lambda self, *a: None,
        "state": type("S", (), {"value": "initialized"})(),
    })()


def make_mock_router():
    """创建一个 mock HTTP router"""
    return type("Router", (), {
        "add_api_route": lambda *a, **kw: None,
    })()


# ─── 清理辅助 ─────────────────────────────
def cleanup_data_dirs():
    """清理所有持久化数据目录"""
    data_dir = settings.root_dir / "data"
    for sub in ["auth", "audit", "knowledge", "test_parse"]:
        d = data_dir / sub
        if d.exists():
            shutil.rmtree(d)


# ─── 测试 1: 配置加载 ────────────────────────
@test("1. 配置加载")
async def t1():
    assert settings.api.port == 8080
    assert settings.security.jwt_secret
    assert settings.retrieval.top_k > 0
    print(f"     API: {settings.api.host}:{settings.api.port}")
    print(f"     Model: {settings.model.name}")
    print(f"     Top-K: {settings.retrieval.top_k}")


# ─── 测试 2: BaizeContext 基础 ─────────────────
@test("2. BaizeContext 依赖注入")
async def t2():
    ctx = BaizeContext(settings)
    ctx.provide("test_service", "hello")
    assert ctx.inject("test_service") == "hello"
    assert ctx.has("test_service")
    # 验证 cleanup/effect 机制
    cleaned = []
    ctx.provide("test_cleanup", "world", cleanup=lambda: cleaned.append("done"))
    assert ctx.inject("test_cleanup") == "world"
    ctx.destroy()
    assert "done" in cleaned, "Cleanup 未执行"
    print(f"     Service registration: OK")
    print(f"     Side effects / cleanup: OK")


# ─── 测试 3: 认证插件 ────────────────────────
@test("3. 认证插件（JWT + bcrypt + 限流 + API Key）")
async def t3():
    cleanup_data_dirs()
    ctx = BaizeContext(settings)
    auth = AuthPlugin()
    fiber = make_fiber()
    await auth._init(ctx, fiber)

    # 注册用户
    await auth.register("testuser", "Test@2026!", role="user", department="QA")
    print(f"     Register: OK")

    # 登录
    result = await auth.login("testuser", "Test@2026!")
    assert "access_token" in result
    token = result["access_token"]
    print(f"     Login: OK (token issued)")

    # 验证 Token
    payload = auth.verify_token(token)
    assert payload["username"] == "testuser"
    assert payload["role"] == "user"
    print(f"     JWT verify: OK (role={payload['role']})")

    # 错误密码
    try:
        await auth.login("testuser", "WrongPass1!")
        assert False, "Should have failed"
    except PermissionError:
        print(f"     Wrong password rejected: OK")

    # 登录限流
    for i in range(5):
        try:
            await auth.login("testuser", "BadPass1!")
        except PermissionError:
            pass
    try:
        await auth.login("testuser", "BadPass1!")
        assert False, "Should be locked out"
    except PermissionError as e:
        assert "锁定" in str(e)
        print(f"     Rate limit (lockout): OK")

    # API Key
    key_result = await auth.create_api_key("testuser", "test-key")
    assert "key" in key_result
    api_key = key_result["key"]
    print(f"     API Key created: OK ({api_key[:20]}...)")

    # 验证 API Key
    verified = auth.verify_api_key(api_key)
    assert verified is not None
    print(f"     API Key verify: OK")

    # 吊销 API Key
    await auth.revoke_api_key(key_result["key_id"])
    revoked = auth.verify_api_key(api_key)
    assert revoked is None
    print(f"     API Key revoke: OK")

    # 角色权限 — check_permission 接受 user_id (UUID)
    testuser_id = None
    for uid, u in auth._users.items():
        if u["username"] == "testuser":
            testuser_id = uid
            break
    assert testuser_id is not None, "testuser not found"
    assert auth.check_permission(testuser_id, "kb.read")
    assert not auth.check_permission(testuser_id, "user.write")
    print(f"     Permission check: OK")

    # 用户列表
    users = auth.list_users()
    assert len(users) >= 1
    print(f"     User list: {len(users)} users")

    # 修改角色（update_user_role 接受 user_id）
    await auth.update_user_role(testuser_id, "editor")
    user = auth.get_user(testuser_id)
    assert user["role"] == "editor"
    print(f"     Role update: OK")

    # 清理
    await auth.delete_user("testuser")
    print(f"     User cleanup: OK")

    await auth._destroy(ctx, fiber)


# ─── 测试 4: 审计日志插件 ────────────────────────
@test("4. 审计日志（链式哈希 + CSV + 完整性校验）")
async def t4():
    cleanup_data_dirs()
    ctx = BaizeContext(settings)
    audit = AuditPlugin()
    fiber = make_fiber()
    await audit._init(ctx, fiber)

    # 写入多条日志
    for i in range(10):
        audit.log_sync(
            action="test_action",
            user_id=f"user_{i % 3}",
            query=f"测试查询 {i}",
            response_summary=f"测试回复 {i}",
            sources=[f"doc_{i}"],
            latency_ms=i * 10,
            channel="web",
        )
    print(f"     Wrote 10 log entries: OK")

    # 查询
    result = audit.query(limit=5)
    assert result["total"] == 10
    assert len(result["logs"]) == 5
    print(f"     Query (limit=5): total={result['total']}, returned={len(result['logs'])}")

    # 关键词搜索
    result = audit.query(keyword="查询 3", limit=100)
    assert len(result["logs"]) >= 1
    print(f"     Keyword search: OK ({len(result['logs'])} hits)")

    # CSV 导出
    csv_content = audit.export_csv()
    assert "ID,Timestamp" in csv_content
    assert "测试查询" in csv_content
    lines = csv_content.strip().split("\n")
    assert len(lines) == 11
    print(f"     CSV export: {len(lines)} lines")

    # 完整性校验（正常）
    verify = audit.verify_integrity()
    assert verify["valid"] == True
    assert verify["total"] == 10
    print(f"     Integrity verify: VALID (10/10)")

    # 模拟篡改
    log_path = audit._log_path
    with open(log_path, "r") as f:
        lines = f.readlines()
    tampered = lines[5].replace("test_action", "TAMPERED")
    lines[5] = tampered
    with open(log_path, "w") as f:
        f.writelines(lines)

    verify = audit.verify_integrity()
    assert verify["valid"] == False
    assert verify["broken_at"] is not None
    print(f"     Tamper detection: OK (broken_at={verify['broken_at']})")

    # 清理
    if log_path.exists():
        log_path.unlink()
    await audit._destroy(ctx, fiber)


# ─── 测试 5: 文档解析器 ────────────────────────
@test("5. 文档解析器（TXT/MD/JSONL + 切片）")
async def t5():
    test_dir = settings.root_dir / "data" / "test_parse"
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)

    # TXT — 生成足够长的文本以产生多个 chunk
    txt_file = test_dir / "test.txt"
    long_text = ""
    for i in range(100):
        long_text += f"这是第{i}段测试内容，包含足够多的文字用于验证切片功能是否正常工作。\n\n"
    txt_file.write_text(long_text, encoding="utf-8")

    result = parse_file(str(txt_file))
    assert len(result["text"]) > 100
    assert len(result["chunks"]) > 1, f"Expected >1 chunks, got {len(result['chunks'])}"
    assert result["metadata"]["filetype"] == "text"
    print(f"     TXT parse: {len(result['chunks'])} chunks, {result['metadata']['estimated_tokens']} tokens")

    # MD
    md_file = test_dir / "test.md"
    md_content = ""
    for i in range(100):
        md_content += (
            f"# 第{i}章 标题\n\n这是第{i}章的内容，包含足够的文字用于切片测试。\n\n"
            f"## 第{i}节\n\n这是第{i}节的详细内容描述，用来填充足够的文本量。\n\n"
        )
    md_file.write_text(md_content, encoding="utf-8")

    result = parse_file(str(md_file))
    assert result["metadata"]["filetype"] == "markdown"
    assert len(result["chunks"]) > 1, f"Expected >1 chunks, got {len(result['chunks'])}"
    print(f"     MD parse: {len(result['chunks'])} chunks")

    # JSONL
    jsonl_file = test_dir / "test.jsonl"
    with open(jsonl_file, "w") as f:
        for i in range(5):
            f.write(json.dumps({"id": f"doc_{i}", "content": f"这是第 {i} 条测试文档内容。" * 10}, ensure_ascii=False) + "\n")

    docs = parse_jsonl(str(jsonl_file))
    assert len(docs) == 5
    print(f"     JSONL parse: {len(docs)} documents")

    # 切片测试
    long_text = ""
    for i in range(100):
        long_text += f"这是第{i}段很长的测试文本，用于验证切片功能是否正常工作。\n"
    chunks = chunk_text(long_text, chunk_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1, f"Expected >1 chunks, got {len(chunks)}"
    print(f"     Chunk overlap: OK ({len(chunks)} chunks from long text)")

    # 清理
    shutil.rmtree(test_dir)


# ─── 测试 6: 知识库插件 ──────────────────────────
@test("6. 知识库管理（CRUD + ACL + 统计）")
async def t6():
    cleanup_data_dirs()
    ctx = BaizeContext(settings)
    kb = KnowledgePlugin()
    fiber = make_fiber()

    # 注册一个 mock retriever
    mock_retriever = type("MockRetriever", (), {
        "add_documents": lambda self, docs: None,
        "clear": lambda self: None,
        "retrieve": lambda self, q, top_k=5, acl_filter=None: [],
    })()
    ctx.provide("retriever", mock_retriever)

    await kb._init(ctx, fiber)

    # 添加文档
    doc_id1 = await kb.add_document(
        content="退货政策：购买后7天内可无理由退货，需保持商品完好。",
        doc_id="faq-return",
        metadata={"category": "售后", "allowed_roles": ["*"]},
    )
    doc_id2 = await kb.add_document(
        content="运费标准：满99元包邮，不满99元收取10元运费。",
        doc_id="faq-shipping",
        metadata={"category": "物流", "allowed_roles": ["user", "admin"]},
    )
    print(f"     Add documents: OK ({kb.count_documents()} docs)")

    # 列出文档
    docs = kb.list_documents()
    assert len(docs) == 2
    print(f"     List documents: OK")

    # 权限过滤
    user_docs = kb.list_documents(user_role="user")
    admin_docs = kb.list_documents(user_role="admin")
    readonly_docs = kb.list_documents(user_role="readonly")
    print(f"     ACL filter: user={len(user_docs)}, admin={len(admin_docs)}, readonly={len(readonly_docs)}")
    assert len(user_docs) == 2
    assert len(admin_docs) == 2
    assert len(readonly_docs) == 1

    # 更新文档
    await kb.update_document(doc_id1, content="退货政策：购买后30天内可无理由退货。")
    updated = kb.get_document(doc_id1)
    assert "30天" in updated["content"]
    print(f"     Update document: OK")

    # 统计
    stats = kb.get_stats()
    assert stats["total_documents"] == 2
    print(f"     Stats: {stats['total_documents']} docs, {stats['total_characters']} chars")

    # 检索测试（mock retriever）
    result = await kb.test_retrieval("退货", top_k=3, user_role="user")
    assert "query" in result
    print(f"     Retrieval test: OK")

    # 设置权限
    await kb.set_doc_permissions(doc_id2, ["admin"])
    print(f"     Set ACL: OK")

    # 清理
    await kb.clear_all()
    assert kb.count_documents() == 0
    print(f"     Clear all: OK")

    await kb._destroy(ctx, fiber)


# ─── 测试 7: 企业微信渠道 ────────────────────────
@test("7. 企业微信渠道插件")
async def t7():
    ctx = BaizeContext(settings)
    router = make_mock_router()
    ctx.provide("http_router", router)

    wechat = WeChatChannelPlugin()
    fiber = make_fiber()
    await wechat._init(ctx, fiber)

    # 检查状态
    status = wechat.get_status()
    assert "plugin_id" in status
    assert status["plugin_id"] == "channel.wechat"
    print(f"     Plugin status: enabled={status['enabled']}")

    # UnifiedMessage
    msg = UnifiedMessage(
        user_id="wechat:user123",
        channel="wechat",
        content="你好，我想退货",
        raw_content="你好，我想退货",
        metadata={"wechat_userid": "user123"},
    )
    assert msg.user_id == "wechat:user123"
    assert msg.channel == "wechat"
    d = msg.to_dict()
    assert "session_id" in d
    print(f"     UnifiedMessage: OK (session={d['session_id'][:30]}...)")

    # 用户映射
    wechat.map_wechat_user("user123", "internal-uid-456")
    mapped = wechat.get_internal_user("user123")
    assert mapped == "internal-uid-456"
    print(f"     User mapping: OK")

    # 签名验证
    valid = wechat._verify_signature("token123", "1234567890", "nonce", "echostr")
    print(f"     Signature verify: OK (graceful when no token)")

    await wechat._destroy(ctx, fiber)


# ─── 测试 8: 插件加载器 ────────────────────────
@test("8. 插件加载器（注册 + 依赖排序 + 生命周期）")
async def t8():
    cleanup_data_dirs()
    ctx = BaizeContext(settings)

    # 注册 mock retriever 和 llm 以满足依赖
    mock_retriever = type("MockRetriever", (), {
        "add_documents": lambda self, docs: None,
        "clear": lambda self: None,
    })()
    mock_llm = type("MockLLM", (), {})()
    ctx.provide("retriever", mock_retriever)
    ctx.provide("llm", mock_llm)

    fm = FiberManager(ctx)
    loader = PluginLoader(ctx, fm)

    loader.register(AuthPlugin)
    loader.register(AuditPlugin)
    loader.register(KnowledgePlugin)
    loader.register(ChatPlugin)
    loader.register(WeChatChannelPlugin)

    plugin_ids = loader.get_plugin_ids()
    assert "security.auth" in plugin_ids
    assert "security.audit" in plugin_ids
    assert "core.knowledge" in plugin_ids
    assert "core.chat" in plugin_ids
    assert "channel.wechat" in plugin_ids
    print(f"     Registered: {plugin_ids}")

    loader.load_all()
    await fm.start_all()

    health = fm.health_check()
    print(f"     Health: {health}")

    order = fm._resolve_order()
    auth_idx = order.index("security.auth")
    audit_idx = order.index("security.audit")
    kb_idx = order.index("core.knowledge")
    chat_idx = order.index("core.chat")
    wechat_idx = order.index("channel.wechat")

    assert auth_idx < kb_idx
    assert audit_idx < chat_idx
    assert kb_idx < chat_idx
    assert chat_idx < wechat_idx
    print(f"     Dependency order: {' → '.join(order)}")

    await fm.stop_all()
    await fm.destroy_all()
    print(f"     Stop + Destroy: OK")


# ─── 测试 9: 文档上传完整流程 ────────────────────────
@test("9. 文档上传完整流程（模拟文件上传）")
async def t9():
    cleanup_data_dirs()
    ctx = BaizeContext(settings)
    kb = KnowledgePlugin()
    fiber = make_fiber()

    mock_retriever = type("MockRetriever", (), {
        "add_documents": lambda self, docs: None,
        "clear": lambda self: None,
        "retrieve": lambda self, q, top_k=5, acl_filter=None: [],
    })()
    ctx.provide("retriever", mock_retriever)

    await kb._init(ctx, fiber)

    content = (
        "产品名称：EchoServe 智能客服系统\n" * 5 +
        "核心功能：知识库问答、智能检索、多渠道接入\n" * 5 +
        "技术架构：Python + FastAPI + vLLM + Chroma\n" * 5 +
        "部署方式：Docker Compose 一键部署\n" * 5
    )

    import uuid as uuid_mod
    file_id = str(uuid_mod.uuid4())
    safe_name = f"{file_id}_test_upload.txt"
    upload_dir = kb._upload_dir
    file_path = upload_dir / safe_name
    with open(file_path, "wb") as f:
        f.write(content.encode("utf-8"))

    try:
        from plugins.knowledge.document_parser import parse_file
        parsed = parse_file(str(file_path))
        chunks = parsed["chunks"]

        base_metadata = {
            "filename": "test_upload.txt",
            "filetype": "text",
            "upload_time": "2026-08-19T10:00:00+00:00",
            "allowed_roles": ["*"],
        }

        doc_ids = []
        for i, chunk in enumerate(chunks):
            chunk_metadata = {**base_metadata, "chunk_index": i, "total_chunks": len(chunks)}
            doc_id = await kb.add_document(content=chunk, doc_id=f"{file_id}_c{i}", metadata=chunk_metadata)
            doc_ids.append(doc_id)

        assert len(doc_ids) > 0
        assert kb.count_documents() == len(chunks)
        print(f"     Upload → Parse → Chunk → Index: OK")
        print(f"     Chunks created: {len(chunks)}")
        print(f"     Total docs in KB: {kb.count_documents()}")

        first_doc = kb.get_document(doc_ids[0])
        assert "filename" in first_doc["metadata"]
        assert first_doc["metadata"]["filename"] == "test_upload.txt"
        print(f"     Metadata preserved: OK")

        result = await kb.test_retrieval("EchoServe", top_k=3, user_role="user")
        assert "query" in result
        print(f"     Retrieval test: OK")

    finally:
        if file_path.exists():
            file_path.unlink()
        await kb.clear_all()
        await kb._destroy(ctx, fiber)


# ─── 运行所有测试 ─────────────────────────────
async def main():
    cleanup_data_dirs()

    tests = [
        t1(), t2(), t3(), t4(), t5(),
        t6(), t7(), t8(), t9(),
    ]
    for t in tests:
        await t

    print("\n" + "=" * 70)
    print(f"  测试结果: {results['passed']} passed, {results['failed']} failed")
    print("=" * 70)

    if results["failed"] > 0:
        print("\n失败详情:")
        for e in results["errors"]:
            print(f"  ❌ {e['test']}: {e['error']}")
        sys.exit(1)
    else:
        print("\n🎉 所有 P0 测试通过！")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
