"""
EchoServe P1 — 监控插件

注册服务：
- "metrics" → MetricsCollector 实例
- "monitoring" → MonitoringPlugin 实例

提供：
- /metrics 端点（Prometheus 格式）
- 指标自动采集（GPU/系统/业务）
- 管理后台仪表盘数据 API
"""
from __future__ import annotations

import logging
import time
import asyncio
from typing import Optional, Dict, Any

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .metrics import MetricsCollector

logger = logging.getLogger("echoseve.monitoring")


class MonitoringPlugin(BaizePlugin):
    """
    监控插件。

    职责：
    - 创建 MetricsCollector 并注册到 Context
    - 启动后台采集任务（GPU/系统指标）
    - 提供指标查询接口
    - 暴露 /metrics 端点数据
    """

    plugin_id = "core.monitoring"
    plugin_name = "监控插件"
    plugin_version = "0.1.0"
    dependencies = ["core.config"]

    def __init__(self):
        self.ctx: Optional[BaizeContext] = None
        self.collector: Optional[MetricsCollector] = None
        self._collect_task: Optional[asyncio.Task] = None
        self._collect_interval: int = 15  # 每 15 秒采集一次
        self._started_at: float = 0.0

    # ─── 生命周期 ──────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化监控"""
        self.ctx = ctx
        self._started_at = time.time()

        # 创建指标收集器
        namespace = ctx.settings.api.host.replace(".", "_")
        self.collector = MetricsCollector(namespace="echoseve")

        # 注册服务
        ctx.provide("metrics", self.collector)
        ctx.provide("monitoring", self)

        logger.info(f"[{self.plugin_id}] 监控插件初始化完成")

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        """启动后台采集任务"""
        self._fiber = fiber
        task = asyncio.create_task(self._collect_loop())
        fiber.add_task(task)
        self._collect_task = task
        logger.info(f"[{self.plugin_id}] 后台采集已启动 (间隔 {self._collect_interval}s)")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """停止采集"""
        if self._collect_task:
            self._collect_task.cancel()
        logger.info(f"[{self.plugin_id}] 已停止")

    # ─── 后台采集循环 ──────────────────────────────

    async def _collect_loop(self):
        """周期性采集 GPU 和系统指标"""
        while True:
            try:
                await asyncio.sleep(self._collect_interval)

                if not self.collector:
                    continue

                # GPU 指标
                gpu_ok = self.collector.collect_gpu_metrics()
                if not gpu_ok:
                    # 尝试 nvidia-smi 命令
                    await self._collect_gpu_cli()

                # 系统指标
                self.collector.collect_system_metrics()

                # 业务指标（从其他插件获取）
                await self._collect_business_metrics()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.plugin_id}] 采集异常: {e}")

    async def _collect_gpu_cli(self):
        """通过 nvidia-smi CLI 采集 GPU 指标"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            line = stdout.decode().strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3:
                self.collector.update_gpu(
                    float(parts[0]),
                    float(parts[1]),
                    float(parts[2]),
                )
        except Exception as e:
            logger.debug(f"  nvidia-smi 采集失败: {e}")

    async def _collect_business_metrics(self):
        """采集业务指标"""
        if not self.collector:
            return

        # 会话数
        chat = self.ctx.inject("chat_manager")
        if chat:
            sessions = await chat.list_sessions()
            self.collector.update_sessions(len(sessions))

        # 知识库
        kb = self.ctx.inject("knowledge_base")
        if kb:
            try:
                doc_count = kb.count_documents()
                chunk_count = getattr(kb, "count_chunks", lambda: 0)()
                self.collector.update_knowledge(doc_count, chunk_count)
            except Exception as e:
                logger.debug(f"Failed to get KB stats: {e}")

        # 插件状态
        for plugin_id, plugin in self.ctx.list_plugins().items():
            status = getattr(plugin, "status", "unknown")
            self.collector.update_plugin_status(plugin_id, status)

        # 训练状态
        evolver = self.ctx.inject("evolver")
        if evolver:
            training_status = getattr(evolver, "training_status", "idle")
            self.collector.update_training(training_status)

    # ─── 对外接口 ──────────────────────────────────

    def get_prometheus_metrics(self) -> str:
        """返回 Prometheus 格式的指标文本"""
        if not self.collector:
            return "# No metrics collector available\n"
        return self.collector.export_prometheus()

    def get_snapshot(self) -> Dict[str, Any]:
        """返回 JSON 格式的当前指标快照"""
        if not self.collector:
            return {"error": "collector not initialized"}

        snap = self.collector.snapshot()

        # 补充运行时信息
        snap["started_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(self._started_at)
        )
        snap["uptime_seconds"] = round(time.time() - self._started_at, 1)

        return snap

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        返回管理后台仪表盘所需的数据。

        包含：
        - 系统指标（GPU/内存/CPU）
        - 业务指标（会话/知识库/请求量）
        - 插件状态
        - 训练状态
        """
        snap = self.get_snapshot()

        # 提取关键指标
        gauges = snap.get("gauges", {})
        counters = snap.get("counters", {})

        # 计算请求率（最近采集周期）
        req_counter = counters.get("echoseve_requests_total", {})
        total_requests = sum(req_counter.values()) if req_counter else 0

        return {
            "uptime_seconds": snap.get("uptime_seconds", 0),
            "system": {
                "gpu_utilization": self._get_gauge(gauges, "echoseve_gpu_utilization"),
                "gpu_memory_used_mb": self._get_gauge(gauges, "echoseve_gpu_memory_used_mb"),
                "gpu_memory_total_mb": self._get_gauge(gauges, "echoseve_gpu_memory_total_mb"),
                "memory_percent": self._get_gauge(gauges, "echoseve_system_memory_percent"),
                "cpu_percent": self._get_gauge(gauges, "echoseve_system_cpu_percent"),
            },
            "business": {
                "active_sessions": int(self._get_gauge(gauges, "echoseve_chat_sessions_active")),
                "total_documents": int(self._get_gauge(gauges, "echoseve_knowledge_docs_total")),
                "total_chunks": int(self._get_gauge(gauges, "echoseve_knowledge_chunks_total")),
                "total_requests": int(total_requests),
                "prefix_cache_hit_rate": self._get_gauge(gauges, "echoseve_model_prefix_cache_hit_rate"),
            },
            "training": {
                "status": int(self._get_gauge(gauges, "echoseve_training_status")),
                "loss": self._get_gauge(gauges, "echoseve_training_loss"),
            },
            "plugins": self._get_plugin_statuses(gauges),
        }

    # ─── 内部方法 ──────────────────────────────────

    def _get_gauge(self, gauges: Dict, name: str) -> float:
        """安全获取 Gauge 值"""
        if name in gauges:
            values = list(gauges[name].values())
            return values[0] if values else 0.0
        return 0.0

    def _get_plugin_statuses(self, gauges: Dict) -> Dict[str, Any]:
        """提取插件状态"""
        statuses = {}
        key = "echoseve_plugin_status"
        if key in gauges:
            for label_tuple, value in gauges[key].items():
                labels = dict(label_tuple)
                plugin_id = labels.get("plugin_id", "unknown")
                statuses[plugin_id] = {
                    "status": labels.get("status", "unknown"),
                    "value": value,
                }
        return statuses

    def record_request(self, duration_ms: float, status: str = "success", method: str = "chat"):
        """代理：记录请求指标"""
        if self.collector:
            self.collector.record_request(duration_ms, status, method)

    def record_retrieval(self, duration_ms: float, result_count: int):
        """代理：记录检索指标"""
        if self.collector:
            self.collector.record_retrieval(duration_ms, result_count)

    def record_inference(self, duration_ms: float):
        """代理：记录推理指标"""
        if self.collector:
            self.collector.record_inference(duration_ms)
