"""
EchoServe Evolution System — Phase 3: TemplateGenerator

候选模板生成器。
将 PatternMiner 挖掘出的高频模式转化为可执行的技能模板候选。

设计约束：
- 从 SkillPattern 自动推导触发条件和参数映射
- 生成可模拟运行的候选模板
- 支持多种模板格式（技能序列、条件分支、循环）
- 输出包含置信度评分
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..shared.models import SkillPattern, SkillTemplateCandidate, TemplateStatus

logger = logging.getLogger("echoserve.evolution.template_generator")


@dataclass
class GenerationConfig:
    """模板生成配置。"""

    min_confidence: float = 5.0  # 最小置信度（frequency * success_rate）
    max_templates_per_intent: int = 3  # 每个意图最多生成模板数
    include_parameter_mapping: bool = True  # 是否推导参数映射
    output_template_format: str = "json"  # json / yaml


class TemplateGenerator:
    """
    候选技能模板生成器。

    职责：
    1. 接收 PatternMiner 输出模式列表
    2. 为每个模式生成标准化的 SkillTemplateCandidate
    3. 推导触发条件（基于意图 + 关键词）
    4. 推导参数映射（基于技能序列的输入/输出依赖）
    5. 生成预期输出模板

    生成逻辑：
        - 触发条件：从意图和样本 query 中提取关键词
        - 技能序列：直接复用模式中的序列
        - 参数映射：分析技能间的输入输出依赖关系

    使用示例：
        generator = TemplateGenerator()
        candidates = generator.generate(patterns, config)
    """

    def __init__(self) -> None:
        self._candidates: list[SkillTemplateCandidate] = []
        self._generation_history: list[dict[str, Any]] = []
        logger.info("[TemplateGenerator] Initialized")

    def generate(
        self,
        patterns: list[SkillPattern],
        config: GenerationConfig | None = None,
    ) -> list[SkillTemplateCandidate]:
        """
        从模式列表生成候选模板。

        Returns:
            生成的候选模板列表
        """
        cfg = config or GenerationConfig()
        candidates: list[SkillTemplateCandidate] = []

        # 按意图分组，每个意图限制数量
        intent_groups: dict[str, list[SkillPattern]] = {}
        for p in patterns:
            if p.confidence < cfg.min_confidence:
                continue
            intent_groups.setdefault(p.intent, []).append(p)

        for intent, group in intent_groups.items():
            # 按置信度排序，取 Top N
            group.sort(key=lambda p: p.confidence, reverse=True)
            selected = group[: cfg.max_templates_per_intent]

            for pattern in selected:
                candidate = self._build_candidate(pattern, cfg)
                candidates.append(candidate)
                logger.info(
                    f"[TemplateGenerator] Generated candidate: {candidate.id} "
                    f"for intent='{intent}', sequence={pattern.skill_sequence}"
                )

        self._candidates.extend(candidates)
        self._generation_history.append(
            {
                "input_patterns": len(patterns),
                "output_candidates": len(candidates),
                "config": cfg,
            }
        )

        logger.info(
            f"[TemplateGenerator] Generation complete: {len(candidates)} "
            f"candidates from {len(patterns)} patterns"
        )
        return candidates

    def generate_single(self, pattern: SkillPattern) -> SkillTemplateCandidate:
        """从单个模式生成候选模板。"""
        return self._build_candidate(pattern, GenerationConfig())

    def get_candidates(
        self, status: TemplateStatus | None = None
    ) -> list[SkillTemplateCandidate]:
        """获取候选模板列表。"""
        if status is None:
            return list(self._candidates)
        return [c for c in self._candidates if c.status == status]

    def get_candidate(self, candidate_id: str) -> SkillTemplateCandidate | None:
        """获取指定候选模板。"""
        for c in self._candidates:
            if c.id == candidate_id:
                return c
        return None

    def clear(self) -> None:
        """清空所有候选模板。"""
        self._candidates.clear()
        self._generation_history.clear()
        logger.info("[TemplateGenerator] Cleared")

    def _build_candidate(
        self, pattern: SkillPattern, config: GenerationConfig
    ) -> SkillTemplateCandidate:
        """从单个模式构建候选模板。"""
        # 触发条件推导
        triggers = self._derive_triggers(pattern)

        # 参数映射推导
        param_mapping = {}
        if config.include_parameter_mapping:
            param_mapping = self._derive_parameter_mapping(pattern)

        # 预期输出模板
        output_template = self._derive_output_template(pattern)

        # 名称生成
        name = f"auto_{pattern.intent}_{'_'.join(pattern.skill_sequence[:2])}"

        return SkillTemplateCandidate(
            name=name,
            intent=pattern.intent,
            trigger_conditions=triggers,
            skill_sequence=pattern.skill_sequence,
            parameter_mapping=param_mapping,
            expected_output_template=output_template,
            source_pattern=pattern,
            status=TemplateStatus.DRAFT,
        )

    @staticmethod
    def _derive_triggers(pattern: SkillPattern) -> list[str]:
        """
        从模式推导触发条件。

        基于意图和常见关键词推断触发规则。
        """
        triggers = [pattern.intent]

        # 从技能序列推断额外触发词
        skill_triggers = {
            "search": ["搜索", "查找", "查询"],
            "fetch": ["获取", "下载", "读取"],
            "summarize": ["总结", "概括", "摘要"],
            "calculate": ["计算", "算出", "求"],
            "translate": ["翻译", "转成"],
            "code": ["代码", "编程", "写"],
        }

        for skill in pattern.skill_sequence:
            for key, words in skill_triggers.items():
                if key.lower() in skill.lower():
                    triggers.extend(words)

        # 去重
        return list(dict.fromkeys(triggers))

    @staticmethod
    def _derive_parameter_mapping(pattern: SkillPattern) -> dict[str, str]:
        """
        推导技能间的参数映射关系。

        简单的启发式规则：前一个技能的输出 -> 后一个技能的输入
        """
        mapping = {}
        seq = pattern.skill_sequence
        for i in range(len(seq) - 1):
            from_skill = seq[i]
            to_skill = seq[i + 1]
            mapping[f"{from_skill}.output"] = f"{to_skill}.input"
        return mapping

    @staticmethod
    def _derive_output_template(pattern: SkillPattern) -> str:
        """推导预期输出格式模板。"""
        if not pattern.skill_sequence:
            return ""

        # 基于最后一个技能推导输出格式
        last_skill = pattern.skill_sequence[-1]
        templates = {
            "summarize": "{{query}} 的摘要：\n{{result}}",
            "search": "关于 {{query}} 的搜索结果：\n{{results}}",
            "translate": "{{query}} 的翻译：\n{{translation}}",
            "calculate": "{{query}} 的计算结果：\n{{result}}",
        }

        for key, template in templates.items():
            if key.lower() in last_skill.lower():
                return template

        return "{{query}} 的处理结果：\n{{result}}"
