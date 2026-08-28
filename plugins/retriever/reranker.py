"""
EchoServe P1 — Cross-Encoder 重排序器

功能：
- 对 RRF 融合后的 Top-N 结果进行精细打分重排
- 支持本地 Cross-Encoder 模型（如 bge-reranker-v2-m3）
- 模型可选加载，未安装时优雅降级为原始排序
- 支持分数归一化和阈值过滤
"""
from __future__ import annotations

import logging
from typing import Any
import asyncio

logger = logging.getLogger("echoserve.retriever.reranker")


class CrossEncoderReranker:
    """
    Cross-Encoder 交叉编码器重排序器。

    原理：
        Bi-Encoder（向量检索）：query 和 doc 分别编码，适合粗排
        Cross-Encoder：query 和 doc 拼接后联合编码，适合精排

    流程：
        BM25 + Vector → RRF 融合 Top-20 → Cross-Encoder 精排 Top-5

    使用示例：
        reranker = CrossEncoderReranker()
        await reranker.initialize()
        reranked = reranker.rerank("退货政策是什么？", candidates)
    """

    # 推荐的轻量 Cross-Encoder 模型
    DEFAULT_MODELS = [
        "BAAI/bge-reranker-v2-m3",       # 多语言，效果好
        "BAAI/bge-reranker-base",         # 英文为主
        "cross-encoder/ms-marco-MiniLM-L-6-v2",  # 轻量快速
    ]

    def __init__(
        self,
        model_name: (str | None) = None,
        device: str = "cpu",  # 重排序用 CPU 即可，不占 GPU
        max_length: int = 512,
        score_threshold: float = 0.0,
        enabled: bool = True,
    ):
        self.model_name = model_name or self.DEFAULT_MODELS[0]
        self.device = device
        self.max_length = max_length
        self.score_threshold = score_threshold
        self.enabled = enabled
        self._model = None
        self._tokenizer = None
        self._available = False

    # ─── 生命周期 ──────────────────────────────────

    async def initialize(self) -> bool:
        """
        异步初始化模型。

        Returns:
            True 如果模型加载成功
        """
        if not self.enabled:
            logger.info(f"[{self.__class__.__name__}] 重排序已禁用")
            return False

        try:
            from sentence_transformers import CrossEncoder  # type: ignore

            logger.info(f"[{self.__class__.__name__}] 加载 Cross-Encoder: {self.model_name}")

            # 在事件循环中运行（模型加载是 CPU 密集型的）
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None, self._load_model, CrossEncoder, self.model_name
            )
            self._available = True
            logger.info(f"[{self.__class__.__name__}] Cross-Encoder 加载成功")
            return True

        except ImportError:
            logger.warning(
                f"[{self.__class__.__name__}] sentence-transformers 未安装，"
                f"重排序降级为原始排序"
            )
            self._available = False
            return False
        except Exception as e:
            logger.warning(
                f"[{self.__class__.__name__}] 模型加载失败: {e}，"
                f"重排序降级为原始排序"
            )
            self._available = False
            return False

    def _load_model(self, ce_class, model_name: str):
        """同步加载模型（在线程池中执行）"""
        return ce_class(model_name, max_length=self.max_length, device=self.device)

    async def shutdown(self):
        """释放模型资源"""
        self._model = None
        self._available = False
        logger.info(f"[{self.__class__.__name__}] 已释放")

    @property
    def is_available(self) -> bool:
        return self._available and self._model is not None

    # ─── 核心方法 ──────────────────────────────────

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        对候选文档进行重排序。

        Args:
            query: 用户查询
            candidates: RRF 融合后的候选列表，每项需含 "id", "content", "score"
            top_k: 返回数量

        Returns:
            重排序后的文档列表（新增 "rerank_score" 字段）
        """
        if not self.is_available or len(candidates) <= 1:
            # 不可用或候选太少，直接返回原始排序
            for doc in candidates[:top_k]:
                if "rerank_score" not in doc:
                    doc["rerank_score"] = doc.get("score", 0.0)
            return candidates[:top_k]

        # 准备输入对
        pairs = []
        valid_candidates = []
        for doc in candidates:
            content = doc.get("content") or doc.get("text") or ""
            if not content:
                continue
            # 截断过长内容
            content_truncated = content[: self.max_length * 2]
            pairs.append((query, content_truncated))
            valid_candidates.append(doc)

        if not pairs:
            return candidates[:top_k]

        # 预测分数（CPU 推理，可能较慢）
        try:
            scores = self._model.predict(pairs)  # type: ignore
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] 预测失败: {e}，使用原始排序")
            return candidates[:top_k]

        # 组装结果
        scored = []
        for doc, score in zip(valid_candidates, scores):
            doc_copy = dict(doc)
            doc_copy["rerank_score"] = float(score)
            doc_copy["original_score"] = doc.get("score", 0.0)
            scored.append(doc_copy)

        # 按重排序分数降序
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)

        # 阈值过滤
        if self.score_threshold > 0:
            scored = [d for d in scored if d["rerank_score"] >= self.score_threshold]

        return scored[:top_k]

    async def rerank_async(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        异步重排序（将 CPU 密集型推理放到线程池）。
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.rerank, query, candidates, top_k
        )

    # ─── 批量重排序 ─────────────────────────────────

    def rerank_batch(
        self,
        queries: list[str],
        candidate_lists: list[list[dict[str, Any]]],
        top_k: int = 5,
    ) -> list[list[dict[str, Any]]]:
        """
        批量重排序（多个查询）。

        Args:
            queries: 查询列表
            candidate_lists: 每个查询对应的候选列表
            top_k: 每个查询返回数量

        Returns:
            每个查询的重排序结果
        """
        results = []
        for query, cands in zip(queries, candidate_lists):
            results.append(self.rerank(query, cands, top_k))
        return results

    # ─── 配置管理 ──────────────────────────────────

    def set_threshold(self, threshold: float):
        """动态调整分数阈值"""
        self.score_threshold = threshold
        logger.info(f"[{self.__class__.__name__}] 阈值调整为: {threshold}")

    def enable(self):
        """启用重排序"""
        self.enabled = True
        logger.info(f"[{self.__class__.__name__}] 已启用")

    def disable(self):
        """禁用重排序（快速降级）"""
        self.enabled = False
        logger.info(f"[{self.__class__.__name__}] 已禁用")


class RerankerFactory:
    """
    重排序器工厂。

    根据配置创建不同档次的重排序器：
    - light: 无重排序（仅 RRF）
    - standard: MiniLM 轻量 Cross-Encoder
    - high_precision: bge-reranker-v2-m3 高精度
    """

    @staticmethod
    def create(
        tier: str = "standard",
        device: str = "cpu",
        enabled: bool = True,
    ) -> CrossEncoderReranker:
        """
        创建重排序器。

        Args:
            tier: "light" | "standard" | "high_precision"
            device: "cpu" | "cuda"
            enabled: 是否启用
        """
        configs = {
            "light": {
                "model_name": None,  # None = 禁用
                "enabled": False,
            },
            "standard": {
                "model_name": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "max_length": 256,
            },
            "high_precision": {
                "model_name": "BAAI/bge-reranker-v2-m3",
                "max_length": 512,
            },
        }

        cfg = configs.get(tier, configs["standard"])

        if tier == "light":
            return CrossEncoderReranker(
                model_name=None,
                device=device,
                enabled=False,
            )

        return CrossEncoderReranker(
            model_name=cfg["model_name"],
            device=device,
            max_length=cfg.get("max_length", 512),
            enabled=enabled,
        )
