"""
EchoServe V0.1.0 — 向量检索

基于 ChromaDB 的语义向量检索。
嵌入模型支持 BGE / M3E 等国产模型，通过 SentenceTransformers 本地推理。

用法：
    retriever = VectorRetriever(host="http://chroma:8000", ...)
    await retriever.initialize()
    await retriever.add_documents([...])
    results = await retriever.search("如何退货", k=10)
"""
from __future__ import annotations

import logging
from typing import List, Dict, Any
import numpy as np

logger = logging.getLogger("echoseve.retriever.vector")

# 延迟导入，避免启动时必须安装
try:
    import chromadb
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False
    logger.warning("[Vector] chromadb not installed, run: pip install chromadb")

try:
    from sentence_transformers import SentenceTransformer
    HAS_ST = True
except ImportError:
    HAS_ST = False
    logger.warning("[Vector] sentence-transformers not installed")


class VectorRetriever:
    """
    ChromaDB 向量检索器。

    嵌入模型在本地通过 SentenceTransformer 推理，
    向量存储和检索由 ChromaDB 负责。
    """

    def __init__(
        self,
        host: str = "http://localhost:8000",
        collection: str = "echoseve_kb",
        embedding_model: str = "BAAI/bge-large-zh-v1.5",
        embedding_dim: int = 1024,
        persist_dir: str = "./data/chroma",
    ):
        self.host = host
        self.collection_name = collection
        self.embedding_model_name = embedding_model
        self.embedding_dim = embedding_dim
        self.persist_dir = persist_dir

        self._client = None
        self._collection = None
        self._embedder = None
        self._batch_size = 64

    async def initialize(self):
        """初始化 Chroma 客户端和嵌入模型"""
        if not HAS_CHROMA:
            logger.warning("[Vector] chromadb not installed, running in fallback mode (BM25 only)")
            self._collection = None
            return

        # 初始化 Chroma 客户端
        # 支持 HTTP 远程模式和本地持久化模式
        if self.host.startswith("http"):
            self._client = chromadb.HttpClient(host=self._extract_host(), port=self._extract_port())
            logger.info(f"[Vector] Connected to Chroma server at {self.host}")
        else:
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            logger.info(f"[Vector] Chroma persistent client at {self.persist_dir}")

        # 获取或创建 collection
        try:
            self._collection = self._client.get_collection(name=self.collection_name)
            logger.info(f"[Vector] Using existing collection: {self.collection_name} "
                        f"(count={self._collection.count()})")
        except Exception as e:
            logger.debug(f"Collection '{self.collection_name}' not found, creating new one: {e}")
            self._collection = self._client.create_collection(
                name=self.collection_name,
                metadata={"description": "EchoServe Knowledge Base"}
            )
            logger.info(f"[Vector] Created new collection: {self.collection_name}")

        # 初始化嵌入模型（延迟加载）
        await self._load_embedder()

    async def _load_embedder(self):
        """加载嵌入模型"""
        if self._embedder is not None:
            return

        if not HAS_ST:
            raise RuntimeError(
                "sentence-transformers is required for local embedding. "
                "Install with: pip install sentence-transformers"
            )

        import asyncio
        # 模型加载是 CPU 密集型，放到线程池
        loop = asyncio.get_event_loop()
        self._embedder = await loop.run_in_executor(
            None,
            lambda: SentenceTransformer(self.embedding_model_name)
        )
        logger.info(f"[Vector] Embedding model loaded: {self.embedding_model_name}")

    def _extract_host(self) -> str:
        """从 URL 中提取主机名"""
        import re
        match = re.search(r'https?://([^:/]+)', self.host)
        return match.group(1) if match else "localhost"

    def _extract_port(self) -> int:
        """从 URL 中提取端口"""
        import re
        match = re.search(r':(\d+)', self.host)
        return int(match.group(1)) if match else 8000

    async def _embed(self, texts: List[str]) -> np.ndarray:
        """批量生成嵌入向量"""
        import asyncio
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._embedder.encode(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        )
        return np.array(embeddings)

    async def add_documents(self, documents: List[Dict[str, Any]]):
        """批量添加文档到向量库"""
        if not HAS_CHROMA:
            return  # Fallback 模式，跳过向量索引
        if not self._collection:
            await self.initialize()
        if not self._collection:
            return

        if not documents:
            return

        # 分批处理
        for i in range(0, len(documents), self._batch_size):
            batch = documents[i:i + self._batch_size]

            ids = [doc.get("id") or f"doc_{j}" for j, doc in enumerate(batch)]
            contents = [doc.get("content", "") for doc in batch]
            metadatas = [doc.get("metadata", {}) for doc in batch]

            # 生成嵌入
            embeddings = await self._embed(contents)

            # 写入 Chroma
            self._collection.add(
                ids=ids,
                documents=contents,
                embeddings=embeddings.tolist(),
                metadatas=metadatas,
            )

            logger.debug(f"[Vector] Added batch {i//self._batch_size + 1}: {len(batch)} docs")

        logger.info(f"[Vector] Total indexed: {self._collection.count()} documents")

    async def search(self, query: str, k: int = 10) -> List[Dict[str, Any]]:
        """
        语义向量检索。

        Returns:
            [{"id": str, "content": str, "score": float, "metadata": dict}, ...]
            注意：Chroma 返回的距离值需要转换为相似度分数
        """
        if not self._collection:
            # Fallback 模式或尚未初始化
            if not HAS_CHROMA:
                return []
            await self.initialize()
            if not self._collection:
                return []

        if not query.strip():
            return []

        # 生成查询向量
        query_embedding = await self._embed([query])
        query_vector = query_embedding[0].tolist()

        # 查询 Chroma
        results = self._collection.query(
            query_embeddings=[query_vector],
            n_results=k,
        )

        # 格式化结果
        formatted = []
        if results and results.get("documents") and results["documents"][0]:
            docs = results["documents"][0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            ids = results.get("ids", [[]])[0]

            for i, (doc, meta, dist, doc_id) in enumerate(zip(docs, metadatas, distances, ids)):
                # Chroma 返回 L2 距离，转换为相似度分数（0-1）
                score = 1.0 / (1.0 + dist)
                formatted.append({
                    "id": doc_id,
                    "content": doc,
                    "score": float(score),
                    "metadata": meta or {},
                    "source": "vector",
                })

        return formatted

    async def clear(self):
        """清空 collection"""
        if not HAS_CHROMA or not self._collection:
            return
        try:
            self._client.delete_collection(self.collection_name)
            self._collection = self._client.create_collection(
                name=self.collection_name,
                metadata={"description": "EchoServe Knowledge Base"}
            )
            logger.info(f"[Vector] Collection '{self.collection_name}' cleared")
        except Exception as e:
            logger.error(f"[Vector] Clear failed: {e}")

    async def close(self):
        """释放资源"""
        if not HAS_CHROMA:
            return
        if self._embedder is not None:
            del self._embedder
            self._embedder = None
        self._collection = None
        self._client = None
        logger.info("[Vector] Closed")

    @property
    def count(self) -> int:
        """返回索引文档数"""
        if self._collection:
            return self._collection.count()
        return 0
