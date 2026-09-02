"""
EchoServe V0.3.0 - 工单系统插件

功能:
  - 工单全生命周期管理(创建/分配/流转/关闭)
  - 优先级分级(low/medium/high/urgent)
  - SLA 超时预警
  - 工单关联会话ID, 支持追溯
  - 分类标签管理
  - 工单评论与操作日志
"""
from __future__ import annotations

import json
import sqlite3
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

logger = logging.getLogger("echoserve.ticket")

# ─── 常量 ──────────────────────────────────────────────

TICKET_STATUSES = ("open", "in_progress", "waiting", "resolved", "closed")
TICKET_PRIORITIES = ("low", "medium", "high", "urgent")

# SLA 超时阈值(秒): 按优先级
SLA_THRESHOLDS = {
    "urgent": 2 * 3600,    # 2h
    "high": 4 * 3600,      # 4h
    "medium": 24 * 3600,   # 24h
    "low": 48 * 3600,      # 48h
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


class TicketPlugin(BaizePlugin):
    """工单系统插件"""

    plugin_id = "service.ticket"
    plugin_name = "工单系统"
    plugin_version = "0.3.0"
    dependencies = []

    def __init__(self):
        self._db_path: Path | None = None
        self._conn: sqlite3.Connection | None = None

    # ─── 生命周期 ──────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        data_dir = Path(ctx.settings.root_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "ticket.db"
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self.provide("ticket_service", self)
        logger.info(f"[{self.plugin_id}] Initialized (db={self._db_path.name})")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        if self._conn:
            self._conn.close()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 数据库 ──────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                description     TEXT DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'open',
                priority        TEXT NOT NULL DEFAULT 'medium',
                category        TEXT DEFAULT 'general',
                session_id      TEXT DEFAULT '',
                customer_id     TEXT DEFAULT '',
                customer_name   TEXT DEFAULT '',
                channel         TEXT DEFAULT 'web',
                assigned_agent  TEXT DEFAULT '',
                created_by      TEXT NOT NULL DEFAULT 'system',
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL,
                resolved_at     REAL,
                closed_at       REAL,
                tags            TEXT DEFAULT '[]',
                metadata        TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS ticket_comments (
                id          TEXT PRIMARY KEY,
                ticket_id   TEXT NOT NULL,
                author_id   TEXT NOT NULL,
                author_name TEXT DEFAULT '',
                content     TEXT NOT NULL,
                is_internal INTEGER DEFAULT 0,
                created_at  REAL NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_status     ON tickets(status);
            CREATE INDEX IF NOT EXISTS idx_tickets_priority   ON tickets(priority);
            CREATE INDEX IF NOT EXISTS idx_tickets_assigned  ON tickets(assigned_agent);
            CREATE INDEX IF NOT EXISTS idx_tickets_created    ON tickets(created_at);
            CREATE INDEX IF NOT EXISTS idx_comments_ticket    ON ticket_comments(ticket_id);
        """)
        self._conn.commit()

    # ─── 工单 CRUD ──────────────────────────────────────────

    def create_ticket(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        category: str = "general",
        session_id: str = "",
        customer_id: str = "",
        customer_name: str = "",
        channel: str = "web",
        assigned_agent: str = "",
        created_by: str = "system",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tid = f"TK-{uuid.uuid4().hex[:8].upper()}"
        now = _now_ts()
        self._conn.execute(
            """INSERT INTO tickets
               (id, title, description, status, priority, category,
                session_id, customer_id, customer_name, channel,
                assigned_agent, created_by, created_at, updated_at,
                tags, metadata)
               VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid, title, description, priority, category,
                session_id, customer_id, customer_name, channel,
                assigned_agent, created_by, now, now,
                json.dumps(tags or []), json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()
        logger.info(f"[{self.plugin_id}] Ticket created: {tid} ({title[:40]})")
        return self.get_ticket(tid)

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def update_ticket(
        self,
        ticket_id: str,
        **kwargs,
    ) -> dict[str, Any] | None:
        allowed = {
            "title", "description", "status", "priority", "category",
            "assigned_agent", "tags", "metadata",
        }
        updates: list[str] = []
        values: list[Any] = []
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k in ("tags",):
                v = json.dumps(v)
            if k in ("metadata",):
                v = json.dumps(v)
            updates.append(f"{k} = ?")
            values.append(v)

        if not updates:
            return self.get_ticket(ticket_id)

        now = _now_ts()
        updates.append("updated_at = ?")
        values.append(now)
        values.append(ticket_id)

        self._conn.execute(
            f"UPDATE tickets SET {', '.join(updates)} WHERE id = ?", values
        )

        # 状态变更时记录时间戳
        if "status" in kwargs:
            status = kwargs["status"]
            if status == "resolved":
                self._conn.execute(
                    "UPDATE tickets SET resolved_at = ? WHERE id = ?",
                    (now, ticket_id),
                )
            elif status == "closed":
                self._conn.execute(
                    "UPDATE tickets SET closed_at = ? WHERE id = ?",
                    (now, ticket_id),
                )

        self._conn.commit()
        return self.get_ticket(ticket_id)

    def list_tickets(
        self,
        status: str | None = None,
        priority: str | None = None,
        assigned_agent: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        if assigned_agent:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if keyword:
            conditions.append("(title LIKE ? OR description LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM tickets {where}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""SELECT * FROM tickets {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [self._row_to_dict(r) for r in rows],
        }

    def delete_ticket(self, ticket_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM tickets WHERE id = ?", (ticket_id,)
        )
        self._conn.execute(
            "DELETE FROM ticket_comments WHERE ticket_id = ?", (ticket_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ─── 工单评论 ──────────────────────────────────────────

    def add_comment(
        self,
        ticket_id: str,
        author_id: str,
        content: str,
        author_name: str = "",
        is_internal: bool = False,
    ) -> dict[str, Any]:
        cid = f"CM-{uuid.uuid4().hex[:8].upper()}"
        now = _now_ts()
        self._conn.execute(
            """INSERT INTO ticket_comments
               (id, ticket_id, author_id, author_name, content, is_internal, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cid, ticket_id, author_id, author_name, content,
             1 if is_internal else 0, now),
        )
        # 更新工单 updated_at
        self._conn.execute(
            "UPDATE tickets SET updated_at = ? WHERE id = ?",
            (now, ticket_id),
        )
        self._conn.commit()
        return {
            "id": cid,
            "ticket_id": ticket_id,
            "author_id": author_id,
            "author_name": author_name,
            "content": content,
            "is_internal": is_internal,
            "created_at": now,
        }

    def get_comments(self, ticket_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM ticket_comments
               WHERE ticket_id = ?
               ORDER BY created_at ASC""",
            (ticket_id,),
        ).fetchall()
        return [self._comment_to_dict(r) for r in rows]

    # ─── SLA / 统计 ──────────────────────────────────────────

    def get_sla_breached(self) -> list[dict[str, Any]]:
        """返回已超 SLA 但未关闭的工单"""
        now = _now_ts()
        rows = self._conn.execute(
            """SELECT * FROM tickets
               WHERE status NOT IN ('resolved', 'closed')
               ORDER BY created_at ASC""",
        ).fetchall()

        breached: list[dict[str, Any]] = []
        for row in rows:
            d = self._row_to_dict(row)
            threshold = SLA_THRESHOLDS.get(d["priority"], SLA_THRESHOLDS["medium"])
            elapsed = now - d["created_at"]
            if elapsed > threshold:
                d["sla_threshold_sec"] = threshold
                d["sla_elapsed_sec"] = round(elapsed, 1)
                d["sla_overdue_sec"] = round(elapsed - threshold, 1)
                breached.append(d)

        return breached

    def get_stats(self) -> dict[str, Any]:
        """工单统计看板"""
        now = _now_ts()

        total = self._conn.execute(
            "SELECT COUNT(*) FROM tickets"
        ).fetchone()[0]

        by_status: dict[str, int] = {}
        for s in TICKET_STATUSES:
            by_status[s] = self._conn.execute(
                "SELECT COUNT(*) FROM tickets WHERE status = ?", (s,)
            ).fetchone()[0]

        by_priority: dict[str, int] = {}
        for p in TICKET_PRIORITIES:
            by_priority[p] = self._conn.execute(
                "SELECT COUNT(*) FROM tickets WHERE priority = ?", (p,)
            ).fetchone()[0]

        # 今日新建
        today_start = now - (now % 86400)
        today_created = self._conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE created_at >= ?", (today_start,)
        ).fetchone()[0]

        # SLA 超时数
        sla_breached = len(self.get_sla_breached())

        # 平均解决时间(小时)
        resolved_rows = self._conn.execute(
            """SELECT created_at, resolved_at FROM tickets
               WHERE resolved_at IS NOT NULL""",
        ).fetchall()
        avg_resolve_h = 0.0
        if resolved_rows:
            total_secs = sum(
                r["resolved_at"] - r["created_at"] for r in resolved_rows
            )
            avg_resolve_h = round(total_secs / len(resolved_rows) / 3600, 2)

        return {
            "total": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "today_created": today_created,
            "sla_breached": sla_breached,
            "avg_resolve_hours": avg_resolve_h,
        }

    # ─── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["created_at_iso"] = datetime.fromtimestamp(
            d["created_at"], tz=timezone.utc
        ).isoformat()
        d["updated_at_iso"] = datetime.fromtimestamp(
            d["updated_at"], tz=timezone.utc
        ).isoformat()
        return d

    @staticmethod
    def _comment_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["is_internal"] = bool(d.get("is_internal"))
        d["created_at_iso"] = datetime.fromtimestamp(
            d["created_at"], tz=timezone.utc
        ).isoformat()
        return d
