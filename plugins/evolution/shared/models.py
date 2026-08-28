"""
EchoServe Evolution System — Data Models

全链路共享的数据模型定义。
使用 dataclass 确保类型安全和序列化兼容性。
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ─── 基础枚举 ───────────────────────────────────

class FeedbackType(str, Enum):
    """用户反馈类型。"""
    LIKE = "like"
    DISLIKE = "dislike"


class ExperimentStatus(str, Enum):
    """实验状态。"""
    PENDING = "pending"
    RUNNING = "running"
    CONVERGED = "converged"
    FAILED = "failed"
    PAUSED = "paused"
    APPROVED = "approved"
    REJECTED = "rejected"


class TemplateStatus(str, Enum):
    """技能模板状态。"""
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANARY = "canary"
    ACTIVE = "active"
    DISABLED = "disabled"
    ROLLED_BACK = "rolled_back"


class DegradationLevel(str, Enum):
    """降级级别。"""
    NORMAL = "normal"
    LEVEL_1 = "level_1"  # 单参数实验暂停
    LEVEL_2 = "level_2"  # 灰度模板禁用 + 实验暂停
    LEVEL_3 = "level_3"  # EvolutionService 只读


# ─── Phase 1: 数据采集模型 ──────────────────────


def _serialize_ts(ts: Any) -> str:
    """Serialize timestamp to ISO string (defensive: accepts datetime or str)."""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)

@dataclass
class ChatLogRecord:
    """对话日志记录。"""
    session_id: str
    query: str
    reply: str
    retrieved_docs: list[str] = field(default_factory=list)
    latency_ms: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    feedback_type: FeedbackType | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _serialize_ts(self.timestamp)
        if self.feedback_type:
            d["feedback_type"] = self.feedback_type.value
        return d


@dataclass
class SkillTraceRecord:
    """技能执行链路记录。"""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_id: str = ""
    skill_id: str = ""
    skill_sequence: list[str] = field(default_factory=list)
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    latency_ms: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0
    user_feedback: FeedbackType | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _serialize_ts(self.timestamp)
        if self.user_feedback:
            d["user_feedback"] = self.user_feedback.value
        return d


@dataclass
class FeedbackRecord:
    """用户反馈记录。"""
    session_id: str
    feedback_type: FeedbackType
    comment: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "manual"  # manual / implicit

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _serialize_ts(self.timestamp)
        d["feedback_type"] = self.feedback_type.value
        return d


@dataclass
class RouteLogRecord:
    """路由决策日志。"""
    query: str
    top_k: int
    bm25_weight: float
    vector_weight: float
    retrieved_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rerank_threshold: float = 0.1
    final_doc_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _serialize_ts(self.timestamp)
        return d


@dataclass
class SystemMetricRecord:
    """系统指标记录。"""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    gpu_util: float = 0.0
    gpu_mem_percent: float = 0.0
    active_sessions: int = 0
    qps: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _serialize_ts(self.timestamp)
        return d


# ─── Phase 2: 参数调优模型 ──────────────────────

@dataclass
class ExperimentConfig:
    """单参数实验配置。"""
    param_name: str
    current_value: Any
    candidate_values: list[Any]
    eval_metric: str
    min_samples: int = 500
    max_samples: int = 2000
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: ExperimentStatus = ExperimentStatus.PENDING
    experiment_version: str = field(default_factory=lambda: str(uuid.uuid4())[:6])


@dataclass
class EvalResult:
    """实验评估结果。"""
    experiment_id: str
    param_name: str
    candidate_value: Any
    winner: str = ""  # "treatment" or "control"
    control_metric: float = 0.0
    treatment_metric: float = 0.0
    p_value: float = 1.0
    sample_size: int = 0
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_significant: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evaluated_at"] = self.evaluated_at.isoformat()
        d["candidate_value"] = str(self.candidate_value)
        return d


@dataclass
class ParamAssignment:
    """用户参数分配记录。"""
    user_id: str
    param_name: str
    experiment_version: str
    group: str = ""  # "control" or "treatment"
    assigned_value: Any = None
    assigned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Phase 3: 技能进化模型 ──────────────────────

@dataclass
class SkillPattern:
    """挖掘出的技能执行模式。"""
    intent: str
    skill_sequence: list[str]
    frequency: int = 0
    success_rate: float = 0.0
    sample_records: list[str] = field(default_factory=list)
    avg_latency_ms: float = 0.0
    mined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def confidence(self) -> float:
        """模式置信度 = 出现次数 * 成功率。"""
        return self.frequency * self.success_rate


@dataclass
class SkillTemplateCandidate:
    """候选技能模板。"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    intent: str = ""
    trigger_conditions: list[str] = field(default_factory=list)
    skill_sequence: list[str] = field(default_factory=list)
    parameter_mapping: dict[str, str] = field(default_factory=dict)
    expected_output_template: str = ""
    source_pattern: SkillPattern | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: TemplateStatus = TemplateStatus.DRAFT
    simulation_pass_rate: float = 0.0


@dataclass
class SkillTemplateReview:
    """人工审核记录。"""
    template_id: str
    reviewer: str
    decision: str = ""  # "approve", "reject", "modify"
    comments: str | None = None
    reviewed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    modified_trigger_conditions: list[str] | None = None
    modified_skill_sequence: list[str] | None = None


@dataclass
class TemplateActivation:
    """模板激活记录（灰度/全量）。"""
    template_id: str
    rollout_percent: float = 0.1
    status: TemplateStatus = TemplateStatus.CANARY
    activated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metrics_snapshot: dict[str, float] = field(default_factory=dict)


# NOTE: EvolutionConfig 已迁移至 config/settings.py (Pydantic BaseModel 版本)
# 统一配置源，消除 dataclass 与 Pydantic 双重定义的维护负担
