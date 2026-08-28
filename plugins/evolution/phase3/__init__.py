"""
EchoServe Evolution System — Phase 3: Skill Evolution

技能自动化进化模块。
包含：
- pattern_miner: 技能模式挖掘器
- template_generator: 候选模板生成器
- reviewer: 人工审核台
- template_registry: 模板注册表（灰度/全量/回滚）

职责：
从 Phase 1+2 的数据中挖掘高频高成功率的技能执行模式，
自动生成候选模板，经人工审核后灰度发布，最终全量激活。
"""
from __future__ import annotations

from .pattern_miner import PatternMiner
from .reviewer import Reviewer
from .template_generator import TemplateGenerator
from .template_registry import TemplateRegistry

__all__ = [
    "PatternMiner",
    "TemplateGenerator",
    "Reviewer",
    "TemplateRegistry",
]
