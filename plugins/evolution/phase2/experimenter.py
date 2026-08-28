"""
EchoServe Evolution System — Phase 2: Experimenter

A/B 实验器。
基于一致性哈希将用户分流到对照组/实验组，
确保同一用户始终分配到同一组。

    设计约束：
    - 分流算法：MD5 一致性哈希，无需外部存储
    - 实验组分配：用户 ID 哈希后取模
    - 支持多参数并行实验（每个参数独立分流）
"""
from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ..shared.models import ExperimentConfig, ExperimentStatus, ParamAssignment
from .param_pool import ParamPool

logger = logging.getLogger("echoserve.evolution.experimenter")


@dataclass
class ExperimentState:
    """实验运行状态。"""

    config: ExperimentConfig
    control_group: set[str] = field(default_factory=set)
    treatment_group: set[str] = field(default_factory=set)
    control_metrics: list[float] = field(default_factory=list)
    treatment_metrics: list[float] = field(default_factory=list)
    # 按候选值分组的实验组指标，用于选择最优候选值
    treatment_metrics_by_candidate: dict[str, list[float]] = field(default_factory=dict)


class Experimenter:
    """
    A/B 实验器。

    负责：
    1. 创建和管理单参数实验
    2. 用户分流（一致性哈希）
    3. 记录用户参数分配
    4. 收集实验组/对照组的指标数据

    分流算法：
        hash = md5(user_id + param_name + experiment_version)
        group = "treatment" if hash % 100 < traffic_percent else "control"

    使用示例：
        experimenter = Experimenter(param_pool)
        await experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        assignment = experimenter.assign_user("user_123", "top_k")
        value = assignment.assigned_value  # 3, 7, 10 (treatment) or 5 (control)
    """

    DEFAULT_TRAFFIC_PERCENT = 50  # 实验组流量占比
    MAX_ASSIGNMENTS = 10000  # 最大分配记录数，防止内存无限增长

    def __init__(
        self,
        param_pool: ParamPool,
        traffic_percent: int = DEFAULT_TRAFFIC_PERCENT,
    ) -> None:
        self._pool = param_pool
        self._traffic_percent = max(0, min(100, traffic_percent))
        self._experiments: dict[str, ExperimentState] = {}
        self._assignments: OrderedDict[str, ParamAssignment] = OrderedDict()
        logger.info(
            f"[Experimenter] Initialized (traffic={self._traffic_percent}%)"
        )

    @property
    def traffic_percent(self) -> int:
        """实验组流量占比（0-100）。"""
        return self._traffic_percent

    @property
    def experiments(self) -> dict[str, Any]:
        """所有实验状态（只读视图）。"""
        return dict(self._experiments)

    async def create_experiment(
        self,
        param_name: str,
        candidate_values: list[Any],
        eval_metric: str,
        min_samples: int = 500,
        max_samples: int = 2000,
    ) -> str:
        """
        创建新实验。

        Returns:
            实验版本号
        """
        if param_name not in self._pool.list_params():
            raise ValueError(f"Param '{param_name}' not registered in pool")

        config = ExperimentConfig(
            param_name=param_name,
            current_value=self._pool.get(param_name),
            candidate_values=candidate_values,
            eval_metric=eval_metric,
            min_samples=min_samples,
            max_samples=max_samples,
            status=ExperimentStatus.RUNNING,
        )

        self._pool.set_experiment(param_name, config.experiment_version, candidate_values)
        state = ExperimentState(config=config)
        self._experiments[config.experiment_version] = state

        logger.info(
            f"[Experimenter] Experiment created: {param_name} "
            f"version={config.experiment_version}, "
            f"candidates={candidate_values}, metric={eval_metric}"
        )
        return config.experiment_version

    def assign_user(self, user_id: str, param_name: str) -> ParamAssignment:
        """
        为用户分配实验组/对照组。

        同一用户 + 同一参数 + 同一实验版本始终得到相同分配。

        Returns:
            ParamAssignment 包含分配的组名和参数值
        """
        param = self._pool.get_definition(param_name)
        if not param.is_active_experiment or not param.experiment_version:
            # 无实验时返回基线值
            return ParamAssignment(
                user_id=user_id,
                param_name=param_name,
                experiment_version="baseline",
                group="control",
                assigned_value=param.current_value,
            )

        exp_id = param.experiment_version
        state = self._experiments.get(exp_id)
        if not state or state.config.status != ExperimentStatus.RUNNING:
            return ParamAssignment(
                user_id=user_id,
                param_name=param_name,
                experiment_version=exp_id,
                group="control",
                assigned_value=param.current_value,
            )

        # 一致性哈希分流
        group = self._hash_user(user_id, param_name, exp_id)

        if group == "treatment":
            # 实验组：从候选值中再哈希选择一个
            candidate = self._select_candidate(user_id, param_name, exp_id, param.candidate_values)
            state.treatment_group.add(user_id)
        else:
            candidate = param.current_value
            state.control_group.add(user_id)

        assignment = ParamAssignment(
            user_id=user_id,
            param_name=param_name,
            experiment_version=exp_id,
            group=group,
            assigned_value=candidate,
        )

        key = f"{user_id}:{param_name}:{exp_id}"
        self._assignments[key] = assignment
        self._assignments.move_to_end(key)
        if len(self._assignments) > self.MAX_ASSIGNMENTS:
            self._assignments.popitem(last=False)

        logger.debug(
            f"[Experimenter] Assigned: user={user_id}, param={param_name}, "
            f"group={group}, value={candidate}"
        )
        return assignment

    def record_metric(
        self, user_id: str, param_name: str, metric_value: float
    ) -> None:
        """
        记录用户的实验指标值。

        由业务系统在请求处理完成后调用。
        """
        param = self._pool.get_definition(param_name)
        if not param.is_active_experiment or not param.experiment_version:
            return

        exp_id = param.experiment_version
        state = self._experiments.get(exp_id)
        if not state:
            return

        key = f"{user_id}:{param_name}:{exp_id}"
        assignment = self._assignments.get(key)
        if not assignment:
            return

        if assignment.group == "treatment":
            state.treatment_metrics.append(metric_value)
            # 同时按候选值分组记录，供 _select_winner 使用
            candidate_key = str(assignment.assigned_value)
            if candidate_key not in state.treatment_metrics_by_candidate:
                state.treatment_metrics_by_candidate[candidate_key] = []
            state.treatment_metrics_by_candidate[candidate_key].append(metric_value)
        else:
            state.control_metrics.append(metric_value)

        # 检查是否达到最大样本数
        total = len(state.control_metrics) + len(state.treatment_metrics)
        if total >= state.config.max_samples:
            logger.info(
                f"[Experimenter] Max samples reached for {exp_id}: {total}"
            )

    def get_experiment_state(self, experiment_id: str) -> ExperimentState | None:
        """获取实验状态。"""
        return self._experiments.get(experiment_id)

    def list_experiments(
        self, status: ExperimentStatus | None = None
    ) -> list[str]:
        """列出实验 ID。"""
        if status is None:
            return list(self._experiments.keys())
        return [
            eid
            for eid, state in self._experiments.items()
            if state.config.status == status
        ]

    def pause_experiment(self, experiment_id: str) -> None:
        """暂停实验（由 FailoverManager Level 1 触发）。"""
        state = self._experiments.get(experiment_id)
        if state:
            state.config.status = ExperimentStatus.PAUSED
            logger.warning(f"[Experimenter] Experiment paused: {experiment_id}")

    def resume_experiment(self, experiment_id: str) -> None:
        """恢复实验。"""
        state = self._experiments.get(experiment_id)
        if state:
            state.config.status = ExperimentStatus.RUNNING
            logger.info(f"[Experimenter] Experiment resumed: {experiment_id}")

    def get_assignment_stats(self, experiment_id: str) -> dict[str, Any]:
        """获取实验分配统计。"""
        state = self._experiments.get(experiment_id)
        if not state:
            return {}
        return {
            "control_group_size": len(state.control_group),
            "treatment_group_size": len(state.treatment_group),
            "control_metrics_count": len(state.control_metrics),
            "treatment_metrics_count": len(state.treatment_metrics),
            "config": state.config,
        }

    @staticmethod
    def _hash_user(user_id: str, param_name: str, experiment_version: str) -> str:
        """
        一致性哈希分流。

        使用 MD5 算法计算 hash，结果在 0-99 之间，
        小于 traffic_percent 的为实验组。
        """
        key = f"{user_id}:{param_name}:{experiment_version}"
        hash_val = hashlib.md5(key.encode()).hexdigest()
        bucket = int(hash_val, 16) % 100
        return "treatment" if bucket < Experimenter.DEFAULT_TRAFFIC_PERCENT else "control"

    @staticmethod
    def _select_candidate(
        user_id: str, param_name: str, experiment_version: str, candidates: list[Any]
    ) -> Any:
        """为实验组用户从候选值中选择一个。"""
        key = f"{user_id}:{param_name}:{experiment_version}:candidate"
        hash_val = hashlib.md5(key.encode()).hexdigest()
        idx = int(hash_val, 16) % len(candidates)
        return candidates[idx]
