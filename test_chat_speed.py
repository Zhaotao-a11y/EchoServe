import os
import requests
import time

# 从环境变量读取密码，未设置时使用提示
ADMIN_PASSWORD = os.getenv("ECHOSERVE_ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD:
    print("[WARN] 请在环境变量 ECHOSERVE_ADMIN_PASSWORD 中设置管理员密码")
    ADMIN_PASSWORD = input("请输入管理员密码: ")

# 1. 登录获取 token
login_resp = requests.post('http://localhost:8080/api/auth/login', json={
    'username': 'admin',
    'password': ADMIN_PASSWORD
})
if login_resp.status_code != 200:
    print(f"Login failed: {login_resp.status_code} - {login_resp.text}")
    exit(1)

token = login_resp.json()['access_token']
print(f"✅ Token acquired (expires in {login_resp.json()['expires_in']}s)")

# 2. 测试无 RAG 对话速度
headers = {
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/json',
}

payload = {
    'message': '你好',
    'use_rag': False,
}

print("\n--- Test 1: Chat without RAG ---")
start = time.time()
resp = requests.post('http://localhost:8080/api/chat', json=payload, headers=headers, timeout=120)
elapsed = time.time() - start
print(f"Status: {resp.status_code}")
print(f"Response time: {elapsed:.2f}s")
if resp.status_code == 200:
    data = resp.json()
    print(f"Reply: {data['reply'][:100]}...")
else:
    print(f"Error: {resp.text}")

# 3. 测试有 RAG 对话速度
print("\n--- Test 2: Chat with RAG ---")
payload['use_rag'] = True
start = time.time()
resp = requests.post('http://localhost:8080/api/chat', json=payload, headers=headers, timeout=120)
elapsed = time.time() - start
print(f"Status: {resp.status_code}")
print(f"Response time: {elapsed:.2f}s")
if resp.status_code == 200:
    data = resp.json()
    print(f"Docs retrieved: {len(data.get('retrieved_docs', []))}")
    print(f"Reply: {data['reply'][:100]}...")
else:
    print(f"Error: {resp.text}")

print("\n--- Done ---")
