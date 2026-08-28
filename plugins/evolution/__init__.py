"""
EchoServe Evolution System — Evolution Plugin

全链路自动化进化插件。

三阶段架构：
- Phase 1 (Data Collection): 事件采集 → 结构化存储
- Phase 2 (Parameter Optimisation): 单参数 A/B 实验 → 统计检验
- Phase 3 (Skill Evolution): 模式挖掘 → 模板生成 → 人工审核 → 灰度/全量发布

共享基础设施：
- shared.models: 全链路数据模型
- shared.metrics: 指标采集器
- shared.failover: 降级管理器

使用示例：
    from evolution import EvolutionService
    service = EvolutionService(store, metrics, failover)
    await service.start()
"""
from __future__ import annotations

from .phase1.collector import EvolutionCollector
from .phase2.evaluator import Evaluator
from .phase2.experimenter import Experimenter
from .phase2.param_pool import ParamPool
from .phase3.pattern_miner import PatternMiner
from .phase3.reviewer import Reviewer
from .phase3.template_generator import TemplateGenerator
from .phase3.template_registry import TemplateRegistry
from .shared.failover import FailoverManager
from .shared.metrics import MetricsCollector

__all__ = [
    "EvolutionCollector",
    "ParamPool",
    "Experimenter",
    "Evaluator",
    "PatternMiner",
    "TemplateGenerator",
    "Reviewer",
    "TemplateRegistry",
    "FailoverManager",
    "MetricsCollector",
]
