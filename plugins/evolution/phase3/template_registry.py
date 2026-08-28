"""
EchoServe Evolution System — Phase 3: TemplateRegistry

模板注册表。
管理已审核通过模板的全生命周期：灰度发布 -> 全量激活 -> 回滚。

设计约束：
- 状态机：APPROVED -> CANARY -> ACTIVE -> DISABLED/ROLLED_BACK
- 灰度发布：按用户/流量百分比逐步放量
- 回滚机制：一键回退到上一个稳定版本
- 版本管理：同一意图支持多版本共存
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..shared.failover import FailoverManager
from ..shared.models import (
    SkillTemplateCandidate,
    TemplateActivation,
    TemplateStatus,
)

logger = logging.getLogger("echoserve.evolution.template_registry")


@dataclass
class TemplateVersion:
    """模板版本信息。"""

    candidate: SkillTemplateCandidate
    activation: TemplateActivation
    previous_version: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)


class TemplateRegistry:
    """
    模板注册表。

    职责：
    1. 注册已审核通过的模板
    2. 管理灰度发布（Canary）
    3. 管理全量激活（Active）
    4. 支持回滚到上一个版本
    5. 监控模板运行指标

    发布流程：
        APPROVED -> 注册 -> CANARY (10% 流量) -> 监控 -> ACTIVE (100% 流量)
                                        |
                                        v
                                  指标不达标 -> ROLLED_BACK

    使用示例：
        registry = TemplateRegistry()
        registry.register(candidate)
        registry.canary(template_id, rollout_percent=0.1)
        registry.promote(template_id)      # 全量
        registry.rollback(template_id)     # 回滚
    """

    DEFAULT_CANARY_PERCENT: float = 0.1  # 默认灰度 10%

    def __init__(
        self,
        failover: FailoverManager | None = None,
        auto_promote_enabled: bool = False,
        promote_threshold: float = 0.95,
    ) -> None:
        self._templates: dict[str, TemplateVersion] = {}
        self._intent_index: dict[str, list[str]] = {}  # intent -> [template_ids]
        self._active_versions: dict[str, str] = {}  # intent -> active_template_id
        self._failover = failover
        self._rollback_history: list[dict[str, Any]] = []
        self._auto_promote_enabled = auto_promote_enabled
        self._promote_threshold: float = promote_threshold
        logger.info(
            f"[TemplateRegistry] Initialized (auto_promote={auto_promote_enabled})"
        )

    def register(self, candidate: SkillTemplateCandidate) -> str:
        """
        注册审核通过的模板。

        状态需为 APPROVED，注册后状态变为 CANARY-ready。
        """
        if candidate.status != TemplateStatus.APPROVED:
            raise ValueError(
                f"Template must be APPROVED to register, got {candidate.status}"
            )

        candidate.status = TemplateStatus.CANARY
        activation = TemplateActivation(
            template_id=candidate.id,
            rollout_percent=0.0,
            status=TemplateStatus.CANARY,
        )

        version = TemplateVersion(
            candidate=candidate,
            activation=activation,
        )
        self._templates[candidate.id] = version
        self._intent_index.setdefault(candidate.intent, []).append(candidate.id)

        logger.info(
            f"[TemplateRegistry] Registered: {candidate.id} for intent='{candidate.intent}'"
        )
        return candidate.id

    def canary(self, template_id: str, rollout_percent: float = DEFAULT_CANARY_PERCENT) -> bool:
        """
        启动灰度发布。

        Args:
            template_id: 模板 ID
            rollout_percent: 流量百分比 (0.0 - 1.0)

        Returns:
            是否成功启动
        """
        version = self._templates.get(template_id)
        if not version:
            logger.warning(f"[TemplateRegistry] Template not found: {template_id}")
            return False

        if version.candidate.status not in (TemplateStatus.CANARY, TemplateStatus.ACTIVE):
            logger.warning(
                f"[TemplateRegistry] Cannot canary, status={version.candidate.status}"
            )
            return False

        version.activation.rollout_percent = max(0.0, min(1.0, rollout_percent))
        version.activation.status = TemplateStatus.CANARY
        version.candidate.status = TemplateStatus.CANARY

        logger.info(
            f"[TemplateRegistry] Canary started: {template_id} "
            f"at {version.activation.rollout_percent * 100:.1f}%"
        )
        return True

    def promote(self, template_id: str) -> bool:
        """
        全量激活模板。

        将流量百分比提升到 100%，并将同一意图的旧版本降级。
        """
        version = self._templates.get(template_id)
        if not version:
            logger.warning(f"[TemplateRegistry] Template not found: {template_id}")
            return False

        intent = version.candidate.intent

        # 记录上一个活跃版本用于回滚
        if intent in self._active_versions:
            old_id = self._active_versions[intent]
            if old_id in self._templates:
                self._templates[old_id].candidate.status = TemplateStatus.DISABLED
                version.previous_version = old_id
                logger.info(
                    f"[TemplateRegistry] Disabled previous: {old_id} for intent='{intent}'"
                )

        # 激活新版本
        version.activation.rollout_percent = 1.0
        version.activation.status = TemplateStatus.ACTIVE
        version.candidate.status = TemplateStatus.ACTIVE
        self._active_versions[intent] = template_id

        logger.info(
            f"[TemplateRegistry] Promoted to ACTIVE: {template_id} for intent='{intent}'"
        )
        return True

    def rollback(self, template_id: str) -> bool:
        """
        回滚模板到上一个版本。

        当前版本变为 ROLLED_BACK，上一个版本恢复为 ACTIVE。
        """
        version = self._templates.get(template_id)
        if not version:
            logger.warning(f"[TemplateRegistry] Template not found: {template_id}")
            return False

        prev_id = version.previous_version
        if not prev_id or prev_id not in self._templates:
            # 没有上一个版本，直接禁用当前版本
            version.candidate.status = TemplateStatus.DISABLED
            version.activation.status = TemplateStatus.DISABLED
            intent = version.candidate.intent
            if self._active_versions.get(intent) == template_id:
                del self._active_versions[intent]

            self._rollback_history.append(
                {
                    "template_id": template_id,
                    "to": "disabled",
                    "timestamp": version.activation.activated_at.isoformat(),
                }
            )
            logger.warning(
                f"[TemplateRegistry] Rolled back to disabled: {template_id}"
            )
            return True

        # 恢复上一个版本
        prev_version = self._templates[prev_id]
        prev_version.candidate.status = TemplateStatus.ACTIVE
        prev_version.activation.status = TemplateStatus.ACTIVE
        prev_version.activation.rollout_percent = 1.0

        # 禁用当前版本
        version.candidate.status = TemplateStatus.ROLLED_BACK
        version.activation.status = TemplateStatus.ROLLED_BACK
        version.activation.rollout_percent = 0.0

        intent = version.candidate.intent
        self._active_versions[intent] = prev_id

        self._rollback_history.append(
            {
                "template_id": template_id,
                "to": prev_id,
                "timestamp": version.activation.activated_at.isoformat(),
            }
        )

        logger.warning(
            f"[TemplateRegistry] Rolled back: {template_id} -> {prev_id}"
        )
        return True

    def disable(self, template_id: str) -> bool:
        """禁用模板（非回滚，直接下线）。"""
        version = self._templates.get(template_id)
        if not version:
            return False

        version.candidate.status = TemplateStatus.DISABLED
        version.activation.status = TemplateStatus.DISABLED
        version.activation.rollout_percent = 0.0

        intent = version.candidate.intent
        if self._active_versions.get(intent) == template_id:
            del self._active_versions[intent]

        logger.info(f"[TemplateRegistry] Disabled: {template_id}")
        return True

    def get_active(self, intent: str) -> SkillTemplateCandidate | None:
        """获取指定意图的当前活跃模板。"""
        template_id = self._active_versions.get(intent)
        if not template_id:
            return None
        version = self._templates.get(template_id)
        return version.candidate if version else None

    def get_template(self, template_id: str) -> TemplateVersion | None:
        """获取模板版本信息。"""
        return self._templates.get(template_id)

    def list_by_intent(self, intent: str) -> list[TemplateVersion]:
        """获取指定意图的所有版本。"""
        return [
            self._templates[tid]
            for tid in self._intent_index.get(intent, [])
            if tid in self._templates
        ]

    def list_active(self) -> list[SkillTemplateCandidate]:
        """获取所有活跃模板。"""
        return [
            self._templates[tid].candidate
            for tid in self._active_versions.values()
            if tid in self._templates
        ]

    def record_metrics(self, template_id: str, metrics: dict[str, float]) -> None:
        """记录模板运行指标。"""
        version = self._templates.get(template_id)
        if version:
            version.metrics.update(metrics)
            version.activation.metrics_snapshot.update(metrics)

            # 自动全量：灰度成功率达标（需显式启用 auto_promote）
            if (
                self._auto_promote_enabled
                and version.candidate.status == TemplateStatus.CANARY
                and metrics.get("success_rate", 0) >= self._promote_threshold
                and version.activation.rollout_percent >= self.DEFAULT_CANARY_PERCENT
            ):
                logger.info(
                    f"[TemplateRegistry] Auto-promote threshold met: {template_id}"
                )
                self.promote(template_id)

    def get_rollback_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取回滚历史。"""
        return self._rollback_history[-limit:]

    def summary(self) -> dict[str, Any]:
        """获取注册表摘要。"""
        return {
            "total_templates": len(self._templates),
            "active_intents": len(self._active_versions),
            "rollback_count": len(self._rollback_history),
            "status_breakdown": self._status_breakdown(),
        }

    def _status_breakdown(self) -> dict[str, int]:
        """统计各状态模板数量。"""
        counts: dict[str, int] = {}
        for v in self._templates.values():
            status = v.candidate.status.value
            counts[status] = counts.get(status, 0) + 1
        return counts
