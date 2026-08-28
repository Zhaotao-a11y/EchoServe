"""
EchoServe Evolution System — Phase 1: EvolutionQuery

查询接口层。
提供 REST API 用于人工分析、调试和报表生成。

设计约束：
- 所有查询操作只读，不修改存储
- 支持按时间范围、条件过滤、分页
- 查询耗时控制在 500ms 以内（P95）
- 所有数据端点需 JWT Bearer 认证（/health 豁免）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from config.settings import settings as default_settings

logger = logging.getLogger("echoserve.evolution.query")

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


def _serialize_ts(ts: Any) -> str:
    """Serialize timestamp to ISO string (defensive: accepts datetime or str)."""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


# Bearer Token 安全方案（auto_error=False 使健康检查不需要认证）
_security_scheme = HTTPBearer(auto_error=False)


# ─── JWT 认证依赖 ──────────────────────────────────

async def verify_jwt(
    credentials: HTTPAuthorizationCredentials | None = Security(_security_scheme),
) -> dict[str, Any]:
    """验证 JWT Bearer Token，返回 payload。

    Raises:
        HTTPException 401: 未提供凭据 / Token 过期 / Token 无效
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="未提供认证凭据")
    token = credentials.credentials
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            default_settings.security.jwt_secret,
            algorithms=["HS256"],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token 无效")
    except Exception as e:
        logger.error(f"[EvolutionQuery] JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="认证失败")


# ─── Pydantic 请求/响应模型 ───────────────────────

class ChatLogQuery(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    feedback_type: str | None = None
    min_latency_ms: int | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class SkillStatsQuery(BaseModel):
    skill_id: str | None = None
    days: int = Field(default=7, ge=1, le=90)


class FeedbackSummaryQuery(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


class TimeRangeQuery(BaseModel):
    start: datetime
    end: datetime
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


# ─── 响应模型 ─────────────────────────────────────

class ChatLogResponse(BaseModel):
    total: int
    records: list[dict[str, Any]]
    has_more: bool


class SkillStatsResponse(BaseModel):
    skill_id: str | None
    total_calls: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    error_distribution: dict[str, int]
    period_days: int


class FeedbackSummaryResponse(BaseModel):
    total_feedback: int
    likes: int
    dislikes: int
    nps: float
    period_days: int
    trend: list[dict[str, Any]]


class RouteStatsResponse(BaseModel):
    total_queries: int
    avg_top_k: float
    avg_bm25_weight: float
    avg_retrieved_count: float
    latency_distribution: dict[str, float]


# ─── 依赖注入 ─────────────────────────────────────

# 由插件主入口注入（保持向后兼容）
_evolution_store: Any = None  # type: ignore[assignment]
_evolution_plugin: Any = None


def set_store(store: Any) -> None:
    """注入 EvolutionStore 实例（由 EvolutionPlugin.on_init 调用）。"""
    global _evolution_store
    _evolution_store = store


def set_evolution_plugin(plugin: Any) -> None:
    """注入 EvolutionPlugin 实例（供 Phase 2/3 查询端点使用）。"""
    global _evolution_plugin
    _evolution_plugin = plugin


async def get_store() -> Any:
    """FastAPI 依赖：获取 EvolutionStore 实例。

    Raises:
        HTTPException 503: Store 未初始化
    """
    if _evolution_store is None:
        raise HTTPException(status_code=503, detail="EvolutionStore not initialized")
    return _evolution_store


# ─── API 端点 ─────────────────────────────────────

@router.get("/health", summary="Evolution Service 健康检查")
async def health() -> dict[str, str]:
    """检查 EvolutionService 健康状态（无需认证）。"""
    return {
        "status": "healthy",
        "store_connected": str(_evolution_store is not None),
    }


@router.post("/chat-log", response_model=ChatLogResponse, summary="查询对话记录")
async def query_chat_log(
    params: ChatLogQuery,
    store: Any = Depends(get_store),
    _user: dict[str, Any] = Depends(verify_jwt),
) -> ChatLogResponse:
    """
    按时间范围和条件查询对话记录。

    用于人工分析对话质量、检索效果等。
    """
    conditions: dict[str, Any] = {}
    if params.feedback_type:
        conditions["feedback_type"] = params.feedback_type

    try:
        records = await store.query(
            table="chat_log",
            start=params.start,
            end=params.end,
            conditions=conditions if conditions else None,
            limit=params.limit,
            offset=params.offset,
        )

        total = await store.count(
            table="chat_log",
            start=params.start,
            end=params.end,
            conditions=conditions if conditions else None,
        )

        return ChatLogResponse(
            total=total,
            records=records,
            has_more=(params.offset + len(records)) < total,
        )
    except Exception as e:
        logger.error(f"[EvolutionQuery] query_chat_log failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skill-stats", response_model=SkillStatsResponse, summary="统计技能调用指标")
async def skill_statistics(
    params: SkillStatsQuery,
    store: Any = Depends(get_store),
    _user: dict[str, Any] = Depends(verify_jwt),
) -> SkillStatsResponse:
    """
    统计技能调用成功率、平均耗时、错误分布。

    用于评估技能稳定性和性能。
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=params.days)

    try:
        conditions: dict[str, Any] = {}
        if params.skill_id:
            conditions["skill_id"] = params.skill_id

        records = await store.query(
            table="skill_trace",
            start=start,
            end=end,
            conditions=conditions if conditions else None,
            limit=10000,
        )

        total = len(records)
        if total == 0:
            return SkillStatsResponse(
                skill_id=params.skill_id,
                total_calls=0,
                success_count=0,
                failure_count=0,
                success_rate=0.0,
                avg_latency_ms=0.0,
                p95_latency_ms=0.0,
                error_distribution={},
                period_days=params.days,
            )

        success_count = sum(1 for r in records if r.get("success"))
        failure_count = total - success_count
        latencies = [r.get("latency_ms", 0) for r in records]
        sorted_latencies = sorted(latencies)
        p95_idx = int(len(sorted_latencies) * 0.95)

        # 错误分布
        error_dist: dict[str, int] = {}
        for r in records:
            if not r.get("success"):
                err = r.get("error", "unknown")
                error_dist[err] = error_dist.get(err, 0) + 1

        return SkillStatsResponse(
            skill_id=params.skill_id,
            total_calls=total,
            success_count=success_count,
            failure_count=failure_count,
            success_rate=success_count / total * 100,
            avg_latency_ms=sum(latencies) / len(latencies),
            p95_latency_ms=sorted_latencies[min(p95_idx, len(sorted_latencies) - 1)],
            error_distribution=error_dist,
            period_days=params.days,
        )
    except Exception as e:
        logger.error(f"[EvolutionQuery] skill_statistics failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback-summary", response_model=FeedbackSummaryResponse, summary="汇总用户反馈")
async def feedback_summary(
    params: FeedbackSummaryQuery,
    store: Any = Depends(get_store),
    _user: dict[str, Any] = Depends(verify_jwt),
) -> FeedbackSummaryResponse:
    """
    汇总正负反馈数量、满意度趋势。

    用于评估对话质量和用户满意度变化趋势。
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=params.days)

    try:
        records = await store.query(
            table="feedback",
            start=start,
            end=end,
            limit=10000,
        )

        total = len(records)
        likes = sum(1 for r in records if r.get("feedback_type") == "like")
        dislikes = sum(1 for r in records if r.get("feedback_type") == "dislike")
        nps = (likes - dislikes) / max(total, 1) * 100

        # 按天分桶的趋势
        trend_map: dict[str, dict[str, int]] = {}
        for r in records:
            day = r.get("timestamp", "")[:10]  # YYYY-MM-DD
            if day not in trend_map:
                trend_map[day] = {"likes": 0, "dislikes": 0, "total": 0}
            trend_map[day]["total"] += 1
            if r.get("feedback_type") == "like":
                trend_map[day]["likes"] += 1
            else:
                trend_map[day]["dislikes"] += 1

        trend = [
            {"date": k, **v, "nps": (v["likes"] - v["dislikes"]) / max(v["total"], 1) * 100}
            for k, v in sorted(trend_map.items())
        ]

        return FeedbackSummaryResponse(
            total_feedback=total,
            likes=likes,
            dislikes=dislikes,
            nps=nps,
            period_days=params.days,
            trend=trend,
        )
    except Exception as e:
        logger.error(f"[EvolutionQuery] feedback_summary failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/route-stats", response_model=RouteStatsResponse, summary="路由决策统计")
async def route_statistics(
    params: TimeRangeQuery,
    store: Any = Depends(get_store),
    _user: dict[str, Any] = Depends(verify_jwt),
) -> RouteStatsResponse:
    """
    统计路由决策参数分布和延迟。

    用于分析检索策略效果。
    """
    try:
        records = await store.query(
            table="route_log",
            start=params.start,
            end=params.end,
            limit=params.limit,
            offset=params.offset,
        )

        total = len(records)
        if total == 0:
            return RouteStatsResponse(
                total_queries=0,
                avg_top_k=0.0,
                avg_bm25_weight=0.0,
                avg_retrieved_count=0.0,
                latency_distribution={},
            )

        top_ks = [r.get("top_k", 0) for r in records]
        bm25_weights = [r.get("bm25_weight", 0) for r in records]
        retrieved = [r.get("retrieved_count", 0) for r in records]

        return RouteStatsResponse(
            total_queries=total,
            avg_top_k=sum(top_ks) / len(top_ks),
            avg_bm25_weight=sum(bm25_weights) / len(bm25_weights),
            avg_retrieved_count=sum(retrieved) / len(retrieved),
            latency_distribution={"p50": 0.0, "p95": 0.0, "p99": 0.0},  # 简化
        )
    except Exception as e:
        logger.error(f"[EvolutionQuery] route_statistics failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", summary="存储统计概览")
async def store_stats(
    store: Any = Depends(get_store),
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回 EvolutionStore 的存储统计概览。"""
    try:
        return await store.get_stats()
    except Exception as e:
        logger.error(f"[EvolutionQuery] store_stats failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Phase 2: A/B 实验查询 ───────────────────────


@router.get("/experiments", summary="A/B 实验列表")
async def list_experiments(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回所有 A/B 实验及其状态。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        experimenter = _evolution_plugin.experimenter
        experiments = []
        for exp_id, state in experimenter.experiments.items():
            cfg = state.config
            stats = experimenter.get_assignment_stats(exp_id)
            experiments.append({
                "exp_id": exp_id,
                "param_name": cfg.param_name,
                "candidate_values": [str(v) for v in cfg.candidate_values],
                "eval_metric": cfg.eval_metric,
                "status": cfg.status.value if hasattr(cfg.status, "value") else str(cfg.status),
                "traffic_percent": experimenter.traffic_percent,
                "created_at": _serialize_ts(cfg.created_at),
                "control_group_size": stats.get("control_group_size", 0),
                "treatment_group_size": stats.get("treatment_group_size", 0),
                "control_metrics_count": stats.get("control_metrics_count", 0),
                "treatment_metrics_count": stats.get("treatment_metrics_count", 0),
            })
        return {"total": len(experiments), "experiments": experiments}
    except Exception as e:
        logger.error(f"[EvolutionQuery] list_experiments failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Phase 3: 模式挖掘结果 ───────────────────────

@router.get("/patterns", summary="已挖掘技能模式")
async def list_patterns(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回 PatternMiner 已挖掘的技能模式。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        miner = _evolution_plugin.pattern_miner
        mined = miner.mine()
        patterns = []
        for p in mined:
            patterns.append({
                "intent": p.intent,
                "skill_sequence": p.skill_sequence,
                "frequency": p.frequency,
                "success_rate": p.success_rate,
                "avg_latency_ms": p.avg_latency_ms,
                "confidence": p.confidence,
            })
        patterns.sort(key=lambda x: x["confidence"], reverse=True)
        return {"total": len(patterns), "patterns": patterns[:50]}
    except Exception as e:
        logger.error(f"[EvolutionQuery] list_patterns failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Phase 3: 模板注册表 ─────────────────────────

@router.get("/templates", summary="模板注册表概览")
async def list_templates(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回 TemplateRegistry 中所有模板的状态。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        registry = _evolution_plugin.template_registry
        templates = []
        for tid, version in registry._templates.items():
            candidate = version.candidate
            templates.append({
                "template_id": tid,
                "name": candidate.name,
                "intent": candidate.intent,
                "status": candidate.status.value if hasattr(candidate.status, "value") else str(candidate.status),
                "rollout_percent": version.activation.rollout_percent,
                "previous_version": version.previous_version,
                "metrics": version.metrics,
                "generated_at": _serialize_ts(candidate.generated_at),
            })
        return {
            "total": len(templates),
            "templates": templates,
            "summary": registry.summary(),
        }
    except Exception as e:
        logger.error(f"[EvolutionQuery] list_templates failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── 降级容错状态 ─────────────────────────────────

@router.get("/failover", summary="降级容错状态")
async def failover_status(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回 FailoverManager 的当前降级状态。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        failover = _evolution_plugin.failover
        return {
            "current_level": failover.current_level.value if hasattr(failover.current_level, "value") else str(failover.current_level),
            "rules_count": failover.rules_count,
            "history_count": failover.history_count,
            "history": failover.get_history(20),
        }
    except Exception as e:
        logger.error(f"[EvolutionQuery] failover_status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── 进化系统总览 ─────────────────────────────────

@router.get("/overview", summary="进化系统总览")
async def evolution_overview(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回进化系统全貌（Phase 1-3 汇总）。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        plugin = _evolution_plugin
        store = _evolution_store

        # 并行化：async store stats 与 sync 操作并行执行
        store_stats_task = asyncio.create_task(store.get_stats()) if store else None

        # 同步操作（在 store_stats_task 执行期间并行运行）
        collector_stats = plugin.collector.get_stats() if plugin.collector else {}

        experimenter = plugin.experimenter
        active_experiments = sum(
            1 for state in experimenter.experiments.values()
            if state.config.status.value == "running"
        ) if experimenter else 0

        miner = plugin.pattern_miner
        mined = miner.mine() if miner else []
        pattern_count = len(mined)

        registry = plugin.template_registry
        template_summary = registry.summary() if registry else {}

        failover = plugin.failover
        current_level = (
            failover.current_level.value
            if hasattr(failover.current_level, "value")
            else str(failover.current_level)
        ) if failover else "unknown"

        # 等待 async 任务完成
        store_stats = await store_stats_task if store_stats_task else {}

        return {
            "store": store_stats,
            "collector": collector_stats,
            "experiments": {
                "total": len(experimenter.experiments),
                "active": active_experiments,
            },
            "patterns": {
                "total": pattern_count,
            },
            "templates": template_summary,
            "failover": {
                "current_level": current_level,
                "rules_count": failover.rules_count,
            },
            "config": {
                "mining_min_success_rate": getattr(plugin.config, "mining_min_success_rate", 0.9),
                "mining_min_support": getattr(plugin.config, "mining_min_support", 10),
                "template_auto_promote": getattr(plugin.config, "template_auto_promote", False),
                "eval_interval": getattr(plugin.config, "eval_interval", 3600),
            },
        }
    except Exception as e:
        logger.error(f"[EvolutionQuery] evolution_overview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Phase 3: 人工审核工作台 ─────────────────────


class ReviewActionRequest(BaseModel):
    """审核操作请求体。"""

    comments: str | None = Field(default=None, description="审核意见")


@router.get("/review/pending", summary="待审核模板列表")
async def list_pending_reviews(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回所有待审核的候选模板。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        reviewer = _evolution_plugin.reviewer
        if not reviewer:
            raise HTTPException(status_code=503, detail="Reviewer not initialized")
        pending = reviewer.list_pending()
        return {
            "total": len(pending),
            "pending": [
                {
                    "id": c.id,
                    "name": c.name,
                    "intent": c.intent,
                    "trigger_conditions": c.trigger_conditions,
                    "skill_sequence": c.skill_sequence,
                    "parameter_mapping": c.parameter_mapping,
                    "expected_output_template": c.expected_output_template,
                    "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                    "generated_at": _serialize_ts(c.generated_at),
                    "simulation_pass_rate": c.simulation_pass_rate,
                }
                for c in pending
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[EvolutionQuery] list_pending_reviews failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review/stats", summary="审核统计")
async def review_stats(
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """返回审核队列统计（待审/已批准/已驳回）。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        reviewer = _evolution_plugin.reviewer
        if not reviewer:
            raise HTTPException(status_code=503, detail="Reviewer not initialized")
        stats = reviewer.get_stats()
        return {
            "pending": stats.get("pending", 0),
            "approved": stats.get("approved", 0),
            "rejected": stats.get("rejected", 0),
            "total": stats.get("total", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[EvolutionQuery] review_stats failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/{candidate_id}/approve", summary="批准候选模板")
async def approve_review(
    candidate_id: str,
    req: ReviewActionRequest,
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """通过审核，候选模板状态变为 APPROVED。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        reviewer = _evolution_plugin.reviewer
        if not reviewer:
            raise HTTPException(status_code=503, detail="Reviewer not initialized")
        reviewer_name = _user.get("username", "unknown")
        success = reviewer.approve(candidate_id, reviewer_name, req.comments)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Candidate not found or not in PENDING_REVIEW status",
            )
        return {
            "candidate_id": candidate_id,
            "decision": "approved",
            "reviewer": reviewer_name,
            "comments": req.comments,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[EvolutionQuery] approve_review failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/{candidate_id}/reject", summary="驳回候选模板")
async def reject_review(
    candidate_id: str,
    req: ReviewActionRequest,
    _user: dict[str, Any] = Depends(verify_jwt),
) -> dict[str, Any]:
    """驳回审核，候选模板状态变为 REJECTED。"""
    if not _evolution_plugin:
        raise HTTPException(status_code=503, detail="EvolutionPlugin not initialized")
    try:
        reviewer = _evolution_plugin.reviewer
        if not reviewer:
            raise HTTPException(status_code=503, detail="Reviewer not initialized")
        reviewer_name = _user.get("username", "unknown")
        success = reviewer.reject(candidate_id, reviewer_name, req.comments)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Candidate not found or not in PENDING_REVIEW status",
            )
        return {
            "candidate_id": candidate_id,
            "decision": "rejected",
            "reviewer": reviewer_name,
            "comments": req.comments,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[EvolutionQuery] reject_review failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
