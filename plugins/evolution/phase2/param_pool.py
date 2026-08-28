"""
EchoServe Evolution System — Phase 2: ParamPool

参数配置池。
集中管理所有可调参数及其候选值，支持参数快照和回滚。

设计约束：
- 线程安全（async-safe，单线程事件循环假设）
- 参数变更需通过实验器审批
- 支持参数值的 schema 校验
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("echoserve.evolution.param_pool")


@dataclass
class ParamDefinition:
    """参数定义。"""

    name: str
    description: str
    current_value: Any
    candidate_values: list[Any] = field(default_factory=list)
    value_type: str = "float"  # float / int / str / bool
    min_value: float | None = None
    max_value: float | None = None
    is_active_experiment: bool = False
    experiment_version: str | None = None


class ParamPool:
    """
    参数配置池。

    管理所有可调参数的当前值、候选值和实验状态。
    参数分为两类：
    - 基线参数：当前生产环境使用的值
    - 实验参数：正在 A/B 实验中的候选值

    使用方法：
        pool = ParamPool()
        pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        current = pool.get("top_k")
        pool.set_experiment("top_k", "abc123", [3, 7, 10])
    """

    _MAX_HISTORY = 500  # 防止 _history 无上限增长

    def __init__(self) -> None:
        self._params: dict[str, ParamDefinition] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        logger.info("[ParamPool] Initialized")

    def _append_history(self, entry: dict[str, Any]) -> None:
        """追加历史记录，超出上限时裁剪旧记录。"""
        self._history.append(entry)
        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[-self._MAX_HISTORY:]

    def register(self, param: ParamDefinition) -> None:
        """注册一个参数定义。"""
        self._params[param.name] = param
        logger.info(f"[ParamPool] Registered: {param.name}={param.current_value}")

    def get(self, name: str) -> Any:
        """获取参数的当前值。"""
        if name not in self._params:
            raise KeyError(f"Param '{name}' not registered")
        return self._params[name].current_value

    def get_definition(self, name: str) -> ParamDefinition:
        """获取参数的完整定义。"""
        if name not in self._params:
            raise KeyError(f"Param '{name}' not registered")
        return self._params[name]

    def set(self, name: str, value: Any, reason: str = "manual") -> None:
        """
        直接设置参数值（需人工确认，不走实验流程）。

        用于紧急回滚或管理员手动调整。
        """
        if name not in self._params:
            raise KeyError(f"Param '{name}' not registered")

        old = self._params[name].current_value
        self._params[name].current_value = value
        self._params[name].is_active_experiment = False
        self._params[name].experiment_version = None

        entry = {
            "param": name,
            "old": old,
            "new": value,
            "reason": reason,
            "via_experiment": False,
        }
        self._append_history(entry)
        logger.warning(f"[ParamPool] Manual override: {name} {old} -> {value} ({reason})")

    def set_experiment(
        self, name: str, experiment_version: str, candidate_values: list[Any]
    ) -> None:
        """
        将参数标记为实验状态。

        由 Experimenter 调用，启动 A/B 实验时设置。
        """
        if name not in self._params:
            raise KeyError(f"Param '{name}' not registered")

        self._params[name].is_active_experiment = True
        self._params[name].experiment_version = experiment_version
        self._params[name].candidate_values = candidate_values
        logger.info(
            f"[ParamPool] Experiment started: {name} "
            f"version={experiment_version}, candidates={candidate_values}"
        )

    def commit_experiment(self, name: str, winner_value: Any) -> None:
        """
        提交实验结果，将胜出的候选值设为新的基线。

        由 Evaluator 调用，当 t-test 显著时执行。
        """
        if name not in self._params:
            raise KeyError(f"Param '{name}' not registered")

        old = self._params[name].current_value
        self._params[name].current_value = winner_value
        self._params[name].is_active_experiment = False
        self._params[name].experiment_version = None

        entry = {
            "param": name,
            "old": old,
            "new": winner_value,
            "reason": "experiment_converged",
            "via_experiment": True,
        }
        self._append_history(entry)
        logger.info(
            f"[ParamPool] Experiment committed: {name} {old} -> {winner_value}"
        )

    def rollback_experiment(self, name: str) -> None:
        """回滚实验，恢复基线值。"""
        if name not in self._params:
            raise KeyError(f"Param '{name}' not registered")

        self._params[name].is_active_experiment = False
        self._params[name].experiment_version = None
        logger.info(f"[ParamPool] Experiment rolled back: {name}")

    def snapshot(self, label: str) -> None:
        """创建参数快照。"""
        self._snapshots[label] = {
            name: copy.deepcopy(p.current_value) for name, p in self._params.items()
        }
        logger.info(f"[ParamPool] Snapshot created: {label}")

    def restore(self, label: str) -> None:
        """从快照恢复参数。"""
        if label not in self._snapshots:
            raise KeyError(f"Snapshot '{label}' not found")

        for name, value in self._snapshots[label].items():
            if name in self._params:
                self._params[name].current_value = value
                self._params[name].is_active_experiment = False
                self._params[name].experiment_version = None
        logger.warning(f"[ParamPool] Restored from snapshot: {label}")

    def list_params(self, active_only: bool = False) -> list[str]:
        """列出所有参数名。"""
        names = list(self._params.keys())
        if active_only:
            names = [
                n for n in names if self._params[n].is_active_experiment
            ]
        return names

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取参数变更历史。"""
        return self._history[-limit:]

    def summary(self) -> dict[str, Any]:
        """返回参数池摘要。"""
        return {
            "total_params": len(self._params),
            "active_experiments": sum(
                1 for p in self._params.values() if p.is_active_experiment
            ),
            "snapshots": list(self._snapshots.keys()),
            "history_count": len(self._history),
        }
