"""
EchoServe V0.1.0 — 会话存储抽象层

设计目标：
    提供统一的会话持久化接口，Redis 可用时使用 Redis（多实例共享 + 重启恢复），
    Redis 不可用时自动回退到内存 OrderedDict（与旧版行为一致）。

接口：
    - save_session(session_id, messages)     — 保存会话历史
    - load_session(session_id) -> list       — 加载会话历史
    - delete_session(session_id)             — 删除会话
    - list_sessions() -> list[str]           — 列出所有活跃会话
    - update_timestamp(session_id)           — 更新最后活跃时间
    - cleanup_expired(ttl) -> int            — 清理过期会话，返回清理数量
    - close()                                — 释放资源
"""
from __future__ import annotations

import json
import time
import logging
from typing import Any
from collections import OrderedDict

logger = logging.getLogger("echoserve.chat.session_store")


class SessionStore:
    """会话存储抽象基类"""

    async def save_session(self, session_id: str, messages: list[dict[str, str]]) -> None:
        raise NotImplementedError

    async def load_session(self, session_id: str) -> list[dict[str, str]]:
        raise NotImplementedError

    async def delete_session(self, session_id: str) -> bool:
        raise NotImplementedError

    async def list_sessions(self) -> list[str]:
        raise NotImplementedError

    async def update_timestamp(self, session_id: str) -> None:
        raise NotImplementedError

    async def cleanup_expired(self, ttl: int) -> int:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class MemorySessionStore(SessionStore):
    """
    内存会话存储（回退方案）。

    使用 OrderedDict 实现 LRU 淘汰，行为与 v0.1.2 一致。
    """

    def __init__(self, max_sessions: int = 1000):
        self._sessions: Ordereddict[str, list[dict[str, str]]] = OrderedDict()
        self._timestamps: dict[str, float] = {}
        self._max_sessions = max_sessions

    async def save_session(self, session_id: str, messages: list[dict[str, str]]) -> None:
        if session_id in self._sessions:
            self._sessions.pop(session_id)
        self._sessions[session_id] = list(messages)
        self._timestamps[session_id] = time.time()

        # LRU 淘汰
        while len(self._sessions) > self._max_sessions:
            oldest_id, _ = self._sessions.popitem(last=False)
            self._timestamps.pop(oldest_id, None)
            logger.warning(f"[MemoryStore] Evicted oldest session: {oldest_id}")

    async def load_session(self, session_id: str) -> list[dict[str, str]]:
        if session_id in self._sessions:
            # LRU: 移到末尾
            history = self._sessions.pop(session_id)
            self._sessions[session_id] = history
            return list(history)
        return []

    async def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._timestamps.pop(session_id, None)
            return True
        return False

    async def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def update_timestamp(self, session_id: str) -> None:
        self._timestamps[session_id] = time.time()

    async def cleanup_expired(self, ttl: int) -> int:
        now = time.time()
        expired = [
            sid for sid, ts in self._timestamps.items()
            if now - ts > ttl
        ]
        for sid in expired:
            await self.delete_session(sid)
        return len(expired)

    async def close(self) -> None:
        pass


class RedisSessionStore(SessionStore):
    """
    Redis 会话存储（生产方案）。

    会话数据结构：
        - echoseve:session:{session_id}   → JSON list (消息历史)
        - echoseve:session:ts:{session_id} → timestamp (float string)
        - echoseve:session:index          → SET (所有活跃 session_id)

    TTL 自动过期：每个会话 key 设置 TTL，到期后 Redis 自动清理。
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        key_prefix: str = "echoseve:session:",
        session_ttl: int = 1800,
    ):
        self._url = url
        self._prefix = key_prefix
        self._ttl = session_ttl
        self._redis = None
        self._connected = False

    async def _connect(self) -> bool:
        """尝试连接 Redis，成功返回 True"""
        if self._connected and self._redis:
            return True
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=5,
            )
            # Ping 测试连接
            await self._redis.ping()
            self._connected = True
            logger.info(f"[RedisStore] Connected to Redis: {self._url}")
            return True
        except ImportError:
            logger.warning("[RedisStore] redis not installed, run: pip install redis")
            return False
        except Exception as e:
            logger.warning(f"[RedisStore] Failed to connect to Redis: {e}")
            self._redis = None
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _msg_key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def _ts_key(self, session_id: str) -> str:
        return f"{self._prefix}ts:{session_id}"

    def _index_key(self) -> str:
        return f"{self._prefix}index"

    async def save_session(self, session_id: str, messages: list[dict[str, str]]) -> None:
        if not await self._connect():
            raise RuntimeError("Redis not connected")
        data = json.dumps(messages, ensure_ascii=False)
        pipe = self._redis.pipeline()
        pipe.set(self._msg_key(session_id), data, ex=self._ttl)
        pipe.set(self._ts_key(session_id), str(time.time()), ex=self._ttl)
        pipe.sadd(self._index_key(), session_id)
        pipe.expire(self._index_key(), self._ttl * 2)  # index 活久一点
        await pipe.execute()

    async def load_session(self, session_id: str) -> list[dict[str, str]]:
        if not await self._connect():
            raise RuntimeError("Redis not connected")
        data = await self._redis.get(self._msg_key(session_id))
        if not data:
            return []
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            logger.error(f"[RedisStore] Corrupted session data for {session_id}")
            return []

    async def delete_session(self, session_id: str) -> bool:
        if not await self._connect():
            raise RuntimeError("Redis not connected")
        pipe = self._redis.pipeline()
        pipe.delete(self._msg_key(session_id))
        pipe.delete(self._ts_key(session_id))
        pipe.srem(self._index_key(), session_id)
        result = await pipe.execute()
        return result[0] > 0

    async def list_sessions(self) -> list[str]:
        if not await self._connect():
            raise RuntimeError("Redis not connected")
        members = await self._redis.smembers(self._index_key())
        return list(members) if members else []

    async def update_timestamp(self, session_id: str) -> None:
        if not await self._connect():
            raise RuntimeError("Redis not connected")
        await self._redis.set(
            self._ts_key(session_id), str(time.time()), ex=self._ttl
        )

    async def cleanup_expired(self, ttl: int) -> int:
        """
        Redis 模式下 TTL 自动过期，这里做 index 集合清理：
        遍历 index 中的 session_id，如果 msg_key 已过期则从 index 中移除。
        """
        if not await self._connect():
            raise RuntimeError("Redis not connected")
        all_ids = await self._redis.smembers(self._index_key())
        if not all_ids:
            return 0
        pipe = self._redis.pipeline()
        for sid in all_ids:
            pipe.exists(self._msg_key(sid))
        results = await pipe.execute()

        expired = 0
        pipe2 = self._redis.pipeline()
        for sid, exists in zip(all_ids, results):
            if not exists:
                pipe2.srem(self._index_key(), sid)
                pipe2.delete(self._ts_key(sid))
                expired += 1
        if expired:
            await pipe2.execute()
        return expired

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            self._connected = False
            logger.info("[RedisStore] Connection closed")


def create_session_store(
    redis_url: (str | None) = None,
    key_prefix: str = "echoseve:session:",
    session_ttl: int = 1800,
    max_sessions: int = 1000,
) -> SessionStore:
    """
    工厂方法：尝试创建 Redis 存储实例（不连接），运行时自动降级到内存。

    Args:
        redis_url: Redis 连接 URL，为 None 则直接使用内存存储
        key_prefix: Redis key 前缀
        session_ttl: 会话 TTL（秒）
        max_sessions: 内存模式最大会话数

    Returns:
        SessionStore 实例
    """
    if redis_url:
        return RedisSessionStore(
            url=redis_url,
            key_prefix=key_prefix,
            session_ttl=session_ttl,
        )
    return MemorySessionStore(max_sessions=max_sessions)
