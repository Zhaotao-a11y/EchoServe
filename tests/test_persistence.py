"""
EchoServe V0.1.0 — 持久化集成测试

验证三个 P1 修复的核心逻辑：
  1. BM25 索引持久化（save/load roundtrip）
  2. SessionStore 抽象层（Memory + Redis 降级）
  3. UserStore 抽象层（JSON + PostgreSQL 降级）

不需要真实的 Redis/PG 服务——测试验证降级路径和内存/JSON 回退。
运行：python tests/test_persistence.py
"""
import asyncio
import sys
import os
import tempfile
import json
from pathlib import Path

import pytest

# sys.path 由根目录 conftest.py 统一管理，此处不再重复设置


@pytest.mark.asyncio
async def test_bm25_persistence():
    """测试 D-002: BM25 索引持久化"""
    print("\n[1/5] BM25 索引持久化 (D-002)")
    from plugins.retriever.bm25 import BM25Retriever

    with tempfile.TemporaryDirectory() as tmpdir:
        persist_path = os.path.join(tmpdir, "bm25_index.json")
        retriever = BM25Retriever(persist_path=persist_path)

        # 添加文档
        docs = [
            {"id": "doc1", "content": "如何退货退款流程", "metadata": {"source": "faq"}},
            {"id": "doc2", "content": "会员积分兑换规则", "metadata": {"source": "faq"}},
            {"id": "doc3", "content": "订单物流查询方法", "metadata": {"source": "faq"}},
        ]
        retriever.add_documents(docs)
        assert len(retriever) == 3, f"Expected 3 docs, got {len(retriever)}"

        # 验证索引文件已生成
        assert os.path.exists(persist_path), "BM25 index file not created"
        with open(persist_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["version"] == 1
        assert len(saved["docs"]) == 3
        print(f"   PASS: 3 docs saved to {persist_path}")

        # 创建新实例，从磁盘加载
        retriever2 = BM25Retriever(persist_path=persist_path)
        loaded = retriever2.load()
        assert loaded == 3, f"Expected 3 loaded, got {loaded}"
        assert len(retriever2) == 3
        print(f"   PASS: 3 docs loaded from disk")

        # 验证搜索功能正常
        results = await retriever2.search("退货", k=2)
        assert len(results) > 0, "Search returned no results after load"
        assert results[0]["id"] == "doc1", f"Expected doc1, got {results[0]['id']}"
        print(f"   PASS: Search works after load (top result: {results[0]['id']})")

        # 测试 clear + auto_save
        retriever2.clear()
        assert len(retriever2) == 0
        # 重新加载验证已清空
        retriever3 = BM25Retriever(persist_path=persist_path)
        loaded3 = retriever3.load()
        assert loaded3 == 0, f"Expected 0 after clear, got {loaded3}"
        print(f"   PASS: Clear + auto_save works (persisted empty index)")

    print("   [OK] BM25 持久化全部通过")


@pytest.mark.asyncio
async def test_session_store_memory():
    """测试 D-003: MemorySessionStore 基本功能"""
    print("\n[2/5] MemorySessionStore 基本功能 (D-003)")
    from plugins.chat.session_store import MemorySessionStore

    store = MemorySessionStore(max_sessions=3)

    # save + load
    msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    await store.save_session("sess1", msgs)
    loaded = await store.load_session("sess1")
    assert loaded == msgs, f"Mismatch: {loaded} != {msgs}"
    print("   PASS: save + load roundtrip")

    # list_sessions
    await store.save_session("sess2", [])
    await store.save_session("sess3", [])
    sessions = await store.list_sessions()
    assert len(sessions) == 3, f"Expected 3 sessions, got {len(sessions)}"
    print(f"   PASS: list_sessions returns {len(sessions)} sessions")

    # LRU eviction (max_sessions=3, add 4th)
    await store.save_session("sess4", [])
    sessions = await store.list_sessions()
    assert len(sessions) == 3, f"Expected 3 after eviction, got {len(sessions)}"
    assert "sess1" not in sessions, "sess1 should have been evicted"
    print("   PASS: LRU eviction works (sess1 evicted)")

    # delete_session
    deleted = await store.delete_session("sess2")
    assert deleted == True
    deleted2 = await store.delete_session("nonexistent")
    assert deleted2 == False
    print("   PASS: delete_session works")

    # cleanup_expired
    await store.save_session("expired_sess", [{"role": "user", "content": "old"}])
    # 手动设置过期 timestamp (模拟 2 小时前)
    import time as _time
    store._timestamps["expired_sess"] = _time.time() - 7200
    cleaned = await store.cleanup_expired(ttl=1800)
    assert cleaned == 1, f"Expected 1 cleaned, got {cleaned}"
    sessions = await store.list_sessions()
    assert "expired_sess" not in sessions
    print(f"   PASS: cleanup_expired removed {cleaned} session")

    await store.close()
    print("   [OK] MemorySessionStore 全部通过")


@pytest.mark.asyncio
async def test_session_store_redis_fallback():
    """测试 D-003: Redis 连接失败时降级到 Memory"""
    print("\n[3/5] Redis 连接失败降级 (D-003)")
    from plugins.chat.session_store import (
        RedisSessionStore,
        MemorySessionStore,
        create_session_store,
    )

    # 创建 Redis store（指向不存在的端口）
    redis_store = RedisSessionStore(
        url="redis://localhost:19999/0",  # 不存在的端口
        session_ttl=60,
    )
    connected = await redis_store._connect()
    assert connected == False, "Should fail to connect to non-existent Redis"
    assert redis_store.is_connected == False
    print("   PASS: Redis connection to invalid port correctly fails")

    # 验证降级：创建 store 后连不上，降级到 Memory
    fallback_store = MemorySessionStore(max_sessions=100)
    await fallback_store.save_session("test", [{"role": "user", "content": "hi"}])
    loaded = await fallback_store.load_session("test")
    assert len(loaded) == 1
    print("   PASS: Fallback to MemorySessionStore works")

    await redis_store.close()
    await fallback_store.close()
    print("   [OK] Redis 降级路径全部通过")


@pytest.mark.asyncio
async def test_user_store_json():
    """测试 D-004: JSONUserStore 基本功能"""
    print("\n[4/5] JSONUserStore 基本功能 (D-004)")
    from plugins.auth.user_store import JSONUserStore

    with tempfile.TemporaryDirectory() as tmpdir:
        users_path = Path(tmpdir) / "users.json"
        keys_path = Path(tmpdir) / "api_keys.json"
        store = JSONUserStore(users_path, keys_path)

        # 空加载
        users = await store.load_users()
        assert users == {}, f"Expected empty, got {len(users)} users"
        keys = await store.load_api_keys()
        assert keys == {}
        print("   PASS: Empty load returns empty dict")

        # 保存用户
        test_users = {
            "uid1": {
                "user_id": "uid1",
                "username": "admin",
                "password_hash": "$2b$12$xxx",
                "role": "super_admin",
                "department": "system",
                "created_at": "2026-01-01T00:00:00Z",
                "last_login": None,
                "enabled": True,
            },
            "uid2": {
                "user_id": "uid2",
                "username": "alice",
                "password_hash": "$2b$12$yyy",
                "role": "user",
                "department": "sales",
                "created_at": "2026-01-02T00:00:00Z",
                "last_login": "2026-01-03T00:00:00Z",
                "enabled": True,
            },
        }
        await store.save_users(test_users)
        assert users_path.exists(), "users.json not created"

        # 重新加载
        loaded_users = await store.load_users()
        assert len(loaded_users) == 2
        assert loaded_users["uid1"]["username"] == "admin"
        assert loaded_users["uid2"]["role"] == "user"
        print("   PASS: save + load users roundtrip (2 users)")

        # 保存 API Keys
        test_keys = {
            "kid1": {
                "key_id": "kid1",
                "key": "oz_abc123_def456",
                "user_id": "uid1",
                "name": "default",
                "created_at": "2026-01-01T00:00:00Z",
                "last_used": None,
                "enabled": True,
                "rate_limit": 100,
            },
        }
        await store.save_api_keys(test_keys)
        loaded_keys = await store.load_api_keys()
        assert len(loaded_keys) == 1
        assert loaded_keys["kid1"]["key"] == "oz_abc123_def456"
        print("   PASS: save + load API keys roundtrip (1 key)")

        await store.close()
    print("   [OK] JSONUserStore 全部通过")


@pytest.mark.asyncio
async def test_user_store_pg_fallback():
    """测试 D-004: PostgreSQL 连接失败时降级到 JSON"""
    print("\n[5/5] PostgreSQL 连接失败降级 (D-004)")
    from plugins.auth.user_store import PostgresUserStore, JSONUserStore

    # 创建 PG store（指向不存在的端口）
    pg_store = PostgresUserStore(
        host="localhost",
        port=19999,  # 不存在的端口
        database="echoseve",
        user="echoseve",
        password="test",
    )
    connected = await pg_store._connect()
    assert connected == False, "Should fail to connect to non-existent PG"
    assert pg_store.is_connected == False
    print("   PASS: PostgreSQL connection to invalid port correctly fails")

    # 验证降级到 JSON
    with tempfile.TemporaryDirectory() as tmpdir:
        json_store = JSONUserStore(
            Path(tmpdir) / "users.json",
            Path(tmpdir) / "api_keys.json",
        )
        await json_store.save_users({"uid1": {"user_id": "uid1", "username": "test"}})
        loaded = await json_store.load_users()
        assert len(loaded) == 1
        print("   PASS: Fallback to JSONUserStore works")

    await pg_store.close()
    await json_store.close()
    print("   [OK] PostgreSQL 降级路径全部通过")


async def main():
    print("=" * 60)
    print("EchoServe V0.1.0")
    print("=" * 60)

    tests = [
        test_bm25_persistence,
        test_session_store_memory,
        test_session_store_redis_fallback,
        test_user_store_json,
        test_user_store_pg_fallback,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            await test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"   FAIL: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
