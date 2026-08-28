"""Unit tests for EvolutionQuery Phase 2/3 endpoints (M-17).

Tests cover the 5 new GET endpoints that depend on _evolution_plugin:
  - GET /evolution/experiments
  - GET /evolution/patterns
  - GET /evolution/templates
  - GET /evolution/failover
  - GET /evolution/overview

Each endpoint is tested for:
  1. 200 OK with correct response structure (with mock plugin)
  2. 401 Unauthorized (no token)
  3. 503 Service Unavailable (plugin not initialized)
"""
from __future__ import annotations

import jwt
import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Import after conftest.py sets up sys.path
from plugins.evolution.phase1.query import (
    router,
    set_store,
    set_evolution_plugin,
    _evolution_store as _orig_store,
    _evolution_plugin as _orig_plugin,
)
from config.settings import settings

# ─── 测试常量 ─────────────────────────────────────

TEST_JWT_SECRET = "test-secret-for-unit-tests"


# ─── Fixtures ─────────────────────────────────────


@pytest.fixture
def valid_token() -> str:
    """Create a valid JWT token for testing."""
    payload = {
        "sub": "test-user",
        "exp": datetime.now(timezone.utc).timestamp() + 3600,
    }
    secret = settings.security.jwt_secret or TEST_JWT_SECRET
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def auth_headers(valid_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {valid_token}"}


@pytest.fixture
def mock_experimenter():
    """Mock Experimenter with one experiment."""
    config = SimpleNamespace(
        param_name="top_k",
        candidate_values=[3, 5, 7],
        eval_metric="success_rate",
        status=SimpleNamespace(value="running"),
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    state = SimpleNamespace(config=config)
    experimenter = MagicMock()
    experimenter.experiments = {"exp-001": state}
    experimenter.traffic_percent = 50
    experimenter.get_assignment_stats = MagicMock(
        return_value={
            "control_group_size": 100,
            "treatment_group_size": 120,
            "control_metrics_count": 80,
            "treatment_metrics_count": 95,
        }
    )
    return experimenter


@pytest.fixture
def mock_pattern_miner():
    """Mock PatternMiner with two mined patterns."""
    pattern_a = SimpleNamespace(
        intent="refund_query",
        skill_sequence=["intent_detect", "policy_lookup", "refund_calc"],
        frequency=150,
        success_rate=0.95,
        avg_latency_ms=320.0,
        confidence=0.88,
    )
    pattern_b = SimpleNamespace(
        intent="order_status",
        skill_sequence=["intent_detect", "order_query"],
        frequency=300,
        success_rate=0.92,
        avg_latency_ms=180.0,
        confidence=0.75,
    )
    miner = MagicMock()
    miner.mine = MagicMock(return_value=[pattern_a, pattern_b])
    return miner


@pytest.fixture
def mock_template_registry():
    """Mock TemplateRegistry with one template."""
    candidate = SimpleNamespace(
        name="refund_v2",
        intent="refund_query",
        status=SimpleNamespace(value="shadow"),
        generated_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
    )
    version = SimpleNamespace(
        candidate=candidate,
        activation=SimpleNamespace(rollout_percent=10.0),
        previous_version="refund_v1",
        metrics={"success_rate": 0.93, "avg_latency_ms": 290.0},
    )
    registry = MagicMock()
    registry._templates = {"refund_v2": version}
    registry.summary = MagicMock(
        return_value={"total": 1, "active": 1, "shadow": 1}
    )
    return registry


@pytest.fixture
def mock_failover():
    """Mock FailoverManager."""
    failover = MagicMock()
    failover.current_level = SimpleNamespace(value="normal")
    failover.rules_count = 5
    failover.history_count = 3
    failover.get_history = MagicMock(
        return_value=[
            {"timestamp": "2025-01-01T10:00:00Z", "level": "normal", "reason": "init"},
        ]
    )
    return failover


@pytest.fixture
def mock_collector():
    """Mock DataCollector."""
    collector = MagicMock()
    collector.get_stats = MagicMock(
        return_value={"total_records": 1000, "buffered": 50}
    )
    return collector


@pytest.fixture
def mock_config():
    """Mock EvolutionConfig."""
    return SimpleNamespace(
        mining_min_success_rate=0.9,
        mining_min_support=10,
        template_auto_promote=False,
        eval_interval=3600,
    )


@pytest.fixture
def mock_store():
    """Mock EvolutionStore."""
    store = MagicMock()
    store.get_stats = AsyncMock(
        return_value={"total_records": 5000, "tables": ["chat_log", "feedback"]}
    )
    return store


@pytest.fixture
def app_with_plugin(
    mock_experimenter,
    mock_pattern_miner,
    mock_template_registry,
    mock_failover,
    mock_collector,
    mock_config,
    mock_store,
):
    """Create a FastAPI app with mock evolution plugin injected."""
    # Ensure jwt_secret is set
    if not settings.security.jwt_secret:
        settings.security.jwt_secret = TEST_JWT_SECRET

    plugin = SimpleNamespace(
        experimenter=mock_experimenter,
        pattern_miner=mock_pattern_miner,
        template_registry=mock_template_registry,
        failover=mock_failover,
        collector=mock_collector,
        config=mock_config,
    )

    set_store(mock_store)
    set_evolution_plugin(plugin)

    app = FastAPI()
    app.include_router(router)

    yield app

    # Restore originals
    set_store(_orig_store)
    set_evolution_plugin(_orig_plugin)


@pytest.fixture
def app_without_plugin():
    """Create app with no plugin initialized (triggers 503)."""
    if not settings.security.jwt_secret:
        settings.security.jwt_secret = TEST_JWT_SECRET

    set_store(None)
    set_evolution_plugin(None)

    app = FastAPI()
    app.include_router(router)

    yield app

    set_store(_orig_store)
    set_evolution_plugin(_orig_plugin)


@pytest.fixture
def client_with_plugin(app_with_plugin) -> TestClient:
    return TestClient(app_with_plugin)


@pytest.fixture
def client_without_plugin(app_without_plugin) -> TestClient:
    return TestClient(app_without_plugin)


# ─── GET /evolution/experiments ───────────────────


class TestListExperiments:
    """Tests for GET /evolution/experiments."""

    def test_200_returns_experiments(self, client_with_plugin, auth_headers):
        resp = client_with_plugin.get("/evolution/experiments", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        exp = data["experiments"][0]
        assert exp["exp_id"] == "exp-001"
        assert exp["param_name"] == "top_k"
        assert exp["candidate_values"] == ["3", "5", "7"]
        assert exp["eval_metric"] == "success_rate"
        assert exp["status"] == "running"
        assert exp["traffic_percent"] == 50
        assert exp["control_group_size"] == 100
        assert exp["treatment_group_size"] == 120

    def test_401_no_token(self, client_with_plugin):
        resp = client_with_plugin.get("/evolution/experiments")
        assert resp.status_code == 401

    def test_503_plugin_not_initialized(self, client_without_plugin, auth_headers):
        resp = client_without_plugin.get("/evolution/experiments", headers=auth_headers)
        assert resp.status_code == 503


# ─── GET /evolution/patterns ──────────────────────


class TestListPatterns:
    """Tests for GET /evolution/patterns."""

    def test_200_returns_patterns_sorted_by_confidence(self, client_with_plugin, auth_headers):
        resp = client_with_plugin.get("/evolution/patterns", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        # Sorted by confidence descending
        assert data["patterns"][0]["confidence"] == 0.88
        assert data["patterns"][1]["confidence"] == 0.75
        p = data["patterns"][0]
        assert p["intent"] == "refund_query"
        assert p["skill_sequence"] == ["intent_detect", "policy_lookup", "refund_calc"]
        assert p["frequency"] == 150
        assert p["success_rate"] == 0.95
        assert p["avg_latency_ms"] == 320.0

    def test_401_no_token(self, client_with_plugin):
        resp = client_with_plugin.get("/evolution/patterns")
        assert resp.status_code == 401

    def test_503_plugin_not_initialized(self, client_without_plugin, auth_headers):
        resp = client_without_plugin.get("/evolution/patterns", headers=auth_headers)
        assert resp.status_code == 503


# ─── GET /evolution/templates ─────────────────────


class TestListTemplates:
    """Tests for GET /evolution/templates."""

    def test_200_returns_templates(self, client_with_plugin, auth_headers):
        resp = client_with_plugin.get("/evolution/templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        t = data["templates"][0]
        assert t["template_id"] == "refund_v2"
        assert t["name"] == "refund_v2"
        assert t["intent"] == "refund_query"
        assert t["status"] == "shadow"
        assert t["rollout_percent"] == 10.0
        assert t["previous_version"] == "refund_v1"
        assert "success_rate" in t["metrics"]
        assert "summary" in data
        assert data["summary"]["total"] == 1

    def test_401_no_token(self, client_with_plugin):
        resp = client_with_plugin.get("/evolution/templates")
        assert resp.status_code == 401

    def test_503_plugin_not_initialized(self, client_without_plugin, auth_headers):
        resp = client_without_plugin.get("/evolution/templates", headers=auth_headers)
        assert resp.status_code == 503


# ─── GET /evolution/failover ──────────────────────


class TestFailoverStatus:
    """Tests for GET /evolution/failover."""

    def test_200_returns_failover_status(self, client_with_plugin, auth_headers):
        resp = client_with_plugin.get("/evolution/failover", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_level"] == "normal"
        assert data["rules_count"] == 5
        assert data["history_count"] == 3
        assert len(data["history"]) == 1
        assert data["history"][0]["level"] == "normal"

    def test_401_no_token(self, client_with_plugin):
        resp = client_with_plugin.get("/evolution/failover")
        assert resp.status_code == 401

    def test_503_plugin_not_initialized(self, client_without_plugin, auth_headers):
        resp = client_without_plugin.get("/evolution/failover", headers=auth_headers)
        assert resp.status_code == 503


# ─── GET /evolution/overview ──────────────────────


class TestEvolutionOverview:
    """Tests for GET /evolution/overview."""

    def test_200_returns_overview(self, client_with_plugin, auth_headers):
        resp = client_with_plugin.get("/evolution/overview", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # Store stats
        assert "store" in data
        assert data["store"]["total_records"] == 5000
        # Collector stats
        assert "collector" in data
        assert data["collector"]["total_records"] == 1000
        # Experiments
        assert data["experiments"]["total"] == 1
        assert data["experiments"]["active"] == 1
        # Patterns
        assert data["patterns"]["total"] == 2
        # Templates
        assert "templates" in data
        assert data["templates"]["total"] == 1
        # Failover
        assert data["failover"]["current_level"] == "normal"
        assert data["failover"]["rules_count"] == 5
        # Config
        assert data["config"]["mining_min_success_rate"] == 0.9
        assert data["config"]["mining_min_support"] == 10
        assert data["config"]["template_auto_promote"] is False
        assert data["config"]["eval_interval"] == 3600

    def test_401_no_token(self, client_with_plugin):
        resp = client_with_plugin.get("/evolution/overview")
        assert resp.status_code == 401

    def test_503_plugin_not_initialized(self, client_without_plugin, auth_headers):
        resp = client_without_plugin.get("/evolution/overview", headers=auth_headers)
        assert resp.status_code == 503
