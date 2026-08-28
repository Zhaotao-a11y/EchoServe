"""
EchoServe Evolution System — Phase 3: Reviewer

人工审核台。
管理候选模板的人工审核流程，支持审批/驳回/修改操作。

设计约束：
- 审核状态机：DRAFT -> PENDING_REVIEW -> APPROVED/REJECTED
- 记录审核人、审核意见、修改内容
- 支持批量审核
- 审核通过后方可进入 TemplateRegistry 的灰度阶段
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..shared.models import (
    SkillTemplateCandidate,
    SkillTemplateReview,
    TemplateStatus,
)

logger = logging.getLogger("echoserve.evolution.reviewer")


@dataclass
class ReviewQueue:
    """审核队列。"""

    pending: list[str] = field(default_factory=list)
    approved: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


class Reviewer:
    """
    人工审核台。

    职责：
    1. 接收 TemplateGenerator 生成的候选模板
    2. 管理审核队列（待审/已通过/已驳回）
    3. 记录审核意见和修改建议
    4. 支持批量提交到 TemplateRegistry

    审核流程：
        DRAFT --(submit)--> PENDING_REVIEW --(approve)--> APPROVED
                                          --(reject)--> REJECTED
                                          --(modify)--> DRAFT (重新生成)

    使用示例：
        reviewer = Reviewer()
        reviewer.submit(candidate)           # 提交审核
        reviewer.approve(cid, "admin", "OK") # 通过
        approved = reviewer.list_approved()  # 获取已通过列表
    """

    def __init__(self) -> None:
        self._candidates: dict[str, SkillTemplateCandidate] = {}
        self._reviews: dict[str, list[SkillTemplateReview]] = {}
        self._queue = ReviewQueue()
        logger.info("[Reviewer] Initialized")

    def submit(self, candidate: SkillTemplateCandidate) -> str:
        """
        提交候选模板进入审核队列。

        Returns:
            候选模板 ID
        """
        candidate.status = TemplateStatus.PENDING_REVIEW
        self._candidates[candidate.id] = candidate
        self._reviews[candidate.id] = []
        self._queue.pending.append(candidate.id)

        logger.info(f"[Reviewer] Submitted: {candidate.id} '{candidate.name}'")
        return candidate.id

    def submit_batch(self, candidates: list[SkillTemplateCandidate]) -> list[str]:
        """批量提交候选模板。"""
        return [self.submit(c) for c in candidates]

    def approve(
        self,
        candidate_id: str,
        reviewer_name: str,
        comments: str | None = None,
    ) -> bool:
        """
        通过审核。

        Returns:
            是否成功
        """
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            logger.warning(f"[Reviewer] Candidate not found: {candidate_id}")
            return False

        if candidate.status != TemplateStatus.PENDING_REVIEW:
            logger.warning(
                f"[Reviewer] Cannot approve, status={candidate.status}: {candidate_id}"
            )
            return False

        candidate.status = TemplateStatus.APPROVED
        self._queue.pending.remove(candidate_id)
        self._queue.approved.append(candidate_id)

        review = SkillTemplateReview(
            template_id=candidate_id,
            reviewer=reviewer_name,
            decision="approve",
            comments=comments,
        )
        self._reviews[candidate_id].append(review)

        logger.info(
            f"[Reviewer] Approved: {candidate_id} by {reviewer_name}"
        )
        return True

    def reject(
        self,
        candidate_id: str,
        reviewer_name: str,
        comments: str | None = None,
    ) -> bool:
        """驳回审核。"""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            logger.warning(f"[Reviewer] Candidate not found: {candidate_id}")
            return False

        if candidate.status != TemplateStatus.PENDING_REVIEW:
            logger.warning(
                f"[Reviewer] Cannot reject, status={candidate.status}: {candidate_id}"
            )
            return False

        candidate.status = TemplateStatus.REJECTED
        self._queue.pending.remove(candidate_id)
        self._queue.rejected.append(candidate_id)

        review = SkillTemplateReview(
            template_id=candidate_id,
            reviewer=reviewer_name,
            decision="reject",
            comments=comments,
        )
        self._reviews[candidate_id].append(review)

        logger.info(
            f"[Reviewer] Rejected: {candidate_id} by {reviewer_name}"
        )
        return True

    def modify(
        self,
        candidate_id: str,
        reviewer_name: str,
        trigger_conditions: list[str] | None = None,
        skill_sequence: list[str] | None = None,
        comments: str | None = None,
    ) -> bool:
        """
        修改后重新提交（驳回修改）。

        修改触发条件或技能序列后，状态回到 DRAFT。
        """
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            logger.warning(f"[Reviewer] Candidate not found: {candidate_id}")
            return False

        if candidate.status not in (TemplateStatus.PENDING_REVIEW, TemplateStatus.REJECTED):
            logger.warning(
                f"[Reviewer] Cannot modify, status={candidate.status}: {candidate_id}"
            )
            return False

        if trigger_conditions is not None:
            candidate.trigger_conditions = trigger_conditions
        if skill_sequence is not None:
            candidate.skill_sequence = skill_sequence

        candidate.status = TemplateStatus.DRAFT

        if candidate_id in self._queue.pending:
            self._queue.pending.remove(candidate_id)
        if candidate_id in self._queue.rejected:
            self._queue.rejected.remove(candidate_id)

        review = SkillTemplateReview(
            template_id=candidate_id,
            reviewer=reviewer_name,
            decision="modify",
            comments=comments,
            modified_trigger_conditions=trigger_conditions,
            modified_skill_sequence=skill_sequence,
        )
        self._reviews[candidate_id].append(review)

        logger.info(
            f"[Reviewer] Modified: {candidate_id} by {reviewer_name}"
        )
        return True

    def get_candidate(self, candidate_id: str) -> SkillTemplateCandidate | None:
        """获取候选模板。"""
        return self._candidates.get(candidate_id)

    def get_reviews(self, candidate_id: str) -> list[SkillTemplateReview]:
        """获取审核历史。"""
        return list(self._reviews.get(candidate_id, []))

    def list_pending(self) -> list[SkillTemplateCandidate]:
        """获取待审核列表。"""
        return [self._candidates[cid] for cid in self._queue.pending if cid in self._candidates]

    def list_approved(self) -> list[SkillTemplateCandidate]:
        """获取已通过列表。"""
        return [
            self._candidates[cid]
            for cid in self._queue.approved
            if cid in self._candidates
        ]

    def list_rejected(self) -> list[SkillTemplateCandidate]:
        """获取已驳回列表。"""
        return [
            self._candidates[cid]
            for cid in self._queue.rejected
            if cid in self._candidates
        ]

    def get_stats(self) -> dict[str, Any]:
        """获取审核统计。"""
        return {
            "pending": len(self._queue.pending),
            "approved": len(self._queue.approved),
            "rejected": len(self._queue.rejected),
            "total": len(self._candidates),
        }
