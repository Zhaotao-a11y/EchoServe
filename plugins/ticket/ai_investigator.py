# -*- coding: utf-8 -*-
"""
EchoServe Phase 2.5 — AI 自动工单调查模块

功能:
  1. AI 意图识别: 从用户消息自动识别投诉/Bug/功能请求/咨询
  2. 自动创建工单: 分类 + 优先级 + 标签 + 关联会话
  3. 根因调查:
     - 查询知识库匹配已知问题
     - 检查关键词匹配历史工单
     - 生成调查摘要
  4. RCA 报告生成: 问题描述 + 影响范围 + 根因分析 + 建议修复
  5. 进度跟踪: 调查结果写入工单评论, 支持后续追问
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger("echoserve.ticket.investigator")

# ─── 意图分类 ──────────────────────────────────────────

INTENT_COMPLAINT = "complaint"
INTENT_BUG = "bug_report"
INTENT_FEATURE = "feature_request"
INTENT_CONSULT = "consult"
INTENT_GENERAL = "general"

# 意图关键词库
INTENT_KEYWORDS: dict[str, list[str]] = {
    INTENT_COMPLAINT: [
        "投诉", "不满", "差评", "态度", "服务差", "垃圾", "退款",
        "赔偿", "维权", "315", "举报", "不认可",
    ],
    INTENT_BUG: [
        "bug", "错误", "报错", "异常", "崩溃", "闪退", "白屏",
        "黑屏", "卡死", "无法", "失败", "不工作", "出问题",
        "系统错误", "500", "404", "超时", "报错码",
    ],
    INTENT_FEATURE: [
        "建议", "希望", "想要", "能否增加", "能不能加",
        "功能请求", "需求", "期望", "如果能有", "什么时候支持",
        "什么时候上线", "有没有计划",
    ],
    INTENT_CONSULT: [
        "怎么", "如何", "哪里", "什么是", "为什么", "请问",
        "咨询", "了解", "查询", "查看", "帮我", "能否帮我",
    ],
}

# 优先级推断规则
PRIORITY_RULES: dict[str, str] = {
    INTENT_COMPLAINT: "high",
    INTENT_BUG: "high",
    INTENT_FEATURE: "low",
    INTENT_CONSULT: "medium",
    INTENT_GENERAL: "medium",
}

# 紧急关键词 (覆盖默认优先级为 urgent)
URGENT_KEYWORDS: list[str] = [
    "紧急", "马上", "立刻", "现在就要", "非常急",
    "法律", "律师", "媒体", "曝光", "工商",
    "人身安全", "隐私泄露", "数据丢失",
]

# Bug 严重程度关键词 (覆盖优先级为 urgent)
SEVERITY_KEYWORDS: list[str] = [
    "全站", "所有用户", "大面积", "系统瘫痪",
    "数据丢失", "资金损失", "安全漏洞",
]


class IntentClassifier:
    """
    基于关键词 + 规则的意图分类器。

    后续可升级为 LLM-based 分类 (调用 model_provider.chat with classification prompt)。
    """

    @staticmethod
    def classify(message: str) -> dict[str, Any]:
        """
        分析用户消息，返回意图分类结果。

        Returns:
            {
                "intent": str,          # 意图类别
                "confidence": float,    # 置信度 0-1
                "priority": str,        # 建议优先级
                "matched_keywords": list[str],  # 匹配到的关键词
                "is_urgent": bool,      # 是否紧急
            }
        """
        message_lower = message.lower()
        scores: dict[str, int] = {}
        matched: dict[str, list[str]] = {}

        for intent, keywords in INTENT_KEYWORDS.items():
            count = 0
            hits = []
            for kw in keywords:
                if kw in message_lower:
                    count += 1
                    hits.append(kw)
            scores[intent] = count
            matched[intent] = hits

        # 选择得分最高的意图
        best_intent = max(scores.keys(), key=lambda k: scores[k])
        best_score = scores[best_intent]
        total_score = sum(scores.values())

        # 置信度计算
        if total_score == 0:
            best_intent = INTENT_GENERAL
            confidence = 0.3
        else:
            confidence = min(best_score / (total_score + 1), 0.95)

        # 优先级推断
        priority = PRIORITY_RULES.get(best_intent, "medium")

        # 紧急关键词检测
        is_urgent = any(kw in message_lower for kw in URGENT_KEYWORDS)
        if is_urgent:
            priority = "urgent"
        elif best_intent == INTENT_BUG:
            if any(kw in message_lower for kw in SEVERITY_KEYWORDS):
                priority = "urgent"

        return {
            "intent": best_intent,
            "confidence": round(confidence, 2),
            "priority": priority,
            "matched_keywords": matched.get(best_intent, []),
            "is_urgent": is_urgent,
        }


# ─── 根因调查器 ──────────────────────────────────────────

class RootCauseInvestigator:
    """
    工单根因调查 Agent。

    调查步骤:
    1. 查询知识库: 是否有已知问题/FAQ
    2. 查询历史工单: 是否有类似问题
    3. 生成调查摘要
    4. 输出 RCA 报告
    """

    def __init__(self):
        self._knowledge_base = None
        self._ticket_service = None

    def set_services(
        self,
        knowledge_base: Any | None = None,
        ticket_service: Any | None = None,
    ):
        """注入依赖服务"""
        if knowledge_base:
            self._knowledge_base = knowledge_base
        if ticket_service:
            self._ticket_service = ticket_service

    async def investigate(
        self,
        ticket_id: str,
        user_message: str,
        classification: dict[str, Any],
        session_id: str = "",
    ) -> dict[str, Any]:
        """
        对工单进行根因调查。

        Args:
            ticket_id: 工单 ID
            user_message: 用户原始消息
            classification: 意图分类结果
            session_id: 关联会话 ID

        Returns:
            RCA 报告 dict
        """
        steps: list[dict[str, Any]] = []

        # Step 1: 查询知识库
        kb_result = await self._search_knowledge(user_message)
        steps.append({
            "step": "knowledge_search",
            "status": "completed" if kb_result.get("found") else "no_match",
            "detail": kb_result,
        })

        # Step 2: 查询历史工单
        history_result = self._search_history_tickets(user_message, classification)
        steps.append({
            "step": "history_search",
            "status": "completed" if history_result.get("found") else "no_match",
            "detail": history_result,
        })

        # Step 3: 生成 RCA 报告
        rca_report = self._generate_rca_report(
            ticket_id=ticket_id,
            user_message=user_message,
            classification=classification,
            kb_result=kb_result,
            history_result=history_result,
            session_id=session_id,
        )

        # 写入工单评论
        if self._ticket_service:
            try:
                self._ticket_service.add_comment(
                    ticket_id=ticket_id,
                    author_id="ai_investigator",
                    author_name="AI调查员",
                    content=rca_report,
                    is_internal=True,
                )
            except Exception as e:
                logger.warning(f"Failed to add RCA comment: {e}")

        return {
            "ticket_id": ticket_id,
            "classification": classification,
            "investigation_steps": steps,
            "rca_report": rca_report,
            "kb_matched": kb_result.get("found", False),
            "history_matched": history_result.get("found", False),
        }

    async def _search_knowledge(self, query: str) -> dict[str, Any]:
        """查询知识库匹配已知问题"""
        if not self._knowledge_base:
            return {"found": False, "reason": "knowledge_base_unavailable"}

        try:
            result = await self._knowledge_base.test_retrieval(
                query=query, top_k=3
            )
            results = result.get("results", [])
            if results:
                return {
                    "found": True,
                    "matched_count": len(results),
                    "top_match": {
                        "doc_id": results[0].get("doc_id", ""),
                        "score": results[0].get("score", 0),
                        "content_preview": results[0].get("content", "")[:200],
                        "source": results[0].get("source", ""),
                    },
                    "all_matches": [
                        {
                            "doc_id": r.get("doc_id", ""),
                            "score": r.get("score", 0),
                        }
                        for r in results
                    ],
                }
            return {"found": False, "reason": "no_match"}
        except Exception as e:
            logger.warning(f"Knowledge search failed: {e}")
            return {"found": False, "reason": f"error: {e}"}

    def _search_history_tickets(
        self,
        message: str,
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        """查询历史工单匹配相似问题"""
        if not self._ticket_service:
            return {"found": False, "reason": "ticket_service_unavailable"}

        try:
            # 按分类查询历史工单
            intent = classification.get("intent", "")
            category_map = {
                INTENT_COMPLAINT: "complaint",
                INTENT_BUG: "bug",
                INTENT_FEATURE: "feature_request",
                INTENT_CONSULT: "consult",
                INTENT_GENERAL: "general",
            }
            category = category_map.get(intent, "general")

            result = self._ticket_service.list_tickets(
                category=category,
                limit=5,
            )

            items = result.get("items", [])
            if not items:
                return {"found": False, "reason": "no_history"}

            # 关键词匹配
            message_lower = message.lower()
            matched = []
            for item in items:
                title = item.get("title", "").lower()
                desc = item.get("description", "").lower()
                # 简单匹配: 标题或描述中包含用户消息关键词
                keywords = classification.get("matched_keywords", [])
                if keywords:
                    hit_count = sum(
                        1 for kw in keywords
                        if kw in title or kw in desc
                    )
                    if hit_count > 0:
                        matched.append({
                            "ticket_id": item.get("id", ""),
                            "title": item.get("title", ""),
                            "status": item.get("status", ""),
                            "match_count": hit_count,
                        })

            if matched:
                return {
                    "found": True,
                    "matched_count": len(matched),
                    "matches": matched[:3],
                }
            return {
                "found": False,
                "reason": "no_keyword_match",
                "total_checked": len(items),
            }
        except Exception as e:
            logger.warning(f"History search failed: {e}")
            return {"found": False, "reason": f"error: {e}"}

    def _generate_rca_report(
        self,
        ticket_id: str,
        user_message: str,
        classification: dict[str, Any],
        kb_result: dict[str, Any],
        history_result: dict[str, Any],
        session_id: str = "",
    ) -> str:
        """生成 RCA (Root Cause Analysis) 报告"""

        intent = classification.get("intent", "unknown")
        priority = classification.get("priority", "medium")
        confidence = classification.get("confidence", 0)
        keywords = classification.get("matched_keywords", [])
        is_urgent = classification.get("is_urgent", False)

        # 影响范围评估
        if is_urgent:
            impact = "高影响 — 检测到紧急关键词，建议立即处理"
        elif priority == "high":
            impact = "中高影响 — 需优先处理"
        elif priority == "urgent":
            impact = "极高影响 — 紧急级别"
        else:
            impact = "标准影响 — 常规处理流程"

        # 根因分析
        root_causes: list[str] = []

        if kb_result.get("found"):
            top_match = kb_result.get("top_match", {})
            root_causes.append(
                f"知识库匹配: 找到 {kb_result.get('matched_count', 0)} 条相关记录，"
                f"最高匹配分数 {top_match.get('score', 0):.3f}，"
                f"来源: {top_match.get('source', '未知')}"
            )
        else:
            root_causes.append("知识库无直接匹配，可能为新问题")

        if history_result.get("found"):
            matches = history_result.get("matches", [])
            root_causes.append(
                f"历史工单匹配: 找到 {len(matches)} 个相似工单，"
                f"参考工单号: {', '.join(m.get('ticket_id', '') for m in matches[:2])}"
            )
        else:
            root_causes.append("历史工单无相似记录，为首次报告")

        # 建议修复
        suggestions: list[str] = []
        if intent == INTENT_BUG:
            suggestions.append("建议技术团队复现问题并排查日志")
            suggestions.append("检查近期部署变更记录")
            if priority == "urgent":
                suggestions.append("建议立即拉群排查，设置 15 分钟同步频率")
        elif intent == INTENT_COMPLAINT:
            suggestions.append("建议客服主管优先跟进，安抚用户情绪")
            suggestions.append("核查投诉涉及的业务环节")
            if is_urgent:
                suggestions.append("建议升级至管理层处理")
        elif intent == INTENT_FEATURE:
            suggestions.append("建议产品团队评估需求价值并排期")
            suggestions.append("汇总同类需求纳入迭代规划")
        else:
            suggestions.append("建议客服按知识库标准话术回复")
            suggestions.append("如无法解决，转交对应技术支持")

        # 组装报告
        report = (
            f"=== AI 调查报告 (RCA) ===\n"
            f"工单号: {ticket_id}\n"
            f"会话ID: {session_id or 'N/A'}\n"
            f"\n--- 问题描述 ---\n"
            f"用户消息: {user_message[:300]}\n"
            f"意图分类: {intent} (置信度: {confidence})\n"
            f"优先级: {priority}{' [紧急]' if is_urgent else ''}\n"
            f"匹配关键词: {', '.join(keywords) if keywords else '无'}\n"
            f"\n--- 影响范围 ---\n"
            f"{impact}\n"
            f"\n--- 根因分析 ---\n"
        )
        for i, cause in enumerate(root_causes, 1):
            report += f"{i}. {cause}\n"

        report += f"\n--- 建议修复 ---\n"
        for i, sug in enumerate(suggestions, 1):
            report += f"{i}. {sug}\n"

        report += f"\n--- 调查结论 ---\n"
        if kb_result.get("found") and history_result.get("found"):
            report += "已知问题且历史有记录，建议按既有方案处理。"
        elif kb_result.get("found"):
            report += "知识库有记录但历史无工单，可能为偶发或新报告。"
        elif history_result.get("found"):
            report += "历史有相似工单但知识库无记录，建议补充知识库。"
        else:
            report += "新问题，无知识库和历史匹配，建议人工深入调查。"

        return report


# ─── 工单管理器 ──────────────────────────────────────────

class AIInvestigatorManager:
    """
    AI 工单调查管理器: 串联意图识别 → 工单创建 → 根因调查 → 报告生成。
    """

    def __init__(self):
        self.classifier = IntentClassifier()
        self.investigator = RootCauseInvestigator()
        self._ticket_service = None
        self._knowledge_base = None
        self._llm = None

    def set_services(
        self,
        ticket_service: Any | None = None,
        knowledge_base: Any | None = None,
        llm: Any | None = None,
    ):
        """注入依赖服务"""
        if ticket_service:
            self._ticket_service = ticket_service
        if knowledge_base:
            self._knowledge_base = knowledge_base
        if llm:
            self._llm = llm
        self.investigator.set_services(
            knowledge_base=self._knowledge_base,
            ticket_service=self._ticket_service,
        )

    async def auto_create_and_investigate(
        self,
        user_message: str,
        session_id: str = "",
        customer_id: str = "",
        customer_name: str = "",
        channel: str = "web",
    ) -> dict[str, Any]:
        """
        自动创建工单并执行根因调查。

        完整流程:
        1. 意图分类 → 分类 + 优先级
        2. 自动创建工单
        3. 根因调查 (知识库 + 历史工单)
        4. 返回工单 + RCA 报告

        Args:
            user_message: 用户原始消息
            session_id: 会话 ID
            customer_id: 客户 ID
            customer_name: 客户名
            channel: 渠道

        Returns:
            {
                "ticket": dict,        # 创建的工单
                "classification": dict, # 意图分类
                "investigation": dict,  # 调查结果
            }
        """
        # 1. 意图分类
        classification = self.classifier.classify(user_message)
        logger.info(
            f"[AIInvestigator] Intent: {classification['intent']}, "
            f"priority: {classification['priority']}, "
            f"confidence: {classification['confidence']}"
        )

        # 2. 自动创建工单
        if not self._ticket_service:
            return {
                "ticket": None,
                "classification": classification,
                "investigation": {"error": "ticket_service_unavailable"},
            }

        # 构建工单标题 (截取用户消息前30字)
        title = user_message[:30].replace("\n", " ")
        if len(user_message) > 30:
            title += "..."

        # 分类映射到工单 category
        intent = classification["intent"]
        category_map = {
            INTENT_COMPLAINT: "complaint",
            INTENT_BUG: "bug",
            INTENT_FEATURE: "feature_request",
            INTENT_CONSULT: "consult",
            INTENT_GENERAL: "general",
        }

        # Phase 2.5 Fix: 异步上下文中同步 I/O 可能阻塞事件循环
        # 若 create_ticket 为同步方法，用 run_in_executor 包装
        create_ticket_fn = self._ticket_service.create_ticket
        if asyncio.iscoroutinefunction(create_ticket_fn):
            ticket = await create_ticket_fn(
                title=title,
                description=user_message,
                priority=classification["priority"],
                category=category_map.get(intent, "general"),
                session_id=session_id,
                customer_id=customer_id,
                customer_name=customer_name,
                channel=channel,
                created_by="ai_investigator",
                tags=[intent] + classification.get("matched_keywords", [])[:3],
                metadata={
                    "ai_classification": classification,
                    "source": "auto_investigation",
                },
            )
        else:
            loop = asyncio.get_running_loop()
            ticket = await loop.run_in_executor(
                None,
                lambda: create_ticket_fn(
                    title=title,
                    description=user_message,
                    priority=classification["priority"],
                    category=category_map.get(intent, "general"),
                    session_id=session_id,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    channel=channel,
                    created_by="ai_investigator",
                    tags=[intent] + classification.get("matched_keywords", [])[:3],
                    metadata={
                        "ai_classification": classification,
                        "source": "auto_investigation",
                    },
                ),
            )

        if not ticket:
            return {
                "ticket": None,
                "classification": classification,
                "investigation": {"error": "ticket_creation_failed"},
            }

        ticket_id = ticket.get("id", "")

        # 3. 根因调查
        investigation = await self.investigator.investigate(
            ticket_id=ticket_id,
            user_message=user_message,
            classification=classification,
            session_id=session_id,
        )

        logger.info(
            f"[AIInvestigator] Investigation completed for {ticket_id}: "
            f"kb_matched={investigation.get('kb_matched')}, "
            f"history_matched={investigation.get('history_matched')}"
        )

        return {
            "ticket": ticket,
            "classification": classification,
            "investigation": investigation,
        }

    def should_create_ticket(
        self,
        user_message: str,
        session_id: str = "",
    ) -> bool:
        """
        判断是否应该自动创建工单。

        触发条件:
        - 意图为投诉或Bug报告
        - 或包含紧急关键词
        - 或用户明确要求创建工单
        """
        # 明确的工单创建意图
        explicit_keywords = ["工单", "ticket", "记录一下", "帮我登记", "上报"]
        if any(kw in user_message.lower() for kw in explicit_keywords):
            return True

        classification = self.classifier.classify(user_message)
        intent = classification.get("intent", "")
        is_urgent = classification.get("is_urgent", False)

        # 投诉或Bug自动创建工单
        if intent in (INTENT_COMPLAINT, INTENT_BUG):
            return True

        # 紧急情况自动创建工单
        if is_urgent:
            return True

        return False
