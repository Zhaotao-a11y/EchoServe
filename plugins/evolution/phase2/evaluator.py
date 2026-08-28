"""
EchoServe Evolution System — Phase 2: Evaluator

效果评估器。
对 A/B 实验结果进行 t-test 统计检验，判断候选参数是否显著优于基线。

设计约束：
- 使用 Welch's t-test（不假设等方差）
- 显著性水平 alpha = 0.05
- 效应量：Cohen's d（小/中/大）
- 最小样本数检查（防止过早下结论）
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from ..shared.failover import FailoverManager
from ..shared.models import EvalResult, ExperimentConfig, ExperimentStatus
from .experimenter import Experimenter
from .param_pool import ParamPool

logger = logging.getLogger("echoserve.evolution.evaluator")


@dataclass
class TTestResult:
    """t-test 计算结果。"""

    t_statistic: float
    p_value: float
    degrees_of_freedom: float
    control_mean: float
    treatment_mean: float
    control_std: float
    treatment_std: float
    cohens_d: float
    is_significant: bool


class Evaluator:
    """
    效果评估器。

    职责：
    1. 定期扫描运行中的实验
    2. 收集足够的样本后进行 t-test
    3. 计算效应量（Cohen's d）
    4. 输出评估结果，通知 ParamPool 提交或回滚

    统计假设：
    - H0: 对照组和实验组的指标均值相等（候选参数无改进）
    - H1: 实验组均值 > 对照组均值（候选参数有显著改进）
    - 使用单侧检验（one-tailed）

    使用示例：
        evaluator = Evaluator(param_pool, experimenter)
        result = evaluator.evaluate("abc123")
        if result and result.is_significant:
            evaluator.commit("abc123", result)
    """

    ALPHA: float = 0.05  # 显著性水平
    MIN_EFFECT_SIZE: float = 0.2  # 最小可检测效应量（小效应）

    def __init__(
        self,
        param_pool: ParamPool,
        experimenter: Experimenter,
        failover: FailoverManager | None = None,
    ) -> None:
        self._pool = param_pool
        self._experimenter = experimenter
        self._failover = failover
        self._results: dict[str, EvalResult] = {}
        logger.info("[Evaluator] Initialized")

    def evaluate(self, experiment_id: str) -> TTestResult | None:
        """
        对指定实验执行 t-test 评估。

        Returns:
            TTestResult 如果样本足够；None 如果样本不足或实验不存在
        """
        state = self._experimenter.get_experiment_state(experiment_id)
        if not state:
            logger.warning(f"[Evaluator] Experiment not found: {experiment_id}")
            return None

        control = state.control_metrics
        treatment = state.treatment_metrics

        # 最小样本数检查
        min_samples = state.config.min_samples
        if len(control) < min_samples or len(treatment) < min_samples:
            logger.info(
                f"[Evaluator] Insufficient samples for {experiment_id}: "
                f"control={len(control)}, treatment={len(treatment)}, "
                f"required={min_samples}"
            )
            return None

        # Welch's t-test
        ttest = self._welch_ttest(control, treatment)

        logger.info(
            f"[Evaluator] Experiment {experiment_id}: t={ttest.t_statistic:.4f}, "
            f"p={ttest.p_value:.6f}, d={ttest.cohens_d:.4f}, "
            f"significant={ttest.is_significant}"
        )

        return ttest

    def commit(self, experiment_id: str, ttest_result: TTestResult) -> None:
        """
        提交实验结果：将胜出的候选值设为新的基线。

        只有当 t-test 显著且效应量足够大时才提交。
        """
        state = self._experimenter.get_experiment_state(experiment_id)
        if not state:
            return

        param_name = state.config.param_name

        control_n = len(state.control_metrics)
        treatment_n = len(state.treatment_metrics)

        if not ttest_result.is_significant:
            logger.info(
                f"[Evaluator] Not significant, rolling back: {experiment_id}"
            )
            self._pool.rollback_experiment(param_name)
            state.config.status = ExperimentStatus.FAILED
            self._record_result(experiment_id, state.config, ttest_result, "control", control_n, treatment_n)
            return

        if ttest_result.cohens_d < self.MIN_EFFECT_SIZE:
            logger.info(
                f"[Evaluator] Effect size too small ({ttest_result.cohens_d:.4f}), "
                f"rolling back: {experiment_id}"
            )
            self._pool.rollback_experiment(param_name)
            state.config.status = ExperimentStatus.FAILED
            self._record_result(experiment_id, state.config, ttest_result, "control", control_n, treatment_n)
            return

        # 实验组胜出：选择使均值最优的候选值
        winner = self._select_winner(state)
        self._pool.commit_experiment(param_name, winner)
        state.config.status = ExperimentStatus.CONVERGED
        self._record_result(experiment_id, state.config, ttest_result, "treatment", control_n, treatment_n)

        logger.info(
            f"[Evaluator] Experiment committed: {experiment_id}, "
            f"param={param_name}, winner={winner}"
        )

    def batch_evaluate(self) -> list[TTestResult]:
        """
        扫描所有运行中的实验并评估。

        Returns:
            所有完成评估的实验结果列表
        """
        results: list[TTestResult] = []
        for exp_id in self._experimenter.list_experiments(ExperimentStatus.RUNNING):
            result = self.evaluate(exp_id)
            if result:
                self.commit(exp_id, result)
                results.append(result)
        return results

    def get_result(self, experiment_id: str) -> EvalResult | None:
        """获取实验评估结果。"""
        return self._results.get(experiment_id)

    def list_results(self) -> dict[str, EvalResult]:
        """列出所有评估结果。"""
        return dict(self._results)

    @staticmethod
    def _welch_ttest(control: list[float], treatment: list[float]) -> TTestResult:
        """
        Welch's t-test（不假设等方差）。

        使用单侧检验：H1: treatment_mean > control_mean
        """
        n1 = len(control)
        n2 = len(treatment)

        # 防御：空列表或样本不足时返回不显著结果
        if n1 == 0 or n2 == 0:
            logger.warning(
                f"[Evaluator] _welch_ttest called with empty data: "
                f"control(n={n1}), treatment(n={n2})"
            )
            return TTestResult(
                t_statistic=0.0,
                p_value=1.0,
                cohens_d=0.0,
                is_significant=False,
                control_mean=0.0,
                treatment_mean=0.0,
                df=0.0,
            )

        m1 = sum(control) / n1
        m2 = sum(treatment) / n2

        # 样本方差（无偏估计）
        var1 = sum((x - m1) ** 2 for x in control) / (n1 - 1) if n1 > 1 else 0.0
        var2 = sum((x - m2) ** 2 for x in treatment) / (n2 - 1) if n2 > 1 else 0.0

        std1 = math.sqrt(var1)
        std2 = math.sqrt(var2)

        # Welch-Satterthwaite 自由度
        if var1 == 0 and var2 == 0:
            df = float(n1 + n2 - 2)
        else:
            numerator = (var1 / n1 + var2 / n2) ** 2
            denominator = (var1 / n1) ** 2 / (n1 - 1) + (var2 / n2) ** 2 / (n2 - 1)
            df = numerator / denominator if denominator > 0 else float(n1 + n2 - 2)

        # 标准误
        se = math.sqrt(var1 / n1 + var2 / n2)

        # t 统计量
        if se > 0:
            t_stat = (m2 - m1) / se
        elif m2 > m1:
            t_stat = float("inf")
        elif m2 < m1:
            t_stat = float("-inf")
        else:
            t_stat = 0.0

        # p-value（单侧）—— 使用正态近似（大样本下准确）
        p_value = Evaluator._normal_cdf(-abs(t_stat))

        # Cohen's d（合并标准差）
        pooled_std = math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)) if (n1 + n2 > 2) else 0.0
        cohens_d = (m2 - m1) / pooled_std if pooled_std > 0 else 0.0

        # 单侧检验：treatment > control
        is_significant = t_stat > 0 and p_value < Evaluator.ALPHA

        return TTestResult(
            t_statistic=t_stat,
            p_value=p_value,
            degrees_of_freedom=df,
            control_mean=m1,
            treatment_mean=m2,
            control_std=std1,
            treatment_std=std2,
            cohens_d=cohens_d,
            is_significant=is_significant,
        )

    @staticmethod
    def _normal_cdf(z: float) -> float:
        """标准正态分布累积分布函数（Abramowitz & Stegun 近似）。"""
        if z < 0:
            return 1.0 - Evaluator._normal_cdf(-z)
        # A&S formula 26.2.17
        b1 = 0.319381530
        b2 = -0.356563782
        b3 = 1.781477937
        b4 = -1.821255978
        b5 = 1.330274429
        p = 0.2316419
        c = 0.39894228

        t = 1.0 / (1.0 + p * z)
        return 1.0 - c * math.exp(-z * z / 2.0) * (
            t * (b1 + t * (b2 + t * (b3 + t * (b4 + t * b5))))
        )

    @staticmethod
    def _select_winner(state: Any) -> Any:
        """
        从实验组中选择最优候选值（基于指标均值）。

        遍历 treatment_metrics_by_candidate，选择均值最高的候选值。
        如果无候选值指标数据，回退到 config.candidate_values[0]。
        """
        by_candidate = getattr(state, "treatment_metrics_by_candidate", None)
        if not by_candidate:
            # 无按候选值分组的指标数据，回退到第一个候选值
            config = state.config
            if config.candidate_values:
                return config.candidate_values[0]
            return config.current_value

        best_value: str | None = None
        best_mean: float = float("-inf")

        for candidate_key, metrics in by_candidate.items():
            if not metrics:
                continue
            mean = sum(metrics) / len(metrics)
            if mean > best_mean:
                best_mean = mean
                best_value = candidate_key

        if best_value is not None:
            # 尝试将字符串 key 转换回原始类型
            config = state.config
            for cv in config.candidate_values:
                if str(cv) == best_value:
                    return cv
            return best_value

        # 所有候选值均无指标数据，回退
        config = state.config
        if config.candidate_values:
            return config.candidate_values[0]
        return config.current_value

    def _record_result(
        self,
        experiment_id: str,
        config: ExperimentConfig,
        ttest: TTestResult,
        winner: str,
        control_n: int = 0,
        treatment_n: int = 0,
    ) -> None:
        """记录评估结果到结果表。"""
        result = EvalResult(
            experiment_id=experiment_id,
            param_name=config.param_name,
            candidate_value=config.candidate_values[0] if config.candidate_values else config.current_value,
            winner=winner,
            control_metric=ttest.control_mean,
            treatment_metric=ttest.treatment_mean,
            p_value=ttest.p_value,
            sample_size=control_n + treatment_n,
            is_significant=ttest.is_significant,
        )
        self._results[experiment_id] = result
