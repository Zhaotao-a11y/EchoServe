"""
EchoServe V0.2.0 - Audit-to-Training Converter (P0)

Role:
    Periodically scan data/audit/audit.log.jsonl, extract chat_query /
    chat_query_stream entries (query + response_summary + sources),
    and write them to a training pool in Alpaca format.

Design:
    - Stateful: remembers the last processed log id via a checkpoint file
      (data/training/audit_converter_state.json), so repeated runs only
      process new logs.
    - Idempotent: re-running with the same checkpoint produces no duplicates.
    - Quality filters: skip logs with empty query / response, or response
      containing "sorry / not found" type refusal phrases.
    - Writes to data/training/training_pool.jsonl (append-only).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

logger = logging.getLogger("echoseve.evolve.audit_to_training")


# Refusal phrases that indicate low-quality / unhelpful responses
_REFUSAL_PHRASES = [
    "sorry, i cannot",
    "抱歉，我无法",
    "暂未找到相关信息",
    "未找到相关",
    "无法回答",
    "i don't know",
    "不知道",
]


class AuditToTrainingConverter:
    """
    Convert audit log entries into SFT training samples.

    Usage:
        converter = AuditToTrainingConverter(
            audit_log_path="./data/audit/audit.log.jsonl",
            training_pool_path="./data/training/training_pool.jsonl",
        )
        result = converter.convert()
        # result = {"status": "success", "new_samples": 42, "total_in_pool": 100, ...}
    """

    def __init__(
        self,
        audit_log_path: str = "./data/audit/audit.log.jsonl",
        training_pool_path: str = "./data/training/training_pool.jsonl",
        state_path: str = "./data/training/audit_converter_state.json",
        min_query_len: int = 3,
        min_response_len: int = 10,
        max_query_len: int = 2000,
        max_response_len: int = 4000,
        refusal_phrases: Optional[List[str]] = None,
    ):
        self.audit_log_path = Path(audit_log_path)
        self.training_pool_path = Path(training_pool_path)
        self.state_path = Path(state_path)
        self.min_query_len = min_query_len
        self.min_response_len = min_response_len
        self.max_query_len = max_query_len
        self.max_response_len = max_response_len
        self._refusal_phrases = refusal_phrases or _REFUSAL_PHRASES
        import threading
        self._lock = threading.Lock()

        # Ensure parent directories exist
        self.training_pool_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    # ─── Public API ───────────────────────────────────

    def convert(
        self,
        since_id: Optional[int] = None,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scan audit log and convert new entries to training samples.

        Args:
            since_id: Process logs with id > since_id. If None, uses
                      the checkpoint from state file (or 0 if no checkpoint).
            output_path: Override output path. Defaults to training_pool_path.

        Returns:
            {
                "status": "success",
                "new_samples": int,
                "skipped": int,
                "last_processed_id": int,
                "total_in_pool": int,
                "output_path": str,
            }
        """
        start_time = time.time()

        # Resolve output path
        out_path = Path(output_path) if output_path else self.training_pool_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine starting point
        if since_id is None:
            since_id = self._load_checkpoint()

        logger.info(
            f"[AuditToTraining] Scanning audit log since id={since_id} "
            f"-> {self.audit_log_path}"
        )

        if not self.audit_log_path.exists():
            logger.warning(f"[AuditToTraining] Audit log not found: {self.audit_log_path}")
            return {
                "status": "success",
                "new_samples": 0,
                "skipped": 0,
                "last_processed_id": since_id,
                "total_in_pool": self._count_lines(out_path),
                "output_path": str(out_path),
                "message": "audit log not found",
            }

        # Scan and convert
        new_samples: List[Dict[str, Any]] = []
        skipped = 0
        last_id = since_id

        with open(self.audit_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                log_id = entry.get("id", 0)
                if log_id <= since_id:
                    continue

                # Only convert chat queries
                action = entry.get("action", "")
                if action not in ("chat_query", "chat_query_stream"):
                    continue

                sample = self._convert_entry(entry)
                if sample:
                    new_samples.append(sample)
                    last_id = max(last_id, log_id)
                else:
                    skipped += 1
                    if log_id > last_id:
                        last_id = log_id

        # Append new samples to pool
        with self._lock:
            if new_samples:
                with open(out_path, "a", encoding="utf-8") as f:
                    for sample in new_samples:
                        f.write(json.dumps(sample, ensure_ascii=False) + "\n")

            # Save checkpoint
            self._save_checkpoint(last_id)

        total_in_pool = self._count_lines(out_path)
        elapsed = round(time.time() - start_time, 2)

        logger.info(
            f"[AuditToTraining] Done: {len(new_samples)} new samples, "
            f"{skipped} skipped, pool total={total_in_pool}, "
            f"last_id={last_id}, elapsed={elapsed}s"
        )

        return {
            "status": "success",
            "new_samples": len(new_samples),
            "skipped": skipped,
            "last_processed_id": last_id,
            "total_in_pool": total_in_pool,
            "output_path": str(out_path),
            "elapsed_seconds": elapsed,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return current state statistics."""
        checkpoint = self._load_checkpoint()
        pool_count = self._count_lines(self.training_pool_path) if self.training_pool_path.exists() else 0
        audit_count = self._count_lines(self.audit_log_path) if self.audit_log_path.exists() else 0

        return {
            "last_processed_id": checkpoint,
            "audit_log_entries": audit_count,
            "training_pool_size": pool_count,
            "audit_log_path": str(self.audit_log_path),
            "training_pool_path": str(self.training_pool_path),
        }

    def reset_checkpoint(self):
        """Reset the checkpoint to 0, allowing full re-processing."""
        self._save_checkpoint(0)
        logger.info("[AuditToTraining] Checkpoint reset to 0")

    # ─── Internal Methods ─────────────────────────────

    def _convert_entry(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Convert a single audit log entry to an Alpaca-format training sample.

        Returns None if the entry is filtered out (low quality).
        """
        query = entry.get("query", "").strip()
        response = entry.get("response_summary", "").strip()
        sources = entry.get("sources", [])
        latency_ms = entry.get("latency_ms", 0)
        user_id = entry.get("user_id", "anonymous")
        channel = entry.get("channel", "web")
        timestamp = entry.get("timestamp", "")

        # Quality filters
        if len(query) < self.min_query_len:
            return None
        if len(response) < self.min_response_len:
            return None
        if len(query) > self.max_query_len:
            return None
        if len(response) > self.max_response_len:
            return None

        # Skip refusal responses
        response_lower = response.lower()
        if any(phrase in response_lower for phrase in self._refusal_phrases):
            return None

        # Build Alpaca format sample
        sample = {
            "instruction": "Please answer the user's question based on the company knowledge base:",
            "input": query,
            "output": response,
            "metadata": {
                "source": "audit_log",
                "audit_id": entry.get("id"),
                "sources": sources,
                "latency_ms": latency_ms,
                "user_id": user_id,
                "channel": channel,
                "timestamp": timestamp,
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        }

        return sample

    def _load_checkpoint(self) -> int:
        """Load the last processed log id from state file."""
        if not self.state_path.exists():
            return 0
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            return state.get("last_processed_id", 0)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[AuditToTraining] Failed to load checkpoint: {e}")
            return 0

    def _save_checkpoint(self, last_id: int):
        """Save the last processed log id to state file."""
        state = {
            "last_processed_id": last_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"[AuditToTraining] Failed to save checkpoint: {e}")

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
