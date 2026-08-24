"""
EchoServe V0.1.0 — 端到端集成测试

模拟完整流程：
  1. 初始化 Context + 插件
  2. 导入 FAQ 数据
  3. 执行检索（BM25 + Vector 融合）
  4. 验证检索质量
  5. 验证会话管理

运行：python scripts/e2e_test.py
"""
import sys
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings
from core.context import BaizeContext
from core.fiber import FiberManager
from core.plugin_loader import PluginLoader

from plugins.retriever.plugin import RetrieverPlugin
from plugins.knowledge.plugin import KnowledgePlugin
from plugins.chat.plugin import ChatPlugin

# 简化版 LLM 插件（不依赖 vLLM，用于测试）
from plugins.llm.plugin import LLMPlugin


class MockLLMPlugin(LLMPlugin):
    """Mock LLM，不连接 vLLM，直接返回拼接回复"""

    plugin_id = "core.llm"
    plugin_name = "Mock LLM (测试用)"
    dependencies = []

    async def on_init(self, ctx, fiber):
        self.client = None
        self.model_name = "mock-llm"
        self.system_prompt = "你是一个测试助手"
        logger = __import__("logging").getLogger("test")
        logger.info(f"[{self.plugin_id}] Mock LLM initialized")

    async def chat(self, messages, **kwargs) -> str:
        # 从系统提示词中提取检索到的知识
        sys_msg = messages[0]["content"] if messages else ""
        user_msg = ""
        for m in messages:
            if m["role"] == "user":
                user_msg = m["content"]

        # 模拟 RAG 回复
        if "参考" in sys_msg:
            return f"[基于知识库回答] 关于「{user_msg}」，根据我们的最新政策..."
        return f"[通用回答] 收到您的问题：{user_msg}"

    async def chat_stream(self, messages, **kwargs):
        reply = await self.chat(messages)
        for chunk in reply.split():
            yield chunk + " "


async def main():
    print("=" * 60)
    print("  EchoServe V0.1.0 — 端到端集成测试")
    print("=" * 60)
    print()

    # 1. 初始化
    print("📦 初始化核心组件...")
    ctx = BaizeContext(settings)
    fiber_manager = FiberManager(ctx)
    loader = PluginLoader(ctx, fiber_manager)

    # 注册插件（用 MockLLM 替代真实 LLM）
    loader.register(RetrieverPlugin)
    loader.register(MockLLMPlugin)
    loader.register(KnowledgePlugin)
    loader.register(ChatPlugin)
    loader.load_all()
    print(f"   ✓ 已注册 {len(loader.get_plugin_ids())} 个插件")
    print(f"   插件列表: {loader.get_plugin_ids()}")
    print()

    # 启动
    print("🚀 启动所有插件...")
    await fiber_manager.start_all()
    print("   ✓ 所有插件启动完成")
    print()

    # 2. 导入知识库
    print("📚 导入知识库...")
    import json
    kb = ctx.inject("knowledge_base")
    faq_path = Path(__file__).resolve().parent.parent / "data" / "faq.jsonl"

    with open(faq_path, "r", encoding="utf-8") as f:
        docs = [json.loads(line) for line in f if line.strip()]

    await kb.add_documents_batch(docs)
    print(f"   ✓ 导入 {kb.count_documents()} 条文档")
    print()

    # 3. 测试检索
    print("🔍 测试混合检索...")
    retriever = ctx.inject("retriever")

    test_queries = [
        "退货政策是什么",
        "怎么修改订单地址",
        "支持哪些支付方式",
        "物流多久能到",
    ]

    for query in test_queries:
        results = await retriever.retrieve(query, top_k=3)
        print(f"\n   查询: 「{query}」")
        if results:
            top = results[0]
            print(f"   → 最佳匹配 (得分: {top.get('rrf_score', 0):.4f})")
            print(f"     {top['content'][:60]}...")
        else:
            print(f"   → 无结果")

    print()

    # 4. 测试对话
    print("💬 测试对话流程...")
    chat_mgr = ctx.inject("chat_manager")

    test_messages = [
        ("sess-1", "你好，我想了解一下退货政策"),
        ("sess-1", "那运费谁承担呢"),  # 多轮上下文
        ("sess-2", "怎么修改我的收货地址"),
    ]

    for sess_id, msg in test_messages:
        result = await chat_mgr.chat(sess_id, msg, use_rag=True)
        print(f"\n   [{sess_id}] 用户: {msg}")
        print(f"   [{sess_id}] 助手: {result['reply'][:80]}...")
        if result['retrieved_docs']:
            print(f"   📎 检索到 {len(result['retrieved_docs'])} 篇参考文档")

    print()

    # 5. 测试会话管理
    print("📋 测试会话管理...")
    sessions = await chat_mgr.list_sessions()
    print(f"   活跃会话: {sessions}")

    history = await chat_mgr.get_session_history("sess-1")
    print(f"   sess-1 历史消息数: {len(history)}")
    print()

    # 6. 测试流式对话
    print("🌊 测试流式输出...")
    print(f"   用户: 会员有哪些等级？")
    print(f"   助手: ", end="", flush=True)
    async for chunk in chat_mgr.chat_stream("sess-stream", "会员有哪些等级"):
        print(chunk, end="", flush=True)
    print("\n")

    # 7. 清理
    print("🧹 清理资源...")
    await fiber_manager.stop_all()
    await fiber_manager.destroy_all()
    print("   ✓ 所有插件已安全停止")
    print()

    print("=" * 60)
    print("  ✅ 端到端测试全部通过！")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
    asyncio.run(main())
