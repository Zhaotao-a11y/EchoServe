"""
EchoServe Evolution System — Tests: Data Models

测试 shared.models 中所有数据模型的创建、序列化和边界行为。
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evolution.shared.models import (
    ChatLogRecord,
    EvalResult,
    ExperimentConfig,
    ExperimentStatus,
    FeedbackRecord,
    FeedbackType,
    ParamAssignment,
    RouteLogRecord,
    SkillPattern,
    SkillTemplateCandidate,
    SkillTemplateReview,
    SkillTraceRecord,
    SystemMetricRecord,
    TemplateActivation,
    TemplateStatus,
)


class TestChatLogRecord(unittest.TestCase):
    """测试 ChatLogRecord 模型。"""

    def test_basic_creation(self):
        """测试基本创建和字段默认值。"""
        record = ChatLogRecord(session_id="s1", query="hello", reply="hi")
        self.assertEqual(record.session_id, "s1")
        self.assertEqual(record.query, "hello")
        self.assertEqual(record.reply, "hi")
        self.assertEqual(record.retrieved_docs, [])
        self.assertEqual(record.latency_ms, 0)
        self.assertIsInstance(record.timestamp, datetime)

    def test_to_dict(self):
        """测试序列化。"""
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        record = ChatLogRecord(
            session_id="s1",
            query="q",
            reply="r",
            retrieved_docs=["d1", "d2"],
            latency_ms=100,
            timestamp=ts,
            feedback_type=FeedbackType.LIKE,
        )
        d = record.to_dict()
        self.assertEqual(d["session_id"], "s1")
        self.assertEqual(d["timestamp"], "2026-01-01T12:00:00+00:00")
        self.assertEqual(d["feedback_type"], "like")
        self.assertEqual(d["retrieved_docs"], ["d1", "d2"])


class TestSkillTraceRecord(unittest.TestCase):
    """测试 SkillTraceRecord 模型。"""

    def test_auto_trace_id(self):
        """测试 trace_id 自动生成。"""
        r1 = SkillTraceRecord()
        r2 = SkillTraceRecord()
        self.assertIsNotNone(r1.trace_id)
        self.assertIsNotNone(r2.trace_id)
        self.assertNotEqual(r1.trace_id, r2.trace_id)
        self.assertEqual(len(r1.trace_id), 8)  # uuid 前 8 位

    def test_to_dict_with_feedback(self):
        """测试带反馈的序列化。"""
        record = SkillTraceRecord(
            session_id="s1",
            skill_id="search",
            success=False,
            error="timeout",
            user_feedback=FeedbackType.DISLIKE,
        )
        d = record.to_dict()
        self.assertEqual(d["user_feedback"], "dislike")
        self.assertEqual(d["error"], "timeout")
        self.assertFalse(d["success"])


class TestFeedbackRecord(unittest.TestCase):
    """测试 FeedbackRecord 模型。"""

    def test_creation(self):
        """测试创建和序列化。"""
        record = FeedbackRecord(
            session_id="s1",
            feedback_type=FeedbackType.LIKE,
            comment="helpful",
            source="manual",
        )
        d = record.to_dict()
        self.assertEqual(d["feedback_type"], "like")
        self.assertEqual(d["source"], "manual")
        self.assertEqual(d["comment"], "helpful")


class TestRouteLogRecord(unittest.TestCase):
    """测试 RouteLogRecord 模型。"""

    def test_defaults(self):
        """测试默认值。"""
        record = RouteLogRecord(
            query="test",
            top_k=5,
            bm25_weight=0.5,
            vector_weight=0.5,
            retrieved_count=0,
        )
        self.assertEqual(record.bm25_weight, 0.5)
        self.assertEqual(record.vector_weight, 0.5)
        self.assertEqual(record.rerank_threshold, 0.1)


class TestSystemMetricRecord(unittest.TestCase):
    """测试 SystemMetricRecord 模型。"""

    def test_all_fields(self):
        """测试完整字段。"""
        record = SystemMetricRecord(
            cpu_percent=45.0,
            memory_percent=60.0,
            gpu_util=80.0,
            gpu_mem_percent=70.0,
            active_sessions=100,
            qps=10.5,
        )
        d = record.to_dict()
        self.assertEqual(d["cpu_percent"], 45.0)
        self.assertEqual(d["qps"], 10.5)


class TestExperimentConfig(unittest.TestCase):
    """测试 ExperimentConfig 模型。"""

    def test_creation(self):
        """测试实验配置创建。"""
        config = ExperimentConfig(
            param_name="top_k",
            current_value=5,
            candidate_values=[3, 7, 10],
            eval_metric="retrieval_hit_rate",
            min_samples=100,
            max_samples=500,
        )
        self.assertEqual(config.param_name, "top_k")
        self.assertEqual(config.status, ExperimentStatus.PENDING)
        self.assertEqual(len(config.experiment_version), 6)  # uuid 前 6 位


class TestEvalResult(unittest.TestCase):
    """测试 EvalResult 模型。"""

    def test_significance(self):
        """测试显著性判断。"""
        result = EvalResult(
            experiment_id="exp1",
            param_name="top_k",
            candidate_value=7,
            winner="treatment",
            control_metric=0.6,
            treatment_metric=0.75,
            p_value=0.01,
            sample_size=1000,
            is_significant=True,
        )
        d = result.to_dict()
        self.assertEqual(d["winner"], "treatment")
        self.assertTrue(d["is_significant"])
        self.assertEqual(d["candidate_value"], "7")


class TestParamAssignment(unittest.TestCase):
    """测试 ParamAssignment 模型。"""

    def test_baseline_assignment(self):
        """测试基线分配。"""
        assignment = ParamAssignment(
            user_id="user_1",
            param_name="top_k",
            experiment_version="baseline",
            group="control",
            assigned_value=5,
        )
        self.assertEqual(assignment.group, "control")
        self.assertEqual(assignment.assigned_value, 5)


class TestSkillPattern(unittest.TestCase):
    """测试 SkillPattern 模型。"""

    def test_confidence(self):
        """测试置信度计算。"""
        pattern = SkillPattern(
            intent="search",
            skill_sequence=["search", "summarize"],
            frequency=100,
            success_rate=0.8,
        )
        self.assertEqual(pattern.confidence, 80.0)  # 100 * 0.8

    def test_zero_confidence(self):
        """测试零置信度。"""
        pattern = SkillPattern(
            intent="test",
            skill_sequence=["a"],
            frequency=0,
            success_rate=0.0,
        )
        self.assertEqual(pattern.confidence, 0.0)


class TestSkillTemplateCandidate(unittest.TestCase):
    """测试 SkillTemplateCandidate 模型。"""

    def test_default_status(self):
        """测试默认状态。"""
        candidate = SkillTemplateCandidate(name="test", intent="search")
        self.assertEqual(candidate.status, TemplateStatus.DRAFT)
        self.assertEqual(len(candidate.id), 8)

    def test_with_source_pattern(self):
        """测试关联源模式。"""
        pattern = SkillPattern(
            intent="search",
            skill_sequence=["search"],
            frequency=50,
            success_rate=0.9,
        )
        candidate = SkillTemplateCandidate(
            name="search_template",
            intent="search",
            source_pattern=pattern,
        )
        self.assertIsNotNone(candidate.source_pattern)
        self.assertEqual(candidate.source_pattern.frequency, 50)


class TestSkillTemplateReview(unittest.TestCase):
    """测试 SkillTemplateReview 模型。"""

    def test_creation(self):
        """测试审核记录创建。"""
        review = SkillTemplateReview(
            template_id="t1",
            reviewer="admin",
            decision="approve",
            comments="looks good",
        )
        self.assertEqual(review.decision, "approve")
        self.assertEqual(review.reviewer, "admin")
        self.assertIsInstance(review.reviewed_at, datetime)


class TestTemplateActivation(unittest.TestCase):
    """测试 TemplateActivation 模型。"""

    def test_default_canary(self):
        """测试默认灰度配置。"""
        activation = TemplateActivation(template_id="t1")
        self.assertEqual(activation.rollout_percent, 0.1)
        self.assertEqual(activation.status, TemplateStatus.CANARY)


class TestEnums(unittest.TestCase):
    """测试枚举类型。"""

    def test_feedback_type_values(self):
        """测试反馈类型值。"""
        self.assertEqual(FeedbackType.LIKE.value, "like")
        self.assertEqual(FeedbackType.DISLIKE.value, "dislike")

    def test_experiment_status_values(self):
        """测试实验状态值。"""
        self.assertEqual(ExperimentStatus.PENDING.value, "pending")
        self.assertEqual(ExperimentStatus.CONVERGED.value, "converged")
        self.assertEqual(ExperimentStatus.FAILED.value, "failed")

    def test_template_status_values(self):
        """测试模板状态值。"""
        self.assertEqual(TemplateStatus.DRAFT.value, "draft")
        self.assertEqual(TemplateStatus.CANARY.value, "canary")
        self.assertEqual(TemplateStatus.ACTIVE.value, "active")
        self.assertEqual(TemplateStatus.ROLLED_BACK.value, "rolled_back")


if __name__ == "__main__":
    unittest.main()
