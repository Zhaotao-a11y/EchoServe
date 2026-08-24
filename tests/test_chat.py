"""
EchoServe V0.1.0 — 测试

运行：
    pytest tests/ -v

或单独运行某个测试：
    pytest tests/test_chat.py::test_rrf_fusion -v
"""
import sys
import pytest
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.retriever.rrf import rrf_fuse, rrf_fuse_multi
from plugins.retriever.bm25 import BM25Retriever
from config.settings import settings


# ─── RRF 融合测试 ─────────────────────────────────────────

class TestRRF:
    """RRF 融合算法测试"""

    def test_basic_fusion(self):
        """基本融合：两路结果不同排序"""
        bm25 = [
            {"id": "a", "content": "doc a", "score": 10.0},
            {"id": "b", "content": "doc b", "score": 8.0},
            {"id": "c", "content": "doc c", "score": 5.0},
        ]
        vector = [
            {"id": "b", "content": "doc b", "score": 0.9},
            {"id": "a", "content": "doc a", "score": 0.8},
            {"id": "d", "content": "doc d", "score": 0.7},
        ]

        result = rrf_fuse(bm25, vector, top_k=4)

        # "a" 和 "b" 都在两路前列，得分应最高
        assert len(result) == 4
        assert result[0]["id"] in ("a", "b")
        # 所有结果都有 rrf_score
        assert all("rrf_score" in r for r in result)

    def test_empty_results(self):
        """空结果处理"""
        result = rrf_fuse([], [], top_k=5)
        assert result == []

    def test_single_result(self):
        """单路有结果"""
        bm25 = [{"id": "only", "content": "solo", "score": 1.0}]
        vector = []
        result = rrf_fuse(bm25, vector, top_k=5)
        assert len(result) == 1
        assert result[0]["id"] == "only"

    def test_weight_bias(self):
        """权重偏向测试"""
        bm25 = [{"id": "x", "content": "x", "score": 1.0}]
        vector = [{"id": "y", "content": "y", "score": 1.0}]

        # BM25 权重高时，x 应排前面
        result = rrf_fuse(bm25, vector, bm25_weight=0.9, vector_weight=0.1, top_k=2)
        assert result[0]["id"] == "x"

    def test_multi_way_fusion(self):
        """多路融合（3路）"""
        lists = [
            [{"id": f"d{i}", "content": "", "score": 1.0} for i in range(3)],
            [{"id": f"d{i}", "content": "", "score": 1.0} for i in range(3, 0, -1)],
            [{"id": "d2", "content": "", "score": 1.0},
             {"id": "d1", "content": "", "score": 1.0},
             {"id": "d3", "content": "", "score": 1.0}],
        ]
        result = rrf_fuse_multi(lists, top_k=3)
        assert len(result) == 3
        # d2 在三路中都出现，应该排第一
        assert result[0]["id"] == "d2"


# ─── BM25 检索测试 ───────────────────────────────────────

class TestBM25:
    """BM25 关键词检索测试"""

    @pytest.mark.asyncio
    async def test_basic_search(self):
        """基本关键词搜索"""
        retriever = BM25Retriever()
        retriever.add_documents([
            {"id": "1", "content": "如何申请退货退款", "metadata": {"cat": "售后"}},
            {"id": "2", "content": "退货政策是7天无理由", "metadata": {"cat": "售后"}},
            {"id": "3", "content": "快递物流配送时效查询", "metadata": {"cat": "物流"}},
        ])

        results = await retriever.search("退货", k=2)
        assert len(results) >= 1
        # 退货相关文档应排在前面
        assert any("退货" in r["content"] for r in results)

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """空查询返回空结果"""
        retriever = BM25Retriever()
        retriever.add_documents([
            {"id": "1", "content": "测试文档", "metadata": {}},
        ])
        results = await retriever.search("", k=5)
        assert results == []

    def test_clear(self):
        """清空索引"""
        retriever = BM25Retriever()
        retriever.add_documents([
            {"id": "1", "content": "测试", "metadata": {}},
        ])
        assert len(retriever) == 1
        retriever.clear()
        assert len(retriever) == 0


# ─── 配置测试 ─────────────────────────────────────────────

class TestSettings:
    """配置加载测试"""

    def test_settings_loaded(self):
        """配置对象可正常创建"""
        assert settings is not None
        assert settings.api.port == 8080
        assert settings.retrieval.top_k > 0

    def test_chroma_config(self):
        """Chroma 配置"""
        assert settings.chroma.collection == "echoseve_kb"

    def test_retrieval_weights_sum(self):
        """检索权重之和应为 1"""
        total = settings.retrieval.bm25_weight + settings.retrieval.vector_weight
        assert abs(total - 1.0) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
