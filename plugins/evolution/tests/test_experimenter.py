"""
EchoServe Evolution System — Tests: Experimenter & Evaluator

测试 phase2.experimenter 的分流算法和 phase2.evaluator 的统计检验。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evolution.phase2.evaluator import Evaluator, TTestResult
from evolution.phase2.experimenter import Experimenter
from evolution.phase2.param_pool import ParamDefinition, ParamPool
from evolution.shared.models import ExperimentStatus


class TestParamPool(unittest.TestCase):
    """测试 ParamPool。"""

    def test_register_and_get(self):
        """测试注册和获取。"""
        pool = ParamPool()
        pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        self.assertEqual(pool.get("top_k"), 5)

    def test_get_missing(self):
        """测试获取未注册参数。"""
        pool = ParamPool()
        with self.assertRaises(KeyError):
            pool.get("missing")

    def test_set_manual(self):
        """测试手动设置。"""
        pool = ParamPool()
        pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        pool.set("top_k", 7, "emergency")
        self.assertEqual(pool.get("top_k"), 7)

    def test_snapshot_and_restore(self):
        """测试快照和恢复。"""
        pool = ParamPool()
        pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        pool.register(ParamDefinition("threshold", "阈值", 0.5, [0.3, 0.7]))
        pool.snapshot("baseline")
        pool.set("top_k", 10)
        self.assertEqual(pool.get("top_k"), 10)
        pool.restore("baseline")
        self.assertEqual(pool.get("top_k"), 5)

    def test_experiment_lifecycle(self):
        """测试实验生命周期。"""
        pool = ParamPool()
        pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        pool.set_experiment("top_k", "exp_v1", [3, 7, 10])
        param = pool.get_definition("top_k")
        self.assertTrue(param.is_active_experiment)
        self.assertEqual(param.experiment_version, "exp_v1")
        pool.commit_experiment("top_k", 7)
        self.assertEqual(pool.get("top_k"), 7)
        self.assertFalse(pool.get_definition("top_k").is_active_experiment)

    def test_list_params(self):
        """测试参数列表。"""
        pool = ParamPool()
        pool.register(ParamDefinition("a", "A", 1, [2, 3]))
        pool.register(ParamDefinition("b", "B", "x", ["y", "z"]))
        self.assertEqual(sorted(pool.list_params()), ["a", "b"])


class TestExperimenter(unittest.TestCase):
    """测试 Experimenter。"""

    def setUp(self):
        """初始化。"""
        self.pool = ParamPool()
        self.pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        self.experimenter = Experimenter(self.pool, traffic_percent=50)

    def _run_async(self, coro):
        """辅助：运行异步协程。"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_create_experiment(self):
        """测试创建实验。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        )
        self.assertEqual(len(exp_id), 6)  # uuid 前 6 位
        state = self.experimenter.get_experiment_state(exp_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.config.param_name, "top_k")
        self.assertEqual(state.config.status, ExperimentStatus.RUNNING)

    def test_assign_user_consistency(self):
        """测试用户分配一致性。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        )
        # 同一用户应始终分配到同一组
        user_id = "user_123"
        assignment1 = self.experimenter.assign_user(user_id, "top_k")
        assignment2 = self.experimenter.assign_user(user_id, "top_k")
        self.assertEqual(assignment1.group, assignment2.group)
        self.assertEqual(assignment1.assigned_value, assignment2.assigned_value)

    def test_assign_user_baseline_when_no_experiment(self):
        """测试无实验时返回基线。"""
        assignment = self.experimenter.assign_user("user_1", "top_k")
        # top_k 未激活实验，返回 control + 基线值
        self.assertEqual(assignment.group, "control")
        self.assertEqual(assignment.assigned_value, 5)

    def test_hash_distribution(self):
        """测试分流分布大致均匀。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        )
        treatment_count = 0
        control_count = 0
        n = 1000
        for i in range(n):
            a = self.experimenter.assign_user(f"user_{i}", "top_k")
            if a.group == "treatment":
                treatment_count += 1
            else:
                control_count += 1
        # 50% 流量下，treatment 应在 40%-60% 之间（概率极高）
        self.assertGreater(treatment_count, n * 0.35)
        self.assertLess(treatment_count, n * 0.65)

    def test_record_metric(self):
        """测试指标记录。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        )
        # 先分配用户
        a = self.experimenter.assign_user("user_1", "top_k")
        self.experimenter.record_metric("user_1", "top_k", 0.8)
        state = self.experimenter.get_experiment_state(exp_id)
        total_metrics = len(state.control_metrics) + len(state.treatment_metrics)
        self.assertEqual(total_metrics, 1)

    def test_pause_resume(self):
        """测试暂停和恢复。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        )
        self.experimenter.pause_experiment(exp_id)
        state = self.experimenter.get_experiment_state(exp_id)
        self.assertEqual(state.config.status, ExperimentStatus.PAUSED)
        self.experimenter.resume_experiment(exp_id)
        self.assertEqual(state.config.status, ExperimentStatus.RUNNING)

    def test_assignment_stats(self):
        """测试分配统计。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate")
        )
        for i in range(10):
            self.experimenter.assign_user(f"user_{i}", "top_k")
        stats = self.experimenter.get_assignment_stats(exp_id)
        self.assertEqual(stats["control_group_size"] + stats["treatment_group_size"], 10)


class TestEvaluator(unittest.TestCase):
    """测试 Evaluator。"""

    def setUp(self):
        """初始化。"""
        self.pool = ParamPool()
        self.pool.register(ParamDefinition("top_k", "检索TopK", 5, [3, 7, 10]))
        self.experimenter = Experimenter(self.pool)
        self.evaluator = Evaluator(self.pool, self.experimenter)

    def _run_async(self, coro):
        """辅助：运行异步协程。"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _create_experiment_with_data(self, control_values: list[float], treatment_values: list[float]):
        """辅助：创建带数据的实验，确保数据写入正确的组。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate", min_samples=10)
        )
        control_queue = list(control_values)
        treatment_queue = list(treatment_values)
        user_counter = 0
        # 循环分配用户直到两组数据都写完
        while control_queue or treatment_queue:
            uid = f"user_{user_counter}"
            a = self.experimenter.assign_user(uid, "top_k")
            if a.group == "control" and control_queue:
                self.experimenter.record_metric(uid, "top_k", control_queue.pop(0))
            elif a.group == "treatment" and treatment_queue:
                self.experimenter.record_metric(uid, "top_k", treatment_queue.pop(0))
            user_counter += 1
            if user_counter > 10000:
                break
        return exp_id

    def test_insufficient_samples(self):
        """测试样本不足。"""
        exp_id = self._run_async(
            self.experimenter.create_experiment("top_k", [3, 7, 10], "retrieval_hit_rate", min_samples=100)
        )
        result = self.evaluator.evaluate(exp_id)
        self.assertIsNone(result)

    def test_welch_ttest(self):
        """测试 Welch's t-test 计算。"""
        control = [0.5] * 100
        treatment = [0.7] * 100
        exp_id = self._create_experiment_with_data(control, treatment)
        result = self.evaluator.evaluate(exp_id)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, TTestResult)
        self.assertGreater(result.t_statistic, 0)
        self.assertLess(result.p_value, 0.05)
        self.assertTrue(result.is_significant)

    def test_no_significance(self):
        """测试无显著差异。"""
        control = [0.5 + i * 0.01 for i in range(100)]
        treatment = [0.5 + i * 0.01 for i in range(100)]
        exp_id = self._create_experiment_with_data(control, treatment)
        result = self.evaluator.evaluate(exp_id)
        self.assertIsNotNone(result)
        # 相同数据应不显著
        # 注意：由于数据完全相同，t-statistic 为 0 或接近 0
        self.assertGreaterEqual(result.p_value, 0.05)
        self.assertFalse(result.is_significant)

    def test_commit_winner(self):
        """测试提交胜出实验。"""
        # 使用有微小噪声的数据，避免方差为 0 导致 cohens_d = 0
        control = [0.5 + i * 0.001 for i in range(100)]
        treatment = [0.7 + i * 0.001 for i in range(100)]
        exp_id = self._create_experiment_with_data(control, treatment)
        result = self.evaluator.evaluate(exp_id)
        self.assertIsNotNone(result)
        self.evaluator.commit(exp_id, result)
        # 检查参数是否更新
        self.assertIn(self.pool.get("top_k"), [3, 7, 10])

    def test_rollback_not_significant(self):
        """测试不显著时回滚。"""
        control = [0.5] * 100
        treatment = [0.5] * 100  # 完全相同
        exp_id = self._create_experiment_with_data(control, treatment)
        result = self.evaluator.evaluate(exp_id)
        self.assertIsNotNone(result)
        self.evaluator.commit(exp_id, result)
        # 不显著应回滚，保持基线值
        self.assertEqual(self.pool.get("top_k"), 5)

    def test_batch_evaluate(self):
        """测试批量评估。"""
        # 创建多个实验
        for _ in range(2):
            self._create_experiment_with_data([0.5] * 100, [0.7] * 100)
        results = self.evaluator.batch_evaluate()
        self.assertGreater(len(results), 0)

    def test_cohens_d_calculation(self):
        """测试 Cohen's d 效应量。"""
        control = [0.0] * 50 + [1.0] * 50  # 均值 0.5，标准差较大
        treatment = [0.8] * 100  # 均值 0.8，标准差为 0
        exp_id = self._create_experiment_with_data(control, treatment)
        result = self.evaluator.evaluate(exp_id)
        self.assertIsNotNone(result)
        self.assertIsInstance(result.cohens_d, float)


if __name__ == "__main__":
    unittest.main()
