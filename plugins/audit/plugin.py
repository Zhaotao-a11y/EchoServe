"""
EchoServe V0.1.0 — 审计日志插件

功能：
  - append-only 日志记录
  - 链式哈希防篡改（每条日志包含上一条的哈希）
  - 日志查询（按日期/用户/关键词筛选）
  - CSV 导出
  - 完整性校验
"""
from __future__ import annotations

import json
import hashlib
import csv
import io
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

logger = logging.getLogger("echoseve.audit")


class AuditPlugin(BaizePlugin):
    """审计日志插件"""

    plugin_id = "security.audit"
    plugin_name = "审计日志"
    plugin_version = "0.1.0"
    dependencies = []

    def __init__(self):
        self._log_path: Optional[Path] = None
        self._chain_path: Optional[Path] = None
        self._last_hash: str = "0000000000000000000000000000000000000000000000000000000000000000"
        self._log_count: int = 0
        self._retention_days: int = 90

    # ─── 生命周期 ──────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        data_dir = Path(ctx.settings.root_dir) / "data" / "audit"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = data_dir / "audit.log.jsonl"
        self._chain_path = data_dir / "chain_state.json"

        await self._load_chain_state()
        await self._count_existing_logs()

        self.provide("audit_logger", self)

        logger.info(
            f"[{self.plugin_id}] Initialized "
            f"({self._log_count} existing logs)"
        )

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        await self._save_chain_state()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 日志记录 ──────────────────────────────────────────

    async def log(
        self,
        action: str,
        user_id: str = "system",
        query: str = "",
        response_summary: str = "",
        sources: Optional[List[str]] = None,
        latency_ms: int = 0,
        channel: str = "web",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        写入一条审计日志，返回日志ID。
        日志格式：
        {
            "id": "int",
            "timestamp": "ISO8601",
            "user_id": "...",
            "action": "...",
            "query": "...",
            "response_summary": "...",
            "sources": [...],
            "latency_ms": 0,
            "channel": "web",
            "ip_address": "...",
            "metadata": {},
            "prev_hash": "...",
            "hash": "..."
        }
        """
        now = datetime.now(timezone.utc)
        entry = {
            "id": self._log_count + 1,
            "timestamp": now.isoformat(),
            "user_id": user_id,
            "action": action,
            "query": query[:2000],  # 截断超长内容
            "response_summary": response_summary[:2000],
            "sources": sources or [],
            "latency_ms": latency_ms,
            "channel": channel,
            "metadata": metadata or {},
            "prev_hash": self._last_hash,
        }

        # 计算当前哈希
        hash_input = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        current_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        entry["hash"] = current_hash

        # 写入文件（append）
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self._last_hash = current_hash
        self._log_count += 1

        return str(entry["id"])

    def log_sync(
        self,
        action: str,
        user_id: str = "system",
        query: str = "",
        response_summary: str = "",
        sources: Optional[List[str]] = None,
        latency_ms: int = 0,
        channel: str = "web",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """同步版本（供非 async 代码调用）"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已有事件循环在跑，用同步方式写
                return self._write_log_entry(
                    action, user_id, query, response_summary,
                    sources, latency_ms, channel, metadata
                )
            else:
                return loop.run_until_complete(self.log(
                    action, user_id, query, response_summary,
                    sources, latency_ms, channel, metadata
                ))
        except RuntimeError:
            # 没有事件循环，直接同步写
            return self._write_log_entry(
                action, user_id, query, response_summary,
                sources, latency_ms, channel, metadata
            )

    def _write_log_entry(
        self,
        action: str,
        user_id: str,
        query: str,
        response_summary: str,
        sources: Optional[List[str]],
        latency_ms: int,
        channel: str,
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        """内部同步写入"""
        now = datetime.now(timezone.utc)
        entry = {
            "id": self._log_count + 1,
            "timestamp": now.isoformat(),
            "user_id": user_id,
            "action": action,
            "query": query[:2000],
            "response_summary": response_summary[:2000],
            "sources": sources or [],
            "latency_ms": latency_ms,
            "channel": channel,
            "metadata": metadata or {},
            "prev_hash": self._last_hash,
        }
        hash_input = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        current_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        entry["hash"] = current_hash

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self._last_hash = current_hash
        self._log_count += 1
        return str(entry["id"])

    # ─── 日志查询 ──────────────────────────────────────────

    def query(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[str] = None,
        keyword: Optional[str] = None,
        action: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """查询审计日志"""
        if not self._log_path or not self._log_path.exists():
            return {"total": 0, "logs": []}

        results = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 筛选
                if start_date and entry["timestamp"] < start_date:
                    continue
                if end_date and entry["timestamp"] > end_date:
                    continue
                if user_id and entry["user_id"] != user_id:
                    continue
                if action and entry["action"] != action:
                    continue
                if keyword:
                    haystack = (
                        entry.get("query", "") + " " +
                        entry.get("response_summary", "")
                    ).lower()
                    if keyword.lower() not in haystack:
                        continue

                results.append(entry)

        total = len(results)
        results = results[offset:offset + limit]

        return {"total": total, "offset": offset, "limit": limit, "logs": results}

    # ─── CSV 导出 ──────────────────────────────────────────

    def export_csv(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """导出审计日志为 CSV 字符串"""
        query_result = self.query(
            start_date=start_date, end_date=end_date, user_id=user_id,
            limit=100000  # 导出全部
        )

        output = io.StringIO()
        writer = csv.writer(output)

        # 表头
        writer.writerow([
            "ID", "Timestamp", "User ID", "Action", "Query",
            "Response Summary", "Sources", "Latency(ms)", "Channel", "Hash"
        ])

        for entry in query_result["logs"]:
            writer.writerow([
                entry["id"],
                entry["timestamp"],
                entry["user_id"],
                entry["action"],
                entry.get("query", ""),
                entry.get("response_summary", ""),
                "|".join(entry.get("sources", [])),
                entry.get("latency_ms", 0),
                entry.get("channel", ""),
                entry.get("hash", ""),
            ])

        return output.getvalue()

    async def export_csv_file(
        self,
        output_path: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """导出 CSV 到文件，返回文件路径"""
        csv_content = self.export_csv(start_date, end_date, user_id)

        if not output_path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_dir = self._log_path.parent / "exports"
            export_dir.mkdir(exist_ok=True)
            output_path = str(export_dir / f"audit_export_{ts}.csv")

        with open(output_path, "w", encoding="utf-8-sig") as f:
            f.write(csv_content)

        logger.info(f"[{self.plugin_id}] CSV exported: {output_path}")
        return output_path

    # ─── 完整性校验 ────────────────────────────────────────

    def verify_integrity(self) -> Dict[str, Any]:
        """
        验证整条哈希链是否完整。
        如果任何一条日志被篡改，哈希链会断裂。
        返回：{"valid": bool, "total": int, "broken_at": int|None}
        """
        if not self._log_path or not self._log_path.exists():
            return {"valid": True, "total": 0, "broken_at": None}

        prev_hash = "0000000000000000000000000000000000000000000000000000000000000000"
        count = 0
        broken_at = None

        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    broken_at = count + 1
                    break

                # 验证 prev_hash 链
                if entry.get("prev_hash") != prev_hash:
                    broken_at = entry.get("id", count + 1)
                    break

                # 重新计算当前哈希
                entry_copy = {k: v for k, v in entry.items() if k != "hash"}
                hash_input = json.dumps(entry_copy, ensure_ascii=False, sort_keys=True)
                expected_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

                if entry.get("hash") != expected_hash:
                    broken_at = entry.get("id", count + 1)
                    break

                prev_hash = entry["hash"]
                count += 1

        return {
            "valid": broken_at is None,
            "total": count,
            "broken_at": broken_at,
        }

    # ─── 内部方法 ──────────────────────────────────────────

    async def _load_chain_state(self):
        """加载哈希链状态"""
        if not self._chain_path.exists():
            return
        try:
            with open(self._chain_path, "r") as f:
                state = json.load(f)
            self._last_hash = state.get("last_hash", self._last_hash)
            logger.info(f"[{self.plugin_id}] Chain state loaded")
        except Exception as e:
            logger.warning(f"[{self.plugin_id}] Failed to load chain state: {e}")

    async def _save_chain_state(self):
        """保存哈希链状态"""
        if not self._chain_path:
            return
        state = {
            "last_hash": self._last_hash,
            "log_count": self._log_count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(self._chain_path, "w") as f:
            json.dump(state, f, indent=2)

    async def _count_existing_logs(self):
        """统计已有日志条数"""
        if not self._log_path.exists():
            self._log_count = 0
            return
        with open(self._log_path, "r", encoding="utf-8") as f:
            self._log_count = sum(1 for line in f if line.strip())
        logger.info(f"[{self.plugin_id}] Found {self._log_count} existing log entries")
