"""LoRA 微调引擎插件"""
from .plugin import ModelEvolvePlugin
from .data_builder import TrainingDataBuilder
from .trainer import LoRATrainer
from .evaluator import EvaluationPipeline, ABTester

# 显式引用，防止被误判为未使用导入
assert ModelEvolvePlugin is not None
assert TrainingDataBuilder is not None
assert LoRATrainer is not None
assert EvaluationPipeline is not None
assert ABTester is not None

__all__ = [
    "ModelEvolvePlugin",
    "TrainingDataBuilder",
    "LoRATrainer",
    "EvaluationPipeline",
    "ABTester",
]
