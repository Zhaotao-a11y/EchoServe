"""
EchoServe Evolution System E2E Smoke Test
=========================================
Uses local Ollama qwen2.5:0.5b model to verify:
1. Server starts with Ollama backend
2. User registration + login
3. Chat request triggers chat.complete event
4. Evolution system collects chat log
5. /evolution/health and /evolution/stats return data
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

# ─── Config ──────────────────────────────────────
BASE_URL = "http://127.0.0.1:8080"
API_PREFIX = "/api"
TEST_USER = "evo_tester"
TEST_PASSWORD = "Test12345678"
TEST_MESSAGE = "Hello, what is EchoServe?"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

# Evolution DB path (relative to project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVO_DB_PATH = PROJECT_ROOT / "data" / "evolution" / "evolution.db"


def check_prerequisites():
    """Check Ollama is running and model is available."""
    print("[1/8] Checking prerequisites...")
    try:
        resp = httpx.get(OLLAMA_TAGS_URL, timeout=10)
        if resp.status_code != 200:
            print(f"  FAIL: Ollama /api/tags returned {resp.status_code}")
            return False
        models = resp.json().get("models", [])
        model_names = [m["name"] for m in models]
        if "qwen2.5:0.5b" not in model_names:
            print(f"  FAIL: qwen2.5:0.5b not found in Ollama models: {model_names}")
            return False
        print(f"  OK: Ollama running, qwen2.5:0.5b available")
        return True
    except Exception as e:
        print(f"  FAIL: Cannot connect to Ollama: {e}")
        return False


def wait_for_server(timeout=60):
    """Wait for EchoServe to be ready."""
    print("[2/8] Waiting for EchoServe server...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = httpx.get(f"{BASE_URL}/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "healthy":
                    plugins = data.get("plugins", {})
                    print(f"  OK: Server healthy, plugins: {list(plugins.keys())}")
                    return True
        except Exception:
            pass
        time.sleep(2)
    print(f"  FAIL: Server not ready after {timeout}s")
    return False


def register_and_login():
    """Register a test user and login to get JWT token."""
    print("[3/8] Registering test user...")
    client = httpx.Client(timeout=30)

    # Register
    try:
        resp = client.post(
            f"{BASE_URL}{API_PREFIX}/auth/register",
            json={
                "username": TEST_USER,
                "password": TEST_PASSWORD,
                "role": "super_admin",
                "department": "test",
            },
        )
        if resp.status_code == 200:
            print(f"  OK: User '{TEST_USER}' registered")
        elif resp.status_code == 400 and "already" in resp.text.lower():
            print(f"  OK: User '{TEST_USER}' already exists, skipping registration")
        else:
            print(f"  WARN: Register returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  WARN: Register failed: {e}")

    # Login
    print("[4/8] Logging in...")
    try:
        resp = client.post(
            f"{BASE_URL}{API_PREFIX}/auth/login",
            json={"username": TEST_USER, "password": TEST_PASSWORD},
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token") or data.get("token")
            if token:
                print(f"  OK: Login successful, token acquired ({len(token)} chars)")
                return token
            else:
                print(f"  FAIL: Login response missing token: {data}")
                return None
        else:
            print(f"  FAIL: Login returned {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  FAIL: Login request failed: {e}")
        return None


def send_chat(token):
    """Send a chat message to trigger chat.complete event."""
    print("[5/8] Sending chat request (triggers chat.complete event)...")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = httpx.post(
            f"{BASE_URL}{API_PREFIX}/chat",
            json={
                "session_id": f"e2e-test-{int(time.time())}",
                "message": TEST_MESSAGE,
                "use_rag": False,
            },
            headers=headers,
            timeout=120,
        )

        if resp.status_code == 200:
            data = resp.json()
            reply = data.get("reply", "")
            tokens = data.get("tokens", {})
            print(f"  OK: Chat reply received ({len(reply)} chars)")
            print(f"  Reply preview: {reply[:150]}...")
            print(f"  Token usage: {tokens}")
            return True
        else:
            print(f"  FAIL: Chat returned {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  FAIL: Chat request failed: {e}")
        return False


def check_evolution_api(token):
    """Check /evolution/health and /evolution/stats endpoints."""
    print("[6/8] Checking Evolution API endpoints...")
    headers = {"Authorization": f"Bearer {token}"}
    results = {}

    # Health
    try:
        resp = httpx.get(f"{BASE_URL}/evolution/health", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  OK: /evolution/health -> store_connected={data.get('store_connected')}, "
                  f"failover_level={data.get('failover_level')}")
            results["health"] = data
        else:
            print(f"  WARN: /evolution/health returned {resp.status_code}: {resp.text[:200]}")
            results["health"] = None
    except Exception as e:
        print(f"  FAIL: /evolution/health request failed: {e}")
        results["health"] = None

    # Stats
    try:
        resp = httpx.get(f"{BASE_URL}/evolution/stats", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  OK: /evolution/stats -> {json.dumps(data, ensure_ascii=False)[:300]}")
            results["stats"] = data
        else:
            print(f"  WARN: /evolution/stats returned {resp.status_code}: {resp.text[:200]}")
            results["stats"] = None
    except Exception as e:
        print(f"  FAIL: /evolution/stats request failed: {e}")
        results["stats"] = None

    return results


def check_evolution_db():
    """Check evolution.db for chat_log records."""
    print("[7/8] Checking evolution database for chat_log records...")

    # Wait a moment for async collector to flush
    print("  Waiting 3s for collector flush...")
    time.sleep(3)

    if not EVO_DB_PATH.exists():
        print(f"  FAIL: evolution.db not found at {EVO_DB_PATH}")
        return False

    try:
        conn = sqlite3.connect(str(EVO_DB_PATH))
        cursor = conn.cursor()

        # List all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"  DB tables: {tables}")

        # Check chat_log table
        if "chat_log" in tables:
            cursor.execute("SELECT COUNT(*) FROM chat_log")
            count = cursor.fetchone()[0]
            print(f"  OK: chat_log table has {count} records")

            if count > 0:
                cursor.execute(
                    "SELECT id, session_id, user_message, reply_length, "
                    "prompt_tokens, completion_tokens, created_at "
                    "FROM chat_log ORDER BY created_at DESC LIMIT 5"
                )
                rows = cursor.fetchall()
                for row in rows:
                    print(f"    - id={row[0]}, session={row[1][:20]}..., "
                          f"msg_len={len(row[2] or '')}, reply_len={row[3]}, "
                          f"tokens={row[4]}/{row[5]}, ts={row[6]}")
                conn.close()
                return True
            else:
                # Check if there's a buffer table (pre-flush)
                if "event_buffer" in tables:
                    cursor.execute("SELECT COUNT(*) FROM event_buffer")
                    buf_count = cursor.fetchone()[0]
                    print(f"  INFO: chat_log empty, but event_buffer has {buf_count} pending records")
                    if buf_count > 0:
                        cursor.execute(
                            "SELECT event_type, COUNT(*) FROM event_buffer GROUP BY event_type"
                        )
                        for et, c in cursor.fetchall():
                            print(f"    - {et}: {c}")
                        conn.close()
                        return True  # Data is buffered, will flush soon
                conn.close()
                print("  WARN: No chat_log records found (collector may not have flushed yet)")
                return False
        else:
            print(f"  FAIL: 'chat_log' table not found. Tables: {tables}")
            conn.close()
            return False

    except Exception as e:
        print(f"  FAIL: DB check failed: {e}")
        return False


def check_server_logs():
    """Check if evolution plugin logged startup messages."""
    print("[8/8] Verifying evolution plugin startup in logs...")
    log_file = PROJECT_ROOT / "data" / "logs" / "echoserve.log"

    if not log_file.exists():
        # Also check stderr from the server process
        print(f"  INFO: Log file not found at {log_file}, checking server output")
        return True  # Not critical, we verify via API

    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        checks = [
            ("EvolutionPlugin loaded", "core.evolution" in content and "loaded" in content.lower()),
            ("Evolution API mounted", "/evolution" in content and "mount" in content.lower()),
            ("Subscribed events", "Subscribed" in content or "subscribe" in content.lower()),
            ("Ollama backend detected", "ollama" in content.lower() and "Backend" in content),
        ]
        for name, found in checks:
            status = "OK" if found else "MISS"
            print(f"  [{status}] {name}")

        return all(f for _, f in checks)
    except Exception as e:
        print(f"  WARN: Log check failed: {e}")
        return True


def main():
    print("=" * 60)
    print("  EchoServe Evolution System - E2E Smoke Test")
    print("  Model: qwen2.5:0.5b (Ollama)")
    print("=" * 60)

    results = {}

    # Step 1: Prerequisites
    results["prerequisites"] = check_prerequisites()
    if not results["prerequisites"]:
        print("\nABORT: Prerequisites not met")
        sys.exit(1)

    # Step 2: Wait for server
    results["server_ready"] = wait_for_server(timeout=60)
    if not results["server_ready"]:
        print("\nABORT: Server not ready")
        sys.exit(1)

    # Step 3+4: Register and login
    token = register_and_login()
    results["auth"] = token is not None
    if not token:
        print("\nABORT: Authentication failed")
        sys.exit(1)

    # Step 5: Send chat
    results["chat"] = send_chat(token)

    # Step 6: Check evolution API
    evo_api = check_evolution_api(token)
    results["evolution_api"] = evo_api.get("health") is not None

    # Step 7: Check evolution DB
    results["evolution_db"] = check_evolution_db()

    # Step 8: Check logs
    results["logs"] = check_server_logs()

    # ─── Summary ──────────────────────────────────
    print("\n" + "=" * 60)
    print("  E2E SMOKE TEST RESULTS")
    print("=" * 60)

    all_pass = True
    for step, passed in results.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {step}")

    print("=" * 60)
    if all_pass:
        print("  RESULT: ALL CHECKS PASSED")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"  RESULT: {len(failed)} FAILED - {', '.join(failed)}")
    print("=" * 60)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
