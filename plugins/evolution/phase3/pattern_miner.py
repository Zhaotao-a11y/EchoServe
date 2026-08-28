"""
EchoServe Evolution System — Phase 3: PatternMiner

技能模式挖掘器。
从 Phase 1+2 的结构化日志中挖掘高频、高成功率的技能执行模式。

设计约束：
- 基于 Apriori/Frequent Pattern Growth 思想
- 挖掘维度：意图 -> 技能序列 -> 成功/失败
- 输出模式按置信度排序
- 支持最小支持度/最小成功率过滤
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from ..shared.models import SkillPattern, SkillTraceRecord

logger = logging.getLogger("echoserve.evolution.pattern_miner")


@dataclass
class MiningConfig:
    """模式挖掘配置。"""

    min_support: int = 10  # 最小出现次数
    min_success_rate: float = 0.9  # 最小成功率（与方案 V010 对齐）
    max_sequence_length: int = 5  # 最大技能序列长度
    time_window_hours: int = 168  # 数据时间窗口（默认 7 天）


class PatternMiner:
    """
    技能模式挖掘器。

    职责：
    1. 从 SkillTraceRecord 中提取 (intent, skill_sequence) 模式
    2. 统计每个模式的出现频率和成功率
    3. 计算置信度并排序
    4. 输出可转化为模板的候选模式

    挖掘流程：
        1. 按意图聚合技能序列
        2. 序列规范化（去重、截断）
        3. 统计频率和成功率
        4. 过滤低置信度模式
        5. 按置信度排序输出

    使用示例：
        miner = PatternMiner()
        miner.ingest_records(records)
        patterns = miner.mine(config)
    """

    def __init__(self) -> None:
        self._records: list[SkillTraceRecord] = []
        self._intent_index: dict[str, list[SkillTraceRecord]] = defaultdict(list)
        self._cached_patterns: list[SkillPattern] | None = None  # 缓存
        self._cache_dirty: bool = True  # 新记录摄入后置 True
        logger.info("[PatternMiner] Initialized")

    def ingest_records(self, records: list[SkillTraceRecord]) -> None:
        """批量摄入技能执行记录。"""
        self._records.extend(records)
        for r in records:
            intent = r.input_data.get("intent", "unknown")
            self._intent_index[intent].append(r)
        self._cache_dirty = True  # 标记缓存失效
        logger.info(f"[PatternMiner] Ingested {len(records)} records")

    def mine(self, config: MiningConfig | None = None) -> list[SkillPattern]:
        """
        执行模式挖掘（带缓存）。

        若自上次挖掘后无新记录摄入，直接返回缓存结果。

        Returns:
            按置信度降序排列的 SkillPattern 列表
        """
        # 缓存命中：无新记录且未传自定义 config
        if not self._cache_dirty and config is None and self._cached_patterns is not None:
            return self._cached_patterns

        cfg = config or MiningConfig()
        patterns: list[SkillPattern] = []

        for intent, records in self._intent_index.items():
            if len(records) < cfg.min_support:
                continue

            # 提取并规范化技能序列
            sequences = self._extract_sequences(records, cfg.max_sequence_length)

            # 统计序列频率
            freq = Counter(tuple(s) for s in sequences)

            # 计算每个序列的成功率
            success_counts = defaultdict(int)
            total_counts = defaultdict(int)
            latency_sums = defaultdict(float)

            for record in records:
                seq = self._normalize_sequence(record.skill_sequence, cfg.max_sequence_length)
                seq_key = tuple(seq)
                total_counts[seq_key] += 1
                if record.success:
                    success_counts[seq_key] += 1
                latency_sums[seq_key] += record.latency_ms

            # 生成模式
            for seq_key, count in freq.items():
                if count < cfg.min_support:
                    continue

                success_rate = success_counts[seq_key] / total_counts[seq_key]
                if success_rate < cfg.min_success_rate:
                    continue

                avg_latency = latency_sums[seq_key] / total_counts[seq_key]
                sample_ids = [
                    r.trace_id
                    for r in records
                    if tuple(self._normalize_sequence(r.skill_sequence, cfg.max_sequence_length)) == seq_key
                ][:10]  # 最多保留 10 个样本

                pattern = SkillPattern(
                    intent=intent,
                    skill_sequence=list(seq_key),
                    frequency=count,
                    success_rate=success_rate,
                    sample_records=sample_ids,
                    avg_latency_ms=avg_latency,
                )
                patterns.append(pattern)

        # 按置信度降序
        patterns.sort(key=lambda p: p.confidence, reverse=True)

        logger.info(
            f"[PatternMiner] Mined {len(patterns)} patterns from "
            f"{len(self._records)} records"
        )

        # 更新缓存（仅当使用默认 config 时）
        if config is None:
            self._cached_patterns = patterns
            self._cache_dirty = False

        return patterns

    def mine_from_dicts(
        self, records: list[dict[str, Any]], config: MiningConfig | None = None
    ) -> list[SkillPattern]:
        """从字典列表挖掘（兼容原始数据）。"""
        typed = []
        for r in records:
            try:
                rec = SkillTraceRecord(
                    trace_id=r.get("trace_id", ""),
                    session_id=r.get("session_id", ""),
                    skill_id=r.get("skill_id", ""),
                    skill_sequence=r.get("skill_sequence", []),
                    input_data=r.get("input_data", {}),
                    output_data=r.get("output_data", {}),
                    success=r.get("success", True),
                    error=r.get("error"),
                    latency_ms=r.get("latency_ms", 0),
                    retry_count=r.get("retry_count", 0),
                )
                typed.append(rec)
            except Exception as e:
                logger.warning(f"[PatternMiner] Skip invalid record: {e}")
        if not typed:
            return []
        # 必须先摄入记录，mine() 才能从 self._records 中读取
        self.ingest_records(typed)
        return self.mine(config)

    def get_top_patterns(
        self, n: int = 10, config: MiningConfig | None = None
    ) -> list[SkillPattern]:
        """获取 Top N 模式。"""
        return self.mine(config)[:n]

    def clear(self) -> None:
        """清空已摄入的数据。"""
        self._records.clear()
        self._intent_index.clear()
        logger.info("[PatternMiner] Cleared")

    @staticmethod
    def _extract_sequences(
        records: list[SkillTraceRecord], max_len: int
    ) -> list[list[str]]:
        """从记录中提取规范化的技能序列。"""
        return [PatternMiner._normalize_sequence(r.skill_sequence, max_len) for r in records]

    @staticmethod
    def _normalize_sequence(seq: list[str], max_len: int) -> list[str]:
        """
        序列规范化。

        - 截断到最大长度
        - 去重相邻重复技能（如 A,A,B -> A,B）
        """
        if not seq:
            return []
        # 去重相邻重复
        deduped = [seq[0]]
        for s in seq[1:]:
            if s != deduped[-1]:
                deduped.append(s)
        return deduped[:max_len]
