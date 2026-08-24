"""
EchoServe V0.1.0 — 用户/ API Key 存储抽象层

设计目标：
    提供统一的用户和 API Key 持久化接口，PostgreSQL 可用时使用 PG（多实例共享 + 事务一致性），
    PG 不可用时自动回退到 JSON 文件（与旧版行为一致）。

接口：
    - load_users() -> dict                    — 加载全部用户
    - save_users(users: dict)                  — 保存全部用户
    - load_api_keys() -> dict                  — 加载全部 API Key
    - save_api_keys(api_keys: dict)            — 保存全部 API Key
    - close()                                  — 释放资源
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("echoseve.auth.user_store")


class UserStore:
    """用户 / API Key 存储抽象基类"""

    async def load_users(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    async def save_users(self, users: Dict[str, Dict[str, Any]]) -> None:
        raise NotImplementedError

    async def load_api_keys(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    async def save_api_keys(self, api_keys: Dict[str, Dict[str, Any]]) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class JSONUserStore(UserStore):
    """
    JSON 文件存储（回退方案，与 v0.1.0 行为一致）。
    """

    def __init__(self, storage_path: Path, api_key_path: Path):
        self._storage_path = storage_path
        self._api_key_path = api_key_path

    async def load_users(self) -> Dict[str, Dict[str, Any]]:
        if not self._storage_path or not self._storage_path.exists():
            return {}
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            users = {}
            for u in data.get("users", []):
                users[u["user_id"]] = u
            logger.info(f"[JSONStore] Loaded {len(users)} users")
            return users
        except Exception as e:
            logger.error(f"[JSONStore] Failed to load users: {e}")
            return {}

    async def save_users(self, users: Dict[str, Dict[str, Any]]) -> None:
        if not self._storage_path:
            return
        data = {"users": list(users.values())}
        with open(self._storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 限制文件权限为 owner-only（防敏感凭证泄漏）
        try:
            os.chmod(self._storage_path, 0o600)
        except OSError:
            pass  # Windows 上 chmod 行为不同，忽略

    async def load_api_keys(self) -> Dict[str, Dict[str, Any]]:
        if not self._api_key_path or not self._api_key_path.exists():
            return {}
        try:
            with open(self._api_key_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            api_keys = {}
            for ak in data.get("api_keys", []):
                api_keys[ak["key_id"]] = ak
            logger.info(f"[JSONStore] Loaded {len(api_keys)} API keys")
            return api_keys
        except Exception as e:
            logger.error(f"[JSONStore] Failed to load API keys: {e}")
            return {}

    async def save_api_keys(self, api_keys: Dict[str, Dict[str, Any]]) -> None:
        if not self._api_key_path:
            return
        data = {"api_keys": list(api_keys.values())}
        with open(self._api_key_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 限制文件权限为 owner-only（防 API Key 泄漏）
        try:
            os.chmod(self._api_key_path, 0o600)
        except OSError:
            pass  # Windows 上 chmod 行为不同，忽略

    async def close(self) -> None:
        pass


class PostgresUserStore(UserStore):
    """
    PostgreSQL 存储（生产方案）。

    表结构：
        CREATE TABLE IF NOT EXISTS echoseve_users (
            user_id      TEXT PRIMARY KEY,
            username     TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'user',
            department   TEXT DEFAULT '',
            created_at   TEXT NOT NULL,
            last_login   TEXT,
            enabled      BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS echoseve_api_keys (
            key_id      TEXT PRIMARY KEY,
            key         TEXT UNIQUE NOT NULL,
            user_id     TEXT NOT NULL,
             name        TEXT DEFAULT 'default',
            created_at  TEXT NOT NULL,
            last_used   TEXT,
            enabled     BOOLEAN DEFAULT TRUE,
            rate_limit  INTEGER DEFAULT 100
        );
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "echoseve",
        user: str = "echoseve",
        password: str = "",
    ):
        self._conn_params = {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
        }
        self._pool = None
        self._connected = False

    async def _connect(self) -> bool:
        """尝试连接 PostgreSQL 并初始化表结构"""
        if self._connected and self._pool:
            return True
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                min_size=2,
                max_size=10,
                **self._conn_params,
            )
            # 初始化表结构
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS echoseve_users (
                        user_id        TEXT PRIMARY KEY,
                        username       TEXT UNIQUE NOT NULL,
                        password_hash  TEXT NOT NULL,
                        role           TEXT NOT NULL DEFAULT 'user',
                        department     TEXT DEFAULT '',
                        created_at     TEXT NOT NULL,
                        last_login     TEXT,
                        enabled        BOOLEAN DEFAULT TRUE
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS echoseve_api_keys (
                        key_id      TEXT PRIMARY KEY,
                        key         TEXT UNIQUE NOT NULL,
                        user_id     TEXT NOT NULL,
                        name        TEXT DEFAULT 'default',
                        created_at  TEXT NOT NULL,
                        last_used   TEXT,
                        enabled     BOOLEAN DEFAULT TRUE,
                        rate_limit  INTEGER DEFAULT 100
                    )
                """)
            self._connected = True
            logger.info(
                f"[PGStore] Connected to PostgreSQL "
                f"({self._conn_params['host']}:{self._conn_params['port']}/"
                f"{self._conn_params['database']})"
            )
            return True
        except ImportError:
            logger.warning("[PGStore] asyncpg not installed, run: pip install asyncpg")
            return False
        except Exception as e:
            logger.warning(f"[PGStore] Failed to connect to PostgreSQL: {e}")
            self._pool = None
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def load_users(self) -> Dict[str, Dict[str, Any]]:
        if not await self._connect():
            raise RuntimeError("PostgreSQL not connected")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, password_hash, role, department, "
                "created_at, last_login, enabled FROM echoseve_users"
            )
        users = {}
        for row in rows:
            users[row["user_id"]] = {
                "user_id": row["user_id"],
                "username": row["username"],
                "password_hash": row["password_hash"],
                "role": row["role"],
                "department": row["department"],
                "created_at": row["created_at"],
                "last_login": row["last_login"],
                "enabled": row["enabled"],
            }
        logger.info(f"[PGStore] Loaded {len(users)} users")
        return users

    async def save_users(self, users: Dict[str, Dict[str, Any]]) -> None:
        if not await self._connect():
            raise RuntimeError("PostgreSQL not connected")
        async with self._pool.acquire() as conn:
            # 使用 UPSERT 逐条写入
            for u in users.values():
                await conn.execute(
                    """
                    INSERT INTO echoseve_users
                        (user_id, username, password_hash, role, department,
                         created_at, last_login, enabled)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        department = EXCLUDED.department,
                        created_at = EXCLUDED.created_at,
                        last_login = EXCLUDED.last_login,
                        enabled = EXCLUDED.enabled
                    """,
                    u["user_id"],
                    u["username"],
                    u["password_hash"],
                    u["role"],
                    u.get("department", ""),
                    u["created_at"],
                    u.get("last_login"),
                    u.get("enabled", True),
                )

    async def load_api_keys(self) -> Dict[str, Dict[str, Any]]:
        if not await self._connect():
            raise RuntimeError("PostgreSQL not connected")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key_id, key, user_id, name, created_at, "
                "last_used, enabled, rate_limit FROM echoseve_api_keys"
            )
        api_keys = {}
        for row in rows:
            api_keys[row["key_id"]] = {
                "key_id": row["key_id"],
                "key": row["key"],
                "user_id": row["user_id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "last_used": row["last_used"],
                "enabled": row["enabled"],
                "rate_limit": row["rate_limit"],
            }
        logger.info(f"[PGStore] Loaded {len(api_keys)} API keys")
        return api_keys

    async def save_api_keys(self, api_keys: Dict[str, Dict[str, Any]]) -> None:
        if not await self._connect():
            raise RuntimeError("PostgreSQL not connected")
        async with self._pool.acquire() as conn:
            for ak in api_keys.values():
                await conn.execute(
                    """
                    INSERT INTO echoseve_api_keys
                        (key_id, key, user_id, name, created_at,
                         last_used, enabled, rate_limit)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (key_id) DO UPDATE SET
                        key = EXCLUDED.key,
                        user_id = EXCLUDED.user_id,
                        name = EXCLUDED.name,
                        created_at = EXCLUDED.created_at,
                        last_used = EXCLUDED.last_used,
                        enabled = EXCLUDED.enabled,
                        rate_limit = EXCLUDED.rate_limit
                    """,
                    ak["key_id"],
                    ak["key"],
                    ak["user_id"],
                    ak.get("name", "default"),
                    ak["created_at"],
                    ak.get("last_used"),
                    ak.get("enabled", True),
                    ak.get("rate_limit", 100),
                )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            self._connected = False
            logger.info("[PGStore] Connection pool closed")


def create_user_store(
    pg_config: Optional[Dict[str, Any]] = None,
    storage_path: Optional[Path] = None,
    api_key_path: Optional[Path] = None,
) -> UserStore:
    """
    工厂方法：优先创建 PG 存储实例（不连接），运行时自动降级到 JSON。

    Args:
        pg_config: PostgreSQL 连接参数 dict，为 None 则直接使用 JSON
        storage_path: JSON 模式的用户文件路径
        api_key_path: JSON 模式的 API Key 文件路径

    Returns:
        UserStore 实例
    """
    if pg_config and storage_path and api_key_path:
        return PostgresUserStore(**pg_config)
    if storage_path and api_key_path:
        return JSONUserStore(storage_path, api_key_path)
    raise ValueError("Either pg_config or (storage_path + api_key_path) must be provided")
