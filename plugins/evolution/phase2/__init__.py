"""
EchoServe Evolution System — Phase 2: Parameter Optimisation

参数自动化调优模块。
包含：
- param_pool: 参数配置池
- experimenter: A/B 实验器（一致性哈希分流）
- evaluator: 效果评估器（t-test 统计检验）

职责：
基于 Phase 1 采集的数据，对单一参数进行 A/B 实验，
用 t-test 判断候选参数是否显著优于当前基线。
"""
from __future__ import annotations

from .evaluator import Evaluator
from .experimenter import Experimenter
from .param_pool import ParamPool

__all__ = [
    "ParamPool",
    "Experimenter",
    "Evaluator",
]
