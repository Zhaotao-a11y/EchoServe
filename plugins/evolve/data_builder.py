"""
EchoServe P1 — 训练数据增强器

功能：
- 从知识库 FAQ 中提取 (问题, 答案) 对
- 通过 LLM 生成同义变体扩充数据量
- 加入通用对话数据防止灾难性遗忘
- 输出标准 Alpaca 格式 JSONL
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger("echoserve.evolve.data")


class TrainingDataBuilder:
    """
    构建 LoRA 微调训练数据集。

    流程：
        知识库 FAQ → 原始 QA 对 → LLM 生成同义变体 → 混入通用数据 → 输出 JSONL
    """

    def __init__(
        self,
        knowledge_base,
        llm_client=None,
        generic_data_path: (str | None) = None,
        output_path: str = "./data/training/train.jsonl",
        variants_per_q: int = 3,
        generic_ratio: float = 0.15,
    ):
        """
        Args:
            knowledge_base: 知识库对象，需提供 get_all_qa_pairs() 方法
            llm_client: LLM 客户端（用于生成同义变体），可选
            generic_data_path: 通用对话数据路径（防遗忘）
            output_path: 输出 JSONL 文件路径
            variants_per_q: 每个问题生成的同义变体数量
            generic_ratio: 通用数据占比
        """
        self.kb = knowledge_base
        self.llm = llm_client
        self.generic_data_path = generic_data_path
        self.output_path = Path(output_path)
        self.variants_per_q = variants_per_q
        self.generic_ratio = generic_ratio

    # ─── 主入口 ────────────────────────────────────────

    def build(self) -> str:
        """
        构建完整训练数据集，写入 JSONL 文件。

        Returns:
            输出文件路径
        """
        logger.info(f"[{self.__class__.__name__}] 开始构建训练数据...")

        # 1. 从知识库提取原始 QA 对
        qa_pairs = self._extract_qa_pairs()
        logger.info(f"  原始 QA 对: {len(qa_pairs)} 条")

        # 2. 生成同义变体
        dataset = []
        for qa in qa_pairs:
            # 原始 QA
            dataset.append(self._to_alpaca(qa["question"], qa["answer"]))

            # 同义变体
            if self.llm:
                variants = self._generate_variants(qa["question"], n=self.variants_per_q)
                for v in variants:
                    dataset.append(self._to_alpaca(v, qa["answer"]))
            else:
                # 无 LLM 时使用简单模板生成变体
                variants = self._template_variants(qa["question"], n=self.variants_per_q)
                for v in variants:
                    dataset.append(self._to_alpaca(v, qa["answer"]))

        logger.info(f"  含变体后: {len(dataset)} 条")

        # 3. 混入通用对话数据（防灾难性遗忘）
        generic = self._load_generic_data()
        num_generic = int(len(dataset) * self.generic_ratio / (1 - self.generic_ratio))
        if generic:
            sampled = random.sample(generic, min(num_generic, len(generic)))
            dataset.extend(sampled)
            logger.info(f"  混入通用数据: {len(sampled)} 条")

        # 4. 打乱并写入
        random.shuffle(dataset)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            for item in dataset:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        logger.info(f"  最终数据集: {len(dataset)} 条 → {self.output_path}")
        return str(self.output_path)

    # ─── 内部方法 ────────────────────────────────────────

    def _extract_qa_pairs(self) -> list[dict[str, str]]:
        """从知识库提取 QA 对"""
        if hasattr(self.kb, "get_all_qa_pairs"):
            return self.kb.get_all_qa_pairs()
        elif hasattr(self.kb, "get_all_documents"):
            docs = self.kb.get_all_documents()
            pairs = []
            for doc in docs:
                q = doc.get("question") or doc.get("title") or ""
                a = doc.get("answer") or doc.get("content") or ""
                if q and a:
                    pairs.append({"question": q, "answer": a})
            return pairs
        else:
            # 尝试从 JSONL 文件读取
            return self._load_from_jsonl()

    def _load_from_jsonl(self) -> list[dict[str, str]]:
        """从默认知识库文件加载"""
        kb_path = Path("./data/knowledge/documents.jsonl")
        if not kb_path.exists():
            return []
        pairs = []
        with open(kb_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                doc = json.loads(line)
                q = doc.get("question") or doc.get("title") or ""
                a = doc.get("answer") or doc.get("content") or ""
                if q and a:
                    pairs.append({"question": q, "answer": a})
        return pairs

    def _generate_variants(self, question: str, n: int = 3) -> list[str]:
        """
        通过 LLM 生成同义变体。

        如果 LLM 不可用，回退到模板变体。
        """
        if not self.llm:
            return self._template_variants(question, n)

        prompt = f"""生成 {n} 个与下面问题意思相同但措辞不同的问法。
只输出问题，每行一个，不要编号，不要解释。

原始问题：{question}

变体："""

        try:
            response = self.llm.chat([{"role": "user", "content": prompt}])
            variants = [v.strip() for v in response.strip().split("\n") if v.strip()]
            # 过滤掉与原文完全相同的
            variants = [v for v in variants if v != question]
            return variants[:n]
        except Exception as e:
            logger.warning(f"  LLM 变体生成失败: {e}，回退到模板")
            return self._template_variants(question, n)

    def _template_variants(self, question: str, n: int = 3) -> list[str]:
        """基于模板的简单变体生成（无 LLM 时的回退方案）"""
        templates = [
            "请问{}",
            "我想了解一下{}",
            "麻烦告诉我{}",
            "谁能告诉我{}",
            "请教一下{}",
            "{}是怎么回事？",
            "关于{}我有些疑问",
        ]
        # 去掉问号便于拼接
        q_clean = question.rstrip("?？")
        random.shuffle(templates)
        variants = []
        for t in templates[:n]:
            v = t.format(q_clean)
            if not v.endswith(("?", "？")):
                v += "？"
            variants.append(v)
        return variants

    def _load_generic_data(self) -> list[dict[str, str]]:
        """加载通用对话数据（防止灾难性遗忘）"""
        # 优先从指定路径加载
        if self.generic_data_path and Path(self.generic_data_path).exists():
            return self._load_jsonl(self.generic_data_path)

        # 否则使用内置的少量通用数据
        return [
            {"instruction": "你是一个有帮助的助手。", "input": "你好", "output": "你好！有什么可以帮你的吗？"},
            {"instruction": "你是一个有帮助的助手。", "input": "谢谢", "output": "不客气，随时可以找我！"},
            {"instruction": "你是一个有帮助的助手。", "input": "再见", "output": "再见，祝你有美好的一天！"},
            {"instruction": "你是一个有帮助的助手。", "input": "你叫什么名字", "output": "我是 EchoServe，你的智能助手。"},
            {"instruction": "你是一个有帮助的助手。", "input": "你能做什么", "output": "我可以回答关于公司产品、政策和服务的相关问题。"},
            {"instruction": "你是一个有帮助的助手。", "input": "今天天气怎么样", "output": "抱歉，我无法查询实时天气。请问有什么业务问题可以帮你？"},
            {"instruction": "你是一个有帮助的助手。", "input": "你是谁开发的", "output": "我是由 EchoServe 团队开发的智能客服助手。"},
            {"instruction": "你是一个有帮助的助手。", "input": "你几岁了", "output": "我没有年龄的概念，随时为你服务！"},
            {"instruction": "你是一个有帮助的助手。", "input": "你厉害吗", "output": "我会尽力帮助你解决问题，有什么可以帮你的吗？"},
            {"instruction": "你是一个有帮助的助手。", "input": "帮我写个邮件", "output": "抱歉，我的专长是回答公司业务相关问题。邮件撰写建议咨询其他工具。"},
        ]

    def _to_alpaca(self, question: str, answer: str) -> dict[str, str]:
        """转换为 Alpaca 训练格式"""
        return {
            "instruction": "请根据公司知识库回答以下问题：",
            "input": question.strip(),
            "output": answer.strip(),
        }

    def _load_jsonl(self, path: str) -> list[Dict]:
        """通用 JSONL 加载"""
        items = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items

    # ─── 数据质量检查 ─────────────────────────────────────

    def validate(self, dataset_path: (str | None) = None) -> dict[str, Any]:
        """
        验证训练数据质量。

        Returns:
            {
                "total": 总条数,
                "valid": 有效条数,
                "invalid": 无效条数,
                "avg_input_len": 平均输入长度,
                "avg_output_len": 平均输出长度,
                "issues": [问题列表]
            }
        """
        path = Path(dataset_path or self.output_path)
        if not path.exists():
            return {"error": f"文件不存在: {path}"}

        total = 0
        valid = 0
        issues = []
        input_lens = []
        output_lens = []

        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    item = json.loads(line)
                    # 检查必要字段
                    if "instruction" not in item:
                        issues.append(f"Line {i}: 缺少 instruction 字段")
                        continue
                    if "input" not in item and "question" not in item:
                        issues.append(f"Line {i}: 缺少 input/question 字段")
                        continue
                    if "output" not in item and "answer" not in item:
                        issues.append(f"Line {i}: 缺少 output/answer 字段")
                        continue

                    inp = item.get("input") or item.get("question") or ""
                    out = item.get("output") or item.get("answer") or ""
                    input_lens.append(len(inp))
                    output_lens.append(len(out))

                    # 检查异常长度
                    if len(inp) > 2000:
                        issues.append(f"Line {i}: 输入过长 ({len(inp)} 字符)")
                    if len(out) > 5000:
                        issues.append(f"Line {i}: 输出过长 ({len(out)} 字符)")
                    if len(inp) < 2:
                        issues.append(f"Line {i}: 输入过短")

                    valid += 1
                except json.JSONDecodeError:
                    issues.append(f"Line {i}: JSON 解析失败")

        result = {
            "total": total,
            "valid": valid,
            "invalid": total - valid,
            "avg_input_len": round(sum(input_lens) / len(input_lens), 1) if input_lens else 0,
            "avg_output_len": round(sum(output_lens) / len(output_lens), 1) if output_lens else 0,
            "issues": issues[:20],  # 最多显示20条
        }

        logger.info(f"  数据验证: {valid}/{total} 有效, 平均输入 {result['avg_input_len']} 字符")
        return result
