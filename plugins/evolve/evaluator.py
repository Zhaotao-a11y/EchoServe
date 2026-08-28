"""
EchoServe V0.1.0 — 评估器与 A/B 测试

功能：
- EvaluationPipeline: 每周自动评估模型准确率，生成报告
- ABTester: 对比 RAG-only vs RAG+LoRA 效果
- 评估指标: 准确率、幻觉率、回答简洁度
- LLM-as-Judge: 可选注入 LLM 评判函数，替代关键词匹配评分
  - 未注入或调用失败时自动降级到关键词匹配（向后兼容）
"""
from __future__ import annotations

import json
import logging
import time
import random
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("echoserve.evolve.eval")


class EvaluationPipeline:
    """
    自动化评估 Pipeline。

    每周运行一次，评估当前模型在测试集上的表现，
    生成报告并通知管理员（不自动 promote 模型）。
    """

    def __init__(
        self,
        test_set_path: str = "./data/training/test_set.jsonl",
        report_dir: str = "./data/training/reports",
        threshold_for_notification: float = 0.02,
        judge_fn: (Callable[[str, str, str], float] | None) = None,
    ):
        self.test_set_path = Path(test_set_path)
        self.report_dir = Path(report_dir)
        self.threshold = threshold_for_notification
        self.history: list[dict[str, Any]] = []
        # LLM-as-Judge 评判函数 (question, answer, expected) -> score [0, 1]
        # 为 None 时使用关键词匹配评分
        self._judge_fn = judge_fn
        self._scoring_mode = "llm_judge" if judge_fn else "keyword"

    # ─── 主入口 ────────────────────────────────────────

    def evaluate(self, model_predict_fn: Callable[[str], str]) -> dict[str, Any]:
        """
        在测试集上评估模型。

        Args:
            model_predict_fn: 模型预测函数，输入问题字符串，返回回答字符串

        Returns:
            评估结果字典
        """
        test_set = self._load_test_set()
        if not test_set:
            return {"error": "测试集为空或不存在", "accuracy": 0, "total": 0}

        logger.info(f"[{self.__class__.__name__}] 开始评估，测试集: {len(test_set)} 题")

        correct = 0
        results = []
        latency_list = []

        for item in test_set:
            question = item.get("question") or item.get("input") or ""
            expected = item.get("expected") or item.get("output") or ""

            if not question:
                continue

            start = time.time()
            try:
                answer = model_predict_fn(question)
            except Exception as e:
                logger.warning(f"  预测失败: {question[:30]}... → {e}")
                answer = ""
            latency = (time.time() - start) * 1000
            latency_list.append(latency)

            # 评分
            score = self._score(answer, expected, question)
            if score >= 0.8:
                correct += 1

            results.append({
                "question": question[:100],
                "expected": expected[:200],
                "actual": answer[:200],
                "score": round(score, 2),
                "latency_ms": round(latency, 1),
            })

        accuracy = correct / len(results) if results else 0

        report = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(results),
            "correct": correct,
            "accuracy": round(accuracy, 4),
            "avg_latency_ms": round(sum(latency_list) / len(latency_list), 1) if latency_list else 0,
            "p95_latency_ms": round(self._percentile(latency_list, 95), 1) if latency_list else 0,
            "details": results,
        }

        # 保存报告
        self._save_report(report)
        self.history.append({
            "timestamp": report["timestamp"],
            "accuracy": report["accuracy"],
            "total": report["total"],
        })

        logger.info(f"  准确率: {accuracy:.1%} ({correct}/{len(results)})")
        logger.info(f"  平均延迟: {report['avg_latency_ms']}ms | P95: {report['p95_latency_ms']}ms")

        return report

    def weekly_run(self, model_predict_fn: Callable[[str], str]) -> dict[str, Any]:
        """
        每周评估任务（由调度器调用）。

        评估后仅通知管理员，不自动 promote 模型。
        """
        report = self.evaluate(model_predict_fn)

        # 比较历史最佳
        if self.history:
            best = max(h["accuracy"] for h in self.history[:-1]) if len(self.history) > 1 else 0
            diff = report["accuracy"] - best
            if diff > self.threshold:
                notification = (
                    f"📈 模型准确率提升至 {report['accuracy']:.1%} "
                    f"(+{diff:.1%})，建议启动离线训练或切换模型。"
                )
            elif diff < -self.threshold:
                notification = (
                    f"📉 模型准确率下降至 {report['accuracy']:.1%} "
                    f"({diff:.1%})，建议检查知识库或模型状态。"
                )
            else:
                notification = f"📊 本周评估完成，准确率 {report['accuracy']:.1%}（变化 {diff:+.1%}）"
        else:
            notification = f"📊 首次评估完成，准确率 {report['accuracy']:.1%}"

        logger.info(f"[{self.__class__.__name__}] {notification}")

        report["notification"] = notification
        return report

    # ─── A/B 测试 ────────────────────────────────────────

    def run_ab_test(
        self,
        model_a_fn: Callable[[str], str],
        model_b_fn: Callable[[str], str],
        test_set: (list[Dict] | None) = None,
        label_a: str = "RAG-only",
        label_b: str = "RAG+LoRA",
    ) -> dict[str, Any]:
        """
        对比两个模型的表现。

        Args:
            model_a_fn: 模型 A 的预测函数
            model_b_fn: 模型 B 的预测函数
            test_set: 测试集（不传则使用默认）
            label_a/b: 模型标签

        Returns:
            A/B 测试结果
        """
        if test_set is None:
            test_set = self._load_test_set()

        if not test_set:
            return {"error": "测试集为空"}

        logger.info(f"[{self.__class__.__name__}] A/B 测试: {label_a} vs {label_b} ({len(test_set)} 题)")

        results_a = []
        results_b = []

        for item in test_set:
            question = item.get("question") or item.get("input") or ""
            expected = item.get("expected") or item.get("output") or ""
            if not question:
                # 跳过空问题但保持索引对齐，避免 _bucket_analysis 越界
                results_a.append(0)
                results_b.append(0)
                continue

            # 模型 A
            try:
                ans_a = model_a_fn(question)
                score_a = self._score(ans_a, expected, question)
            except Exception as e:
                logger.debug(f"Model A failed on question '{question[:50]}': {e}")
                ans_a = ""
                score_a = 0
            results_a.append(score_a)

            # 模型 B
            try:
                ans_b = model_b_fn(question)
                score_b = self._score(ans_b, expected, question)
            except Exception as e:
                logger.debug(f"Model B failed on question '{question[:50]}': {e}")
                ans_b = ""
                score_b = 0
            results_b.append(score_b)

        avg_a = sum(results_a) / len(results_a) if results_a else 0
        avg_b = sum(results_b) / len(results_b) if results_b else 0

        # 分桶分析：高频 vs 低频
        bucket_analysis = self._bucket_analysis(test_set, results_a, results_b)

        ab_result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(results_a),
            "label_a": label_a,
            "label_b": label_b,
            f"{label_a}_accuracy": round(avg_a, 4),
            f"{label_b}_accuracy": round(avg_b, 4),
            "improvement": round(avg_b - avg_a, 4),
            "improvement_pct": round((avg_b - avg_a) * 100, 1),
            "bucket_analysis": bucket_analysis,
        }

        logger.info(f"  {label_a}: {avg_a:.1%} | {label_b}: {avg_b:.1%} | "
                     f"提升: {ab_result['improvement_pct']:+.1f}%")

        # 保存 A/B 报告
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.report_dir / f"ab_test_{time.strftime('%Y%m%d_%H%M')}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(ab_result, f, indent=2, ensure_ascii=False)

        return ab_result

    # ─── 评分方法 ────────────────────────────────────────

    def _score(self, answer: str, expected: str, question: str = "") -> float:
        """
        多维度评分（0-1）。

        优先使用 LLM-as-Judge（若已注入 judge_fn），
        调用失败或未注入时降级到关键词匹配评分。

        关键词匹配维度：
        - 覆盖度（40%）：expected 中关键片段在 answer 中的命中率
        - 精确包含（20% bonus）：answer 完全包含 expected 核心内容
        - 不包含否定词（20%）
        - 长度合理（20%）
        """
        if not answer or not expected:
            return 0.0

        # 优先：LLM-as-Judge
        if self._judge_fn is not None:
            try:
                score = self._judge_fn(question, answer, expected)
                if 0.0 <= score <= 1.0:
                    return score
                logger.warning(f"  [LLM-Judge] 返回值越界: {score}，降级到关键词匹配")
            except Exception as e:
                logger.warning(f"  [LLM-Judge] 评判失败: {e}，降级到关键词匹配")

        # 降级：关键词匹配
        return self._keyword_score(answer, expected)

    def _keyword_score(self, answer: str, expected: str) -> float:
        """关键词匹配评分（LLM-Judge 降级方案）"""

        score = 0.0

        # 1. 片段覆盖度
        fragments = self._extract_fragments(expected)
        if fragments:
            hits = sum(1 for frag in fragments if frag in answer)
            coverage = hits / len(fragments)
            score += 0.4 * coverage

            # bonus：如果覆盖率 >= 50%，额外加分
            if coverage >= 0.5:
                score += 0.2

        # 2. 否定检查
        negation_words = ["不知道", "无法", "抱歉", "暂未找到"]
        if not any(nw in answer for nw in negation_words):
            score += 0.2
        elif "暂未找到相关信息" in answer:
            score += 0.15

        # 3. 长度合理（5-500 字符为佳）
        length = len(answer)
        if 5 <= length <= 500:
            score += 0.2
        elif length > 500 and length <= 800:
            score += 0.1

        return min(score, 1.0)

    def _extract_fragments(self, text: str, min_len: int = 2) -> list[str]:
        """
        将文本按标点/空格切分为有意义的片段。
        优先保留带实质内容的片段（中文 >=3 字，英文 >=3 字符）。
        """
        import re
        fragments = []

        # 先按中文标点切分
        parts = re.split(r'[，。、；：！？（）《》\-\—\s]+', text)
        for part in parts:
            part = part.strip()
            if len(part) < min_len:
                continue

            # 纯中文部分（去掉数字和标点后）
            cn_only = re.sub(r'[a-zA-Z0-9\s\-]', '', part)
            cn_only = re.sub(r'[^\u4e00-\u9fff]', '', cn_only)

            if len(cn_only) >= 3:
                fragments.append(cn_only)
            elif len(cn_only) >= 2 and len(part) >= 3:
                fragments.append(part)
            elif re.search(r'[a-zA-Z]{3,}', part):
                # 英文单词 >= 3 字符
                eng = re.findall(r'[a-zA-Z]{3,}', part.lower())
                fragments.extend(eng)
            elif len(part) >= 3:
                fragments.append(part)

        # 去重保序
        seen = set()
        unique = []
        for f in fragments:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        return unique[:8]  # 最多 8 个片段

    def _bucket_analysis(
        self,
        test_set: list[Dict],
        scores_a: list[float],
        scores_b: list[float],
    ) -> dict[str, dict[str, float]]:
        """分桶分析：高频 vs 低频问题"""
        # 简化：按问题长度分桶
        short = []  # 短问题（< 15 字符）
        long = []   # 长问题（>= 15 字符）

        for i, item in enumerate(test_set):
            q = item.get("question") or item.get("input") or ""
            if len(q) < 15:
                short.append(i)
            else:
                long.append(i)

        result = {}
        if short:
            avg_a = sum(scores_a[i] for i in short) / len(short)
            avg_b = sum(scores_b[i] for i in short) / len(short)
            result["short_questions"] = {
                "count": len(short),
                "a_score": round(avg_a, 4),
                "b_score": round(avg_b, 4),
                "improvement": round(avg_b - avg_a, 4),
            }
        if long:
            avg_a = sum(scores_a[i] for i in long) / len(long)
            avg_b = sum(scores_b[i] for i in long) / len(long)
            result["long_questions"] = {
                "count": len(long),
                "a_score": round(avg_a, 4),
                "b_score": round(avg_b, 4),
                "improvement": round(avg_b - avg_a, 4),
            }
        return result

    # ─── 工具方法 ────────────────────────────────────────

    def _load_test_set(self) -> list[Dict]:
        """加载测试集"""
        if not self.test_set_path.exists():
            # 尝试从知识库生成简单测试集
            return self._generate_test_set()
        items = []
        with open(self.test_set_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items

    def _generate_test_set(self) -> list[Dict]:
        """从知识库自动生成测试集（80% 训练 / 20% 测试分割）"""
        kb_path = Path("./data/knowledge/documents.jsonl")
        if not kb_path.exists():
            return []

        items = []
        with open(kb_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    doc = json.loads(line)
                    q = doc.get("question") or doc.get("title") or ""
                    a = doc.get("answer") or doc.get("content") or ""
                    if q and a:
                        items.append({"question": q, "expected": a})

        random.seed(42)
        random.shuffle(items)
        split = int(len(items) * 0.2)
        return items[:split]

    def _save_report(self, report: dict[str, Any]):
        """保存评估报告"""
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.report_dir / f"eval_{time.strftime('%Y%m%d_%H%M')}.json"
        # 去掉 details 中的大字段以节省空间
        save_report = {k: v for k, v in report.items() if k != "details"}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(save_report, f, indent=2, ensure_ascii=False)
        logger.info(f"  报告已保存: {report_path}")

    def _percentile(self, data: list[float], p: float) -> float:
        """计算百分位数"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        idx = min(idx, len(sorted_data) - 1)
        return sorted_data[idx]

    def get_history(self) -> list[dict[str, Any]]:
        """获取评估历史"""
        return list(self.history)

    # ─── P1-B: 评估后自动 promote ─────────────────────

    def evaluate_and_promote(
        self,
        current_fn: Callable[[str], str],
        candidate_fn: Callable[[str], str],
        adapter_name: str,
        promote_fn: (Callable[[str, dict[str, Any]], bool] | None) = None,
        label_a: str = "current",
        label_b: str = "candidate",
    ) -> dict[str, Any]:
        """
        对比当前模型与候选模型（新 adapter），若候选胜出则自动 promote。

        判定标准:
          - candidate accuracy - current accuracy >= threshold (默认 0.02)
          - 且 candidate accuracy 不低于历史最佳

        Args:
            current_fn: 当前模型的预测函数
            candidate_fn: 候选模型的预测函数
            adapter_name: 候选 adapter 名称（用于 promote）
            promote_fn: promote 回调，签名 (adapter_name, ab_result) -> bool
                        返回 True 表示 promote 成功
            label_a/b: A/B 测试标签

        Returns:
            {
                "ab_result": {...},
                "promoted": bool,
                "promote_reason": str,
            }
        """
        # 运行 A/B 测试
        ab_result = self.run_ab_test(
            model_a_fn=current_fn,
            model_b_fn=candidate_fn,
            label_a=label_a,
            label_b=label_b,
        )

        if "error" in ab_result:
            return {
                "ab_result": ab_result,
                "promoted": False,
                "promote_reason": f"A/B 测试失败: {ab_result['error']}",
            }

        improvement = ab_result.get("improvement", 0)
        candidate_acc = ab_result.get(f"{label_b}_accuracy", 0)

        # 检查历史最佳
        best_historical = max(
            (h["accuracy"] for h in self.history[:-1]),
            default=0,
        ) if len(self.history) > 1 else 0

        promoted = False
        reason = ""

        if improvement >= self.threshold:
            if candidate_acc >= best_historical:
                # 满足 promote 条件
                if promote_fn is not None:
                    try:
                        promoted = promote_fn(adapter_name, ab_result)
                        if promoted:
                            reason = (
                                f"候选模型准确率 {candidate_acc:.1%} 超过当前模型 "
                                f"(提升 {improvement:+.1%})，且不低于历史最佳 "
                                f"{best_historical:.1%}，已自动 promote"
                            )
                        else:
                            reason = f"promote 回调返回 False，可能切换失败"
                    except Exception as e:
                        reason = f"promote 回调异常: {e}"
                else:
                    reason = (
                        f"候选模型胜出 (提升 {improvement:+.1%})，"
                        f"但未提供 promote_fn，仅记录不切换"
                    )
            else:
                reason = (
                    f"候选模型提升 {improvement:+.1%} 但准确率 {candidate_acc:.1%} "
                    f"低于历史最佳 {best_historical:.1%}，不 promote"
                )
        else:
            reason = (
                f"候选模型提升 {improvement:+.1%} 不足 "
                f"(需 >= {self.threshold:.1%})，不 promote"
            )

        logger.info(f"[EvaluationPipeline] promote 判定: {reason}")

        return {
            "ab_result": ab_result,
            "promoted": promoted,
            "promote_reason": reason,
            "candidate_accuracy": candidate_acc,
            "current_best": best_historical,
            "threshold": self.threshold,
        }

    # ─── LLM-as-Judge ───────────────────────────────────

    @staticmethod
    def create_llm_judge(
        llm_chat_fn: Callable[[str], str],
        max_retries: int = 1,
    ) -> Callable[[str, str, str], float]:
        """
        工厂方法：将 LLM 对话函数封装为 judge_fn。

        Args:
            llm_chat_fn: 输入 prompt 字符串，返回回答字符串的 LLM 接口
            max_retries: 解析失败时的重试次数

        Returns:
            judge_fn(question, answer, expected) -> float [0, 1]

        使用示例:
            from plugins.llm.plugin import LLMPlugin
            llm = LLMPlugin(...)
            evaluator = EvaluationPipeline(
                judge_fn=EvaluationPipeline.create_llm_judge(llm.chat),
            )
        """
        import re

        def judge_fn(question: str, answer: str, expected: str) -> float:
            prompt = EvaluationPipeline._build_judge_prompt(question, answer, expected)

            for attempt in range(max_retries + 1):
                try:
                    raw = llm_chat_fn(prompt)
                    score = EvaluationPipeline._parse_judge_response(raw)
                    if score is not None:
                        return score
                    if attempt < max_retries:
                        logger.debug(f"  [LLM-Judge] 第 {attempt+1} 次解析失败，重试...")
                except Exception as e:
                    if attempt < max_retries:
                        logger.debug(f"  [LLM-Judge] 第 {attempt+1} 次调用异常: {e}")
                        continue
                    raise

            # 所有重试均失败，抛出异常由调用方降级
            raise ValueError("LLM-Judge 响应解析失败，无法提取分数")

        return judge_fn

    @staticmethod
    def _build_judge_prompt(question: str, answer: str, expected: str) -> str:
        """构造 LLM-as-Judge 评分 prompt"""
        return (
            "你是一个严格的评估员。请根据以下信息对回答进行评分（0.0 到 1.0）。\n\n"
            f"问题：{question}\n"
            f"参考答案：{expected}\n"
            f"待评回答：{answer}\n\n"
            "评分维度：\n"
            "1. 准确性（40%）：回答是否与参考答案一致，无事实错误\n"
            "2. 完整性（30%）：回答是否覆盖了参考答案的要点\n"
            "3. 简洁性（20%）：回答是否简洁明了，无冗余信息\n"
            "4. 语言流畅性（10%）：回答是否通顺、无语法错误\n\n"
            "请只返回一个 0.0 到 1.0 之间的数字（保留两位小数），不要包含其他文字。"
        )

    @staticmethod
    def _parse_judge_response(raw: str) -> (float | None):
        """从 LLM 回答中提取分数"""
        import re

        if not raw:
            return None

        raw = raw.strip()

        # 尝试直接解析纯数字
        try:
            val = float(raw)
            if 0.0 <= val <= 1.0:
                return val
        except ValueError:
            pass

        # 尝试从文本中提取数字（如 "0.85" / "评分：0.85" / "score: 0.85"）
        patterns = [
            r'(?:评分|分数|得分|score)[:\s]*([01]?\.\d+)',
            r'([01]\.\d{1,2})',
            r'([01]\.\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, raw, re.IGNORECASE)
            if match:
                try:
                    val = float(match.group(1))
                    if 0.0 <= val <= 1.0:
                        return val
                except ValueError:
                    continue

        return None


class ABTester:
    """
    A/B 测试器（简化封装）。

    使用示例：
        tester = ABTester()
        result = tester.compare(
            model_a=rag_only.predict,
            model_b=rag_lora.predict,
            test_set=test_data,
        )
    """

    def __init__(self, evaluator: (EvaluationPipeline | None) = None):
        self.evaluator = evaluator or EvaluationPipeline()

    def compare(
        self,
        model_a_fn: Callable[[str], str],
        model_b_fn: Callable[[str], str],
        test_set: (list[Dict] | None) = None,
        label_a: str = "Model-A",
        label_b: str = "Model-B",
    ) -> dict[str, Any]:
        """执行 A/B 对比"""
        return self.evaluator.run_ab_test(
            model_a_fn=model_a_fn,
            model_b_fn=model_b_fn,
            test_set=test_set,
            label_a=label_a,
            label_b=label_b,
        )
