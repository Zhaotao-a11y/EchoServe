"""
EchoServe V0.1.0 — RRF 融合算法

Reciprocal Rank Fusion (RRF) 是一种将多个检索结果列表
融合为单一排序的高效算法，无需归一化各路分数。

公式：
    score(d) = Σ  w_i / (k + rank_i(d))

其中：
    w_i  = 第 i 路检索的权重
    k    = 平滑常数（通常 60）
    rank = 文档在该路结果中的排名（从 1 开始）

参考文献：
    Cormack, G. V., et al. "Reciprocal rank fusion outperforms condorcet
    and individual rank learning methods." SIGIR 2009.
"""
from __future__ import annotations

import logging
from typing import Any
from collections import defaultdict

logger = logging.getLogger("echoserve.retriever.rrf")


def rrf_fuse(
    bm25_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
    k: int = 60,
    bm25_weight: float = 0.4,
    vector_weight: float = 0.6,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """
    RRF 融合 BM25 和向量检索结果。

    Args:
        bm25_results: BM25 检索结果列表
        vector_results: 向量检索结果列表
        k: RRF 平滑常数（默认 60）
        bm25_weight: BM25 路权重
        vector_weight: 向量路权重
        top_k: 最终返回数量

    Returns:
        融合排序后的文档列表

    示例：
        >>> bm25 = [{"id": "a", ...}, {"id": "b", ...}]
        >>> vector = [{"id": "b", ...}, {"id": "a", ...}]
        >>> fused = rrf_fuse(bm25, vector, top_k=2)
        >>> # "b" 在两路都是第1/第2名，得分最高
    """
    # 校验权重
    total_weight = bm25_weight + vector_weight
    if total_weight <= 0:
        raise ValueError("bm25_weight + vector_weight must be > 0")
    bm25_weight /= total_weight
    vector_weight /= total_weight

    # 存储每个文档的融合分数和原始内容
    scores: dict[str, float] = defaultdict(float)
    doc_map: dict[str, dict[str, Any]] = {}

    # 处理 BM25 结果
    for rank, doc in enumerate(bm25_results, start=1):
        doc_id = doc["id"]
        rrf_score = bm25_weight / (k + rank)
        scores[doc_id] += rrf_score
        # 保存文档内容（优先保留更详细的）
        if doc_id not in doc_map:
            doc_map[doc_id] = doc

    # 处理向量结果
    for rank, doc in enumerate(vector_results, start=1):
        doc_id = doc["id"]
        rrf_score = vector_weight / (k + rank)
        scores[doc_id] += rrf_score
        if doc_id not in doc_map:
            doc_map[doc_id] = doc

    # 按融合分数排序
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    # 组装结果
    fused_results = []
    for doc_id in sorted_ids[:top_k]:
        doc = doc_map[doc_id].copy()
        doc["rrf_score"] = round(scores[doc_id], 6)
        # 移除原始分数（避免混淆）
        doc.pop("score", None)
        fused_results.append(doc)

    logger.debug(f"[RRF] Fused {len(bm25_results)} BM25 + {len(vector_results)} Vector "
                 f"-> {len(fused_results)} results (k={k})")

    return fused_results


def rrf_fuse_multi(
    result_lists: list[list[dict[str, Any]]],
    weights: (list[float] | None) = None,
    k: int = 60,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """
    多路 RRF 融合（支持 2 路以上）。

    Args:
        result_lists: 多路检索结果列表的列表
        weights: 每路权重（默认均等）
        k: RRF 平滑常数
        top_k: 返回数量
    """
    n = len(result_lists)
    if weights is None:
        weights = [1.0 / n] * n
    elif len(weights) != n:
        raise ValueError(f"weights length ({len(weights)}) must match result_lists ({n})")

    # 归一化权重
    total = sum(weights)
    weights = [w / total for w in weights]

    scores: dict[str, float] = defaultdict(float)
    doc_map: dict[str, dict[str, Any]] = {}

    for results, weight in zip(result_lists, weights):
        for rank, doc in enumerate(results, start=1):
            doc_id = doc["id"]
            scores[doc_id] += weight / (k + rank)
            if doc_id not in doc_map:
                doc_map[doc_id] = doc

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    fused = []
    for doc_id in sorted_ids[:top_k]:
        doc = doc_map[doc_id].copy()
        doc["rrf_score"] = round(scores[doc_id], 6)
        doc.pop("score", None)
        fused.append(doc)

    return fused
