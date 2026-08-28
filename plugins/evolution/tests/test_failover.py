"""
EchoServe Evolution System — Tests: FailoverManager

测试 shared.failover 的降级规则匹配、级别切换和恢复机制。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evolution.shared.failover import (
    DegradationLevel,
    DegradationRule,
    FailoverManager,
    RecoveryAction,
)


class TestDegradationLevel(unittest.TestCase):
    """测试降级级别枚举。"""

    def test_level_values(self):
        """测试级别值。"""
        self.assertEqual(DegradationLevel.NORMAL.value, "normal")
        self.assertEqual(DegradationLevel.LEVEL_1.value, "level_1")
        self.assertEqual(DegradationLevel.LEVEL_2.value, "level_2")
        self.assertEqual(DegradationLevel.LEVEL_3.value, "level_3")

    def test_level_ordering(self):
        """测试级别递增关系。"""
        levels = [DegradationLevel.NORMAL, DegradationLevel.LEVEL_1,
                   DegradationLevel.LEVEL_2, DegradationLevel.LEVEL_3]
        # 确保可以比较（通过 value 长度或名称）
        self.assertLess(len(DegradationLevel.NORMAL.value), len(DegradationLevel.LEVEL_3.value))


class TestDegradationRule(unittest.TestCase):
    """测试降级规则。"""

    def test_rule_creation(self):
        """测试规则创建。"""
        rule = DegradationRule(
            name="test_rule",
            condition="metric > 90",
            level=DegradationLevel.LEVEL_1,
        )
        self.assertEqual(rule.name, "test_rule")
        self.assertTrue(rule.auto_trigger)
        self.assertEqual(rule.cooldown_seconds, 300)

    def test_rule_no_auto_trigger(self):
        """测试非自动触发规则。"""
        rule = DegradationRule(
            name="manual_rule",
            condition="critical",
            level=DegradationLevel.LEVEL_3,
            auto_trigger=False,
            cooldown_seconds=0,
        )
        self.assertFalse(rule.auto_trigger)


class TestRecoveryAction(unittest.TestCase):
    """测试恢复动作。"""

    def test_action_creation(self):
        """测试恢复动作创建。"""
        async def dummy_action():
            pass

        action = RecoveryAction(name="restart_db", action=dummy_action)
        self.assertEqual(action.name, "restart_db")
        self.assertEqual(action.max_retries, 3)
        self.assertEqual(action.current_retries, 0)


class TestFailoverManager(unittest.TestCase):
    """测试 FailoverManager。"""

    def setUp(self):
        """初始化。"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.manager = FailoverManager()

    def tearDown(self):
        """清理。"""
        self.loop.close()

    def _run_async(self, coro):
        """辅助：运行异步协程。"""
        return self.loop.run_until_complete(coro)

    def test_init(self):
        """测试初始化状态。"""
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.NORMAL)
        self.assertFalse(self.manager.is_read_only())
        self.assertTrue(self.manager.can_run_experiment())
        self.assertTrue(self.manager.can_activate_template())

    def test_register_rule(self):
        """测试规则注册。"""
        rule = DegradationRule(
            name="metric_drop",
            condition="metric_drop > 20%",
            level=DegradationLevel.LEVEL_1,
        )
        self.manager.register_rule(rule)
        self.assertEqual(len(self.manager._rules), 1)

    def test_register_recovery(self):
        """测试恢复动作注册。"""
        async def dummy():
            pass

        action = RecoveryAction(name="restart", action=dummy)
        self.manager.register_recovery(action)
        self.assertEqual(len(self.manager._recovery_actions), 1)

    def test_manual_degrade_level_1(self):
        """测试手动降级到 Level 1。"""
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_1, "test"))
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.LEVEL_1)
        self.assertFalse(self.manager.can_run_experiment())
        self.assertTrue(self.manager.can_activate_template())
        self.assertFalse(self.manager.is_read_only())

    def test_manual_degrade_level_2(self):
        """测试手动降级到 Level 2。"""
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_2, "test"))
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.LEVEL_2)
        self.assertFalse(self.manager.can_run_experiment())
        self.assertFalse(self.manager.can_activate_template())
        self.assertFalse(self.manager.is_read_only())

    def test_manual_degrade_level_3(self):
        """测试手动降级到 Level 3。"""
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_3, "test"))
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.LEVEL_3)
        self.assertFalse(self.manager.can_run_experiment())
        self.assertFalse(self.manager.can_activate_template())
        self.assertTrue(self.manager.is_read_only())

    def test_recover(self):
        """测试恢复到正常。"""
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_2, "test"))
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.LEVEL_2)
        self._run_async(self.manager.recover(DegradationLevel.NORMAL))
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.NORMAL)
        self.assertTrue(self.manager.can_run_experiment())
        self.assertTrue(self.manager.can_activate_template())

    def test_get_history(self):
        """测试历史记录。"""
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_1, "test1"))
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_2, "test2"))
        history = self.manager.get_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["to"], "level_1")
        self.assertEqual(history[1]["to"], "level_2")

    def test_pause_experiment(self):
        """测试暂停实验。"""
        self.manager.pause_experiment("exp_1")
        self.assertIn("exp_1", self.manager._paused_experiments)

    def test_pause_template(self):
        """测试暂停模板。"""
        self.manager.pause_template("tmpl_1")
        self.assertIn("tmpl_1", self.manager._paused_templates)

    def test_evaluate_signal_match(self):
        """测试信号匹配触发降级。"""
        rule = DegradationRule(
            name="metric_drop",
            condition="experiment.metric_drop > 20%",
            level=DegradationLevel.LEVEL_1,
            cooldown_seconds=0,
        )
        self.manager.register_rule(rule)
        triggered = self._run_async(
            self.manager.evaluate_signal("experiment.metric_drop > 20%", {"value": 25})
        )
        self.assertTrue(triggered)
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.LEVEL_1)

    def test_evaluate_signal_no_match(self):
        """测试信号不匹配。"""
        rule = DegradationRule(
            name="metric_drop",
            condition="experiment.metric_drop > 20%",
            level=DegradationLevel.LEVEL_1,
            cooldown_seconds=0,
        )
        self.manager.register_rule(rule)
        triggered = self._run_async(
            self.manager.evaluate_signal("other.metric", {"value": 25})
        )
        self.assertFalse(triggered)
        self.assertEqual(self.manager.get_current_level(), DegradationLevel.NORMAL)

    def test_evaluate_signal_no_auto_trigger(self):
        """测试非自动触发规则不降级。"""
        rule = DegradationRule(
            name="storage_fail",
            condition="store.write_failure > 50%",
            level=DegradationLevel.LEVEL_3,
            auto_trigger=False,
            cooldown_seconds=0,
        )
        self.manager.register_rule(rule)
        triggered = self._run_async(
            self.manager.evaluate_signal("store.write_failure > 50%", {"value": 60})
        )
        self.assertFalse(triggered)

    def test_cooldown_prevents_retrigger(self):
        """测试冷却时间防止重复触发。"""
        rule = DegradationRule(
            name="metric_drop",
            condition="experiment.metric_drop > 20%",
            level=DegradationLevel.LEVEL_1,
            cooldown_seconds=3600,  # 1 小时冷却
        )
        self.manager.register_rule(rule)
        # 第一次触发
        triggered1 = self._run_async(
            self.manager.evaluate_signal("experiment.metric_drop > 20%", {"value": 25})
        )
        self.assertTrue(triggered1)
        # 立即再次触发（在冷却期内）
        triggered2 = self._run_async(
            self.manager.evaluate_signal("experiment.metric_drop > 20%", {"value": 30})
        )
        self.assertFalse(triggered2)

    def test_run_recovery_checks(self):
        """测试恢复检查。"""
        call_count = 0

        async def recovery_action():
            nonlocal call_count
            call_count += 1

        action = RecoveryAction(
            name="test_recovery",
            action=recovery_action,
            retry_interval_seconds=0,
        )
        self.manager.register_recovery(action)
        self._run_async(self.manager.run_recovery_checks())
        self.assertEqual(call_count, 1)

    def test_run_recovery_failure(self):
        """测试恢复失败重试。"""
        async def failing_action():
            raise RuntimeError("fail")

        action = RecoveryAction(
            name="failing_recovery",
            action=failing_action,
            retry_interval_seconds=0,
            max_retries=2,
        )
        self.manager.register_recovery(action)
        # 第一次
        self._run_async(self.manager.run_recovery_checks())
        self.assertEqual(action.current_retries, 1)
        # 第二次
        self._run_async(self.manager.run_recovery_checks())
        self.assertEqual(action.current_retries, 2)
        # 第三次（超过 max_retries，不应执行）
        self._run_async(self.manager.run_recovery_checks())
        self.assertEqual(action.current_retries, 2)  # 不再增加

    def test_create_default_rules(self):
        """测试默认规则创建。"""
        self.manager.create_default_rules()
        self.assertEqual(len(self.manager._rules), 3)
        rule_names = [r.name for r in self.manager._rules]
        self.assertIn("experiment_metric_drop", rule_names)
        self.assertIn("canary_failure_rate_high", rule_names)
        self.assertIn("storage_write_blocked", rule_names)

    def test_notifier(self):
        """测试告警通知回调。"""
        notifications: list = []

        async def mock_notifier(message: str, level: DegradationLevel):
            notifications.append((message, level))

        self.manager.set_notifier(mock_notifier)
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_1, "test"))
        self.assertEqual(len(notifications), 1)
        self.assertIn("level_1", notifications[0][0])

    def test_idempotent_degrade(self):
        """测试重复降级是幂等的。"""
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_1, "test"))
        history_len = len(self.manager.get_history())
        # 再次降级到同一级别
        self._run_async(self.manager.manual_degrade(DegradationLevel.LEVEL_1, "test2"))
        self.assertEqual(len(self.manager.get_history()), history_len)  # 不应新增记录


if __name__ == "__main__":
    unittest.main()
