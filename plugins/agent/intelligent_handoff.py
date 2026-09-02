# -*- coding: utf-8 -*-
"""
EchoServe — Intelligent Handoff Module (Phase 1.2)

增强版人机转接系统，参考 ChatterMate 设计：
    1. 情绪检测触发器：自动检测用户负面情绪并触发转接
    2. 意图置信度判断：AI 无法理解时主动转接
    3. 技能匹配 + 负载均衡智能队列
    4. 对话上下文摘要生成
    5. 人工解决后 AI 自动接管

集成方式：
    - 新增 IntelligentHandoffManager，由 AgentPlugin 在 on_start 中初始化
    - 提供 sentiment_analyzer 服务供 workflow engine 使用
    - 扩展 request_handoff 支持 reason_code 和 summary
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("echoserve.agent.intelligent_handoff")


# ─── 情绪分析器（可替换为真实模型）───────────────────

class SentimentAnalyzer:
    """
    轻量级情绪分析器。
    
    Phase 1 使用规则 + 关键词匹配（零依赖启动）。
    Phase 2 可替换为基于 BERT 的模型（如 baidu-sentiment 或本地部署）。
    """

    # 负面情绪关键词库（中文）
    NEGATIVE_KEYWORDS = {
        "愤怒": -0.9, "生气": -0.85, "恼火": -0.8, "火大": -0.85,
        "垃圾": -0.8, "太差": -0.7, "废物": -0.9, "坑人": -0.8,
        "投诉": -0.6, "举报": -0.7, "欺骗": -0.85, "骗子": -0.9,
        "坑": -0.7, "骗": -0.85, "恶心": -0.8, "烦死了": -0.75,
        "滚": -0.9, "傻逼": -0.95, "操": -0.9, "他妈": -0.85,
        "不干了": -0.7, "退款": -0.5, "退货": -0.5, "赔偿": -0.6,
        "损失": -0.6, "耽误": -0.5, "急": -0.4, "快点": -0.3,
    }

    # 正面情绪关键词
    POSITIVE_KEYWORDS = {
        "谢谢": 0.7, "感谢": 0.8, "不错": 0.6, "很好": 0.7,
        "满意": 0.8, "解决了": 0.9, "没问题": 0.5, "好的": 0.3,
    }

    def analyze(self, text: str) -> dict[str, Any]:
        """
        分析文本情绪，返回 -1.0 ~ 1.0 的分数。
        
        Returns:
            {"score": float, "label": str, "confidence": float, "keywords": list}
        """
        if not text:
            return {"score": 0.0, "label": "neutral", "confidence": 0.0, "keywords": []}

        scores = []
        matched_keywords = []

        for keyword, score in self.NEGATIVE_KEYWORDS.items():
            if keyword in text:
                scores.append(score)
                matched_keywords.append((keyword, score))

        for keyword, score in self.POSITIVE_KEYWORDS.items():
            if keyword in text:
                scores.append(score)
                matched_keywords.append((keyword, score))

        if not scores:
            return {"score": 0.0, "label": "neutral", "confidence": 0.5, "keywords": []}

        # 取最极端的分数
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)

        # 如果有强负面，取最小值；如果有强正面，取最大值
        if min_score < -0.5:
            final_score = min_score
        elif max_score > 0.5:
            final_score = max_score
        else:
            final_score = avg_score

        # 标签
        if final_score <= -0.6:
            label = "very_negative"
        elif final_score <= -0.3:
            label = "negative"
        elif final_score < 0.3:
            label = "neutral"
        elif final_score < 0.6:
            label = "positive"
        else:
            label = "very_positive"

        confidence = min(abs(final_score) + 0.3, 1.0)

        return {
            "score": round(final_score, 3),
            "label": label,
            "confidence": round(confidence, 3),
            "keywords": [k for k, s in matched_keywords],
        }


# ─── 对话摘要生成器 ──────────────────────────────────

class ConversationSummarizer:
    """
    生成对话摘要，供坐席快速了解上下文。
    
    Phase 1: 基于规则抽取关键信息。
    Phase 2: 可接入 LLM 生成更自然的摘要。
    """

    def summarize(
        self,
        messages: list[dict[str, Any]],
        max_length: int = 500,
        include_user_emotion: bool = True,
        include_attempted_solutions: bool = True,
    ) -> str:
        """
        生成对话摘要。

        Args:
            messages: [{"role": "user"/"assistant", "content": "...", "timestamp": float}, ...]
            max_length: 摘要最大长度
        """
        if not messages:
            return "[无对话历史]"

        # 提取用户问题
        user_questions = [m["content"] for m in messages if m.get("role") == "user"]
        assistant_responses = [m["content"] for m in messages if m.get("role") == "assistant"]

        lines = []
        lines.append(f"对话轮数: {len(messages)} 轮")

        if user_questions:
            lines.append(f"用户核心问题: {user_questions[-1][:200]}")

        if len(user_questions) > 1:
            lines.append(f"历史问题: {user_questions[0][:100]}...")

        if include_attempted_solutions and assistant_responses:
            lines.append(f"AI 已尝试方案: {len(assistant_responses)} 次")
            # 提取最后一条 AI 回复的关键内容
            last_response = assistant_responses[-1]
            lines.append(f"最后回复摘要: {last_response[:150]}...")

        # 检测情绪
        if include_user_emotion and user_questions:
            analyzer = SentimentAnalyzer()
            sentiment = analyzer.analyze(user_questions[-1])
            lines.append(f"用户情绪: {sentiment['label']} (分数: {sentiment['score']})")

        summary = "\n".join(lines)
        return summary[:max_length]


# ─── 智能队列路由器 ──────────────────────────────────

class SmartQueueRouter:
    """
    智能队列路由：技能匹配 + 负载均衡 + 等待时间预测。
    
    替代原有简单 FIFO，实现最优坐席匹配。
    """

    def __init__(self, agent_plugin):
        """
        Args:
            agent_plugin: AgentPlugin 实例，用于查询坐席信息
        """
        self.agent_plugin = agent_plugin

    def find_best_agent(
        self,
        required_skills: list[str] | None = None,
        priority: str = "normal",
        customer_tier: str = "standard",
    ) -> dict[str, Any] | None:
        """
        找到最适合的坐席。

        策略：
        1. 技能匹配：筛选具备 required_skills 的在线坐席
        2. 负载均衡：选择当前活跃会话数最少的坐席
        3. VIP 优先：高 tier 客户优先分配给经验丰富的坐席
        """
        required_skills = required_skills or []

        # 获取所有在线坐席
        online_agents = self.agent_plugin.list_agents(status="online")
        if not online_agents:
            return None

        candidates = []
        for agent in online_agents:
            agent_id = agent["agent_id"]
            skills = agent.get("skills", [])
            max_concurrent = agent.get("max_concurrent", 5)

            # 计算当前负载
            workload = self.agent_plugin.get_agent_workload(agent_id)
            active_sessions = workload.get("active_sessions", 0)
            available_slots = max_concurrent - active_sessions

            if available_slots <= 0:
                continue  # 已满负荷

            # 技能匹配度（0.0 ~ 1.0）
            if required_skills:
                matched = sum(1 for s in required_skills if s in skills)
                skill_score = matched / len(required_skills)
                if skill_score == 0:
                    continue  # 完全不匹配
            else:
                skill_score = 1.0

            # 经验值（根据总会话数估算）
            total_sessions = workload.get("total_sessions", 0)
            experience_score = min(total_sessions / 100, 1.0)

            # 评分：技能匹配(40%) + 可用槽位(30%) + 经验(20%) + 满意度(10%)
            rating_stats = workload.get("rating_stats", {})
            avg_rating = rating_stats.get("average", 3.0)
            rating_score = (avg_rating - 1) / 4  # 1-5 星映射到 0-1

            # VIP 客户优先考虑经验丰富的坐席
            if customer_tier in ("vip", "enterprise"):
                score = skill_score * 0.3 + (available_slots / max_concurrent) * 0.2 + experience_score * 0.4 + rating_score * 0.1
            else:
                score = skill_score * 0.4 + (available_slots / max_concurrent) * 0.3 + experience_score * 0.2 + rating_score * 0.1

            candidates.append({
                "agent": agent,
                "score": score,
                "available_slots": available_slots,
                "skill_score": skill_score,
                "workload": workload,
            })

        if not candidates:
            return None

        # 选择得分最高的坐席
        best = max(candidates, key=lambda x: x["score"])
        return best["agent"]

    def estimate_wait_time(self, queue_position: int, required_skills: list[str] | None = None) -> int:
        """
        估算等待时间（秒）。

        简单估算：基于排队位置 × 平均处理时间（假设 5 分钟/客户）。
        Phase 2 可接入历史数据统计。
        """
        avg_handle_time = 300  # 5 分钟 = 300 秒
        base_wait = queue_position * avg_handle_time

        # 如果有技能要求，可能等待更久
        if required_skills:
            base_wait = int(base_wait * 1.2)

        return base_wait


# ─── 智能转接决策器 ──────────────────────────────────

class HandoffDecision:
    """转接决策结果"""

    def __init__(
        self,
        should_handoff: bool,
        trigger: str = "",
        priority: str = "normal",
        reason: str = "",
        required_skills: list[str] | None = None,
        summary: str = "",
    ):
        self.should_handoff = should_handoff
        self.trigger = trigger  # user_request / negative_sentiment / low_confidence / manual
        self.priority = priority  # low / normal / high / urgent
        self.reason = reason
        self.required_skills = required_skills or []
        self.summary = summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_handoff": self.should_handoff,
            "trigger": self.trigger,
            "priority": self.priority,
            "reason": self.reason,
            "required_skills": self.required_skills,
            "summary": self.summary,
        }


class IntelligentHandoffManager:
    """
    智能人机转接管理器。

    整合情绪检测、意图判断、智能路由、上下文摘要。
    """

    def __init__(self, agent_plugin):
        self.agent_plugin = agent_plugin
        self.sentiment_analyzer = SentimentAnalyzer()
        self.summarizer = ConversationSummarizer()
        self.smart_router = SmartQueueRouter(agent_plugin)

    def should_handoff(
        self,
        session_messages: list[dict[str, Any]],
        last_message: str,
        intent_confidence: float = 1.0,
        explicit_request: bool = False,
    ) -> HandoffDecision:
        """
        判断是否需要转人工。

        触发条件（按优先级）：
        1. 用户明确请求转人工
        2. 负面情绪检测（分数 < -0.6）
        3. AI 意图置信度低（< 0.5）
        4. 连续多次未解决（可选）
        """
        # 1. 用户明确请求
        if explicit_request or self._is_explicit_handoff_request(last_message):
            return HandoffDecision(
                should_handoff=True,
                trigger="user_request",
                priority="normal",
                reason="用户明确要求转接人工客服",
            )

        # 2. 情绪检测
        sentiment = self.sentiment_analyzer.analyze(last_message)
        if sentiment["score"] < -0.6 and sentiment["confidence"] > 0.7:
            return HandoffDecision(
                should_handoff=True,
                trigger="negative_sentiment",
                priority="high",
                reason=f"用户情绪负面（{sentiment['label']}, 分数={sentiment['score']}），需人工安抚",
                required_skills=["complaint_handling", "customer_relations"],
            )

        # 3. 意图置信度低
        if intent_confidence < 0.5:
            return HandoffDecision(
                should_handoff=True,
                trigger="low_confidence",
                priority="medium",
                reason=f"AI 意图识别置信度过低（{intent_confidence}），无法准确理解用户意图",
            )

        # 4. 轻度负面但无需立即转接
        if sentiment["score"] < -0.3:
            return HandoffDecision(
                should_handoff=False,
                trigger="mild_negative_sentiment",
                priority="low",
                reason=f"用户情绪轻度负面（{sentiment['score']}），但 AI 仍可处理",
                summary=f"情绪分数: {sentiment['score']}，建议 AI 优先安抚",
            )

        return HandoffDecision(should_handoff=False)

    def _is_explicit_handoff_request(self, text: str) -> bool:
        """检测用户是否明确请求转人工"""
        keywords = [
            "转人工", "人工", "人工客服", "找客服", "找人工",
            "人工服务", "人工接待", "人工咨询", "客服",
            "找真人", "真客服", "活人", "客服电话",
            # 英文
            "human", "agent", "real person", "talk to human",
            "customer service", "support agent", "live chat",
        ]
        text_lower = text.lower()
        return any(kw in text_lower for kw in keywords)

    def route_to_agent(self, decision: HandoffDecision) -> dict[str, Any] | None:
        """
        将转接请求路由到最适合的坐席。

        Returns:
            分配结果，包含 agent_id 和估计等待时间
        """
        # 优先尝试智能匹配
        agent = self.smart_router.find_best_agent(
            required_skills=decision.required_skills,
            priority=decision.priority,
        )

        if agent:
            return {
                "agent_id": agent["agent_id"],
                "agent_name": agent["agent_name"],
                "skills": agent.get("skills", []),
                "assignment_type": "smart_match",
                "estimated_wait": 0,  # 立即分配
            }

        # 智能匹配失败，回退到原生的 get_available_agent（FIFO）
        fallback_agent = self.agent_plugin.get_available_agent()
        if fallback_agent:
            queue_status = self.agent_plugin.get_queue_status()
            return {
                "agent_id": fallback_agent["agent_id"],
                "agent_name": fallback_agent["agent_name"],
                "skills": fallback_agent.get("skills", []),
                "assignment_type": "fallback_fifo",
                "estimated_wait": 0,
                "queue_warning": queue_status["queue_length"] > 5,
            }

        # 所有坐席都忙，进入排队
        queue_status = self.agent_plugin.get_queue_status()
        estimated_wait = self.smart_router.estimate_wait_time(
            queue_position=queue_status["queue_length"] + 1,
            required_skills=decision.required_skills,
        )

        return {
            "agent_id": "",
            "agent_name": "",
            "skills": [],
            "assignment_type": "queued",
            "estimated_wait": estimated_wait,
            "queue_position": queue_status["queue_length"] + 1,
        }

    def generate_summary(
        self,
        session_messages: list[dict[str, Any]],
        include_emotion: bool = True,
    ) -> str:
        """生成坐席简报"""
        return self.summarizer.summarize(
            messages=session_messages,
            include_user_emotion=include_emotion,
            include_attempted_solutions=True,
        )

    def create_intelligent_handoff(
        self,
        session_id: str,
        customer_id: str = "",
        customer_name: str = "",
        channel: str = "web",
        messages: list[dict[str, Any]] | None = None,
        intent_confidence: float = 1.0,
        last_message: str = "",
        explicit_request: bool = False,
        customer_tier: str = "standard",
    ) -> dict[str, Any]:
        """
        一站式智能转接：决策 → 路由 → 创建工单。

        Returns:
            完整的转接结果
        """
        # 1. 决策
        decision = self.should_handoff(
            session_messages=messages or [],
            last_message=last_message,
            intent_confidence=intent_confidence,
            explicit_request=explicit_request,
        )

        if not decision.should_handoff:
            return {
                "handoff_required": False,
                "decision": decision.to_dict(),
            }

        # 2. 生成摘要
        summary = self.generate_summary(messages or [])

        # 3. 智能路由
        routing = self.route_to_agent(decision)

        # 4. 创建转接记录
        metadata = {
            "trigger": decision.trigger,
            "priority": decision.priority,
            "summary": summary,
            "required_skills": decision.required_skills,
            "customer_tier": customer_tier,
            "assignment_type": routing.get("assignment_type", "unknown"),
        }

        result = self.agent_plugin.request_handoff(
            session_id=session_id,
            customer_id=customer_id,
            customer_name=customer_name,
            channel=channel,
            reason=decision.reason,
            priority=decision.priority,
            metadata=metadata,
        )

        # 如果有坐席立即分配，更新分配信息
        if routing.get("agent_id"):
            self.agent_plugin.assign_handoff(
                result["id"], routing["agent_id"]
            )
            result["assigned_agent"] = routing["agent_id"]
            result["agent_name"] = routing["agent_name"]
            result["estimated_wait"] = 0
        else:
            result["estimated_wait"] = routing.get("estimated_wait", 300)
            result["queue_position"] = routing.get("queue_position", 1)

        result["handoff_required"] = True
        result["summary"] = summary
        result["decision"] = decision.to_dict()

        return result
