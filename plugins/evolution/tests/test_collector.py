"""
EchoServe Evolution System — Tests: EvolutionCollector

测试 phase1.collector 的批量采集、flush 逻辑和降级行为。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evolution.phase1.collector import EvolutionCollector


class MockStore:
    """模拟存储，支持正常和故障模式。"""

    def __init__(self, fail_mode: bool = False):
        self.batches: list[list[dict[str, Any]]] = []
        self.fail_mode = fail_mode

    async def insert_batch(self, batch: list[dict[str, Any]]) -> None:
        if self.fail_mode:
            raise RuntimeError("Store is down")
        self.batches.append(batch)


class TestEvolutionCollector(unittest.TestCase):
    """测试 EvolutionCollector。"""

    def setUp(self):
        """每个测试前初始化。"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        """每个测试后清理。"""
        self.loop.close()

    def _run_async(self, coro):
        """辅助：运行异步协程。"""
        return self.loop.run_until_complete(coro)

    def test_init_defaults(self):
        """测试默认初始化。"""
        collector = EvolutionCollector()
        self.assertIsNone(collector._store)
        self.assertIsNotNone(collector._metrics)
        self.assertEqual(collector._total_received, 0)

    def test_start_stop(self):
        """测试启动和停止。"""
        collector = EvolutionCollector()
        self._run_async(collector.start())
        self.assertTrue(collector._running)
        self._run_async(collector.stop())
        self.assertFalse(collector._running)

    def test_batch_flush(self):
        """测试批量 flush。"""
        store = MockStore()
        collector = EvolutionCollector(store=store)

        async def test():
            await collector.start()
            # 发送 BATCH_SIZE 条事件触发 flush
            for i in range(collector.BATCH_SIZE):
                await collector._on_chat_complete({
                    "session_id": f"s{i}",
                    "query": f"q{i}",
                    "reply": f"r{i}",
                    "latency_ms": 100,
                })
            # 等待 flush 完成
            await asyncio.sleep(0.1)
            await collector.stop()

        self._run_async(test())
        self.assertEqual(collector._total_received, collector.BATCH_SIZE)
        self.assertEqual(collector._total_written, collector.BATCH_SIZE)
        self.assertEqual(len(store.batches), 1)
        self.assertEqual(len(store.batches[0]), collector.BATCH_SIZE)

    def test_fallback_on_store_failure(self):
        """测试存储故障时降级到 JSONL。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MockStore(fail_mode=True)
            fallback_dir = Path(tmpdir) / "fallback"
            collector = EvolutionCollector(store=store, fallback_dir=fallback_dir)

            async def test():
                await collector.start()
                await collector._on_chat_complete({
                    "session_id": "s1",
                    "query": "q1",
                    "reply": "r1",
                })
                # 手动触发 flush
                await collector._flush_now()
                await collector.stop()

            self._run_async(test())

            # 检查 fallback 文件
            self.assertTrue(fallback_dir.exists())
            jsonl_files = list(fallback_dir.glob("*.jsonl"))
            self.assertGreaterEqual(len(jsonl_files), 1)

            # 验证内容
            with open(jsonl_files[0], "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertGreaterEqual(len(lines), 1)
            self.assertEqual(lines[0]["table"], "chat_log")

    def test_max_backlog_force_flush(self):
        """测试积压上限强制 flush。"""
        store = MockStore()
        collector = EvolutionCollector(store=store)

        async def test():
            # 不启动后台循环，直接积压
            for i in range(collector.MAX_BACKLOG + 10):
                await collector._on_chat_complete({
                    "session_id": f"s{i}",
                    "query": f"q{i}",
                    "reply": f"r{i}",
                })
            # 积压达到上限会触发强制 flush
            await asyncio.sleep(0.1)
            await collector._final_flush()

        self._run_async(test())
        # 数据应该被写入（通过 flush_now 或直接 fallback）
        self.assertGreater(collector._total_written, 0)

    def test_get_stats(self):
        """测试统计信息。"""
        collector = EvolutionCollector()
        stats = collector.get_stats()
        self.assertIn("total_received", stats)
        self.assertIn("total_written", stats)
        self.assertIn("completeness_rate", stats)
        self.assertEqual(stats["total_received"], 0)
        self.assertEqual(stats["completeness_rate"], 0.0)

    def test_on_skill_execute(self):
        """测试技能执行事件处理。"""
        store = MockStore()
        collector = EvolutionCollector(store=store)

        async def test():
            await collector._on_skill_execute({
                "session_id": "s1",
                "skill_id": "search",
                "skill_sequence": ["search", "summarize"],
                "input": {"query": "test"},
                "output": {"results": ["r1"]},
                "success": True,
                "latency_ms": 50,
            })
            await collector._flush_now()

        self._run_async(test())
        self.assertEqual(collector._total_received, 1)

    def test_on_user_feedback(self):
        """测试用户反馈事件处理。"""
        store = MockStore()
        collector = EvolutionCollector(store=store)

        async def test():
            await collector._on_user_feedback({
                "session_id": "s1",
                "feedback_type": "like",
                "comment": "good",
            })
            await collector._flush_now()

        self._run_async(test())
        self.assertEqual(collector._total_received, 1)


if __name__ == "__main__":
    unittest.main()
