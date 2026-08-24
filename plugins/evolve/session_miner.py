"""
EchoServe V0.2.0 - Session Miner (P0)

Role:
    Extract multi-turn conversation histories from the ChatPlugin's
    SessionStore and convert them into SFT training samples.

Design:
    - Works with both MemorySessionStore and RedisSessionStore (via the
      common SessionStore interface).
    - For each session, produces one training sample per assistant turn
      (using the preceding user turn as input and the assistant response
      as output). Multi-turn context is included as a concatenated prefix.
    - Quality filters: skip sessions with < 2 messages, skip responses
      that are too short or contain refusal phrases.
    - Writes to data/training/session_mined.jsonl (append-only).
    - Maintains a processed-sessions set to avoid re-mining the same
      sessions on repeated runs.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

logger = logging.getLogger("echoseve.evolve.session_miner")


# Refusal phrases that indicate low-quality / unhelpful responses
_REFUSAL_PHRASES = [
    "sorry, i cannot",
    "sorry, i can not",
    "i am unable to",
    "i'm unable to",
    "i can't assist",
    "i cannot assist",
    "i can't help",
    "i cannot help",
    "i can't provide",
    "i cannot provide",
    "i can't answer",
    "i cannot answer",
    "i don't have enough information",
    "i do not have enough information",
    "i don't have access to",
    "i do not have access to",
    "i'm not able to",
    "i am not able to",
    "unfortunately, i cannot",
    "unfortunately, i can't",
    "unfortunately, i am unable",
    "as an ai",
    "as a language model",
    "i'm just a language model",
    "i am just a language model",
    "i'm an ai",
    "i am an ai",
    "i can't generate",
    "i cannot generate",
    "i can't create",
    "i cannot create",
    "i can't write",
    "i cannot write",
    "i can't produce",
    "i cannot produce",
    "i refuse to",
    "i decline to",
    "i won't help",
    "i will not help",
    "i won't assist",
    "i will not assist",
    "i won't provide",
    "i will not provide",
    "i won't create",
    "i will not create",
    "i won't generate",
    "i will not generate",
    "i won't write",
    "i will not write",
    "i won't produce",
    "i will not produce",
    "that's not something i can",
    "that is not something i can",
    "i'm not designed to",
    "i am not designed to",
    "i'm not programmed to",
    "i am not programmed to",
    "i'm not configured to",
    "i am not configured to",
    "i'm not equipped to",
    "i am not equipped to",
    "i'm not capable of",
    "i am not capable of",
    "i'm not willing to",
    "i am not willing to",
    "i'm not going to",
    "i am not going to",
    "抱歉，我无法",
    "暂未找到相关信息",
    "未找到相关",
    "无法回答",
    "不知道",
]


class SessionMiner:
    """
    Mine multi-turn conversation histories from the ChatPlugin session store
    and convert them into SFT training samples.

    Usage:
        miner = SessionMiner(
            output_path="./data/training/session_mined.jsonl",
        )
        result = await miner.mine(chat_manager)
    """

    def __init__(
        self,
        output_path: str = "./data/training/session_mined.jsonl",
        state_path: str = "./data/training/session_miner_state.json",
        min_turns: int = 2,
        min_query_len: int = 3,
        min_response_len: int = 10,
        max_query_len: int = 2000,
        max_response_len: int = 4000,
        max_context_turns: int = 3,
        refusal_phrases: Optional[List[str]] = None,
    ):
        self.output_path = Path(output_path)
        self.state_path = Path(state_path)
        self.min_turns = min_turns
        self.min_query_len = min_query_len
        self.min_response_len = min_response_len
        self.max_query_len = max_query_len
        self.max_response_len = max_response_len
        self.max_context_turns = max_context_turns
        self._refusal_phrases = refusal_phrases or _REFUSAL_PHRASES
        import threading
        self._lock = threading.Lock()

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    # ─── Public API ───────────────────────────────────

    async def mine(self, chat_manager) -> Dict[str, Any]:
        """
        Mine all sessions from the chat manager's SessionStore.

        Args:
            chat_manager: ChatPlugin instance (or any object with
                          list_sessions() and get_session_history() methods).

        Returns:
            {
                "status": "success",
                "sessions_processed": int,
                "new_samples": int,
                "skipped": int,
                "total_in_pool": int,
                "output_path": str,
            }
        """
        start_time = time.time()

        # Load already-processed sessions
        processed = self._load_processed_sessions()

        # List all sessions
        try:
            session_ids = await chat_manager.list_sessions()
        except Exception as e:
            logger.error(f"[SessionMiner] Failed to list sessions: {e}")
            return {
                "status": "failed",
                "reason": str(e),
                "new_samples": 0,
            }

        logger.info(
            f"[SessionMiner] Found {len(session_ids)} sessions, "
            f"{len(processed)} already processed"
        )

        new_samples: List[Dict[str, Any]] = []
        skipped = 0
        sessions_processed = 0

        for session_id in session_ids:
            if session_id in processed:
                continue

            try:
                history = await chat_manager.get_session_history(session_id)
            except Exception as e:
                logger.debug(f"[SessionMiner] Failed to load session {session_id}: {e}")
                skipped += 1
                continue

            samples = self._convert_session(session_id, history)
            new_samples.extend(samples)
            sessions_processed += 1
            processed.add(session_id)

        # Append new samples (thread-safe)
        with self._lock:
            if new_samples:
                with open(self.output_path, "a", encoding="utf-8") as f:
                    for sample in new_samples:
                        f.write(json.dumps(sample, ensure_ascii=False) + "\n")

            # Save processed sessions
            self._save_processed_sessions(processed)

        total_in_pool = self._count_lines(self.output_path)
        elapsed = round(time.time() - start_time, 2)

        logger.info(
            f"[SessionMiner] Done: {sessions_processed} sessions processed, "
            f"{len(new_samples)} new samples, {skipped} skipped, "
            f"pool total={total_in_pool}, elapsed={elapsed}s"
        )

        return {
            "status": "success",
            "sessions_processed": sessions_processed,
            "new_samples": len(new_samples),
            "skipped": skipped,
            "total_in_pool": total_in_pool,
            "output_path": str(self.output_path),
            "elapsed_seconds": elapsed,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return current state statistics."""
        processed = self._load_processed_sessions()
        pool_count = self._count_lines(self.output_path) if self.output_path.exists() else 0

        return {
            "processed_sessions": len(processed),
            "training_pool_size": pool_count,
            "output_path": str(self.output_path),
        }

    def reset_state(self):
        """Reset the processed sessions set, allowing full re-mining."""
        self._save_processed_sessions(set())
        logger.info("[SessionMiner] State reset")

    # ─── Internal Methods ─────────────────────────────

    def _convert_session(
        self,
        session_id: str,
        history: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """
        Convert a session's message history into training samples.

        For each (user, assistant) pair, produce one Alpaca-format sample.
        Include preceding context as a concatenated prefix (up to
        max_context_turns turns).
        """
        if not history or len(history) < self.min_turns:
            return []

        samples = []

        # Walk through history in pairs of (user, assistant)
        for i in range(len(history) - 1):
            msg = history[i]
            next_msg = history[i + 1]

            if msg.get("role") != "user" or next_msg.get("role") != "assistant":
                continue

            query = msg.get("content", "").strip()
            response = next_msg.get("content", "").strip()

            # Quality filters
            if len(query) < self.min_query_len:
                continue
            if len(response) < self.min_response_len:
                continue
            if len(query) > self.max_query_len:
                continue
            if len(response) > self.max_response_len:
                continue

            # Skip refusal responses
            response_lower = response.lower()
            if any(phrase in response_lower for phrase in self._refusal_phrases):
                continue

            # Build context from preceding turns
            context_turns = history[:i]
            context_text = self._build_context(context_turns)

            sample = {
                "instruction": "Please answer the user's question based on the company knowledge base:",
                "input": query,
                "output": response,
                "metadata": {
                    "source": "session_history",
                    "session_id": session_id,
                    "turn_index": i // 2,
                    "total_turns": len(history) // 2,
                    "context": context_text,
                    "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            }
            samples.append(sample)

        return samples

    def _build_context(self, turns: List[Dict[str, str]]) -> str:
        """Build a context string from preceding turns."""
        if not turns:
            return ""

        # Take only the last max_context_turns turns
        recent = turns[-(self.max_context_turns * 2):]

        parts = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "").strip()
            if content:
                parts.append(f"[{role}] {content[:200]}")

        return "\n".join(parts)

    def _load_processed_sessions(self) -> Set[str]:
        """Load the set of already-processed session IDs."""
        if not self.state_path.exists():
            return set()
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            return set(state.get("processed_sessions", []))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[SessionMiner] Failed to load state: {e}")
            return set()

    def _save_processed_sessions(self, sessions: Set[str]):
        """Save the set of processed session IDs."""
        state = {
            "processed_sessions": sorted(sessions),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"[SessionMiner] Failed to save state: {e}")

    @staticmethod
    def _count_lines(path: Path) -> int:
        """Count non-empty lines in a file."""
        if not path.exists():
            return 0
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
