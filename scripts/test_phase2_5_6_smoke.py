"""
EchoServe Phase 2.5/2.6 烟雾测试
验证 ToolOrchestrator + AI Investigator 集成点是否正确连接。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

def smoke_test():
    print("=" * 60)
    print("Phase 2.5/2.6 Smoke Test — Tool + AI Ticket Integration")
    print("=" * 60)
    errors = []

    # ── 1. Import all new modules ──────────────────────────
    print("\n[1/6] Import check...")
    try:
        from plugins.tools import (
            ToolDefinition, ToolResult, ToolRegistry,
            ToolCallParser, ToolOrchestrator, create_default_tools,
        )
        print("  OK  plugins.tools")
    except Exception as e:
        errors.append(f"tools import: {e}")
        print(f"  FAIL {e}")

    try:
        from plugins.ticket.ai_investigator import (
            IntentClassifier, RootCauseInvestigator, AIInvestigatorManager,
        )
        print("  OK  plugins.ticket.ai_investigator")
    except Exception as e:
        errors.append(f"ai_investigator import: {e}")
        print(f"  FAIL {e}")

    try:
        from plugins.ticket.auto_assigner import AgentInfo, SmartTicketAssigner
        print("  OK  plugins.ticket.auto_assigner")
    except Exception as e:
        errors.append(f"auto_assigner import: {e}")
        print(f"  FAIL {e}")

    try:
        from plugins.chat.plugin import ChatPlugin
        print("  OK  plugins.chat.plugin (with Phase 2.5/2.6)")
    except Exception as e:
        errors.append(f"chat import: {e}")
        print(f"  FAIL {e}")

    # ── 2. ToolRegistry + ToolOrchestrator ─────────────────
    print("\n[2/6] ToolRegistry + Orchestrator...")
    try:
        registry = ToolRegistry()
        for tool in create_default_tools():
            registry.register(tool)
        tools = registry.list_tools()
        assert len(tools) == 3, f"Expected 3 tools, got {len(tools)}"
        tool_names = {t.name for t in tools}
        assert "query_order" in tool_names
        assert "check_inventory" in tool_names
        assert "create_return_request" in tool_names
        print(f"  OK  {len(tools)} tools registered")
    except Exception as e:
        errors.append(f"tool registry: {e}")
        print(f"  FAIL {e}")

    # ── 3. ToolOrchestrator instantiation ──────────────────
    print("\n[3/6] ToolOrchestrator...")
    try:
        orchestrator = ToolOrchestrator(registry)
        assert orchestrator.MAX_TOOL_ROUNDS == 5
        print(f"  OK  MAX_TOOL_ROUNDS={orchestrator.MAX_TOOL_ROUNDS}")
    except Exception as e:
        errors.append(f"orchestrator: {e}")
        print(f"  FAIL {e}")

    # ── 4. AIInvestigatorManager ──────────────────────────
    print("\n[4/6] AIInvestigatorManager...")
    try:
        mgr = AIInvestigatorManager()
        assert mgr.classifier is not None
        assert mgr.investigator is not None
        print("  OK  manager created")

        # Test should_create_ticket
        should = mgr.should_create_ticket("你们的系统出bug了，我要投诉")
        assert should is True, f"Expected True for bug report, got {should}"
        should2 = mgr.should_create_ticket("今天天气怎么样")
        assert should2 is False, f"Expected False for general msg, got {should2}"
        print(f"  OK  should_create_ticket logic verified")
    except Exception as e:
        errors.append(f"ai manager: {e}")
        print(f"  FAIL {e}")

    # ── 5. ChatPlugin has new attributes ───────────────────
    print("\n[5/6] ChatPlugin integration points...")
    try:
        chat = ChatPlugin()
        # Check attributes exist
        assert hasattr(chat, "_tool_orchestrator"), "Missing _tool_orchestrator"
        assert hasattr(chat, "_ai_investigator"), "Missing _ai_investigator"
        assert hasattr(chat, "_should_use_tools"), "Missing _should_use_tools"
        assert hasattr(chat, "_async_create_ticket"), "Missing _async_create_ticket"
        print("  OK  all integration attributes present")

        # Check _should_use_tools logic
        assert chat._should_use_tools("帮我查一下订单ORD-123") is True
        assert chat._should_use_tools("这个产品有货吗") is True
        assert chat._should_use_tools("你们系统怎么用") is False
        print("  OK  _should_use_tools logic verified")
    except Exception as e:
        errors.append(f"chat integration: {e}")
        print(f"  FAIL {e}")

    # ── 6. Ticket API router ───────────────────────────────
    print("\n[6/6] API router import...")
    try:
        from api.routers.ticket_ai import router as ticket_ai_router
        assert ticket_ai_router is not None
        print("  OK  ticket_ai router imported")
    except Exception as e:
        errors.append(f"ticket_ai router: {e}")
        print(f"  FAIL {e}")

    # ── Summary ──────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print(f"FAILED — {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("ALL PASSED — Phase 2.5/2.6 integration smoke test OK")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    smoke_test()
