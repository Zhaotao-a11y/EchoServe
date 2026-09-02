"""
EchoServe V0.3.0 - 快捷回复插件

功能:
  - 模板管理(CRUD)
  - 分类管理
  - 模糊搜索(关键词匹配)
  - 使用次数统计
  - 变量占位符({customer_name}, {order_id} 等)
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

logger = logging.getLogger("echoserve.quick_reply")


def _now_ts() -> float:
    return time.time()


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class QuickReplyPlugin(BaizePlugin):
    """快捷回复模板插件"""

    plugin_id = "service.quick_reply"
    plugin_name = "快捷回复"
    plugin_version = "0.3.0"
    dependencies = []

    def __init__(self):
        self._db_path: Path | None = None
        self._conn: sqlite3.Connection | None = None

    # ─── 生命周期 ──────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        data_dir = Path(ctx.settings.root_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "quick_reply.db"
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._seed_defaults()
        self.provide("quick_reply_service", self)
        logger.info(f"[{self.plugin_id}] Initialized (db={self._db_path.name})")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        if self._conn:
            self._conn.close()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 数据库 ──────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS quick_replies (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                content      TEXT NOT NULL,
                category     TEXT DEFAULT 'general',
                shortcut     TEXT DEFAULT '',
                variables    TEXT DEFAULT '[]',
                created_by   TEXT DEFAULT 'system',
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL,
                use_count    INTEGER DEFAULT 0,
                is_active    INTEGER DEFAULT 1,
                sort_order   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reply_categories (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                description  TEXT DEFAULT '',
                sort_order   INTEGER DEFAULT 0,
                created_at   REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_qr_category  ON quick_replies(category);
            CREATE INDEX IF NOT EXISTS idx_qr_active     ON quick_replies(is_active);
            CREATE INDEX IF NOT EXISTS idx_qr_shortcut   ON quick_replies(shortcut);
        """)
        self._conn.commit()

    def _seed_defaults(self) -> None:
        """预置常用分类和模板"""
        now = _now_ts()
        categories = [
            ("general", "通用", "", 0),
            ("greeting", "问候", "", 1),
            ("order", "订单", "", 2),
            ("refund", "退款", "", 3),
            ("technical", "技术", "", 4),
            ("closing", "结束语", "", 5),
        ]
        for cid, name, desc, order in categories:
            self._conn.execute(
                """INSERT OR IGNORE INTO reply_categories
                   (id, name, description, sort_order, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (cid, name, desc, order, now),
            )

        defaults = [
            ("greeting", "欢迎语", "您好，欢迎咨询，请问有什么可以帮您？", "", []),
            ("greeting", "排队提示", "当前咨询人数较多，请稍候，客服马上为您服务。", "", []),
            ("order", "订单查询", "请提供您的订单号，我帮您查询订单状态。", "", ["order_id"]),
            ("refund", "退款说明", "您的退款申请已受理，预计1-3个工作日到账，请留意查收。", "", []),
            ("technical", "技术排查", "请您尝试清除浏览器缓存后重新操作，如仍有问题请截图反馈。", "", []),
            ("closing", "结束语", "感谢您的咨询，如有其他问题随时联系我们。祝您生活愉快！", "", []),
        ]

        for cat, title, content, shortcut, variables in defaults:
            qr_id = f"QR-{uuid.uuid4().hex[:8].upper()}"
            self._conn.execute(
                """INSERT OR IGNORE INTO quick_replies
                   (id, title, content, category, shortcut, variables,
                    created_by, created_at, updated_at, is_active, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, 'system', ?, ?, 1, 0)""",
                (qr_id, title, content, cat, shortcut,
                 json.dumps(variables), now, now),
            )

        self._conn.commit()

    # ─── 模板 CRUD ──────────────────────────────────────────

    def create_reply(
        self,
        title: str,
        content: str,
        category: str = "general",
        shortcut: str = "",
        variables: list[str] | None = None,
        created_by: str = "system",
        sort_order: int = 0,
    ) -> dict[str, Any]:
        rid = f"QR-{uuid.uuid4().hex[:8].upper()}"
        now = _now_ts()
        self._conn.execute(
            """INSERT INTO quick_replies
               (id, title, content, category, shortcut, variables,
                created_by, created_at, updated_at, is_active, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (rid, title, content, category, shortcut,
             json.dumps(variables or []), created_by, now, now, sort_order),
        )
        self._conn.commit()
        logger.info(f"[{self.plugin_id}] Reply created: {rid} ({title})")
        return self.get_reply(rid)

    def get_reply(self, reply_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM quick_replies WHERE id = ?", (reply_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def update_reply(
        self,
        reply_id: str,
        **kwargs,
    ) -> dict[str, Any] | None:
        allowed = {"title", "content", "category", "shortcut",
                    "variables", "is_active", "sort_order"}
        updates: list[str] = []
        values: list[Any] = []
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k == "variables":
                v = json.dumps(v)
            if k == "is_active":
                v = 1 if v else 0
            updates.append(f"{k} = ?")
            values.append(v)

        if not updates:
            return self.get_reply(reply_id)

        now = _now_ts()
        updates.append("updated_at = ?")
        values.append(now)
        values.append(reply_id)

        self._conn.execute(
            f"UPDATE quick_replies SET {', '.join(updates)} WHERE id = ?", values
        )
        self._conn.commit()
        return self.get_reply(reply_id)

    def delete_reply(self, reply_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM quick_replies WHERE id = ?", (reply_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_replies(
        self,
        category: str | None = None,
        keyword: str | None = None,
        active_only: bool = True,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        params: list[Any] = []

        if active_only:
            conditions.append("is_active = 1")
        if category:
            conditions.append("category = ?")
            params.append(category)
        if keyword:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM quick_replies {where}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""SELECT * FROM quick_replies {where}
                ORDER BY sort_order ASC, created_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [self._row_to_dict(r) for r in rows],
        }

    # ─── 搜索 + 使用 ──────────────────────────────────────────

    def search_replies(
        self,
        keyword: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """模糊搜索快捷回复"""
        rows = self._conn.execute(
            """SELECT * FROM quick_replies
               WHERE is_active = 1
                 AND (title LIKE ? OR content LIKE ? OR shortcut LIKE ?)
               ORDER BY use_count DESC, created_at DESC
               LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def use_reply(self, reply_id: str) -> dict[str, Any] | None:
        """标记使用一次, 返回模板内容"""
        self._conn.execute(
            "UPDATE quick_replies SET use_count = use_count + 1 WHERE id = ?",
            (reply_id,),
        )
        self._conn.commit()
        return self.get_reply(reply_id)

    def render_reply(
        self,
        reply_id: str,
        variables: dict[str, str] | None = None,
    ) -> str:
        """渲染模板, 替换变量占位符"""
        reply = self.get_reply(reply_id)
        if not reply:
            return ""
        content = reply["content"]
        if variables:
            for k, v in variables.items():
                content = content.replace(f"{{{k}}}", v)
        self.use_reply(reply_id)
        return content

    # ─── 分类管理 ──────────────────────────────────────────

    def list_categories(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM reply_categories ORDER BY sort_order ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_category_stats(self) -> list[dict[str, Any]]:
        """每个分类下的模板数"""
        rows = self._conn.execute(
            """SELECT c.id, c.name, c.sort_order,
                      COUNT(q.id) as reply_count,
                      SUM(q.use_count) as total_uses
               FROM reply_categories c
               LEFT JOIN quick_replies q
                 ON c.id = q.category AND q.is_active = 1
               GROUP BY c.id, c.name, c.sort_order
               ORDER BY c.sort_order ASC""",
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["total_uses"] = d.get("total_uses") or 0
            result.append(d)
        return result

    # ─── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["variables"] = json.loads(d.get("variables") or "[]")
        d["is_active"] = bool(d.get("is_active"))
        d["created_at_iso"] = _ts_to_iso(d["created_at"])
        d["updated_at_iso"] = _ts_to_iso(d["updated_at"])
        return d
