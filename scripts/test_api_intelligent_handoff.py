"""
端到端 API 测试：智能转接端点
"""
import httpx
import asyncio

BASE = "http://localhost:8080"

def login() -> str:
    resp = httpx.post(f"{BASE}/api/auth/login", json={
        "username": "admin",
        "password": "admin123",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

async def test_sentiment(token: str):
    async with httpx.AsyncClient() as client:
        # Test negative
        r = await client.post(f"{BASE}/api/handoffs/intelligent/sentiment", 
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "你们这个产品太垃圾了，我要投诉"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["score"] < -0.5
        assert data["label"] in ("negative", "very_negative")
        print(f"  OK sentiment negative: score={data['score']}")

        # Test positive
        r = await client.post(f"{BASE}/api/handoffs/intelligent/sentiment",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "谢谢，问题解决了"})
        assert r.status_code == 200
        data = r.json()
        assert data["score"] > 0.5
        print(f"  OK sentiment positive: score={data['score']}")

async def test_summary(token: str):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/handoffs/intelligent/summary",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [
                    {"role": "user", "content": "订单还没发货"},
                    {"role": "assistant", "content": "已查询，正在打包"},
                ],
                "max_length": 500,
                "include_emotion": True,
            })
        assert r.status_code == 200, r.text
        data = r.json()
        assert "summary" in data
        assert "对话轮数" in data["summary"]
        print(f"  OK summary: {data['summary'][:60]}...")

async def test_analyze(token: str):
    async with httpx.AsyncClient() as client:
        # Negative should trigger handoff
        r = await client.post(f"{BASE}/api/handoffs/intelligent/analyze",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "session_id": "test-001",
                "last_message": "你们这个东西太垃圾了，废物",
                "messages": [],
                "intent_confidence": 0.9,
            })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["decision"]["should_handoff"] is True
        print(f"  OK analyze negative: trigger={data['decision']['trigger']}, priority={data['decision']['priority']}")

        # Normal should not trigger
        r = await client.post(f"{BASE}/api/handoffs/intelligent/analyze",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "session_id": "test-002",
                "last_message": "谢谢",
                "messages": [],
                "intent_confidence": 0.95,
            })
        assert r.status_code == 200
        data = r.json()
        assert data["decision"]["should_handoff"] is False
        print(f"  OK analyze normal: no handoff")

async def test_execute(token: str):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE}/api/handoffs/intelligent/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "session_id": "test-003",
                "last_message": "你们这个垃圾服务我要投诉",
                "messages": [
                    {"role": "user", "content": "订单还没发货"},
                    {"role": "assistant", "content": "已查询，正在打包"},
                    {"role": "user", "content": "你们这个垃圾服务我要投诉"},
                ],
                "intent_confidence": 0.9,
            })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["handoff_required"] is True
        assert "summary" in data
        print(f"  OK execute: handoff_required={data['handoff_required']}, trigger={data['decision']['trigger']}")
        if "assigned_agent" in data:
            print(f"       assigned_agent={data.get('assigned_agent')}")

async def main():
    print("="*60)
    print("API 端到端测试 — Intelligent Handoff")
    print("="*60)
    
    print("\n[Auth] Login...")
    token = login()
    print(f"  OK token received")

    print("\n[1/4] Sentiment API...")
    await test_sentiment(token)

    print("\n[2/4] Summary API...")
    await test_summary(token)

    print("\n[3/4] Analyze API...")
    await test_analyze(token)

    print("\n[4/4] Execute API...")
    await test_execute(token)

    print("\n" + "="*60)
    print("ALL PASSED — API 端点端到端测试通过")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
