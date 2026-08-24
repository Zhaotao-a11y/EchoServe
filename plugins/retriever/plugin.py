"""
EchoServe P1 v0.1.5 — 检索引擎插件（增强版）

混合检索：BM25（关键词） + Vector（语义） → RRF 融合 → Cross-Encoder 重排序
通过 Context 注册为 "retriever" 服务。
"""
from __future__ import annotations

import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .bm25 import BM25Retriever
from .vector import VectorRetriever
from .rrf import rrf_fuse
from .reranker import CrossEncoderReranker, RerankerFactory

logger = logging.getLogger("echoseve.retriever")


class RetrieverPlugin(BaizePlugin):
    """检索引擎插件（P1 增强版，含 Cross-Encoder 重排序）"""

    plugin_id = "core.retriever"
    plugin_name = "混合检索引擎"
    plugin_version = "0.1.5"
    dependencies = []  # 无依赖，基础插件

    def __init__(self):
        self.bm25: Optional[BM25Retriever] = None
        self.vector: Optional[VectorRetriever] = None
        self.reranker: Optional[CrossEncoderReranker] = None
        self.top_k: int = 10
        self.rrf_k: int = 60
        self.bm25_weight: float = 0.4
        self.vector_weight: float = 0.6
        self.rerank_top_n: int = 20  # RRF 后取 Top-N 做重排序
        self.final_top_k: int = 5   # 最终返回数量
        self._rerank_enabled: bool = True

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化 BM25 + 向量检索器 + Cross-Encoder 重排序器"""
        settings = ctx.settings

        # 从配置读取参数
        self.top_k = settings.retrieval.top_k
        self.rrf_k = settings.retrieval.rrf_k
        self.bm25_weight = settings.retrieval.bm25_weight
        self.vector_weight = settings.retrieval.vector_weight

        # 初始化 BM25（带持久化路径）
        bm25_persist_path = str(Path(settings.root_dir) / "data" / "bm25_index.json")
        self.bm25 = BM25Retriever(persist_path=bm25_persist_path)
        loaded = self.bm25.load()
        if loaded > 0:
            logger.info(f"[{self.plugin_id}] BM25 index loaded from disk ({loaded} docs)")
        else:
            logger.info(f"[{self.plugin_id}] BM25 retriever initialized (empty index)")

        # 初始化向量检索器
        self.vector = VectorRetriever(
            host=settings.chroma.host,
            collection=settings.chroma.collection,
            embedding_model=settings.embedding.model,
            embedding_dim=settings.embedding.dim,
        )
        await self.vector.initialize()
        logger.info(f"[{self.plugin_id}] Vector retriever initialized "
                     f"(collection={settings.chroma.collection})")

        # 初始化 Cross-Encoder 重排序器
        rerank_tier = getattr(settings.retrieval, "rerank_tier", "standard")
        self.reranker = RerankerFactory.create(
            tier=rerank_tier,
            device=getattr(settings.retrieval, "rerank_device", "cpu"),
            enabled=getattr(settings.retrieval, "rerank_enabled", True),
        )
        await self.reranker.initialize()
        if self.reranker.is_available:
            logger.info(f"[{self.plugin_id}] Cross-Encoder reranker initialized "
                         f"(model={self.reranker.model_name})")
        else:
            logger.info(f"[{self.plugin_id}] Reranker 不可用，使用 RRF 排序")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """释放资源"""
        if self.vector:
            await self.vector.close()
        if self.reranker:
            await self.reranker.shutdown()
        logger.info(f"[{self.plugin_id}] Destroyed")

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        use_rerank: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行混合检索 + 可选重排序。

        Args:
            query: 用户查询
            top_k: 最终返回数量（默认使用配置）
            use_rerank: 是否启用重排序（默认使用配置）

        Returns:
            融合后的文档列表，每项包含：
            - id, content, score, source, metadata, rerank_score(如有)
        """
        k = top_k or self.final_top_k
        should_rerank = self._rerank_enabled if use_rerank is None else use_rerank

        # 1. 并行执行两种检索
        bm25_task = asyncio.create_task(self.bm25.search(query, k=self.rerank_top_n))
        vector_task = asyncio.create_task(self.vector.search(query, k=self.rerank_top_n))

        bm25_results, vector_results = await asyncio.gather(bm25_task, vector_task)

        # 2. RRF 融合
        fused = rrf_fuse(
            bm25_results=bm25_results,
            vector_results=vector_results,
            k=self.rrf_k,
            bm25_weight=self.bm25_weight,
            vector_weight=self.vector_weight,
            top_k=self.rerank_top_n,  # 取较多候选给重排序
        )

        # 3. Cross-Encoder 重排序（如可用）
        if should_rerank and self.reranker and self.reranker.is_available:
            reranked = await self.reranker.rerank_async(query, fused, top_k=k)
            # 标记使用了重排序
            for doc in reranked:
                doc["reranked"] = True
            results = reranked
        else:
            results = fused[:k]
            for doc in results:
                doc["reranked"] = False

        logger.debug(f"[{self.plugin_id}] Query: '{query[:50]}...' "
                     f"BM25={len(bm25_results)} Vector={len(vector_results)} "
                     f"Fused={len(fused)} Returned={len(results)}")

        # 发布事件
        self.publish("retrieval.done", {
            "query": query,
            "results": results,
            "reranked": should_rerank and self.reranker.is_available,
        })

        return results

    def add_documents(self, documents: List[Dict[str, Any]]):
        """批量添加文档到索引"""
        self.bm25.add_documents(documents)

        # 向量索引（异步）
        if self.vector:
            asyncio.create_task(self.vector.add_documents(documents))

        logger.info(f"[{self.plugin_id}] Added {len(documents)} documents to index")

    def clear(self):
        """清空所有索引"""
        self.bm25.clear()
        if self.vector:
            asyncio.create_task(self.vector.clear())
        logger.info(f"[{self.plugin_id}] All indexes cleared")

    # ─── 重排序控制 ────────────────────────────────

    def enable_rerank(self):
        """启用重排序"""
        self._rerank_enabled = True
        logger.info(f"[{self.plugin_id}] Reranking enabled")

    def disable_rerank(self):
        """禁用重排序（快速降级）"""
        self._rerank_enabled = False
        logger.info(f"[{self.plugin_id}] Reranking disabled")

    def set_rerank_tier(self, tier: str):
        """
        动态切换重排序档次。

        Args:
            tier: "light" | "standard" | "high_precision"
        """
        if not self.reranker:
            return
        # 创建新的重排序器
        new_reranker = RerankerFactory.create(tier=tier)
        # 异步初始化
        asyncio.create_task(self._swap_reranker(new_reranker))

    async def _swap_reranker(self, new_reranker: CrossEncoderReranker):
        """异步切换重排序器"""
        await new_reranker.initialize()
        old = self.reranker
        self.reranker = new_reranker
        if old:
            await old.shutdown()
        logger.info(f"[{self.plugin_id}] Reranker switched to {new_reranker.model_name}")
