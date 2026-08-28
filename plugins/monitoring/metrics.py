"""
EchoServe P1 — Prometheus 指标收集器

功能：
- 定义标准 Prometheus 指标（Counter/Gauge/Histogram/Summary）
- 提供 /metrics 端点数据格式输出
- 自动采集 GPU/内存/CPU 指标（如可用）
- 支持自定义业务指标上报

使用方式：
    collector = MetricsCollector()
    collector.record_request(duration_ms=150, status="success")
    collector.record_retrieval(duration_ms=80, result_count=5)
    metrics_text = collector.export_prometheus()
"""
from __future__ import annotations

import time
import logging
from typing import Any
from collections import defaultdict

logger = logging.getLogger("echoserve.monitoring.metrics")


class MetricsCollector:
    """
    Prometheus 风格指标收集器。

    不依赖 prometheus_client 库，自行实现核心指标类型，
    输出标准 Prometheus text format。

    指标列表：
    - echoseve_requests_total{method, status}          Counter
    - echoseve_request_duration_ms{method}            Histogram
    - echoseve_chat_sessions_active                     Gauge
    - echoseve_chat_messages_total{type}               Counter
    - echoseve_retrieval_duration_ms                   Histogram
    - echoseve_retrieval_results{source}              Histogram
    - echoseve_model_inference_duration_ms            Histogram
    - echoseve_model_prefix_cache_hit_rate            Gauge
    - echoseve_gpu_utilization                        Gauge
    - echoseve_gpu_memory_used_mb                     Gauge
    - echoseve_gpu_memory_total_mb                    Gauge
    - echoseve_system_memory_percent                   Gauge
    - echoseve_system_cpu_percent                     Gauge
    - echoseve_knowledge_docs_total                   Gauge
    - echoseve_knowledge_chunks_total                 Gauge
    - echoseve_plugin_status{plugin_id, status}       Gauge
    - echoseve_audit_logs_total{action}               Counter
    - echoseve_training_status                        Gauge
    - echoseve_training_loss{phase}                  Gauge
    """

    # ─── Prometheus 直方图默认桶 ──────────────────────
    DEFAULT_BUCKETS = (
        5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000
    )

    def __init__(self, namespace: str = "echoseve"):
        self.namespace = namespace
        self._start_time = time.time()

        # Counters: {name: {label_tuple: value}}
        self._counters: dict[str, dict[tuple, float]] = defaultdict(dict)

        # Gauges: {name: {label_tuple: value}}
        self._gauges: dict[str, dict[tuple, float]] = defaultdict(dict)

        # Histograms: {name: {buckets: [...], sum: ..., count: ..., labels: ...}}
        self._histograms: dict[str, Dict] = {}

        # 内置指标初始化
        self._init_builtin_gauges()

    # ─── 初始化 ──────────────────────────────────────

    def _init_builtin_gauges(self):
        """初始化内置 Gauge 指标"""
        # 这些会在 update_* 方法中更新
        self._gauges["gpu_utilization"] = {(): 0.0}
        self._gauges["gpu_memory_used_mb"] = {(): 0.0}
        self._gauges["gpu_memory_total_mb"] = {(): 0.0}
        self._gauges["system_memory_percent"] = {(): 0.0}
        self._gauges["system_cpu_percent"] = {(): 0.0}
        self._gauges["chat_sessions_active"] = {(): 0.0}
        self._gauges["knowledge_docs_total"] = {(): 0.0}
        self._gauges["knowledge_chunks_total"] = {(): 0.0}
        self._gauges["model_prefix_cache_hit_rate"] = {(): 0.0}
        self._gauges["training_status"] = {(): 0.0}  # 0=idle, 1=running, 2=completed, 3=failed

    # ─── Counter API ─────────────────────────────────

    def inc_counter(self, name: str, labels: (dict[str, str] | None) = None, value: float = 1):
        """增加 Counter 值"""
        label_key = self._label_key(labels or {})
        full_name = f"{self.namespace}_{name}_total"
        if label_key not in self._counters[full_name]:
            self._counters[full_name][label_key] = 0.0
        self._counters[full_name][label_key] += value

    def record_request(self, duration_ms: float, status: str = "success", method: str = "chat"):
        """记录一次请求"""
        self.inc_counter("requests", {"method": method, "status": status})
        self._record_histogram("request_duration_ms", duration_ms, {"method": method})

    def record_chat_message(self, msg_type: str = "user"):
        """记录一条聊天消息"""
        self.inc_counter("chat_messages", {"type": msg_type})

    def record_audit_log(self, action: str):
        """记录一条审计日志"""
        self.inc_counter("audit_logs", {"action": action})

    # ─── Gauge API ──────────────────────────────────

    def set_gauge(self, name: str, value: float, labels: (dict[str, str] | None) = None):
        """设置 Gauge 值"""
        label_key = self._label_key(labels or {})
        full_name = f"{self.namespace}_{name}"
        self._gauges[full_name][label_key] = value

    def update_gpu(self, utilization: float, memory_used_mb: float, memory_total_mb: float):
        """更新 GPU 指标"""
        self.set_gauge("gpu_utilization", utilization)
        self.set_gauge("gpu_memory_used_mb", memory_used_mb)
        self.set_gauge("gpu_memory_total_mb", memory_total_mb)

    def update_system(self, memory_percent: float, cpu_percent: float):
        """更新系统指标"""
        self.set_gauge("system_memory_percent", memory_percent)
        self.set_gauge("system_cpu_percent", cpu_percent)

    def update_sessions(self, active_count: int):
        """更新活跃会话数"""
        self.set_gauge("chat_sessions_active", float(active_count))

    def update_knowledge(self, doc_count: int, chunk_count: int):
        """更新知识库指标"""
        self.set_gauge("knowledge_docs_total", float(doc_count))
        self.set_gauge("knowledge_chunks_total", float(chunk_count))

    def update_cache_hit_rate(self, hit_rate: float):
        """更新 Prefix Cache 命中率"""
        self.set_gauge("model_prefix_cache_hit_rate", hit_rate)

    def update_plugin_status(self, plugin_id: str, status: str):
        """更新插件状态"""
        status_map = {"loaded": 1, "started": 2, "stopped": 0, "error": -1, "unloaded": -2}
        value = status_map.get(status, 0)
        self.set_gauge("plugin_status", value, {"plugin_id": plugin_id, "status": status})

    def update_training(self, status: str, loss: (float | None) = None, phase: str = "train"):
        """更新训练状态"""
        status_map = {"idle": 0, "running": 1, "completed": 2, "failed": 3}
        self.set_gauge("training_status", status_map.get(status, 0))
        if loss is not None:
            self.set_gauge(f"training_loss", loss, {"phase": phase})

    # ─── Histogram API ──────────────────────────────

    def record_retrieval(self, duration_ms: float, result_count: int):
        """记录检索性能"""
        self._record_histogram("retrieval_duration_ms", duration_ms)
        self._record_histogram("retrieval_results", float(result_count))

    def record_inference(self, duration_ms: float):
        """记录推理延迟"""
        self._record_histogram("model_inference_duration_ms", duration_ms)

    def _record_histogram(self, name: str, value: float, labels: (dict[str, str] | None) = None):
        """内部：记录直方图数据"""
        full_name = f"{self.namespace}_{name}"
        label_key = self._label_key(labels or {})

        if full_name not in self._histograms:
            self._histograms[full_name] = {
                "buckets": list(self.DEFAULT_BUCKETS),
                "counts": defaultdict(float),
                "sum": 0.0,
                "count": 0,
                "labels": labels or {},
            }

        hist = self._histograms[full_name]
        hist["sum"] += value
        hist["count"] += 1

        for bucket in hist["buckets"]:
            if value <= bucket:
                bucket_key = str(bucket)
                hist["counts"][bucket_key] += 1

    # ─── 导出 Prometheus 格式 ─────────────────────────

    def export_prometheus(self) -> str:
        """
        导出 Prometheus text format。

        Returns:
            Prometheus 格式的文本
        """
        lines = []
        timestamp = int(time.time() * 1000)

        # HELP 和 TYPE 声明
        metric_descriptions = {
            f"{self.namespace}_requests_total": ("Total number of requests", "counter"),
            f"{self.namespace}_request_duration_ms": ("Request duration in milliseconds", "histogram"),
            f"{self.namespace}_chat_sessions_active": ("Number of active chat sessions", "gauge"),
            f"{self.namespace}_chat_messages_total": ("Total chat messages", "counter"),
            f"{self.namespace}_retrieval_duration_ms": ("Retrieval duration in milliseconds", "histogram"),
            f"{self.namespace}_retrieval_results": ("Number of retrieval results", "histogram"),
            f"{self.namespace}_model_inference_duration_ms": ("Model inference duration", "histogram"),
            f"{self.namespace}_model_prefix_cache_hit_rate": ("Prefix cache hit rate", "gauge"),
            f"{self.namespace}_gpu_utilization": ("GPU utilization percentage", "gauge"),
            f"{self.namespace}_gpu_memory_used_mb": ("GPU memory used in MB", "gauge"),
            f"{self.namespace}_gpu_memory_total_mb": ("GPU memory total in MB", "gauge"),
            f"{self.namespace}_system_memory_percent": ("System memory usage percentage", "gauge"),
            f"{self.namespace}_system_cpu_percent": ("System CPU usage percentage", "gauge"),
            f"{self.namespace}_knowledge_docs_total": ("Total knowledge documents", "gauge"),
            f"{self.namespace}_knowledge_chunks_total": ("Total knowledge chunks", "gauge"),
            f"{self.namespace}_plugin_status": ("Plugin status (-2=unloaded, -1=error, 0=stopped, 1=loaded, 2=started)", "gauge"),
            f"{self.namespace}_audit_logs_total": ("Total audit log entries", "counter"),
            f"{self.namespace}_training_status": ("Training status (0=idle, 1=running, 2=completed, 3=failed)", "gauge"),
            f"{self.namespace}_training_loss": ("Training loss", "gauge"),
            f"{self.namespace}_uptime_seconds": ("Process uptime in seconds", "gauge"),
        }

        # 写入声明
        for name, (desc, mtype) in metric_descriptions.items():
            lines.append(f"# HELP {name} {desc}")
            lines.append(f"# TYPE {name} {mtype}")

        # Counters
        for name, label_map in self._counters.items():
            for label_tuple, value in label_map.items():
                label_str = self._format_labels(label_tuple, name)
                lines.append(f"{name}{label_str} {value} {timestamp}")

        # Gauges
        for name, label_map in self._gauges.items():
            for label_tuple, value in label_map.items():
                label_str = self._format_labels(label_tuple, name)
                lines.append(f"{name}{label_str} {value} {timestamp}")

        # Histograms
        for name, hist in self._histograms.items():
            labels = hist.get("labels", {})
            label_pairs = self._label_pairs(labels)

            # 写 bucket
            cumulative = 0
            for bucket in hist["buckets"]:
                bucket_key = str(bucket)
                cumulative += hist["counts"].get(bucket_key, 0)
                lines.append(
                    f"{name}_bucket{label_pairs}le=\"{bucket}\"}} {cumulative} {timestamp}"
                )

            # +Inf bucket
            lines.append(
                f"{name}_bucket{label_pairs}le=\"+Inf\"}} {hist['count']} {timestamp}"
            )
            # sum 和 count
            lines.append(f"{name}_sum{label_pairs} {hist['sum']} {timestamp}")
            lines.append(f"{name}_count{label_pairs} {hist['count']} {timestamp}")

        # Uptime（特殊指标）
        uptime = time.time() - self._start_time
        lines.append(f"# HELP {self.namespace}_uptime_seconds Process uptime in seconds")
        lines.append(f"# TYPE {self.namespace}_uptime_seconds gauge")
        lines.append(f"{self.namespace}_uptime_seconds {uptime:.1f} {timestamp}")

        return "\n".join(lines) + "\n"

    # ─── 工具方法 ──────────────────────────────────

    def _label_key(self, labels: dict[str, str]) -> tuple:
        """将 labels 字典转为可哈希的排序元组"""
        return tuple(sorted(labels.items()))

    def _format_labels(self, label_tuple: tuple, metric_name: str) -> str:
        """格式化标签为 Prometheus 格式"""
        if not label_tuple:
            return ""
        parts = [f'{k}="{v}"' for k, v in label_tuple]
        return "{" + ",".join(parts) + "}"

    def _label_pairs(self, labels: dict[str, str]) -> str:
        """格式化标签对（用于 histogram）"""
        if not labels:
            return "{"
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(parts) + ","

    # ─── GPU 自动采集 ──────────────────────────────────

    def collect_gpu_metrics(self):
        """尝试自动采集 GPU 指标（需要 nvidia-ml-py 或 nvidia-smi）"""
        try:
            import subprocess
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                line = result.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 3:
                    util = float(parts[0])
                    mem_used = float(parts[1])
                    mem_total = float(parts[2])
                    self.update_gpu(util, mem_used, mem_total)
                    return True
        except Exception as e:
            logger.debug(f"  GPU 采集失败: {e}")
        return False

    def collect_system_metrics(self):
        """采集系统指标"""
        try:
            import psutil  # type: ignore
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.1)
            self.update_system(mem.percent, cpu)
            return True
        except ImportError:
            # 回退到 /proc
            try:
                with open("/proc/meminfo", "r") as f:
                    meminfo = f.read()
                # 简单解析
                total = 0
                free = 0
                for line in meminfo.split("\n"):
                    if line.startswith("MemTotal:"):
                        total = int(line.split()[1]) / 1024  # KB → MB
                    elif line.startswith("MemFree:"):
                        free = int(line.split()[1]) / 1024
                if total > 0:
                    self.update_system((total - free) / total * 100, 0)
                return True
            except Exception as e:
                logger.debug(f"Failed to parse /proc/meminfo: {e}")
        return False

    # ─── 快照 ──────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """获取当前指标快照（JSON 格式，供 API 返回）"""
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": {k: dict(v) for k, v in self._counters.items()},
            "gauges": {k: dict(v) for k, v in self._gauges.items()},
            "histograms": {
                k: {
                    "sum": v["sum"],
                    "count": v["count"],
                    "avg": round(v["sum"] / v["count"], 2) if v["count"] > 0 else 0,
                }
                for k, v in self._histograms.items()
            },
        }

    # ─── 重置 ──────────────────────────────────────

    def reset(self):
        """重置所有指标（测试用）"""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._start_time = time.time()

