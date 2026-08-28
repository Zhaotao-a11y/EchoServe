"""
EchoServe Evolution System — Failover & Degradation Manager

三级降级策略和自动恢复机制。
Phase 1-3 共享的失败处理基础设施。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger("echoserve.evolution.failover")


class DegradationLevel(str, Enum):
    """降级级别。"""
    NORMAL = "normal"
    LEVEL_1 = "level_1"  # 单参数实验暂停
    LEVEL_2 = "level_2"  # 灰度模板禁用 + 实验暂停
    LEVEL_3 = "level_3"  # EvolutionService 只读


@dataclass
class DegradationRule:
    """降级规则定义。"""
    name: str
    condition: str  # 人类可读的触发条件描述
    level: DegradationLevel
    auto_trigger: bool = True  # True: 自动触发; False: 仅告警
    cooldown_seconds: int = 300  # 两次触发最小间隔
    last_triggered: float = field(default=0.0)


@dataclass
class RecoveryAction:
    """自动恢复动作。"""
    name: str
    action: Callable[[], Coroutine]
    retry_interval_seconds: int = 3600
    max_retries: int = 3
    current_retries: int = 0
    last_attempt: float = field(default=0.0)


class FailoverManager:
    """
    失败处理与降级管理器。

    职责：
    - 监控系统异常信号
    - 按规则执行三级降级
    - 管理自动恢复任务
    - 记录降级历史

    设计约束：
    - 所有降级动作都是可逆的（除了 Level 3 需要人工介入恢复）
    - 自动恢复是幂等的
    - 告警通知是异步的，不阻塞降级决策
    """

    _MAX_HISTORY = 200  # 防止 _history 无上限增长

    def __init__(self):
        self._current_level: DegradationLevel = DegradationLevel.NORMAL
        self._rules: list[DegradationRule] = []
        self._recovery_actions: dict[str, RecoveryAction] = {}
        self._history: list[dict[str, Any]] = []
        self._paused_experiments: set[str] = set()
        self._paused_templates: set[str] = set()
        self._read_only_mode: bool = False
        self._notifier: Callable[[str, DegradationLevel], Coroutine] | None = None
        logger.info("[FailoverManager] Initialized")

    def set_notifier(
        self, notifier: Callable[[str, DegradationLevel], Coroutine]
    ) -> None:
        """设置告警通知回调（如企业微信/邮件通知）。"""
        self._notifier = notifier

    def register_rule(self, rule: DegradationRule) -> None:
        """注册降级规则。"""
        self._rules.append(rule)
        logger.info(f"[FailoverManager] Rule registered: {rule.name} -> {rule.level.value}")

    def register_recovery(self, action: RecoveryAction) -> None:
        """注册自动恢复动作。"""
        self._recovery_actions[action.name] = action
        logger.info(f"[FailoverManager] Recovery registered: {action.name}")

    @property
    def current_level(self) -> DegradationLevel:
        """获取当前降级级别。"""
        return self._current_level

    @property
    def rules_count(self) -> int:
        """已注册的降级规则数量。"""
        return len(self._rules)

    @property
    def history_count(self) -> int:
        """降级历史记录数量。"""
        return len(self._history)

    def get_current_level(self) -> DegradationLevel:
        """获取当前降级级别（兼容方法调用）。"""
        return self._current_level

    @staticmethod
    def level_int(level: DegradationLevel) -> int:
        """返回降级级别的数值（0-3），用于比较。"""
        mapping = {
            DegradationLevel.NORMAL: 0,
            DegradationLevel.LEVEL_1: 1,
            DegradationLevel.LEVEL_2: 2,
            DegradationLevel.LEVEL_3: 3,
        }
        return mapping.get(level, 0)

    def current_level_int(self) -> int:
        """当前降级级别的数值（0-3）。"""
        return self.level_int(self._current_level)

    def is_read_only(self) -> bool:
        """是否处于只读模式（Level 3）。"""
        return self._current_level == DegradationLevel.LEVEL_3 or self._read_only_mode

    def can_run_experiment(self) -> bool:
        """是否可以运行参数实验（Level 1 及以下）。"""
        return self._current_level == DegradationLevel.NORMAL

    def can_activate_template(self) -> bool:
        """是否可以激活新模板（Level 2 及以下）。"""
        return self._current_level in (
            DegradationLevel.NORMAL,
            DegradationLevel.LEVEL_1,
        )

    async def evaluate_signal(self, signal_name: str, data: dict[str, Any]) -> bool:
        """
        评估异常信号，决定是否触发降级。

        Args:
            signal_name: 信号标识
            data: 信号数据，包含指标值

        Returns:
            是否触发了降级
        """
        triggered = False
        for rule in self._rules:
            if not rule.auto_trigger:
                continue
            now = time.time()
            if now - rule.last_triggered < rule.cooldown_seconds:
                continue

            if await self._match_condition(rule.condition, signal_name, data):
                triggered = True
                rule.last_triggered = now
                await self._trigger_degradation(rule)

        return triggered

    async def manual_degrade(self, level: DegradationLevel, reason: str) -> None:
        """手动触发降级（如管理员发现异常时）。"""
        await self._apply_level(level, f"manual:{reason}")

    async def recover(self, level: DegradationLevel = DegradationLevel.NORMAL) -> None:
        """恢复到指定级别（人工介入恢复 Level 3 时使用）。"""
        await self._apply_level(level, "manual_recovery")

    def pause_experiment(self, experiment_id: str) -> None:
        """暂停指定实验（由 Level 1 触发）。"""
        self._paused_experiments.add(experiment_id)
        logger.warning(f"[FailoverManager] Experiment paused: {experiment_id}")

    def pause_template(self, template_id: str) -> None:
        """暂停指定模板（由 Level 2 触发）。"""
        self._paused_templates.add(template_id)
        logger.warning(f"[FailoverManager] Template paused: {template_id}")

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取降级历史。"""
        return self._history[-limit:]

    async def _match_condition(
        self, condition: str, signal_name: str, data: dict[str, Any]
    ) -> bool:
        """
        简单的规则匹配。

        支持两种匹配模式：
        1. 完全匹配：signal_name == condition
        2. 前缀匹配：signal_name 以 condition 的标识符部分开头

        实际实现中，condition 可以解析为 DSL 表达式。
        """
        if not condition or not signal_name:
            return False
        # 完全匹配
        if condition == signal_name:
            return True
        # 提取 condition 的标识符前缀
        cond_prefix = condition.split()[0] if " " in condition else condition
        if signal_name.startswith(cond_prefix):
            return True
        return False

    async def _trigger_degradation(self, rule: DegradationRule) -> None:
        """触发降级。"""
        await self._apply_level(rule.level, f"auto:{rule.name}")

    async def _apply_level(self, level: DegradationLevel, reason: str) -> None:
        """应用降级级别。"""
        old_level = self._current_level
        if old_level == level:
            return

        self._current_level = level
        entry = {
            "timestamp": time.time(),
            "from": old_level.value,
            "to": level.value,
            "reason": reason,
        }
        self._history.append(entry)
        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[-self._MAX_HISTORY:]
        logger.warning(
            f"[FailoverManager] Degraded: {old_level.value} -> {level.value} ({reason})"
        )

        # 执行级别对应动作
        if level == DegradationLevel.LEVEL_1:
            await self._on_level_1()
        elif level == DegradationLevel.LEVEL_2:
            await self._on_level_2()
        elif level == DegradationLevel.LEVEL_3:
            await self._on_level_3()

        # 发送通知
        if self._notifier:
            try:
                await self._notifier(
                    f"Evolution 降级: {old_level.value} -> {level.value} ({reason})",
                    level,
                )
            except Exception as e:
                logger.error(f"[FailoverManager] Notification failed: {e}")

    async def _on_level_1(self) -> None:
        """Level 1: 暂停所有运行中的参数实验，保持当前参数。"""
        logger.warning("[FailoverManager] Level 1: Pausing all experiments")
        # 实际暂停逻辑由外部调用 pause_experiment 完成

    async def _on_level_2(self) -> None:
        """Level 2: 暂停所有灰度模板，暂停所有实验。"""
        logger.warning("[FailoverManager] Level 2: Disabling canary templates")
        await self._on_level_1()
        # 实际禁用逻辑由外部调用 pause_template 完成

    async def _on_level_3(self) -> None:
        """Level 3: EvolutionService 只读模式。"""
        logger.warning("[FailoverManager] Level 3: Read-only mode")
        self._read_only_mode = True
        await self._on_level_2()

    async def run_recovery_checks(self) -> None:
        """
        定期检查是否有可恢复的降级。

        设计为定时任务（如每 15 分钟运行一次）。
        """
        for name, action in self._recovery_actions.items():
            now = time.time()
            if now - action.last_attempt < action.retry_interval_seconds:
                continue
            if action.current_retries >= action.max_retries:
                continue

            action.last_attempt = now
            try:
                await action.action()
                action.current_retries = 0
                logger.info(f"[FailoverManager] Recovery success: {name}")
            except Exception as e:
                action.current_retries += 1
                logger.error(
                    f"[FailoverManager] Recovery failed ({action.current_retries}/"
                    f"{action.max_retries}): {name} -> {e}"
                )

    def create_default_rules(self) -> None:
        """创建默认降级规则。"""
        self.register_rule(
            DegradationRule(
                name="experiment_metric_drop",
                condition="experiment.metric_drop > 20%",
                level=DegradationLevel.LEVEL_1,
                cooldown_seconds=300,
            )
        )
        self.register_rule(
            DegradationRule(
                name="canary_failure_rate_high",
                condition="template.canary_failure > 20%",
                level=DegradationLevel.LEVEL_2,
                cooldown_seconds=600,
            )
        )
        self.register_rule(
            DegradationRule(
                name="storage_write_blocked",
                condition="store.write_failure > 50%",
                level=DegradationLevel.LEVEL_3,
                auto_trigger=False,  # Level 3 需要人工确认
                cooldown_seconds=0,
            )
        )
