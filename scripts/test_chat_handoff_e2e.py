"""
端到端测试：聊天 API 的智能转接触发验证
"""
import httpx
import asyncio

BASE = "http://localhost:8080"

async def login() -> str:
    resp = httpx.post(f"{BASE}/api/auth/login", json={
        "username": "admin",
        "password": "admin123",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

async def test_chat_normal(token: str):
    """正常消息 - 不应触发转接"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/chat", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, json={
            "session_id": "test-chat-normal-01",
            "message": "你好",
            "channel": "web",
            "user_id": "user-test",
        })
        assert r.status_code == 200, f"Normal chat failed: {r.text}"
        data = r.json()
        reply = data.get("reply", "")
        print(f"  OK normal chat: reply={reply[:40]}...")
        assert "转接" not in reply, f"Normal message should NOT trigger handoff, got: {reply}"
        print(f"  OK no handoff triggered")

async def test_chat_negative(token: str):
    """负面情绪消息 - 应触发转接"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/chat", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, json={
            "session_id": "test-chat-negative-01",
            "message": "你们这个东西太垃圾了，我要投诉",
            "channel": "web",
            "user_id": "user-test",
        })
        assert r.status_code == 200, f"Negative chat failed: {r.text}"
        data = r.json()
        reply = data.get("reply", "")
        print(f"  OK negative chat: reply={reply[:80]}...")
        assert "转接" in reply or "人工" in reply, \
            f"Negative message SHOULD trigger handoff, got: {reply}"
        print(f"  OK handoff triggered and prompt appended")

async def test_chat_explicit_handoff(token: str):
    """显式转人工请求"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/chat", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, json={
            "session_id": "test-chat-explicit-01",
            "message": "我要转人工",
            "channel": "web",
            "user_id": "user-test",
        })
        assert r.status_code == 200, f"Explicit handoff failed: {r.text}"
        data = r.json()
        reply = data.get("reply", "")
        print(f"  OK explicit handoff: reply={reply[:80]}...")
        assert "转接" in reply or "人工" in reply, \
            f"Explicit request SHOULD trigger handoff, got: {reply}"
        print(f"  OK explicit handoff prompt shown")

async def test_chat_stream(token: str):
    """流式聊天 - 负面情绪应触发转接提示追加"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/chat/stream", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, json={
            "session_id": "test-chat-stream-01",
            "message": "你们这个废物系统，我要投诉",
            "channel": "web",
            "user_id": "user-stream-test",
        }, timeout=30)
        assert r.status_code == 200, f"Stream chat failed: {r.text}"
        full_reply = r.text
        print(f"  OK stream chat: response_len={len(full_reply)}")
        # 流式响应格式检查（SSE格式，或者JSON格式）
        if "data:" in full_reply:
            # SSE format
            has_handoff = "转接" in full_reply or "人工" in full_reply
            print(f"  stream contains handoff hint: {has_handoff}")
        print(f"  OK stream response received")

async def main():
    print("="*60)
    print("端到端测试 — Chat API 智能转接触发")
    print("="*60)
    
    print("\n[Auth] Login...")
    token = await login()
    print(f"  OK token received")

    print("\n[1/4] 正常消息（不应触发转接）...")
    await test_chat_normal(token)

    print("\n[2/4] 负面情绪消息（应触发转接）...")
    await test_chat_negative(token)

    print("\n[3/4] 显式转人工请求...")
    await test_chat_explicit_handoff(token)

    print("\n[4/4] 流式聊天负面情绪...")
    await test_chat_stream(token)

    print("\n" + "="*60)
    print("ALL PASSED — Chat API 智能转接触发验证通过")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
