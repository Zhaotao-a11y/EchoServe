"""
EchoServe V0.1.0 — 模型进化引擎插件入口

集成 TrainingDataBuilder + LoRATrainer + DPOTrainer + EvaluationPipeline + ABTester
通过 BaizeContext 注册为 "evolver" 服务。

V0.2.0 变更：
  - LoRATrainer 接入 HuggingFace PEFT 真实训练（降级保留模拟）
  - DPOTrainer 接入 HuggingFace TRL 真实训练（降级保留模拟）
  - EvaluationPipeline 支持 LLM-as-Judge 评分（降级保留关键词匹配）
"""
from __future__ import annotations

import logging
import time
import asyncio
from pathlib import Path
from typing import Any, Callable

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .data_builder import TrainingDataBuilder
from .trainer import LoRATrainer
from .evaluator import EvaluationPipeline, ABTester
from .dpo_trainer import PreferenceStore, DPOTrainer

logger = logging.getLogger("echoserve.evolve")


class ModelEvolvePlugin(BaizePlugin):
    """
    模型进化引擎插件（P1 完整版）。

    注册服务：
    - "evolver" → ModelEvolvePlugin 实例
    - "evaluator" → EvaluationPipeline 实例
    - "ab_tester" → ABTester 实例
    """

    plugin_id = "core.evolve"
    plugin_name = "模型进化引擎"
    plugin_version = "0.2.0"
    dependencies = ["core.model", "core.knowledge", "core.llm"]

    def __init__(self):
        self.ctx: BaizeContext | None = None
        self.data_builder: TrainingDataBuilder | None = None
        self.trainer: LoRATrainer | None = None
        self.evaluator: EvaluationPipeline | None = None
        self.ab_tester: ABTester | None = None
        self.preference_store: PreferenceStore | None = None
        self.adapters: dict[str, dict[str, Any]] = {}
        self.training_status: str = "idle"  # idle/running/completed/failed
        self.last_result: dict[str, Any] | None = None
        self.last_report: dict[str, Any] | None = None
        self.weekly_job_id: str | None = None
        self.last_promote_result: dict[str, Any] | None = None

    # ─── 生命周期 ──────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化进化引擎"""
        self.ctx = ctx

        # 创建评估器
        self.evaluator = EvaluationPipeline(
            test_set_path="./data/training/test_set.jsonl",
            report_dir="./data/training/reports",
        )

        # 创建 A/B 测试器
        self.ab_tester = ABTester(evaluator=self.evaluator)

        # 创建偏好数据存储（P1-A: 自动触发 DPO）
        import os
        prefs_path = os.path.join(
            str(ctx.root_dir), "data", "training", "preferences.jsonl"
        )
        self.preference_store = PreferenceStore(
            store_path=prefs_path,
            auto_trigger_threshold=50,
            on_auto_trigger=self._on_dpo_auto_trigger,
        )

        # 注册服务
        ctx.provide("evolver", self)
        ctx.provide("evaluator", self.evaluator)
        ctx.provide("ab_tester", self.ab_tester)
        ctx.provide("preference_store", self.preference_store)

        logger.info(f"[{self.plugin_id}] 模型进化引擎初始化完成")

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        """启动后注册定时任务"""
        scheduler = ctx.inject("scheduler", None)
        if scheduler and hasattr(scheduler, "add_job"):
            # 每周日凌晨 2 点运行评估
            job = scheduler.add_job(
                self._weekly_evaluation,
                "cron",
                day_of_week=0,
                hour=2,
                minute=0,
                id="weekly_eval",
            )
            self.weekly_job_id = job.id if hasattr(job, "id") else "weekly_eval"
            logger.info(f"[{self.plugin_id}] 已注册每周评估任务 (周日 02:00)")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """清理"""
        if self.weekly_job_id:
            scheduler = ctx.inject("scheduler", None)
            if scheduler and hasattr(scheduler, "remove_job"):
                scheduler.remove_job(self.weekly_job_id)
        logger.info(f"[{self.plugin_id}] 已销毁")

    # ─── 进化策略 ──────────────────────────────────────

    def check_and_evolve(self) -> dict[str, Any]:
        """
        检查知识库规模，返回进化建议。

        Returns:
            {
                "stage": 1|2|3,
                "kb_size": int,
                "recommendation": str,
                "can_train": bool,
                "train_type": "lora"|"full"|None,
            }
        """
        kb = self.ctx.inject("knowledge_base", None)
        kb_size = kb.count_documents() if kb else 0

        if kb_size < 2000:
            return {
                "stage": 1,
                "kb_size": kb_size,
                "recommendation": "纯 RAG 模式，零训练成本。继续积累知识库数据。",
                "can_train": False,
                "train_type": None,
            }
        elif kb_size < 5000:
            return {
                "stage": 2,
                "kb_size": kb_size,
                "recommendation": "建议启动离线 LoRA 微调（r=8, target=q_proj+v_proj），"
                                   "提升高频问题 3-5% 准确率。",
                "can_train": True,
                "train_type": "lora",
            }
        else:
            return {
                "stage": 3,
                "kb_size": kb_size,
                "recommendation": "建议启动全参数微调/蒸馏 + DPO 风格对齐。"
                                   "需要专用 GPU 节点（推荐 A100 40GB+）。",
                "can_train": True,
                "train_type": "full",
            }

    # ─── 训练触发（管理员手动调用）──────────────────

    def trigger_offline_lora(
        self,
        train_data_path: str = "./data/training/train.jsonl",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """
        触发离线 LoRA 训练。

        训练在独立进程/容器中执行，不阻塞推理服务。
        """
        if self.training_status == "running":
            return {"status": "rejected", "reason": "已有训练任务在运行"}

        self.training_status = "running"
        logger.info(f"[{self.plugin_id}] 触发离线 LoRA 训练")

        try:
            # 1. 确保训练数据存在
            if not self._ensure_training_data(train_data_path):
                self.training_status = "failed"
                return {"status": "failed", "reason": "无法准备训练数据"}

            # 2. 创建训练器
            if output_dir is None:
                output_dir = f"./models/adapters/lora-{time.strftime('%Y%m%d-%H%M')}"

            kb = self.ctx.inject("knowledge_base")
            llm = self.ctx.inject("llm")

            self.data_builder = TrainingDataBuilder(
                knowledge_base=kb,
                llm_client=llm,
                output_path=train_data_path,
            )

            self.trainer = LoRATrainer(
                base_model=self.ctx.settings.model.path,
                train_data=train_data_path,
                output_dir=output_dir,
            )

            # 3. 执行训练（同步；生产环境应放到独立进程）
            result = self.trainer.train()

            # 4. 更新状态
            if result.get("status") == "success":
                self.training_status = "completed"
                adapter_name = Path(output_dir).name
                self.adapters[adapter_name] = {
                    "path": output_dir,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "eval_loss": result.get("best_eval_loss"),
                    "train_loss": result.get("train_loss"),
                    "type": "lora",
                }
                self.last_result = result

                # 通知管理员
                self._notify_admin(
                    f"LoRA 训练完成: {adapter_name}, "
                    f"eval_loss={result.get('best_eval_loss'):.4f}"
                )

                # 记录审计
                audit = self.ctx.inject("audit_logger")
                if audit:
                    audit.log_sync(
                        action="lora_training_completed",
                        user_id="system",
                        query=f"LoRA: {adapter_name}",
                        response_summary=f"eval_loss={result.get('best_eval_loss')}",
                        sources=[],
                        latency_ms=int(result.get("training_time_minutes", 0) * 60000),
                        channel="system",
                    )
            else:
                self.training_status = "failed"
                self.last_result = result

            return result

        except Exception as e:
            self.training_status = "failed"
            logger.error(f"[{self.plugin_id}] 训练异常: {e}")
            return {"status": "failed", "reason": str(e)}

    def trigger_offline_full_finetune(
        self,
        train_data_path: str = "./data/training/train_full.jsonl",
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """
        触发全参数微调（阶段三）。

        需要多卡 GPU 或 A100 级别的算力。
        实际训练使用 DeepSpeed ZeRO-3 或 FSDP。
        """
        if self.training_status == "running":
            return {"status": "rejected", "reason": "已有训练任务在运行"}

        self.training_status = "running"
        logger.info(f"[{self.plugin_id}] 触发全参数微调（离线）")

        # 检查是否有足够的训练数据
        import os
        if not os.path.exists(train_data_path):
            self.training_status = "failed"
            return {
                "status": "failed",
                "reason": f"训练数据不存在: {train_data_path}，需要 ≥10000 条数据",
            }

        if output_dir is None:
            output_dir = f"./models/adapters/full-{time.strftime('%Y%m%d-%H%M')}"

        # 模拟训练（实际环境使用 DeepSpeed）
        result = {
            "status": "simulated",
            "message": "全参数微调需要 GPU 集群环境（A100 x4 推荐）",
            "command": (
                f"deepspeed --num_gpus=4 train_full.py "
                f"--model {self.ctx.settings.model.path} "
                f"--data {train_data_path} "
                f"--output {output_dir}"
            ),
            "output_dir": output_dir,
            "note": "请在生产 GPU 环境中执行上述命令",
        }

        self.training_status = "completed"
        self.last_result = result
        return result

    # ─── 评估 ──────────────────────────────────────────

    def run_evaluation(self, model_predict_fn: Callable[[str], str]) -> dict[str, Any]:
        """
        手动触发评估。
        """
        if not self.evaluator:
            return {"error": "评估器未初始化"}
        report = self.evaluator.evaluate(model_predict_fn)
        self.last_report = report
        return report

    def run_ab_test(
        self,
        model_a_fn: Callable[[str], str],
        model_b_fn: Callable[[str], str],
        label_a: str = "RAG-only",
        label_b: str = "RAG+LoRA",
    ) -> dict[str, Any]:
        """
        手动触发 A/B 测试。
        """
        if not self.ab_tester:
            return {"error": "A/B 测试器未初始化"}
        return self.ab_tester.compare(
            model_a_fn=model_a_fn,
            model_b_fn=model_b_fn,
            label_a=label_a,
            label_b=label_b,
        )

    # ─── 查询接口 ──────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """获取进化引擎完整状态"""
        return {
            "training_status": self.training_status,
            "last_result": self.last_result,
            "last_report": self.last_report,
            "last_promote_result": self.last_promote_result,
            "adapters": [
                {"name": name, **info}
                for name, info in self.adapters.items()
            ],
            "evolution": self.check_and_evolve(),
            "evaluation_history": self.evaluator.get_history() if self.evaluator else [],
            "preference_stats": (
                self.preference_store.get_stats() if self.preference_store else None
            ),
        }

    def list_adapters(self) -> list[dict[str, Any]]:
        """列出所有已训练的 adapter"""
        return [
            {"name": name, **info}
            for name, info in self.adapters.items()
        ]

    # ─── 内部方法 ──────────────────────────────────────

    def _ensure_training_data(self, train_data_path: str) -> bool:
        """确保训练数据存在且有效"""
        import os
        if os.path.exists(train_data_path):
            # 验证数据
            if self.data_builder:
                issues = self.data_builder.validate(train_data_path)
                return issues.get("valid", 0) > 0
            return True

        # 尝试构建
        if self.data_builder is None:
            kb = self.ctx.inject("knowledge_base")
            llm = self.ctx.inject("llm")
            self.data_builder = TrainingDataBuilder(
                knowledge_base=kb,
                llm_client=llm,
                output_path=train_data_path,
            )

        try:
            self.data_builder.build()
            return True
        except Exception as e:
            logger.error(f"  构建训练数据失败: {e}")
            return False

    async def _weekly_evaluation(self):
        """每周评估任务"""
        logger.info(f"[{self.plugin_id}] 开始每周评估...")

        # 获取当前推理服务的预测函数
        chat = self.ctx.inject("chat_manager")
        if not chat:
            logger.warning(f"  无法获取 chat_manager，跳过评估")
            return

        # 获取当前运行中的事件循环，供线程内回调使用
        import asyncio

        loop = asyncio.get_running_loop()

        def predict(question: str) -> str:
            """同步预测函数（从线程池中安全调用异步 chat）"""
            future = asyncio.run_coroutine_threadsafe(
                chat.chat(
                    session_id=f"eval_{int(time.time())}",
                    user_message=question,
                    use_rag=True,
                ),
                loop,
            )
            result = future.result(timeout=300)  # 5 分钟超时
            return result.get("reply", "")

        # 在线程池中运行同步的 weekly_run，避免阻塞事件循环
        report = await loop.run_in_executor(None, self.evaluator.weekly_run, predict)
        self.last_report = report

        # 通知管理员
        notification = report.get("notification", "")
        self._notify_admin(f"每周评估: {notification}")

        # 记录审计
        audit = self.ctx.inject("audit_logger")
        if audit:
            audit.log_sync(
                action="weekly_evaluation",
                user_id="system",
                query="weekly_eval",
                response_summary=notification,
                sources=[],
                latency_ms=0,
                channel="system",
            )

    # ─── P1-A/P1-B: 自动 DPO 触发 + 评估 + promote ────

    def _on_dpo_auto_trigger(self, trigger_info: dict[str, Any]):
        """
        PreferenceStore 自动触发回调（P1-A → P1-B 串联）。

        流程:
          1. PreferenceStore 偏好数据达阈值 → 自动构建 DPO 数据集 (P1-A, 已在 PreferenceStore 内完成)
          2. 本回调：执行 DPO 训练
          3. 训练成功后：A/B 评估候选模型 vs 当前模型
          4. 评估胜出：自动 promote 新 adapter (P1-B)

        Args:
            trigger_info: PreferenceStore 传来的触发信息
        """
        logger.info(f"[{self.plugin_id}] DPO 自动触发回调启动")
        logger.info(f"  触发信息: total={trigger_info.get('total_feedback')}, "
                     f"dpo_pairs={trigger_info.get('dpo_dataset', {}).get('count', 0)}")

        if self.training_status == "running":
            logger.warning("  已有训练任务在运行，跳过本次自动触发")
            return

        # 1. 执行 DPO 训练
        dpo_dataset_path = trigger_info.get("dpo_dataset", {}).get("output_path", "")
        if not dpo_dataset_path:
            logger.error("  DPO 数据集路径为空，无法训练")
            return

        self.training_status = "running"
        try:
            import os
            output_dir = os.path.join(
                str(self.ctx.root_dir),
                "models", "adapters",
                f"dpo-auto-{time.strftime('%Y%m%d-%H%M%S')}",
            )

            base_model_path = str(self.ctx.settings.model.path)
            trainer = DPOTrainer(
                base_model=base_model_path,
                dpo_data=dpo_dataset_path,
                output_dir=output_dir,
            )

            train_result = trainer.train()

            if train_result.get("status") not in ("success", "simulated"):
                self.training_status = "failed"
                logger.error(f"  DPO 训练失败: {train_result.get('reason', 'unknown')}")
                self._notify_admin(f"DPO 自动训练失败: {train_result.get('reason', '')}")
                return

            self.training_status = "completed"
            adapter_name = os.path.basename(output_dir)
            self.adapters[adapter_name] = {
                "path": output_dir,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "dpo",
                "train_loss": train_result.get("train_loss"),
                "eval_loss": train_result.get("eval_loss"),
                "pair_count": train_result.get("pair_count"),
                "auto_triggered": True,
            }
            self.last_result = train_result

            logger.info(f"  DPO 训练完成: {adapter_name}")
            self._notify_admin(
                f"DPO 自动训练完成: {adapter_name}, "
                f"train_loss={train_result.get('train_loss')}, "
                f"pairs={train_result.get('pair_count')}"
            )

            # 记录审计
            audit = self.ctx.inject("audit_logger")
            if audit:
                audit.log_sync(
                    action="dpo_auto_training_completed",
                    user_id="system",
                    query=f"DPO: {adapter_name}",
                    response_summary=f"train_loss={train_result.get('train_loss')}, pairs={train_result.get('pair_count')}",
                    sources=[],
                    latency_ms=int(train_result.get("training_time_minutes", 0) * 60000),
                    channel="system",
                )

            # 2. 评估并 promote (P1-B)
            self._evaluate_and_promote(adapter_name, output_dir)

        except Exception as e:
            self.training_status = "failed"
            logger.error(f"[{self.plugin_id}] DPO 自动触发异常: {e}", exc_info=True)
            self._notify_admin(f"DPO 自动触发异常: {e}")

    def _evaluate_and_promote(self, adapter_name: str, adapter_path: str):
        """
        P1-B: 评估新 adapter 并在胜出时自动 promote。

        使用 A/B 测试对比当前模型 vs 候选模型（加载新 adapter）。
        若候选准确率提升 >= threshold 且不低于历史最佳，自动切换。
        """
        logger.info(f"[{self.plugin_id}] 开始评估候选 adapter: {adapter_name}")

        chat = self.ctx.inject("chat_manager")
        model_manager = self.ctx.inject("model_manager")

        if not chat:
            logger.warning("  无法获取 chat_manager，跳过评估")
            return

        if not model_manager:
            logger.warning("  无法获取 model_manager，跳过 promote")
            return

        import asyncio
        import threading

        try:
            # 检测是否在运行中的事件循环内
            asyncio.get_running_loop()

            # 在 async 上下文中——需要在线程中运行，避免嵌套事件循环
            result_holder: dict[str, Any] = {}

            def _run_eval():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result = self._run_eval_async(
                        chat, model_manager, adapter_name, adapter_path
                    )
                    result_holder["result"] = new_loop.run_until_complete(result)
                except Exception as e:
                    result_holder["error"] = e
                finally:
                    new_loop.close()

            t = threading.Thread(target=_run_eval)
            t.start()
            t.join(timeout=600)  # 10 分钟超时

            if "error" in result_holder:
                raise result_holder["error"]
            promote_result = result_holder.get("result")

        except RuntimeError:
            # 没有运行中的事件循环，可以安全地用 asyncio.run()
            promote_result = asyncio.run(
                self._run_eval_async(chat, model_manager, adapter_name, adapter_path)
            )

        if promote_result:
            self.last_promote_result = promote_result
            promoted = promote_result.get("promoted", False)
            reason = promote_result.get("promote_reason", "")

            if promoted:
                self._notify_admin(f"模型自动 promote: {adapter_name}\n原因: {reason}")
                audit = self.ctx.inject("audit_logger")
                if audit:
                    audit.log_sync(
                        action="model_auto_promoted",
                        user_id="system",
                        query=f"promote: {adapter_name}",
                        response_summary=reason,
                        sources=[],
                        latency_ms=0,
                        channel="system",
                    )
            else:
                logger.info(f"  未 promote: {reason}")

    async def _run_eval_async(
        self, chat, model_manager, adapter_name: str, adapter_path: str
    ) -> dict[str, Any]:
        """异步执行评估和 promote 逻辑。

        使用 run_coroutine_threadsafe 在主事件循环上调度 async 调用，
        通过 to_thread 运行同步评估器，避免嵌套事件循环。
        """

        import time as _time

        # 获取当前运行的事件循环，供同步回调使用
        main_loop = asyncio.get_running_loop()

        def current_predict(question: str) -> str:
            """当前模型预测——通过 run_coroutine_threadsafe 调度"""
            try:
                future = asyncio.run_coroutine_threadsafe(
                    chat.chat(
                        session_id=f"eval_cur_{int(_time.time())}",
                        user_message=question,
                        use_rag=True,
                    ),
                    main_loop,
                )
                result = future.result(timeout=60)
                return result.get("reply", "")
            except Exception as e:
                logger.warning(f"  current_predict failed: {e}")
                return ""

        def candidate_predict(question: str) -> str:
            """候选模型预测（使用新 adapter）"""
            try:
                messages = [{"role": "user", "content": question}]
                future = asyncio.run_coroutine_threadsafe(
                    model_manager.chat(
                        messages=messages,
                        lora_name=adapter_name,
                    ),
                    main_loop,
                )
                reply = future.result(timeout=60)
                return reply or ""
            except Exception as e:
                logger.warning(f"  candidate_predict failed: {e}")
                return ""

        def promote_fn(name: str, ab_result: dict[str, Any]) -> bool:
            """执行模型切换"""
            try:
                # 找到基础模型 ID
                models = model_manager.list_models()
                base_model_id = None
                for m in models:
                    if m.get("type") == "base":
                        base_model_id = m["id"]
                        break

                if not base_model_id:
                    logger.error("  未找到基础模型，无法 promote")
                    return False

                future = asyncio.run_coroutine_threadsafe(
                    model_manager.switch_model(base_model_id, use_lora=name),
                    main_loop,
                )
                switch_result = future.result(timeout=30)
                return switch_result.get("status") == "success"
            except Exception as e:
                logger.error(f"  promote 失败: {e}")
                return False

        # 在线程池中运行同步评估器，避免阻塞事件循环
        result = await asyncio.to_thread(
            self.evaluator.evaluate_and_promote,
            current_fn=current_predict,
            candidate_fn=candidate_predict,
            adapter_name=adapter_name,
            promote_fn=promote_fn,
            label_a="current",
            label_b=f"candidate({adapter_name})",
        )

        return result

    def _notify_admin(self, message: str):
        """通知管理员（写入日志 + 审计）"""
        logger.info(f"[{self.plugin_id}] 📧 管理员通知: {message}")
        # 实际部署可接入邮件/企业微信通知
        # 当前仅记录到日志
