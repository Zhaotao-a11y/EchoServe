"""
EchoServe Evolution System v1.0 — 自我进化插件入口

将 Phase 1（数据采集）+ Phase 2（单参数 A/B）+ Phase 3（技能进化）
接入 EchoServe 主框架，通过 BaizePlugin 生命周期完成依赖注入。

生命周期绑定：
    on_load   → 注册服务到 Context
    on_init   → 初始化 Store / Collector / FailoverManager，挂载路由
    on_start  → 订阅 EventBus 事件，启动采集循环
    on_stop   → final_flush 缓冲数据，优雅退出
    on_destroy→ 关闭数据库连接
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from core.context import BaizeContext
from core.fiber import Fiber
from core.plugin import BaizePlugin

try:
    from .phase1.collector import EvolutionCollector
    from .phase1.query import router as evolution_router
    from .phase1.store import EvolutionStore
    from .phase2.evaluator import Evaluator
    from .phase2.experimenter import Experimenter
    from .phase2.param_pool import ParamPool
    from .phase3.pattern_miner import PatternMiner
    from .phase3.reviewer import Reviewer
    from .phase3.template_generator import GenerationConfig, TemplateGenerator
    from .phase3.template_registry import TemplateRegistry
    from .shared.failover import DegradationLevel, FailoverManager
    from .shared.metrics import MetricsCollector
    from .shared.models import ExperimentStatus
    from config.settings import EvolutionConfig
except ImportError:
    # 独立测试时 fallback
    from phase1.collector import EvolutionCollector
    from phase1.query import router as evolution_router
    from phase1.store import EvolutionStore
    from phase2.evaluator import Evaluator
    from phase2.experimenter import Experimenter
    from phase2.param_pool import ParamPool
    from phase3.pattern_miner import PatternMiner
    from phase3.reviewer import Reviewer
    from phase3.template_generator import GenerationConfig, TemplateGenerator
    from phase3.template_registry import TemplateRegistry
    from shared.failover import DegradationLevel, FailoverManager
    from shared.metrics import MetricsCollector
    from shared.models import ExperimentStatus
    from config.settings import EvolutionConfig

logger = logging.getLogger("echoserve.evolution")


class EvolutionPlugin(BaizePlugin):
    """
    EchoServe 智能体自我进化系统插件（v1.0）。

    注册服务：
    - "evolution"      → EvolutionPlugin 实例
    - "evolution_store"→ EvolutionStore 实例
    - "evolution_collector" → EvolutionCollector 实例
    - "failover_manager"    → FailoverManager 实例
    - "param_pool"     → ParamPool 实例
    - "experimenter"   → Experimenter 实例
    - "evaluator"      → Evaluator 实例
    - "pattern_miner"  → PatternMiner 实例
    - "template_generator" → TemplateGenerator 实例
    - "reviewer"       → Reviewer 实例
    - "template_registry"  → TemplateRegistry 实例
    """

    plugin_id = "core.evolution"
    plugin_name = "智能体自我进化系统"
    plugin_version = "1.0.0"
    dependencies = ["core.events"]  # 依赖 EventBus 发布采集事件

    def __init__(self):
        self.ctx: BaizeContext | None = None
        self.config: EvolutionConfig = EvolutionConfig()
        self.store: EvolutionStore | None = None
        self.collector: EvolutionCollector | None = None
        self.failover: FailoverManager | None = None
        self.metrics: MetricsCollector | None = None
        self._param_pool: ParamPool | None = None
        self._experimenter: Experimenter | None = None
        self._evaluator: Evaluator | None = None
        self._pattern_miner: PatternMiner | None = None
        self._template_generator: TemplateGenerator | None = None
        self._reviewer: Reviewer | None = None
        self._template_registry: TemplateRegistry | None = None
        self._evolution_task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None

    # ─── 公共属性（供 query 层安全访问） ──────────────────

    @property
    def experimenter(self) -> Experimenter | None:
        """A/B 实验器实例。"""
        return self._experimenter

    @property
    def pattern_miner(self) -> PatternMiner | None:
        """模式挖掘器实例。"""
        return self._pattern_miner

    @property
    def template_registry(self) -> TemplateRegistry | None:
        """模板注册表实例。"""
        return self._template_registry

    @property
    def reviewer(self) -> Reviewer | None:
        """人工审核器实例。"""
        return self._reviewer

    # ─── 生命周期钩子 ────────────────────────────────────

    async def on_load(self, ctx: BaizeContext, fiber: Fiber):
        """加载阶段：解析配置，注册核心服务。"""
        self.ctx = ctx
        settings = getattr(ctx, "settings", None)

        # 读取配置（兼容旧版无 evolution 配置段）
        if settings and hasattr(settings, "evolution"):
            self.config = settings.evolution
        else:
            self.config = EvolutionConfig()

        # 1. 初始化 Store（双层存储）
        self.store = EvolutionStore(
            db_path=Path(self.config.db_path),
            cold_dir=Path(self.config.archive_dir) if self.config.archive_dir else None,
        )

        # 2. 初始化 Collector（事件订阅 + 批量缓冲）
        self.collector = EvolutionCollector(
            store=self.store,
            fallback_dir=Path(self.config.fallback_dir) if self.config.fallback_dir else None,
        )

        # 3. 初始化 FailoverManager（三级降级）
        self.failover = FailoverManager()
        if self.config.notifier_webhook:
            self.failover.set_notifier(self._webhook_notify)

        # 4. 初始化 Phase 2 组件
        self._param_pool = ParamPool()
        self._experimenter = Experimenter(param_pool=self._param_pool)
        self._evaluator = Evaluator(
            param_pool=self._param_pool,
            experimenter=self._experimenter,
            failover=self.failover,
        )

        # 5. 初始化 Phase 3 组件
        self._pattern_miner = PatternMiner()
        self._template_generator = TemplateGenerator()
        self._reviewer = Reviewer()
        self._template_registry = TemplateRegistry(
            auto_promote_enabled=getattr(self.config, "template_auto_promote", False),
        )

        # 6. 注册服务到 Context（供其他插件/路由注入）
        ctx.provide("evolution", self)
        ctx.provide("evolution_store", self.store)
        ctx.provide("evolution_collector", self.collector)
        ctx.provide("failover_manager", self.failover)
        ctx.provide("param_pool", self._param_pool)
        ctx.provide("experimenter", self._experimenter)
        ctx.provide("evolution_evaluator", self._evaluator)
        ctx.provide("pattern_miner", self._pattern_miner)
        ctx.provide("template_generator", self._template_generator)
        ctx.provide("reviewer", self._reviewer)
        ctx.provide("template_registry", self._template_registry)

        logger.info(f"[{self.plugin_id}] EvolutionPlugin loaded, config={self.config}")

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化阶段：挂载 REST API 路由到插件共享 router。"""
        # 从 Context 获取插件共享 router（由 main.py 在 Fiber 之前创建）
        plugin_router = ctx.inject("http_router", None)
        if plugin_router and isinstance(plugin_router, APIRouter):
            # 注入 store 引用到 query 模块（供 REST 端点使用）
            from .phase1 import query as _query_module
            _query_module.set_store(self.store)
            _query_module.set_evolution_plugin(self)
            plugin_router.include_router(
                evolution_router,
                tags=["Evolution"],
            )
            logger.info(f"[{self.plugin_id}] Evolution API mounted at /api/evolution")
        else:
            logger.warning(
                f"[{self.plugin_id}] http_router not found; "
                "evolution endpoints will not be available. "
                "Ensure main.py provides 'http_router' before plugin init."
            )

        # 初始化降级规则（默认：存储失败 → L1，评估异常 → L2）
        self.failover.create_default_rules()

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        """启动阶段：订阅 EventBus，启动采集循环。"""
        # 初始化存储层（建表、建连接）
        if self.store:
            await self.store.init()
            logger.info(f"[{self.plugin_id}] Store initialized")

        event_bus = ctx.inject("event_bus", None)
        if event_bus:
            # 订阅 5 类核心事件
            event_bus.subscribe("chat.complete", self._on_chat_complete)
            event_bus.subscribe("skill.execute", self._on_skill_execute)
            event_bus.subscribe("user.feedback", self._on_user_feedback)
            event_bus.subscribe("route.decision", self._on_route_decision)
            event_bus.subscribe("system.metric", self._on_system_metric)
            logger.info(f"[{self.plugin_id}] Subscribed 5 event types")

        # 启动采集器后台循环
        await self.collector.start()
        logger.info(f"[{self.plugin_id}] Collector started")

        # 启动 Phase 2 定时评估任务（每小时）
        self._shutdown_event = asyncio.Event()
        self._evolution_task = asyncio.create_task(
            self._evolution_loop(),
            name="evolution_loop",
        )
        logger.info(f"[{self.plugin_id}] Evolution loop started (interval={self.config.eval_interval}s)")

    async def on_stop(self, ctx: BaizeContext, fiber: Fiber):
        """停止阶段：优雅关闭采集器，flush 剩余数据。"""
        if self._shutdown_event:
            self._shutdown_event.set()

        if self._evolution_task and not self._evolution_task.done():
            self._evolution_task.cancel()
            try:
                await self._evolution_task
            except asyncio.CancelledError:
                pass

        if self.collector:
            await self.collector.stop()
            logger.info(f"[{self.plugin_id}] Collector stopped, final flush done")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """销毁阶段：关闭数据库连接。"""
        if self.store:
            await self.store.close()
            logger.info(f"[{self.plugin_id}] Store closed")

    # ─── EventBus 事件处理器 ────────────────────────────

    def _on_chat_complete(self, data: Any):
        """对话完成事件 → 采集对话日志"""
        if self.failover.current_level_int() >= 2:
            return  # L2 及以上禁用采集
        try:
            asyncio.create_task(self.collector._on_chat_complete(data))
        except Exception as e:
            logger.warning(f"[{self.plugin_id}] chat.complete handler error: {e}")

    def _on_skill_execute(self, data: Any):
        """技能执行事件 → 采集技能追踪"""
        if self.failover.current_level_int() >= 2:
            return
        try:
            asyncio.create_task(self.collector._on_skill_execute(data))
        except Exception as e:
            logger.warning(f"[{self.plugin_id}] skill.execute handler error: {e}")

    def _on_user_feedback(self, data: Any):
        """用户反馈事件 → 采集反馈数据"""
        if self.failover.current_level_int() >= 2:
            return
        try:
            asyncio.create_task(self.collector._on_user_feedback(data))
        except Exception as e:
            logger.warning(f"[{self.plugin_id}] user.feedback handler error: {e}")

    def _on_route_decision(self, data: Any):
        """路由决策事件 → 采集路由日志"""
        if self.failover.current_level_int() >= 2:
            return
        try:
            asyncio.create_task(self.collector._on_route_decision(data))
        except Exception as e:
            logger.warning(f"[{self.plugin_id}] route.decision handler error: {e}")

    def _on_system_metric(self, data: Any):
        """系统指标事件 → 采集系统指标"""
        if self.failover.current_level_int() >= 2:
            return
        try:
            asyncio.create_task(self.collector._on_system_metric(data))
        except Exception as e:
            logger.warning(f"[{self.plugin_id}] system.metric handler error: {e}")

    # ─── 后台进化循环 ────────────────────────────────────

    async def _evolution_loop(self):
        """
        后台定时任务：
        1. 评估进行中的 A/B 实验
        2. 触发 PatternMiner（每日一次）
        3. 检查降级自动恢复
        """
        daily_tick = 0
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.config.eval_interval,
                )
                break  # 收到停止信号
            except asyncio.TimeoutError:
                pass

            if self.failover.current_level_int() >= 2:
                logger.debug("Evolution loop skipped (degraded)")
                continue

            try:
                # 1. 评估 A/B 实验
                if self._experimenter and self._evaluator:
                    await self._eval_experiments()

                # 2. 每日 PatternMiner（86400 / eval_interval 次循环 ≈ 每日）
                daily_tick += 1
                if daily_tick >= max(1, 86400 // self.config.eval_interval):
                    daily_tick = 0
                    await self._run_daily_mining()

                # 3. 自动恢复检查
                await self.failover.run_recovery_checks()

            except Exception as e:
                logger.error(f"[{self.plugin_id}] Evolution loop error: {e}", exc_info=True)
                await self.failover.manual_degrade(
                    DegradationLevel.LEVEL_1, f"evolution_loop_error: {e}"
                )

    async def _eval_experiments(self):
        """评估所有进行中的实验，提交显著性结果。"""
        running_ids = self._experimenter.list_experiments(
            status=ExperimentStatus.RUNNING
        )
        for exp_id in running_ids:
            result = self._evaluator.evaluate(exp_id)
            if result and result.is_significant:
                self._evaluator.commit(exp_id, result)
                state = self._experimenter.get_experiment_state(exp_id)
                param_name = state.config.param_name if state else "?"
                logger.info(
                    f"[{self.plugin_id}] Experiment {exp_id} committed: "
                    f"param={param_name}, p={result.p_value:.4f}, "
                    f"d={result.cohens_d:.4f}"
                )

    async def _run_daily_mining(self):
        """每日执行模式挖掘（仅在数据量充足时）。"""
        try:
            if not self.store or not self._pattern_miner:
                return

            # 从 store 查询近 7 天 skill_trace 数据
            from datetime import datetime, timedelta, timezone

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            recent = await self.store.query(
                "skill_trace", start=start, end=end, limit=10000
            )
            if len(recent) < self.config.min_mining_samples:
                logger.debug(
                    f"Mining skipped: {len(recent)} < {self.config.min_mining_samples}"
                )
                return

            # 用 mine_from_dicts 兼容原始 dict 数据
            patterns = self._pattern_miner.mine_from_dicts(recent)
            if not patterns:
                logger.debug("Mining complete: no patterns found")
                return

            # 生成候选模板并提交审核
            candidates = self._template_generator.generate(
                patterns, GenerationConfig()
            )
            for candidate in candidates[: self.config.max_patterns_per_day]:
                self._reviewer.submit(candidate)
                logger.info(
                    f"[{self.plugin_id}] Template candidate submitted: {candidate.id}"
                )

        except Exception as e:
            logger.error(
                f"[{self.plugin_id}] Daily mining error: {e}", exc_info=True
            )

    # ─── 通知回调 ────────────────────────────────────────

    async def _webhook_notify(self, message: str, level: Any) -> None:
        """降级通知 webhook（预留接口）。"""
        webhook_url = self.config.notifier_webhook
        if not webhook_url:
            return
        try:
            import aiohttp
            payload = {"level": str(level), "message": message, "plugin": self.plugin_id}
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status >= 400:
                        logger.warning(f"Webhook notify failed: {resp.status}")
        except Exception as e:
            logger.warning(f"Webhook notify error: {e}")

    # ─── 公开 API（供其他插件调用）───────────────────────

    def get_status(self) -> dict[str, Any]:
        """获取进化系统完整状态（供 dashboard 使用）。"""
        # 构建 experiments 列表（从 ID 获取 state 再序列化）
        exp_list: list[dict[str, Any]] = []
        if self._experimenter:
            for exp_id in self._experimenter.list_experiments():
                state = self._experimenter.get_experiment_state(exp_id)
                if state:
                    exp_list.append({
                        "id": exp_id,
                        "param": state.config.param_name,
                        "status": state.config.status.value,
                        "control_n": len(state.control_metrics),
                        "treatment_n": len(state.treatment_metrics),
                    })

        # store.get_stats() 是 async 方法，这里只返回同步可用的信息
        store_info: dict[str, Any] | None = None
        if self.store:
            store_info = {
                "initialized": self.store._initialized,
                "db_path": str(self.store._db_path),
                "write_count": self.store._write_count,
            }

        return {
            "plugin_version": self.plugin_version,
            "degradation_level": self.failover.current_level.value
            if self.failover
            else "unknown",
            "collector": self.collector.get_stats() if self.collector else None,
            "store": store_info,
            "experiments": exp_list,
            "templates_pending": (
                len(self._reviewer.list_pending()) if self._reviewer else 0
            ),
            "templates_active": (
                len(self._template_registry.list_active())
                if self._template_registry
                else 0
            ),
        }

    async def create_experiment(
        self,
        param_name: str,
        candidate_values: list[Any],
        eval_metric: str,
        min_samples: int = 500,
        max_samples: int = 2000,
    ) -> str:
        """
        手动创建 A/B 实验（管理员调用）。

        Args:
            param_name: 参数名（需已在 ParamPool 中注册）
            candidate_values: 候选值列表
            eval_metric: 评估指标名
            min_samples: 最小样本数
            max_samples: 最大样本数

        Returns:
            experiment_id
        """
        if self.failover.current_level_int() >= 1:
            raise RuntimeError(
                f"Cannot create experiment: degraded to "
                f"{self.failover.current_level.value}"
            )
        return await self._experimenter.create_experiment(
            param_name=param_name,
            candidate_values=candidate_values,
            eval_metric=eval_metric,
            min_samples=min_samples,
            max_samples=max_samples,
        )

    def pause_experiment(self, experiment_id: str):
        """暂停指定实验。"""
        self._experimenter.pause_experiment(experiment_id)

    def resume_experiment(self, experiment_id: str):
        """恢复指定实验。"""
        self._experimenter.resume_experiment(experiment_id)

    def approve_template(self, candidate_id: str, reviewer: str) -> bool:
        """人工审核通过候选模板。"""
        return self._reviewer.approve(candidate_id, reviewer)

    def reject_template(self, candidate_id: str, reviewer: str, reason: str) -> bool:
        """人工审核拒绝候选模板。"""
        return self._reviewer.reject(candidate_id, reviewer, reason)

    def activate_template(self, template_id: str) -> bool:
        """将已审核通过的模板推送到灰度。"""
        candidate = self._reviewer.get_candidate(template_id)
        if not candidate:
            logger.warning(f"[{self.plugin_id}] Candidate not found: {template_id}")
            return False
        # 校验审核状态：仅 APPROVED 模板可注册
        from .shared.models import TemplateStatus
        if candidate.status != TemplateStatus.APPROVED:
            logger.warning(
                f"[{self.plugin_id}] Cannot activate template {template_id}: "
                f"status={candidate.status.value}, requires APPROVED"
            )
            return False
        # 注册到 TemplateRegistry 并启动灰度
        reg_id = self._template_registry.register(candidate)
        return self._template_registry.canary(reg_id)

    def rollback_template(self, template_id: str) -> bool:
        """回滚模板到上一版本。"""
        return self._template_registry.rollback(template_id)
