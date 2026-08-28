"""
EchoServe Evolution System — Phase 1: EvolutionStore

数据存储层。
热数据（最近 7 天）→ SQLite，冷数据（7 天前）→ JSONL 归档。

设计约束：
- 单文件 SQLite，零配置部署
- JSONL 冷数据按天压缩，90 天自动清理
- 批量写入，事务控制
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..shared.metrics import MetricsCollector

logger = logging.getLogger("echoserve.evolution.store")


class EvolutionStore:
    """
    双层存储：SQLite（热）+ JSONL（冷）。

    表结构：
        chat_log       — 对话记录
        skill_trace    — 技能执行链路
        feedback       — 用户反馈
        route_log      — 路由决策
        system_metric  — 系统指标
        experiment_log — 实验记录
        template_log   — 模板生命周期
    """

    HOT_DAYS = 7
    CLEANUP_DAYS = 90
    MAX_BATCH = 500

    # 表名白名单：防止 SQL 注入
    _VALID_TABLES = frozenset({
        "chat_log",
        "skill_trace",
        "feedback",
        "route_log",
        "system_metric",
        "experiment_log",
        "template_log",
    })

    def __init__(
        self,
        db_path: Path,
        cold_dir: Path | None = None,
        metrics: MetricsCollector | None = None,
    ):
        self._db_path = db_path
        self._cold_dir = cold_dir or db_path.parent / "cold"
        self._metrics = metrics or MetricsCollector()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._write_count = 0
        self._conn: sqlite3.Connection | None = None
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="evo-store")

    @staticmethod
    def _validate_table(table: str) -> None:
        """校验表名是否在白名单中，防止 SQL 注入。"""
        if table not in EvolutionStore._VALID_TABLES:
            raise ValueError(
                f"Invalid table name: {table!r}. "
                f"Valid tables: {sorted(EvolutionStore._VALID_TABLES)}"
            )

    async def init(self) -> None:
        """初始化数据库连接和表结构。"""
        if self._initialized:
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cold_dir.mkdir(parents=True, exist_ok=True)

        # SQLite 连接及 PRAGMA 设置（全部在同一线程中完成，避免线程安全错误）
        loop = asyncio.get_running_loop()

        def _init_conn() -> sqlite3.Connection:
            # M-8: 创建连接并设置安全 PRAGMA
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # M-8: 启用防御模式，防止 ATTACH DATABASE 等注入攻击
            conn.execute("PRAGMA defensive=ON")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn

        self._conn = await loop.run_in_executor(self._executor, _init_conn)

        # M-8: 设置数据库文件权限为仅所有者可读写 (0o600)
        try:
            os.chmod(str(self._db_path), 0o600)
        except (OSError, PermissionError) as e:
            logger.warning(f"[EvolutionStore] Failed to set file permissions: {e}")

        await self._create_tables()
        self._initialized = True
        logger.info(f"[EvolutionStore] Initialized: {self._db_path}")

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._conn.close)
            self._conn = None
            self._initialized = False
            self._executor.shutdown(wait=False)
            logger.info("[EvolutionStore] Closed")

    async def insert_batch(self, records: list[dict[str, Any]]) -> None:
        """批量插入记录。"""
        if not records:
            return

        if not self._initialized:
            raise RuntimeError("EvolutionStore not initialized")

        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._insert_batch_sync, records)
            self._write_count += len(records)

    async def query(
        self,
        table: str,
        start: datetime | None = None,
        end: datetime | None = None,
        conditions: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        查询记录。

        Args:
            table: 表名
            start: 起始时间（UTC）
            end: 结束时间（UTC）
            conditions: 额外条件字典 {column: value}
            limit: 最大返回条数
            offset: 分页偏移
        """
        if not self._initialized:
            raise RuntimeError("EvolutionStore not initialized")

        self._validate_table(table)

        query_parts = [f"SELECT * FROM {table} WHERE 1=1"]
        params: list[Any] = []

        if start:
            query_parts.append("AND timestamp >= ?")
            params.append(start.isoformat())
        if end:
            query_parts.append("AND timestamp <= ?")
            params.append(end.isoformat())

        if conditions:
            for col, val in conditions.items():
                # 防御性校验：列名只允许字母、数字、下划线
                if not col.replace("_", "").isalnum():
                    raise ValueError(f"Invalid column name in conditions: {col!r}")
                query_parts.append(f"AND {col} = ?")
                params.append(val)

        query_parts.append("ORDER BY timestamp DESC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        sql = " ".join(query_parts)

        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(self._executor, lambda: self._conn.execute(sql, params).fetchall())
        return [dict(row) for row in rows]

    async def count(
        self,
        table: str,
        start: datetime | None = None,
        end: datetime | None = None,
        conditions: dict[str, Any] | None = None,
    ) -> int:
        """统计记录数。"""
        if not self._initialized:
            raise RuntimeError("EvolutionStore not initialized")

        self._validate_table(table)

        query_parts = [f"SELECT COUNT(*) FROM {table} WHERE 1=1"]
        params: list[Any] = []

        if start:
            query_parts.append("AND timestamp >= ?")
            params.append(start.isoformat())
        if end:
            query_parts.append("AND timestamp <= ?")
            params.append(end.isoformat())

        if conditions:
            for col, val in conditions.items():
                # 防御性校验：列名只允许字母、数字、下划线
                if not col.replace("_", "").isalnum():
                    raise ValueError(f"Invalid column name in conditions: {col!r}")
                query_parts.append(f"AND {col} = ?")
                params.append(val)

        sql = " ".join(query_parts)

        loop = asyncio.get_running_loop()
        row = await loop.run_in_executor(self._executor, lambda: self._conn.execute(sql, params).fetchone())
        return row[0] if row else 0

    async def cleanup(self) -> None:
        """清理过期数据：转存冷数据到 JSONL，删除超期数据。"""
        cutoff_hot = datetime.now(timezone.utc) - timedelta(days=self.HOT_DAYS)
        cutoff_cleanup = datetime.now(timezone.utc) - timedelta(days=self.CLEANUP_DAYS)

        tables = [
            "chat_log",
            "skill_trace",
            "feedback",
            "route_log",
            "system_metric",
        ]

        for table in tables:
            # 1. 热数据 → 冷数据转存
            await self._archive_to_cold(table, cutoff_hot)

            # 2. 超期清理
            await self._delete_old(table, cutoff_cleanup)

        # 3. VACUUM 释放空间
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, lambda: self._conn.execute("VACUUM"))

        logger.info("[EvolutionStore] Cleanup completed")

    async def get_stats(self) -> dict[str, Any]:
        """返回存储统计。"""
        tables = [
            "chat_log",
            "skill_trace",
            "feedback",
            "route_log",
            "system_metric",
        ]
        stats = {}
        for table in tables:
            count = await self.count(table)
            stats[table] = count

        db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        cold_size = sum(
            f.stat().st_size for f in self._cold_dir.glob("*.jsonl.gz")
        ) if self._cold_dir.exists() else 0

        return {
            "records_by_table": stats,
            "total_writes": self._write_count,
            "db_size_bytes": db_size,
            "cold_storage_bytes": cold_size,
            "db_path": str(self._db_path),
        }

    def _insert_batch_sync(self, records: list[dict[str, Any]]) -> None:
        """同步批量插入（在线程池中运行）。"""
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in records:
            grouped[rec["table"]].append(rec["data"])

        cursor = self._conn.cursor()
        try:
            for table, data_list in grouped.items():
                if not data_list:
                    continue

                # 防御性校验：表名白名单
                self._validate_table(table)

                # 构建 INSERT 语句
                columns = list(data_list[0].keys())
                # 防御性校验：列名只允许字母、数字、下划线
                for col in columns:
                    if not col.replace("_", "").isalnum():
                        raise ValueError(f"Invalid column name: {col!r}")
                placeholders = ",".join(["?"] * len(columns))
                sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"

                values = [
                    tuple(
                        json.dumps(d.get(c, None)) if isinstance(d.get(c, None), (list, dict))
                        else d.get(c, None)
                        for c in columns
                    )
                    for d in data_list
                ]
                cursor.executemany(sql, values)

            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            raise e
        finally:
            cursor.close()

    async def _archive_to_cold(self, table: str, cutoff: datetime) -> None:
        """将热区老数据转存到冷存储。"""
        self._validate_table(table)
        rows = await self.query(table, end=cutoff, limit=10000)
        if not rows:
            return

        date_str = cutoff.strftime("%Y%m%d")
        cold_file = self._cold_dir / f"{table}_{date_str}.jsonl.gz"

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._write_cold_file(cold_file, rows),
        )

        # 删除已归档数据
        sql = f"DELETE FROM {table} WHERE timestamp <= ?"
        await loop.run_in_executor(self._executor, lambda: self._conn.execute(sql, (cutoff.isoformat(),)))
        self._conn.commit()

        logger.info(f"[EvolutionStore] Archived {len(rows)} records to {cold_file}")

    def _write_cold_file(self, path: Path, rows: list[dict[str, Any]]) -> None:
        """同步写入 gzip 压缩的 JSONL。"""
        with gzip.open(path, "at", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def _delete_old(self, table: str, cutoff: datetime) -> None:
        """删除超期冷数据。"""
        self._validate_table(table)
        loop = asyncio.get_running_loop()
        sql = f"DELETE FROM {table} WHERE timestamp <= ?"
        await loop.run_in_executor(self._executor, lambda: self._conn.execute(sql, (cutoff.isoformat(),)))
        self._conn.commit()

    async def _create_tables(self) -> None:
        """创建表结构。"""
        schemas = {
            "chat_log": """
                CREATE TABLE IF NOT EXISTS chat_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    query TEXT,
                    reply TEXT,
                    retrieved_docs TEXT,  -- JSON array
                    latency_ms INTEGER,
                    timestamp TEXT NOT NULL,
                    feedback_type TEXT,
                    metadata TEXT  -- JSON
                );
                CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_log(session_id);
                CREATE INDEX IF NOT EXISTS idx_chat_time ON chat_log(timestamp);
            """,
            "skill_trace": """
                CREATE TABLE IF NOT EXISTS skill_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    session_id TEXT,
                    skill_id TEXT NOT NULL,
                    skill_sequence TEXT,  -- JSON array
                    input_data TEXT,  -- JSON
                    output_data TEXT,  -- JSON
                    success INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    latency_ms INTEGER,
                    timestamp TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    user_feedback TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_trace_session ON skill_trace(session_id);
                CREATE INDEX IF NOT EXISTS idx_trace_time ON skill_trace(timestamp);
            """,
            "feedback": """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    feedback_type TEXT NOT NULL,
                    comment TEXT,
                    timestamp TEXT NOT NULL,
                    source TEXT DEFAULT 'manual'
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_time ON feedback(timestamp);
            """,
            "route_log": """
                CREATE TABLE IF NOT EXISTS route_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT,
                    top_k INTEGER,
                    bm25_weight REAL,
                    vector_weight REAL,
                    retrieved_count INTEGER,
                    timestamp TEXT NOT NULL,
                    rerank_threshold REAL,
                    final_doc_ids TEXT  -- JSON array
                );
                CREATE INDEX IF NOT EXISTS idx_route_time ON route_log(timestamp);
            """,
            "system_metric": """
                CREATE TABLE IF NOT EXISTS system_metric (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cpu_percent REAL,
                    memory_percent REAL,
                    gpu_util REAL,
                    gpu_mem_percent REAL,
                    active_sessions INTEGER,
                    qps REAL,
                    timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metric_time ON system_metric(timestamp);
            """,
            "experiment_log": """
                CREATE TABLE IF NOT EXISTS experiment_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    param_name TEXT NOT NULL,
                    control_value TEXT,
                    treatment_value TEXT,
                    status TEXT NOT NULL,
                    result TEXT,  -- JSON
                    started_at TEXT,
                    ended_at TEXT,
                    reviewed_by TEXT,
                    review_comment TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_exp_id ON experiment_log(experiment_id);
            """,
            "template_log": """
                CREATE TABLE IF NOT EXISTS template_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id TEXT NOT NULL,
                    name TEXT,
                    intent TEXT,
                    trigger_conditions TEXT,  -- JSON
                    skill_sequence TEXT,  -- JSON
                    status TEXT NOT NULL,
                    rollout_percent REAL DEFAULT 0.0,
                    activated_at TEXT,
                    disabled_at TEXT,
                    reviewer TEXT,
                    review_comment TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_template_id ON template_log(template_id);
            """,
        }

        loop = asyncio.get_running_loop()
        for table_name, schema in schemas.items():
            await loop.run_in_executor(self._executor, lambda s=schema: self._conn.executescript(s))
            logger.debug(f"[EvolutionStore] Table ensured: {table_name}")
