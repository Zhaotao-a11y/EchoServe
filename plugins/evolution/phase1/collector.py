"""
EchoServe Evolution System — Phase 1: EvolutionCollector

事件采集器。
基于 FiberManager 事件总线，异步订阅所有关键事件。

设计约束：
- 异步写入，不阻塞主流程
- 批量缓冲，每 50 条或每 5 秒 flush
- 采集失败降级到 JSONL，不丢数据
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..shared.metrics import MetricName, MetricsCollector
from ..shared.models import (
    ChatLogRecord,
    FeedbackRecord,
    RouteLogRecord,
    SkillTraceRecord,
    SystemMetricRecord,
)

if TYPE_CHECKING:
    from .store import EvolutionStore

logger = logging.getLogger("echoserve.evolution.collector")


class EvolutionCollector:
    """
    事件采集器。

    基于 EventBus 异步订阅，将事件转换为结构化记录，
    批量写入 EvolutionStore。
    """

    FLUSH_INTERVAL_SECONDS = 5.0
    BATCH_SIZE = 50
    MAX_BACKLOG = 1000

    def __init__(
        self,
        store: EvolutionStore | None = None,
        metrics: MetricsCollector | None = None,
        fallback_dir: Path | None = None,
    ):
        self._store = store
        self._metrics = metrics or MetricsCollector()
        self._fallback_dir = fallback_dir or Path("data/evolution/fallback")
        self._backlog: list[dict[str, Any]] = []
        self._flush_task: asyncio.Task | None = None
        self._running = False
        self._total_received = 0
        self._total_written = 0
        self._write_failures = 0
        self._fallback_files: list[Path] = []

    def attach_to_bus(self, bus: Any) -> None:
        """注册事件监听器到事件总线。"""
        bus.subscribe("chat.complete", self._on_chat_complete)
        bus.subscribe("skill.execute", self._on_skill_execute)
        bus.subscribe("user.feedback", self._on_user_feedback)
        bus.subscribe("route.decision", self._on_route_decision)
        bus.subscribe("system.metric", self._on_system_metric)
        bus.subscribe_wildcard(self._on_any_event)
        logger.info("[EvolutionCollector] Subscribed to event bus")

    async def start(self) -> None:
        """启动后台 flush 任务。"""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())

        # 注册信号处理，退出前强制 flush
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._final_flush()))
        except (NotImplementedError, ValueError):
            # Windows 或某些环境不支持 add_signal_handler
            pass

        logger.info("[EvolutionCollector] Started")

    async def stop(self) -> None:
        """停止采集器，强制 flush 剩余数据。"""
        if not self._running:
            return
        self._running = False

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        await self._final_flush()
        logger.info(
            f"[EvolutionCollector] Stopped. Total: {self._total_received}, "
            f"Written: {self._total_written}, Failures: {self._write_failures}"
        )

    async def _on_chat_complete(self, event: dict[str, Any]) -> None:
        """对话完成事件：记录用户问题、AI回复、引用文档、耗时。"""
        self._total_received += 1
        record = ChatLogRecord(
            session_id=event.get("session_id", ""),
            query=event.get("query", ""),
            reply=event.get("reply", ""),
            retrieved_docs=event.get("retrieved_docs", []),
            latency_ms=event.get("latency_ms", 0),
            timestamp=event.get("timestamp") or self._now(),
            feedback_type=event.get("feedback_type"),
            metadata=event.get("metadata", {}),
        )
        self._backlog.append({"table": "chat_log", "data": record.to_dict()})
        await self._try_flush()

    async def _on_skill_execute(self, event: dict[str, Any]) -> None:
        """技能执行事件：记录技能名、输入参数、输出结果、是否成功。"""
        self._total_received += 1
        record = SkillTraceRecord(
            trace_id=event.get("trace_id", ""),
            session_id=event.get("session_id", ""),
            skill_id=event.get("skill_id", ""),
            skill_sequence=event.get("skill_sequence", []),
            input_data=event.get("input", {}),
            output_data=event.get("output", {}),
            success=event.get("success", True),
            error=event.get("error"),
            latency_ms=event.get("latency_ms", 0),
            timestamp=event.get("timestamp") or self._now(),
            retry_count=event.get("retry_count", 0),
            user_feedback=event.get("user_feedback"),
        )
        self._backlog.append({"table": "skill_trace", "data": record.to_dict()})
        await self._try_flush()

    async def _on_user_feedback(self, event: dict[str, Any]) -> None:
        """用户反馈事件：记录点赞/踩、反馈文本。"""
        self._total_received += 1
        fb_type = event.get("feedback_type", "like")
        from ..shared.models import FeedbackType

        record = FeedbackRecord(
            session_id=event.get("session_id", ""),
            feedback_type=FeedbackType(fb_type),
            comment=event.get("comment"),
            timestamp=event.get("timestamp") or self._now(),
            source=event.get("source", "manual"),
        )
        self._backlog.append({"table": "feedback", "data": record.to_dict()})
        await self._try_flush()

    async def _on_route_decision(self, event: dict[str, Any]) -> None:
        """路由决策事件：记录 TopK、权重、命中结果。"""
        self._total_received += 1
        record = RouteLogRecord(
            query=event.get("query", ""),
            top_k=event.get("top_k", 5),
            bm25_weight=event.get("bm25_weight", 0.5),
            vector_weight=event.get("vector_weight", 0.5),
            retrieved_count=event.get("retrieved_count", 0),
            timestamp=event.get("timestamp") or self._now(),
            rerank_threshold=event.get("rerank_threshold", 0.1),
            final_doc_ids=event.get("final_doc_ids", []),
        )
        self._backlog.append({"table": "route_log", "data": record.to_dict()})
        await self._try_flush()

    async def _on_system_metric(self, event: dict[str, Any]) -> None:
        """系统指标事件。"""
        self._total_received += 1
        record = SystemMetricRecord(
            cpu_percent=event.get("cpu_percent", 0.0),
            memory_percent=event.get("memory_percent", 0.0),
            gpu_util=event.get("gpu_util", 0.0),
            gpu_mem_percent=event.get("gpu_mem_percent", 0.0),
            active_sessions=event.get("active_sessions", 0),
            qps=event.get("qps", 0.0),
            timestamp=event.get("timestamp") or self._now(),
        )
        self._backlog.append({"table": "system_metric", "data": record.to_dict()})
        await self._try_flush()

    async def _on_any_event(self, event_name: str, event_data: Any) -> None:
        """通配符处理器：记录未知事件类型（调试用）。"""
        # 仅记录未被专门处理的事件
        known_prefixes = ("chat.", "skill.", "user.", "route.", "system.")
        if any(event_name.startswith(p) for p in known_prefixes):
            return
        logger.debug(f"[EvolutionCollector] Unknown event: {event_name}")

    async def _try_flush(self) -> None:
        """尝试触发 flush（达到批次或积压上限时）。"""
        if len(self._backlog) >= self.BATCH_SIZE:
            await self._flush_now()
        elif len(self._backlog) >= self.MAX_BACKLOG:
            logger.warning(
                f"[EvolutionCollector] Backlog full ({self.MAX_BACKLOG}), "
                f"forcing flush"
            )
            await self._flush_now()

    async def _flush_loop(self) -> None:
        """定时 flush 循环。"""
        while self._running:
            try:
                await asyncio.sleep(self.FLUSH_INTERVAL_SECONDS)
                if self._backlog:
                    await self._flush_now()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[EvolutionCollector] Flush loop error: {e}")

    async def _flush_now(self) -> None:
        """立即执行 flush。"""
        if not self._backlog:
            return

        batch = self._backlog[:]
        self._backlog.clear()

        if self._store is None:
            # 无存储时降级到 fallback
            await self._write_fallback(batch)
            return

        try:
            with self._metrics.timer("evolution.flush_duration_ms"):
                await self._store.insert_batch(batch)
            self._total_written += len(batch)
            self._metrics.record(
                MetricName.LOG_COMPLETENESS,
                self._total_written / max(self._total_received, 1) * 100,
            )
        except Exception as e:
            self._write_failures += 1
            self._metrics.record(
                MetricName.STORE_WRITE_FAILURE,
                self._write_failures,
            )
            logger.error(f"[EvolutionCollector] Store write failed: {e}, fallback to JSONL")
            # 降级：写 JSONL 文件
            await self._write_fallback(batch)

    async def _write_fallback(self, batch: list[dict[str, Any]]) -> None:
        """降级写入 JSONL 文件。"""
        self._fallback_dir.mkdir(parents=True, exist_ok=True)
        filename = f"evolution_fallback_{int(time.time())}.jsonl"
        filepath = self._fallback_dir / filename

        try:
            with open(filepath, "a", encoding="utf-8") as f:
                for record in batch:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fallback_files.append(filepath)
            self._total_written += len(batch)
            logger.info(f"[EvolutionCollector] Fallback written: {filepath} ({len(batch)} records)")
        except Exception as e:
            logger.error(f"[EvolutionCollector] Fallback write failed: {e}")

    async def _final_flush(self) -> None:
        """进程退出前的最终 flush。

        使用 asyncio.shield 保护 flush 和 fallback 写入不被取消信号打断，
        确保已采集数据不因进程关闭而丢失。
        """
        logger.info(f"[EvolutionCollector] Final flush: {len(self._backlog)} records")
        if not self._backlog:
            return
        batch = list(self._backlog)
        self._backlog.clear()
        try:
            await asyncio.shield(self._store.insert_batch(batch))
            self._total_written += len(batch)
        except asyncio.CancelledError:
            logger.warning(
                "[EvolutionCollector] Final flush cancelled, attempting fallback"
            )
            try:
                await asyncio.shield(self._write_fallback(batch))
            except Exception:
                logger.error("[EvolutionCollector] Fallback also failed during cancel")
            raise
        except Exception as e:
            logger.error(f"[EvolutionCollector] Store write failed: {e}, fallback to JSONL")
            try:
                await asyncio.shield(self._write_fallback(batch))
            except Exception as e2:
                logger.error(f"[EvolutionCollector] Fallback write failed: {e2}")

    def get_stats(self) -> dict[str, Any]:
        """返回采集统计。"""
        return {
            "total_received": self._total_received,
            "total_written": self._total_written,
            "write_failures": self._write_failures,
            "backlog_size": len(self._backlog),
            "fallback_files": len(self._fallback_files),
            "completeness_rate": (
                self._total_written / max(self._total_received, 1) * 100
            ),
        }

    @staticmethod
    def _now():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)
