"""
EchoServe Evolution System — Metrics Collector

统一指标采集器，用于 Phase 1-3 的量化指标评估。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("echoserve.evolution.metrics")


class MetricName(str, Enum):
    """指标名称枚举。"""
    # Phase 1: 采集质量
    LOG_COMPLETENESS = "log_completeness"
    LOG_LATENCY_P99 = "log_latency_p99"
    STORE_WRITE_FAILURE = "store_write_failure"

    # Phase 2: 参数调优
    RETRIEVAL_HIT_RATE = "retrieval_hit_rate"
    CHAT_COMPLETION_RATE = "chat_completion_rate"
    USER_NPS = "user_nps"
    FIRST_TOKEN_LATENCY_P95 = "first_token_latency_p95"

    # Phase 3: 技能进化
    PATTERN_CONFIDENCE = "pattern_confidence"
    SIMULATION_PASS_RATE = "simulation_pass_rate"
    CANARY_SUCCESS_RATE = "canary_success_rate"
    ROLLBACK_RATE = "rollback_rate"


@dataclass(frozen=True)
class MetricSnapshot:
    """指标快照（不可变）。"""
    name: str
    value: float
    timestamp: float
    tags: dict[str, str] = field(default_factory=dict)
    unit: str = ""


class MetricsCollector:
    """
    内存中的环形指标缓冲区。

    设计约束：
    - 不依赖外部时序数据库，降低部署复杂度
    - 环形缓冲（deque maxlen），自动丢弃旧数据，控制内存占用
    - 支持标签过滤和聚合查询
    """

    DEFAULT_CAPACITY = 10000  # 单指标最大保留样本数

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._capacity = capacity
        # 使用 deque(maxlen=capacity) 实现 O(1) 淘汰，替代 list.pop(0) 的 O(n)
        self._buffers: dict[str, deque[MetricSnapshot]] = defaultdict(
            lambda: deque(maxlen=capacity)
        )
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=capacity)
        )
        logger.info(f"[MetricsCollector] Initialized (capacity={capacity})")

    def record(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
        unit: str = "",
    ) -> None:
        """记录一个指标值。"""
        snapshot = MetricSnapshot(
            name=name,
            value=value,
            timestamp=time.time(),
            tags=tags or {},
            unit=unit,
        )
        self._buffers[name].append(snapshot)

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        """计数器 +1。"""
        key = f"{name}:{self._tags_key(tags)}"
        self._counters[key] += 1

    @contextmanager
    def timer(self, name: str, tags: dict[str, str] | None = None):
        """上下文管理器，自动记录耗时。"""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            self.record(name, elapsed_ms, tags, unit="ms")

    def latency_record(self, name: str, value_ms: float) -> None:
        """记录延迟值到延迟统计桶。"""
        self._latencies[name].append(value_ms)

    def get_latest(self, name: str, n: int = 100) -> list[MetricSnapshot]:
        """获取最近 n 条指标记录。"""
        buf = self._buffers.get(name)
        if not buf:
            return []
        return list(buf)[-n:]

    def get_avg(self, name: str, window: int = 100) -> float:
        """获取最近 window 个样本的平均值。"""
        samples = self.get_latest(name, window)
        if not samples:
            return 0.0
        return sum(s.value for s in samples) / len(samples)

    def get_percentile(self, name: str, p: float, window: int = 1000) -> float:
        """获取最近 window 个样本的 p 分位数。"""
        samples = self.get_latest(name, window)
        if not samples:
            return 0.0
        sorted_vals = sorted(s.value for s in samples)
        idx = int(len(sorted_vals) * p)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def get_counter(self, name: str, tags: dict[str, str] | None = None) -> int:
        """获取计数器值。"""
        key = f"{name}:{self._tags_key(tags)}"
        return self._counters.get(key, 0)

    def get_latency_p99(self, name: str) -> float:
        """获取延迟 P99。"""
        latencies = self._latencies.get(name)
        if not latencies:
            return 0.0
        sorted_vals = sorted(latencies)
        idx = int(len(sorted_vals) * 0.99)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def summary(self) -> dict[str, Any]:
        """返回所有指标的摘要。"""
        return {
            "buffered_metrics": list(self._buffers.keys()),
            "counters": dict(self._counters),
            "latencies": {
                name: {"count": len(vals), "p99": self.get_latency_p99(name)}
                for name, vals in self._latencies.items()
            },
        }

    def clear(self) -> None:
        """清空所有指标（用于测试）。"""
        self._buffers.clear()
        self._counters.clear()
        self._latencies.clear()

    @staticmethod
    def _tags_key(tags: dict[str, str] | None) -> str:
        if not tags:
            return ""
        return ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
