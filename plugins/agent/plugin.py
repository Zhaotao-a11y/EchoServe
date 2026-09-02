"""
EchoServe V0.3.0 - 坐席管理 + 人机转接 + 排队管理 + 满意度评价

功能:
  - 坐席状态管理(online/busy/break/offline)
  - 人机转接: AI -> 人工坐席无缝衔接
  - 排队管理: 坐席全忙时客户排队等待
  - 满意度评价: 会话结束后 1-5 星评分 + 标签 + 文字反馈
  - 坐席工作负载统计
  - 转接事件订阅(chat.completed -> 判断是否需转人工)
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

from .intelligent_handoff import IntelligentHandoffManager, SentimentAnalyzer

logger = logging.getLogger("echoserve.agent")

# ─── 常量 ──────────────────────────────────────────────

AGENT_STATUSES = ("online", "busy", "break", "offline")
HANDOFF_STATUSES = ("pending", "queued", "assigned", "active", "completed", "cancelled")
RATING_TAGS = (
    "resolved", "patient", "professional", "fast_response",
    "unresolved", "slow", "rude", "unclear",
)

# 排队超时(秒): 超时后自动告警
QUEUE_TIMEOUT_SEC = 120


def _now_ts() -> float:
    return time.time()


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class AgentPlugin(BaizePlugin):
    """坐席管理 + 人机转接插件"""

    plugin_id = "service.agent"
    plugin_name = "坐席管理"
    plugin_version = "0.3.0"
    dependencies = []

    def __init__(self):
        self._db_path: Path | None = None
        self._conn: sqlite3.Connection | None = None
        # 内存中的排队队列: {queue_id: {session_id, customer_name, ...}}
        self._queue: dict[str, dict[str, Any]] = {}
        # 坐席内存状态(快速查询): {agent_id: {status, ...}}
        self._agent_cache: dict[str, dict[str, Any]] = {}

    # ─── 生命周期 ──────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        data_dir = Path(ctx.settings.root_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "agent.db"
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._load_agent_cache()
        self.provide("agent_service", self)
        
        # Phase 1.2: 初始化智能转接管理器
        self._intelligent_handoff = IntelligentHandoffManager(self)
        self.provide("intelligent_handoff", self._intelligent_handoff)
        self.provide("sentiment_analyzer", SentimentAnalyzer())
        
        logger.info(f"[{self.plugin_id}] Initialized (db={self._db_path.name})")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        if self._conn:
            self._conn.close()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 数据库 ──────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id     TEXT PRIMARY KEY,
                agent_name   TEXT NOT NULL,
                role         TEXT DEFAULT 'agent',
                status       TEXT DEFAULT 'offline',
                max_concurrent INTEGER DEFAULT 5,
                skills       TEXT DEFAULT '[]',
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handoffs (
                id            TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL,
                customer_id   TEXT DEFAULT '',
                customer_name TEXT DEFAULT '',
                channel       TEXT DEFAULT 'web',
                reason        TEXT DEFAULT '',
                priority      TEXT DEFAULT 'medium',
                status        TEXT DEFAULT 'pending',
                assigned_agent TEXT DEFAULT '',
                queue_position INTEGER DEFAULT 0,
                created_at    REAL NOT NULL,
                assigned_at   REAL,
                completed_at  REAL,
                metadata      TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS agent_sessions (
                id            TEXT PRIMARY KEY,
                agent_id      TEXT NOT NULL,
                session_id    TEXT NOT NULL,
                handoff_id    TEXT DEFAULT '',
                status        TEXT DEFAULT 'active',
                started_at    REAL NOT NULL,
                ended_at      REAL,
                message_count INTEGER DEFAULT 0,
                metadata      TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS satisfaction_ratings (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                agent_id     TEXT DEFAULT '',
                handoff_id   TEXT DEFAULT '',
                rating       INTEGER NOT NULL,
                tags         TEXT DEFAULT '[]',
                comment      TEXT DEFAULT '',
                customer_id  TEXT DEFAULT '',
                created_at   REAL NOT NULL,
                metadata     TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_handoffs_status   ON handoffs(status);
            CREATE INDEX IF NOT EXISTS idx_handoffs_session   ON handoffs(session_id);
            CREATE INDEX IF NOT EXISTS idx_agents_status      ON agents(status);
            CREATE INDEX IF NOT EXISTS idx_sessions_agent     ON agent_sessions(agent_id);
            CREATE INDEX IF NOT EXISTS idx_ratings_session    ON satisfaction_ratings(session_id);
        """)
        self._conn.commit()

    def _load_agent_cache(self) -> None:
        rows = self._conn.execute("SELECT * FROM agents").fetchall()
        for row in rows:
            d = dict(row)
            d["skills"] = json.loads(d.get("skills") or "[]")
            self._agent_cache[d["agent_id"]] = d

    # ─── 坐席管理 ──────────────────────────────────────────

    def register_agent(
        self,
        agent_id: str,
        agent_name: str,
        role: str = "agent",
        max_concurrent: int = 5,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        now = _now_ts()
        skills_json = json.dumps(skills or [])
        self._conn.execute(
            """INSERT OR REPLACE INTO agents
               (agent_id, agent_name, role, status, max_concurrent,
                skills, created_at, updated_at)
               VALUES (?, ?, ?, 'offline', ?, ?, ?, ?)""",
            (agent_id, agent_name, role, max_concurrent,
             skills_json, now, now),
        )
        self._conn.commit()
        self._agent_cache[agent_id] = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "role": role,
            "status": "offline",
            "max_concurrent": max_concurrent,
            "skills": skills or [],
            "created_at": now,
            "updated_at": now,
        }
        logger.info(f"[{self.plugin_id}] Agent registered: {agent_id} ({agent_name})")
        return self._agent_cache[agent_id]

    def set_agent_status(self, agent_id: str, status: str) -> dict[str, Any] | None:
        if status not in AGENT_STATUSES:
            return None
        now = _now_ts()
        self._conn.execute(
            "UPDATE agents SET status = ?, updated_at = ? WHERE agent_id = ?",
            (status, now, agent_id),
        )
        self._conn.commit()
        if agent_id in self._agent_cache:
            self._agent_cache[agent_id]["status"] = status
            self._agent_cache[agent_id]["updated_at"] = now
            return self._agent_cache[agent_id]
        return None

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self._agent_cache.get(agent_id)

    def list_agents(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return [a for a in self._agent_cache.values() if a["status"] == status]
        return list(self._agent_cache.values())

    def get_available_agent(self) -> dict[str, Any] | None:
        """找一个空闲且在线的坐席(当前活跃会话数 < max_concurrent)"""
        online_agents = [
            a for a in self._agent_cache.values()
            if a["status"] == "online"
        ]
        if not online_agents:
            return None

        # 统计每个坐席当前活跃会话数
        active_counts: dict[str, int] = {}
        for a in online_agents:
            count = self._conn.execute(
                """SELECT COUNT(*) FROM agent_sessions
                   WHERE agent_id = ? AND status = 'active'""",
                (a["agent_id"],),
            ).fetchone()[0]
            active_counts[a["agent_id"]] = count

        # 选负载最低的
        best = min(
            online_agents,
            key=lambda a: active_counts.get(a["agent_id"], 0),
        )
        if active_counts.get(best["agent_id"], 0) < best["max_concurrent"]:
            return best
        return None

    # ─── 人机转接 ──────────────────────────────────────────

    def request_handoff(
        self,
        session_id: str,
        customer_id: str = "",
        customer_name: str = "",
        channel: str = "web",
        reason: str = "",
        priority: str = "medium",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hid = f"HF-{uuid.uuid4().hex[:8].upper()}"
        now = _now_ts()

        # 尝试直接分配
        agent = self.get_available_agent()
        queue_pos = 0
        if agent:
            status = "assigned"
            assigned_agent = agent["agent_id"]
            assigned_at = now
        else:
            status = "queued"
            assigned_agent = ""
            assigned_at = None
            queue_pos = len(self._queue) + 1

        self._conn.execute(
            """INSERT INTO handoffs
               (id, session_id, customer_id, customer_name, channel,
                reason, priority, status, assigned_agent, queue_position,
                created_at, assigned_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hid, session_id, customer_id, customer_name, channel,
                reason, priority, status, assigned_agent,
                queue_pos if status == "queued" else 0,
                now, assigned_at, json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()

        if status == "queued":
            self._queue[hid] = {
                "session_id": session_id,
                "customer_name": customer_name,
                "queued_at": now,
            }

        result = self.get_handoff(hid)
        logger.info(
            f"[{self.plugin_id}] Handoff {hid}: {status}"
            f"{' -> ' + assigned_agent if assigned_agent else ''}"
        )
        return result

    def get_handoff(self, handoff_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["created_at_iso"] = _ts_to_iso(d["created_at"])
        if d.get("assigned_at"):
            d["assigned_at_iso"] = _ts_to_iso(d["assigned_at"])
        if d.get("completed_at"):
            d["completed_at_iso"] = _ts_to_iso(d["completed_at"])
        return d

    def list_handoffs(
        self,
        status: str | None = None,
        agent_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if agent_id:
            conditions.append("assigned_agent = ?")
            params.append(agent_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM handoffs {where}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""SELECT * FROM handoffs {where}
                ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        items = []
        for row in rows:
            d = dict(row)
            d["metadata"] = json.loads(d.get("metadata") or "{}")
            d["created_at_iso"] = _ts_to_iso(d["created_at"])
            items.append(d)

        return {"total": total, "offset": offset, "limit": limit, "items": items}

    def assign_handoff(self, handoff_id: str, agent_id: str) -> dict[str, Any] | None:
        now = _now_ts()
        self._conn.execute(
            """UPDATE handoffs
               SET status = 'assigned', assigned_agent = ?,
                   assigned_at = ?
               WHERE id = ? AND status IN ('pending', 'queued')""",
            (agent_id, now, handoff_id),
        )
        self._conn.commit()
        # 从排队队列移除
        self._queue.pop(handoff_id, None)
        return self.get_handoff(handoff_id)

    def complete_handoff(self, handoff_id: str) -> dict[str, Any] | None:
        now = _now_ts()
        self._conn.execute(
            """UPDATE handoffs
               SET status = 'completed', completed_at = ?
               WHERE id = ? AND status IN ('assigned', 'active')""",
            (now, handoff_id),
        )
        self._conn.commit()
        self._queue.pop(handoff_id, None)
        return self.get_handoff(handoff_id)

    def cancel_handoff(self, handoff_id: str) -> dict[str, Any] | None:
        now = _now_ts()
        self._conn.execute(
            """UPDATE handoffs
               SET status = 'cancelled', completed_at = ?
               WHERE id = ? AND status NOT IN ('completed', 'cancelled')""",
            (now, handoff_id),
        )
        self._conn.commit()
        self._queue.pop(handoff_id, None)
        return self.get_handoff(handoff_id)

    # ─── 排队管理 ──────────────────────────────────────────

    def get_queue_status(self) -> dict[str, Any]:
        waiting = [
            {
                "handoff_id": hid,
                "session_id": v["session_id"],
                "customer_name": v["customer_name"],
                "waiting_sec": round(_now_ts() - v["queued_at"], 1),
            }
            for hid, v in self._queue.items()
        ]
        # 检查超时
        now = _now_ts()
        timed_out = [
            w for w in waiting if w["waiting_sec"] > QUEUE_TIMEOUT_SEC
        ]
        return {
            "queue_length": len(self._queue),
            "waiting_list": waiting,
            "timed_out_count": len(timed_out),
        }

    def process_queue(self) -> dict[str, Any]:
        """尝试将排队中的客户分配给空闲坐席"""
        assigned: list[str] = []
        for hid in list(self._queue.keys()):
            agent = self.get_available_agent()
            if agent:
                self.assign_handoff(hid, agent["agent_id"])
                assigned.append(hid)
            else:
                break

        return {
            "assigned_count": len(assigned),
            "assigned_handoff_ids": assigned,
            "remaining_queue": len(self._queue),
        }

    # ─── 坐席会话 ──────────────────────────────────────────

    def start_agent_session(
        self,
        agent_id: str,
        session_id: str,
        handoff_id: str = "",
    ) -> dict[str, Any]:
        sid = f"AS-{uuid.uuid4().hex[:8].upper()}"
        now = _now_ts()
        self._conn.execute(
            """INSERT INTO agent_sessions
               (id, agent_id, session_id, handoff_id, status,
                started_at, message_count, metadata)
               VALUES (?, ?, ?, ?, 'active', ?, 0, '{}')""",
            (sid, agent_id, session_id, handoff_id, now),
        )
        # 如果有 handoff, 更新状态为 active
        if handoff_id:
            self._conn.execute(
                "UPDATE handoffs SET status = 'active' WHERE id = ?",
                (handoff_id,),
            )
        self._conn.commit()
        return {
            "id": sid,
            "agent_id": agent_id,
            "session_id": session_id,
            "handoff_id": handoff_id,
            "status": "active",
            "started_at": now,
            "started_at_iso": _ts_to_iso(now),
        }

    def end_agent_session(self, session_id: str) -> bool:
        now = _now_ts()
        cur = self._conn.execute(
            """UPDATE agent_sessions
               SET status = 'ended', ended_at = ?
               WHERE session_id = ? AND status = 'active'""",
            (now, session_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def increment_message_count(self, session_id: str) -> None:
        self._conn.execute(
            """UPDATE agent_sessions
               SET message_count = message_count + 1
               WHERE session_id = ? AND status = 'active'""",
            (session_id,),
        )
        self._conn.commit()

    # ─── 满意度评价 ──────────────────────────────────────────

    def submit_rating(
        self,
        session_id: str,
        rating: int,
        tags: list[str] | None = None,
        comment: str = "",
        agent_id: str = "",
        handoff_id: str = "",
        customer_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= rating <= 5:
            raise ValueError("rating must be 1-5")

        rid = f"SR-{uuid.uuid4().hex[:8].upper()}"
        now = _now_ts()
        self._conn.execute(
            """INSERT INTO satisfaction_ratings
               (id, session_id, agent_id, handoff_id, rating,
                tags, comment, customer_id, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid, session_id, agent_id, handoff_id, rating,
                json.dumps(tags or []), comment, customer_id, now,
                json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()
        logger.info(
            f"[{self.plugin_id}] Rating submitted: session={session_id} "
            f"rating={rating} stars"
        )
        return {
            "id": rid,
            "session_id": session_id,
            "rating": rating,
            "tags": tags or [],
            "comment": comment,
            "created_at": now,
            "created_at_iso": _ts_to_iso(now),
        }

    def get_rating(self, session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM satisfaction_ratings WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["created_at_iso"] = _ts_to_iso(d["created_at"])
        return d

    def get_rating_stats(
        self,
        agent_id: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        cutoff = _now_ts() - days * 86400
        conditions = ["created_at >= ?"]
        params: list[Any] = [cutoff]
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        where = f"WHERE {' AND '.join(conditions)}"

        rows = self._conn.execute(
            f"""SELECT rating, COUNT(*) as cnt
                FROM satisfaction_ratings {where}
                GROUP BY rating ORDER BY rating DESC""",
            params,
        ).fetchall()

        distribution = {r["rating"]: r["cnt"] for r in rows}
        total = sum(distribution.values())

        avg = 0.0
        if total:
            avg = round(
                sum(k * v for k, v in distribution.items()) / total, 2
            )

        return {
            "total_ratings": total,
            "average": avg,
            "distribution": distribution,
            "period_days": days,
        }

    # ─── 坐席工作负载统计 ──────────────────────────────────

    def get_agent_workload(self, agent_id: str) -> dict[str, Any]:
        active_count = self._conn.execute(
            """SELECT COUNT(*) FROM agent_sessions
               WHERE agent_id = ? AND status = 'active'""",
            (agent_id,),
        ).fetchone()[0]

        total_sessions = self._conn.execute(
            """SELECT COUNT(*) FROM agent_sessions
               WHERE agent_id = ?""",
            (agent_id,),
        ).fetchone()[0]

        total_messages = self._conn.execute(
            """SELECT COALESCE(SUM(message_count), 0) FROM agent_sessions
               WHERE agent_id = ?""",
            (agent_id,),
        ).fetchone()[0]

        ratings = self.get_rating_stats(agent_id)

        return {
            "agent_id": agent_id,
            "active_sessions": active_count,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "rating_stats": ratings,
        }

    def get_dashboard_stats(self) -> dict[str, Any]:
        """坐席看板统计"""
        now = _now_ts()

        # 各状态坐席数
        status_counts: dict[str, int] = {}
        for s in AGENT_STATUSES:
            status_counts[s] = len([
                a for a in self._agent_cache.values() if a["status"] == s
            ])

        # 活跃会话总数
        active_sessions = self._conn.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE status = 'active'"
        ).fetchone()[0]

        # 排队数
        queue_len = len(self._queue)

        # 今日转接数
        today_start = now - (now % 86400)
        today_handoffs = self._conn.execute(
            "SELECT COUNT(*) FROM handoffs WHERE created_at >= ?",
            (today_start,),
        ).fetchone()[0]

        # 今日满意度
        ratings_today = self._conn.execute(
            """SELECT rating, COUNT(*) as cnt
               FROM satisfaction_ratings
               WHERE created_at >= ?
               GROUP BY rating""",
            (today_start,),
        ).fetchall()
        rating_dist = {r["rating"]: r["cnt"] for r in ratings_today}
        rating_total = sum(rating_dist.values())
        rating_avg = 0.0
        if rating_total:
            rating_avg = round(
                sum(k * v for k, v in rating_dist.items()) / rating_total, 2
            )

        return {
            "agent_status_counts": status_counts,
            "active_sessions": active_sessions,
            "queue_length": queue_len,
            "today_handoffs": today_handoffs,
            "today_rating": {
                "total": rating_total,
                "average": rating_avg,
                "distribution": rating_dist,
            },
        }
