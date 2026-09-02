"""
EchoServe Phase 2.7 — Intelligent Handoff Integration Smoke Test

Verifies the end-to-end intelligent handoff flow:
    1. Module imports (intelligent_handoff + agent plugin + chat plugin)
    2. SentimentAnalyzer — positive / negative / neutral detection
    3. ConversationSummarizer — summary generation with emotion
    4. HandoffDecision logic — 4 trigger conditions
    5. create_intelligent_handoff — full pipeline (decision -> summary -> routing -> record)
    6. API router — 4 new intelligent endpoints registered
    7. ChatPlugin integration points — _intelligent_handoff attr + handoff logic in chat() and chat_stream()
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from typing import Any

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def smoke_test():
    print("=" * 60)
    print("Phase 2.7 Smoke Test — Intelligent Handoff Integration")
    print("=" * 60)
    errors: list[str] = []

    # ── 1. Import check ─────────────────────────────────
    print("\n[1/7] Import check...")
    try:
        from plugins.agent.intelligent_handoff import (
            SentimentAnalyzer,
            ConversationSummarizer,
            SmartQueueRouter,
            HandoffDecision,
            IntelligentHandoffManager,
        )
        print("  OK  plugins.agent.intelligent_handoff")
    except Exception as e:
        errors.append(f"intelligent_handoff import: {e}")
        print(f"  FAIL {e}")

    try:
        from plugins.chat.plugin import ChatPlugin
        print("  OK  plugins.chat.plugin (with handoff integration)")
    except Exception as e:
        errors.append(f"chat import: {e}")
        print(f"  FAIL {e}")

    try:
        from api.routers.agent import router as agent_router
        print("  OK  api.routers.agent (with intelligent endpoints)")
    except Exception as e:
        errors.append(f"agent router import: {e}")
        print(f"  FAIL {e}")

    # ── 2. SentimentAnalyzer ───────────────────────────
    print("\n[2/7] SentimentAnalyzer...")
    try:
        analyzer = SentimentAnalyzer()

        # Negative sentiment
        neg = analyzer.analyze("你们这个东西太垃圾了，我要投诉")
        assert neg["score"] < -0.5, f"Expected strong negative, got score={neg['score']}"
        assert neg["label"] in ("negative", "very_negative"), f"Got label={neg['label']}"
        assert len(neg["keywords"]) > 0
        print(f"  OK  negative: score={neg['score']}, label={neg['label']}, keywords={neg['keywords']}")

        # Positive sentiment
        pos = analyzer.analyze("非常感谢，问题已经解决了")
        assert pos["score"] > 0.5, f"Expected positive, got score={pos['score']}"
        assert pos["label"] in ("positive", "very_positive"), f"Got label={pos['label']}"
        print(f"  OK  positive: score={pos['score']}, label={pos['label']}")

        # Neutral
        neu = analyzer.analyze("请问今天天气怎么样")
        assert abs(neu["score"]) < 0.3, f"Expected neutral, got score={neu['score']}"
        assert neu["label"] == "neutral"
        print(f"  OK  neutral: score={neu['score']}, label={neu['label']}")

        # Empty text edge case
        empty = analyzer.analyze("")
        assert empty["score"] == 0.0
        assert empty["label"] == "neutral"
        print(f"  OK  empty text handled")
    except Exception as e:
        errors.append(f"sentiment analyzer: {e}")
        print(f"  FAIL {e}")

    # ── 3. ConversationSummarizer ───────────────────────
    print("\n[3/7] ConversationSummarizer...")
    try:
        summarizer = ConversationSummarizer()

        messages = [
            {"role": "user", "content": "我的订单ORD-001还没发货"},
            {"role": "assistant", "content": "已为您查询，订单正在打包中"},
            {"role": "user", "content": "都三天了还没发，太慢了吧"},
        ]
        summary = summarizer.summarize(messages, include_user_emotion=True)

        assert "对话轮数" in summary
        assert "用户核心问题" in summary
        assert "AI 已尝试方案" in summary
        assert "用户情绪" in summary  # include_user_emotion=True
        assert len(summary) <= 500
        print(f"  OK  summary generated ({len(summary)} chars)")
        print(f"       snippet: {summary[:120]}...")

        # Empty messages edge case
        empty_summary = summarizer.summarize([])
        assert "无对话历史" in empty_summary
        print(f"  OK  empty messages handled")
    except Exception as e:
        errors.append(f"summarizer: {e}")
        print(f"  FAIL {e}")

    # ── 4. HandoffDecision logic (should_handoff) ──────
    print("\n[4/7] HandoffDecision logic (should_handoff)...")
    try:
        # Create a mock agent plugin for IntelligentHandoffManager
        mock_agent = _create_mock_agent_plugin()
        mgr = IntelligentHandoffManager(mock_agent)

        # 4a. User explicit request
        decision = mgr.should_handoff(
            session_messages=[],
            last_message="我要转人工",
        )
        assert decision.should_handoff is True
        assert decision.trigger == "user_request"
        print(f"  OK  explicit request: trigger={decision.trigger}")

        # 4b. Negative sentiment trigger
        decision = mgr.should_handoff(
            session_messages=[],
            last_message="你们这个东西太垃圾了，废物",
        )
        assert decision.should_handoff is True
        assert decision.trigger == "negative_sentiment"
        assert decision.priority == "high"
        print(f"  OK  negative sentiment: trigger={decision.trigger}, priority={decision.priority}")

        # 4c. Low confidence trigger
        decision = mgr.should_handoff(
            session_messages=[],
            last_message="今天天气怎么样",
            intent_confidence=0.3,
        )
        assert decision.should_handoff is True
        assert decision.trigger == "low_confidence"
        print(f"  OK  low confidence: trigger={decision.trigger}")

        # 4d. Normal message — no handoff
        decision = mgr.should_handoff(
            session_messages=[],
            last_message="谢谢，问题解决了",
            intent_confidence=0.95,
        )
        assert decision.should_handoff is False
        print(f"  OK  no handoff for normal message")

        # 4e. Mild negative — no immediate handoff
        decision = mgr.should_handoff(
            session_messages=[],
            last_message="退款",
            intent_confidence=0.9,
        )
        assert decision.should_handoff is False
        assert "mild" in decision.trigger
        print(f"  OK  mild negative (no handoff): trigger={decision.trigger}")
    except Exception as e:
        errors.append(f"handoff decision: {e}")
        print(f"  FAIL {e}")

    # ── 5. create_intelligent_handoff (full pipeline) ──
    print("\n[5/7] create_intelligent_handoff (full pipeline)...")
    try:
        mock_agent = _create_mock_agent_plugin()
        mgr = IntelligentHandoffManager(mock_agent)

        messages = [
            {"role": "user", "content": "我的订单ORD-001还没发货"},
            {"role": "assistant", "content": "已为您查询，订单正在打包中"},
            {"role": "user", "content": "都三天了还没发，太垃圾了我要投诉"},
        ]

        # Execute full pipeline — should trigger handoff (negative sentiment)
        result = mgr.create_intelligent_handoff(
            session_id="test-session-001",
            customer_id="customer-001",
            customer_name="Test User",
            channel="web",
            messages=messages,
            intent_confidence=0.9,
            last_message="都三天了还没发，太垃圾了我要投诉",
        )

        assert result["handoff_required"] is True, "Expected handoff_required=True"
        assert "decision" in result
        assert "summary" in result
        assert result["decision"]["trigger"] == "negative_sentiment"
        assert "对话轮数" in result["summary"]
        print(f"  OK  handoff created: trigger={result['decision']['trigger']}")
        print(f"       summary snippet: {result['summary'][:80]}...")

        # Verify request_handoff was called on mock
        assert mock_agent.request_handoff.called, "request_handoff not called"
        print(f"  OK  request_handoff invoked on agent plugin")

        # Verify assign_handoff was called (since mock returns an available agent)
        assert mock_agent.assign_handoff.called, "assign_handoff not called"
        print(f"  OK  assign_handoff invoked (smart routing)")

        # Case: no handoff needed
        result_no = mgr.create_intelligent_handoff(
            session_id="test-session-002",
            messages=[],
            last_message="谢谢",
            intent_confidence=0.95,
        )
        assert result_no["handoff_required"] is False
        print(f"  OK  no handoff for positive message")
    except Exception as e:
        errors.append(f"create_intelligent_handoff: {e}")
        print(f"  FAIL {e}")

    # ── 6. API router endpoints ─────────────────────────
    print("\n[6/7] API router — intelligent endpoints...")
    try:
        from api.routers.agent import router

        routes = {r.path: r.methods for r in router.routes}
        expected = [
            "/handoffs/intelligent/analyze",
            "/handoffs/intelligent/execute",
            "/handoffs/intelligent/sentiment",
            "/handoffs/intelligent/summary",
        ]
        for path in expected:
            assert path in routes, f"Missing route: {path}"
            assert "POST" in routes[path], f"Route {path} should accept POST"
        print(f"  OK  4 intelligent endpoints registered")

        # Verify request models exist
        from api.routers.agent import (
            IntelligentAnalyzeRequest,
            IntelligentExecuteRequest,
            SentimentAnalyzeRequest,
            SummaryRequest,
        )
        print(f"  OK  4 Pydantic request models defined")
    except Exception as e:
        errors.append(f"api router: {e}")
        print(f"  FAIL {e}")

    # ── 7. ChatPlugin integration points ───────────────
    print("\n[7/7] ChatPlugin integration points...")
    try:
        chat = ChatPlugin()
        assert hasattr(chat, "_intelligent_handoff"), "Missing _intelligent_handoff attr"
        print(f"  OK  _intelligent_handoff attribute present")

        # Read source to verify handoff logic exists in both paths
        source = Path(ROOT / "plugins" / "chat" / "plugin.py").read_text(
            encoding="utf-8"
        )

        # Non-stream path
        assert "should_handoff" in source, "Missing should_handoff in chat plugin"
        assert "create_intelligent_handoff" in source, "Missing create_intelligent_handoff"
        assert "chat.handoff_triggered" in source, "Missing handoff event publish"
        assert "[已为您转接人工客服" in source, "Missing handoff prompt"
        print(f"  OK  handoff logic in chat() (non-stream)")

        # Stream path — check for the stream-specific marker
        assert "Intelligent handoff triggered (stream)" in source, \
            "Missing stream-path handoff log"
        assert "stream" in source and "handoff_triggered" in source, \
            "Missing stream handoff flag"
        print(f"  OK  handoff logic in chat_stream() (stream)")
    except Exception as e:
        errors.append(f"chat integration: {e}")
        print(f"  FAIL {e}")

    # ── Summary ────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print(f"FAILED — {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("ALL PASSED — Phase 2.7 intelligent handoff integration OK")
        print("=" * 60)
        return 0


def _create_mock_agent_plugin() -> MagicMock:
    """Create a mock AgentPlugin for testing IntelligentHandoffManager."""
    mock = MagicMock()

    # list_agents returns one online agent
    mock.list_agents.return_value = [
        {
            "agent_id": "agent-001",
            "agent_name": "Test Agent",
            "skills": ["complaint_handling", "customer_relations"],
            "max_concurrent": 5,
        }
    ]

    # get_agent_workload returns low load
    mock.get_agent_workload.return_value = {
        "active_sessions": 1,
        "total_sessions": 50,
        "rating_stats": {"average": 4.5, "count": 50},
    }

    # get_available_agent returns the same agent
    mock.get_available_agent.return_value = {
        "agent_id": "agent-001",
        "agent_name": "Test Agent",
        "skills": ["complaint_handling", "customer_relations"],
    }

    # get_queue_status returns empty queue
    mock.get_queue_status.return_value = {"queue_length": 0}

    # request_handoff returns a handoff record
    mock.request_handoff.return_value = {
        "id": "handoff-001",
        "session_id": "test-session-001",
        "status": "pending",
    }

    # assign_handoff returns success
    mock.assign_handoff.return_value = {"id": "handoff-001", "status": "assigned"}

    return mock


if __name__ == "__main__":
    smoke_test()
