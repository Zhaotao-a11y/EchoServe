# -*- coding: utf-8 -*-
"""
EchoServe Phase 2.5 — 智能工单分配模块

功能:
  1. 技能匹配: 工单分类 → 匹配坐席技能标签
  2. 负载均衡: 在匹配的坐席中选择当前负载最低的
  3. VIP 优先: 高优先级/紧急工单优先分配给高级坐席
  4. 自动分配 + 回退: 无匹配坐席时回退到默认分配策略
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("echoserve.ticket.assigner")

# ─── 技能-分类映射 ──────────────────────────────────────

CATEGORY_SKILL_MAP: dict[str, list[str]] = {
    "complaint": ["complaint_handling", "customer_service", "escalation"],
    "bug": ["technical_support", "debugging", "system_admin"],
    "feature_request": ["product_management", "requirement_analysis"],
    "consult": ["customer_service", "general_knowledge"],
    "general": ["customer_service", "general_knowledge"],
    "refund": ["refund_processing", "finance", "customer_service"],
    "technical": ["technical_support", "system_admin", "network_engineer"],
    "billing": ["finance", "billing", "account_management"],
}

# 坐席级别
AGENT_LEVELS = {
    "junior": 1,
    "standard": 2,
    "senior": 3,
    "expert": 4,
}


class AgentInfo:
    """坐席信息"""

    def __init__(
        self,
        agent_id: str,
        name: str = "",
        skills: list[str] | None = None,
        level: str = "standard",
        max_concurrent: int = 10,
        current_load: int = 0,
        satisfaction_score: float = 0.0,
    ):
        self.agent_id = agent_id
        self.name = name
        self.skills = set(skills or [])
        self.level = level
        self.level_value = AGENT_LEVELS.get(level, 2)
        self.max_concurrent = max_concurrent
        self.current_load = current_load
        self.satisfaction_score = satisfaction_score

    @property
    def is_available(self) -> bool:
        return self.current_load < self.max_concurrent

    @property
    def load_ratio(self) -> float:
        if self.max_concurrent == 0:
            return 1.0
        return self.current_load / self.max_concurrent

    def skill_match_score(self, required_skills: list[str]) -> float:
        """计算技能匹配度 (0-1)"""
        if not required_skills:
            return 0.5  # 无技能要求时给中等分
        if not self.skills:
            return 0.0
        matched = len(set(required_skills) & self.skills)
        return matched / len(required_skills)


class SmartTicketAssigner:
    """
    智能工单分配器。

    分配策略 (按权重排序):
    1. 技能匹配度 (40%): 坐席技能与工单所需技能的重合度
    2. 当前负载 (25%): 优先分配给负载较低的坐席
    3. 坐席级别 (20%): 紧急工单优先高级别坐席
    4. 满意度 (15%): 历史满意度高的坐席优先

    回退策略:
    - 无技能匹配 → 按负载分配给任何可用坐席
    - 无可用坐席 → 返回 unassigned
    """

    # 权重配置
    WEIGHT_SKILL = 0.40
    WEIGHT_LOAD = 0.25
    WEIGHT_LEVEL = 0.20
    WEIGHT_SATISFACTION = 0.15

    def __init__(self):
        self._agents: dict[str, AgentInfo] = {}
        self._ticket_service = None

    def set_ticket_service(self, ticket_service: Any):
        self._ticket_service = ticket_service

    def register_agent(
        self,
        agent_id: str,
        name: str = "",
        skills: list[str] | None = None,
        level: str = "standard",
        max_concurrent: int = 10,
    ):
        """注册坐席"""
        self._agents[agent_id] = AgentInfo(
            agent_id=agent_id,
            name=name,
            skills=skills,
            level=level,
            max_concurrent=max_concurrent,
        )
        logger.info(
            f"[SmartAssigner] Agent registered: {agent_id} "
            f"(skills={skills}, level={level})"
        )

    def remove_agent(self, agent_id: str):
        self._agents.pop(agent_id, None)

    def update_agent_load(self, agent_id: str, current_load: int):
        """更新坐席当前负载"""
        if agent_id in self._agents:
            self._agents[agent_id].current_load = current_load

    def assign_ticket(
        self,
        ticket_id: str,
        category: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        """
        智能分配工单给最合适的坐席。

        Args:
            ticket_id: 工单 ID
            category: 工单分类
            priority: 优先级 (low/medium/high/urgent)

        Returns:
            {
                "ticket_id": str,
                "assigned_agent": str,
                "assignment_reason": str,
                "score": float,
                "candidates_evaluated": int,
            }
        """
        # 获取所需技能
        required_skills = CATEGORY_SKILL_MAP.get(category, ["customer_service"])

        # 筛选可用坐席
        available = [a for a in self._agents.values() if a.is_available]

        if not available:
            logger.warning(
                f"[SmartAssigner] No available agents for ticket {ticket_id}"
            )
            return {
                "ticket_id": ticket_id,
                "assigned_agent": "",
                "assignment_reason": "no_available_agent",
                "score": 0,
                "candidates_evaluated": 0,
            }

        # 评分排序
        scored = []
        for agent in available:
            score = self._calculate_score(agent, required_skills, priority)
            scored.append((agent, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        best_agent, best_score = scored[0]

        # 紧急工单要求最低级别
        if priority == "urgent" and best_agent.level_value < AGENT_LEVELS["senior"]:
            # 尝试找更高级别的坐席
            senior_agents = [
                (a, s) for a, s in scored
                if a.level_value >= AGENT_LEVELS["senior"]
            ]
            if senior_agents:
                best_agent, best_score = senior_agents[0]

        # 更新工单分配
        if self._ticket_service:
            try:
                self._ticket_service.update_ticket(
                    ticket_id=ticket_id,
                    assigned_agent=best_agent.agent_id,
                    metadata={
                        "assignment_method": "smart",
                        "assignment_score": round(best_score, 3),
                        "required_skills": required_skills,
                    },
                )
            except Exception as e:
                logger.warning(f"[SmartAssigner] Failed to update ticket: {e}")

        # 更新坐席负载
        best_agent.current_load += 1

        reason = self._build_reason(best_agent, required_skills, priority)

        logger.info(
            f"[SmartAssigner] Ticket {ticket_id} assigned to {best_agent.agent_id} "
            f"(score={best_score:.3f}, reason={reason})"
        )

        return {
            "ticket_id": ticket_id,
            "assigned_agent": best_agent.agent_id,
            "agent_name": best_agent.name,
            "assignment_reason": reason,
            "score": round(best_score, 3),
            "candidates_evaluated": len(available),
        }

    def _calculate_score(
        self,
        agent: AgentInfo,
        required_skills: list[str],
        priority: str,
    ) -> float:
        """
        计算坐席综合匹配分数 (0-1)。

        分数 = 技能匹配*0.40 + 负载优势*0.25 + 级别*0.20 + 满意度*0.15
        """
        # 技能匹配
        skill_score = agent.skill_match_score(required_skills)

        # 负载优势 (负载越低分越高)
        load_score = 1.0 - agent.load_ratio

        # 级别 (标准化到 0-1)
        level_score = agent.level_value / 4.0

        # 紧急工单加大级别权重
        if priority == "urgent":
            level_weight = 0.35
            skill_weight = 0.30
            load_weight = 0.15
            satisfaction_weight = 0.20
        else:
            skill_weight = self.WEIGHT_SKILL
            load_weight = self.WEIGHT_LOAD
            level_weight = self.WEIGHT_LEVEL
            satisfaction_weight = self.WEIGHT_SATISFACTION

        satisfaction_score = max(agent.satisfaction_score, 0.5)

        total = (
            skill_score * skill_weight
            + load_score * load_weight
            + level_score * level_weight
            + satisfaction_score * satisfaction_weight
        )

        return total

    def _build_reason(
        self,
        agent: AgentInfo,
        required_skills: list[str],
        priority: str,
    ) -> str:
        """构建分配原因描述"""
        matched_skills = set(required_skills) & agent.skills
        parts = []

        if matched_skills:
            parts.append(f"技能匹配({', '.join(matched_skills)})")
        else:
            parts.append("无精确技能匹配(按负载分配)")

        parts.append(f"负载{agent.current_load}/{agent.max_concurrent}")
        parts.append(f"级别{agent.level}")

        if priority == "urgent":
            parts.append("紧急优先")

        return "; ".join(parts)

    def get_agent_workload(self) -> list[dict[str, Any]]:
        """获取所有坐席负载情况"""
        return [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "level": a.level,
                "current_load": a.current_load,
                "max_concurrent": a.max_concurrent,
                "load_ratio": round(a.load_ratio, 2),
                "is_available": a.is_available,
                "skills": list(a.skills),
            }
            for a in self._agents.values()
        ]

    def auto_assign_pending(self) -> list[dict[str, Any]]:
        """
        批量自动分配所有待分配工单。

        Returns:
            分配结果列表
        """
        if not self._ticket_service:
            return []

        results = []
        try:
            # 查询所有未分配的工单
            unassigned = self._ticket_service.list_tickets(
                assigned_agent="",
                status="open",
                limit=100,
            )

            for ticket in unassigned.get("items", []):
                tid = ticket.get("id", "")
                category = ticket.get("category", "general")
                priority = ticket.get("priority", "medium")

                result = self.assign_ticket(
                    ticket_id=tid,
                    category=category,
                    priority=priority,
                )
                results.append(result)

            if results:
                logger.info(
                    f"[SmartAssigner] Auto-assigned {len(results)} pending tickets"
                )
        except Exception as e:
            logger.error(f"[SmartAssigner] Auto-assign failed: {e}")

        return results
