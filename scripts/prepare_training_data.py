"""
EchoServe - Training Data Preparation Script
===============================================
Convert knowledge base (documents.jsonl) to LoRA training dataset.

Usage:
    cd /d/llm_learn/OmniZee-B/OmniZee
    python scripts/prepare_training_data.py

Output:
    data/training/train.jsonl     - 80% training data
    data/training/test.jsonl      - 20% test data
    data/training/train_summary.json - dataset statistics

Format: Alpaca (instruction + input + output)
"""

import json
import re
import random
from pathlib import Path
from typing import List, Dict, Any
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────
KB_PATH = Path("data/knowledge/documents.jsonl")
OUTPUT_DIR = Path("data/training")
TRAIN_RATIO = 0.8
VARIANTS_PER_Q = 3           # 每问题生成同义变体数（0=不生成）
GENERIC_RATIO = 0.15         # 通用数据混入比例

# ─── System Prompt ───────────────────────────────────
SYSTEM_PROMPT = (
    "你是一个专业的智能客服助手，熟悉公司产品、政策和服务。"
    "请根据客户的问题，给出准确、简洁、有帮助的回答。"
    "保持友好、专业的语气。"
)

# ─── Generic conversation data (anti-forgetting) ─────
GENERIC_DATA = [
    {"instruction": SYSTEM_PROMPT, "input": "你好", "output": "你好！我是EchoServe智能助手，有什么可以帮您的？"},
    {"instruction": SYSTEM_PROMPT, "input": "谢谢", "output": "不客气，很高兴能帮到您！"},
    {"instruction": SYSTEM_PROMPT, "input": "再见", "output": "再见，祝您有美好的一天！"},
    {"instruction": SYSTEM_PROMPT, "input": "你叫什么名字", "output": "我是EchoServe，您的智能客服助手。"},
    {"instruction": SYSTEM_PROMPT, "input": "你能做什么", "output": "我可以回答关于公司产品、政策、服务流程等各类问题。"},
    {"instruction": SYSTEM_PROMPT, "input": "今天天气怎么样", "output": "抱歉，我无法查询实时天气。请问有什么业务问题可以帮您？"},
    {"instruction": SYSTEM_PROMPT, "input": "你是谁开发的", "output": "我是由EchoServe团队开发的智能客服系统。"},
    {"instruction": SYSTEM_PROMPT, "input": "帮我写个邮件", "output": "抱歉，我的专长是回答公司业务相关问题。邮件撰写建议咨询其他工具。"},
]


def load_kb(path: Path) -> List[Dict[str, str]]:
    """从知识库加载 QA 对"""
    qa_pairs = []

    if not path.exists():
        logger.error(f"Knowledge base not found: {path}")
        return qa_pairs

    logger.info(f"Loading knowledge base: {path}")
    total = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                doc = json.loads(line)
                content = doc.get("content", "")
                metadata = doc.get("metadata", {})
                
                # Parse "问题：xxx\n回答：xxx" format
                q_match = re.search(r'问题[:：]\s*(.+?)(?:\n|$)', content, re.DOTALL)
                a_match = re.search(r'回答[:：]\s*(.+)', content, re.DOTALL)

                if q_match and a_match:
                    q = q_match.group(1).strip()
                    a = a_match.group(1).strip()
                    if q and a and len(q) > 1 and len(a) > 1:
                        qa_pairs.append({"question": q, "answer": a})
                else:
                    # Fallback: split by newline
                    lines = content.strip().split('\n', 1)
                    if len(lines) >= 2 and lines[0] and lines[1]:
                        qa_pairs.append({
                            "question": lines[0][:200].strip(),
                            "answer": lines[1][:2000].strip(),
                        })
            except json.JSONDecodeError:
                logger.warning(f"  Skip invalid JSON line #{total}")
                continue

    logger.info(f"  Loaded {len(qa_pairs)}/{total} valid QA pairs")
    return qa_pairs


def generate_variants(question: str, n: int = 3) -> List[str]:
    """Generate synonym variants using simple templates (no LLM needed)"""
    if n <= 0:
        return []

    templates = [
        "请问{q}",
        "我想了解一下{q}",
        "麻烦告诉我{q}",
        "{q}是怎样的？",
        "能帮我解答一下{q}吗？",
        "关于{q}我有些疑问",
        "{q}具体是什么意思？",
    ]

    q_clean = question.rstrip("?？")
    random.shuffle(templates)
    variants = []
    for t in templates[:n]:
        v = t.format(q=q_clean)
        if not v.endswith(("?", "？")):
            v += "？"
        variants.append(v)
    return variants


def to_alpaca(instruction: str, question: str, answer: str) -> Dict[str, str]:
    """Convert to Alpaca training format"""
    return {
        "instruction": instruction.strip(),
        "input": question.strip(),
        "output": answer.strip(),
    }


def build_dataset(qa_pairs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Build full training dataset with variants"""
    logger.info("Building training dataset...")
    dataset = []

    # 1. Add all QA pairs + variants
    for qa in qa_pairs:
        # Original QA
        dataset.append(to_alpaca(SYSTEM_PROMPT, qa["question"], qa["answer"]))

        # Synonym variants
        variants = generate_variants(qa["question"], n=VARIANTS_PER_Q)
        for v in variants:
            dataset.append(to_alpaca(SYSTEM_PROMPT, v, qa["answer"]))

    logger.info(f"  After variants: {len(dataset)} samples")

    # 2. Mix generic data (anti-forgetting)
    num_generic = int(len(dataset) * GENERIC_RATIO / (1 - GENERIC_RATIO))
    if num_generic > 0:
        sampled_generic = random.choices(GENERIC_DATA, k=min(num_generic, len(GENERIC_DATA)))
        dataset.extend(sampled_generic)
        logger.info(f"  Added {len(sampled_generic)} generic samples (anti-forgetting)")

    # 3. Shuffle
    random.seed(42)
    random.shuffle(dataset)
    logger.info(f"  Total dataset: {len(dataset)} samples")

    return dataset


def split_dataset(dataset: List[Dict[str, str]], ratio: float = 0.8):
    """Split into train/test"""
    split_idx = int(len(dataset) * ratio)
    train = dataset[:split_idx]
    test = dataset[split_idx:]
    return train, test


def save_jsonl(data: List[Dict], path: Path):
    """Save as JSONL"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info(f"  Saved: {path} ({len(data)} records)")


def validate_dataset(path: Path) -> Dict[str, Any]:
    """Validate dataset quality"""
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
                if "instruction" not in item:
                    issues.append(f"Line {i}: missing 'instruction'")
                    continue
                if "input" not in item or "output" not in item:
                    issues.append(f"Line {i}: missing 'input' or 'output'")
                    continue

                inp = item["input"]
                out = item["output"]
                input_lens.append(len(inp))
                output_lens.append(len(out))

                if len(inp) > 2000:
                    issues.append(f"Line {i}: input too long ({len(inp)} chars)")
                if len(out) > 5000:
                    issues.append(f"Line {i}: output too long ({len(out)} chars)")
                if len(inp) < 2:
                    issues.append(f"Line {i}: input too short")

                valid += 1
            except json.JSONDecodeError:
                issues.append(f"Line {i}: JSON parse error")

    result = {
        "total": total,
        "valid": valid,
        "invalid": total - valid,
        "valid_rate": f"{valid / total * 100:.1f}%" if total else "0%",
        "avg_input_len": round(sum(input_lens) / len(input_lens), 1) if input_lens else 0,
        "avg_output_len": round(sum(output_lens) / len(output_lens), 1) if output_lens else 0,
        "issues": issues[:10],
    }
    return result


def main():
    logger.info("=" * 60)
    logger.info("EchoServe Training Data Preparation")
    logger.info("=" * 60)

    # 1. Load knowledge base
    qa_pairs = load_kb(KB_PATH)
    if not qa_pairs:
        logger.error("No QA pairs loaded! Check knowledge base.")
        return 1

    # 2. Build dataset
    dataset = build_dataset(qa_pairs)

    # 3. Split train/test
    train, test = split_dataset(dataset, TRAIN_RATIO)
    logger.info(f"Train: {len(train)} | Test: {len(test)}")

    # 4. Save
    save_jsonl(train, OUTPUT_DIR / "train.jsonl")
    save_jsonl(test, OUTPUT_DIR / "test.jsonl")

    # 5. Validate
    logger.info("\nValidating train dataset...")
    train_stats = validate_dataset(OUTPUT_DIR / "train.jsonl")
    for k, v in train_stats.items():
        logger.info(f"  {k}: {v}")

    logger.info("\nValidating test dataset...")
    test_stats = validate_dataset(OUTPUT_DIR / "test.jsonl")
    for k, v in test_stats.items():
        logger.info(f"  {k}: {v}")

    # 6. Save summary
    summary = {
        "kb_path": str(KB_PATH),
        "kb_qa_pairs": len(qa_pairs),
        "variants_per_q": VARIANTS_PER_Q,
        "train_size": len(train),
        "test_size": len(test),
        "train_stats": train_stats,
        "test_stats": test_stats,
    }
    with open(OUTPUT_DIR / "train_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info("\n" + "=" * 60)
    logger.info("Done! Next step: python scripts/train_lora.py")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    exit(main())
