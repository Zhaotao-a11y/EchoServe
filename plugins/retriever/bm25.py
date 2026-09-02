"""
EchoServe V0.1.0 — BM25 关键词检索

基于 rank_bm25 实现，支持中文分词（jieba）。
支持索引持久化（JSON 序列化），重启不丢失。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict

logger = logging.getLogger("echoserve.retriever.bm25")

# 尝试导入依赖
try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    logger.warning("[BM25] rank_bm25 not installed, run: pip install rank_bm25")

try:
    import jieba
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False
    logger.warning("[BM25] jieba not installed, run: pip install jieba")


@dataclass
class BM25Doc:
    """BM25 索引中的文档"""
    id: str
    content: str
    metadata: dict[str, Any]
    tokens: list[str]


class BM25Retriever:
    """
    BM25 关键词检索器。

    用法：
        retriever = BM25Retriever()
        retriever.add_documents([
            {"id": "1", "content": "如何退货？", "metadata": {...}}
        ])
        results = await retriever.search("退货流程", k=5)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, persist_path: (str | None) = None):
        self.k1 = k1
        self.b = b
        self._docs: list[BM25Doc] = []
        self._doc_map: dict[str, int] = {}  # id -> index
        self._bm25 = None
        self._corpus_tokens: list[list[str]] = []
        self._persist_path = Path(persist_path) if persist_path else None

    def _tokenize(self, text: str) -> list[str]:
        """中文分词"""
        text = text.lower().strip()
        # 去除多余空白
        text = re.sub(r'\s+', ' ', text)

        if HAS_JIEBA:
            tokens = list(jieba.cut(text))
        else:
            # 回退方案：按字符分词
            tokens = [c for c in text if c.strip()]

        # 过滤空字符串和纯标点
        tokens = [t for t in tokens if t.strip() and t not in '，。！？、；：""''（）【】《》\n\r\t']
        return tokens

    def add_documents(self, documents: list[dict[str, Any]]):
        """批量添加文档"""
        for doc_dict in documents:
            doc_id = doc_dict.get("id") or doc_dict.get("doc_id") or f"doc_{len(self._docs)}"
            content = doc_dict.get("content", "")
            metadata = doc_dict.get("metadata", {})

            tokens = self._tokenize(content)
            if not tokens:
                continue

            idx = len(self._docs)
            self._docs.append(BM25Doc(
                id=doc_id,
                content=content,
                metadata=metadata,
                tokens=tokens,
            ))
            self._doc_map[doc_id] = idx
            self._corpus_tokens.append(tokens)

        # 重建索引
        self._rebuild_index()
        self._auto_save()
        logger.info(f"[BM25] Added {len(documents)} docs, total={len(self._docs)}")

    def _rebuild_index(self):
        """重建 BM25 索引"""
        if not HAS_BM25 or not self._corpus_tokens:
            return
        self._bm25 = BM25Okapi(self._corpus_tokens, k1=self.k1, b=self.b)

    async def search(self, query: str, k: int = 10) -> list[dict[str, Any]]:
        """
        执行 BM25 检索。

        Returns:
            [{"id": str, "content": str, "score": float, "metadata": dict}, ...]
        """
        if not self._bm25 or not self._docs:
            return []

        tokens = self._tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)

        # 取 Top-K
        import numpy as np
        top_indices = np.argsort(scores)[::-1][:k]

        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            doc = self._docs[idx]
            results.append({
                "id": doc.id,
                "content": doc.content,
                "score": float(scores[idx]),
                "metadata": doc.metadata,
                "source": "bm25",
            })

        return results

    def remove_documents(self, doc_ids: list[str]):
        """
        按 ID 删除指定文档（增量更新用）。

        删除后自动重建 BM25 索引并持久化。

        Args:
            doc_ids: 要删除的文档 ID 列表
        """
        if not doc_ids:
            return

        removed = 0
        id_set = set(doc_ids)
        # 重建列表，跳过要删除的
        new_docs = []
        new_corpus = []
        for doc, tokens in zip(self._docs, self._corpus_tokens):
            if doc.id in id_set:
                removed += 1
                continue
            new_docs.append(doc)
            new_corpus.append(tokens)

        self._docs = new_docs
        self._corpus_tokens = new_corpus
        self._doc_map = {doc.id: idx for idx, doc in enumerate(self._docs)}
        self._rebuild_index()
        self._auto_save()
        logger.info(f"[BM25] Removed {removed} docs, remaining={len(self._docs)}")

    def clear(self):
        """清空索引"""
        self._docs.clear()
        self._doc_map.clear()
        self._corpus_tokens.clear()
        self._bm25 = None
        self._auto_save()
        logger.info("[BM25] Index cleared")

    def __len__(self) -> int:
        return len(self._docs)

    # ─── 索引持久化 ──────────────────────────────────────────

    def _auto_save(self):
        """如果配置了 persist_path，自动保存索引到磁盘"""
        if self._persist_path:
            try:
                self.save()
            except Exception as e:
                logger.warning(f"[BM25] Auto-save failed: {e}")

    def save(self, path: (str | None) = None):
        """
        将索引序列化到 JSON 文件。

        Args:
            path: 指定保存路径；不指定则使用 persist_path
        """
        save_path = Path(path) if path else self._persist_path
        if not save_path:
            return

        data = {
            "version": 1,
            "k1": self.k1,
            "b": self.b,
            "docs": [asdict(d) for d in self._docs],
            "corpus_tokens": self._corpus_tokens,
        }
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.info(f"[BM25] Index saved: {len(self._docs)} docs -> {save_path}")

    def load(self, path: (str | None) = None) -> int:
        """
        从 JSON 文件加载索引。

        Args:
            path: 指定加载路径；不指定则使用 persist_path

        Returns:
            加载的文档数量
        """
        load_path = Path(path) if path else self._persist_path
        if not load_path or not load_path.exists():
            return 0

        try:
            with open(load_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"[BM25] Failed to load index: {e}")
            return 0

        self._docs = [
            BM25Doc(
                id=d["id"],
                content=d["content"],
                metadata=d.get("metadata", {}),
                tokens=d.get("tokens", []),
            )
            for d in data.get("docs", [])
        ]
        self._corpus_tokens = data.get("corpus_tokens", [])
        self._doc_map = {doc.id: idx for idx, doc in enumerate(self._docs)}
        self._rebuild_index()

        logger.info(f"[BM25] Index loaded: {len(self._docs)} docs <- {load_path}")
        return len(self._docs)
