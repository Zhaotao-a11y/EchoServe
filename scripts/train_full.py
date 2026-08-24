"""
EchoServe P2 — 全参数微调训练脚本

功能：
  - 使用 DeepSpeed ZeRO-3 或 FSDP 进行全参数微调
  - 支持知识蒸馏（教师模型 → 学生模型）
  - 大规模数据训练（10000+ 条）
  - 需要多卡 GPU 或 A100 级别算力

使用方法：
  # DeepSpeed 方式（推荐，多卡）
  deepspeed --num_gpus=4 train_full.py \
    --model /models/qwen3-14b-q4 \
    --data ./data/training/train_full.jsonl \
    --output ./models/adapters/full-v1

  # FSDP 方式（PyTorch 原生）
  torchrun --nproc_per_node=4 train_full.py \
    --model /models/qwen3-14b-q4 \
    --data ./data/training/train_full.jsonl \
    --output ./models/adapters/full-v1 \
    --method fsdp

依赖：
  - torch >= 2.0
  - transformers >= 4.40
  - datasets >= 2.14
  - deepspeed >= 0.12（使用 DeepSpeed 时）
  - accelerate >= 0.30
"""
from __future__ import annotations

import argparse
import json
import time
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("echoseve.train.full")


# ═══════════════════════════════════════════
#  训练参数配置
# ═══════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(description="EchoServe 全参数微调")

    # 模型与数据
    parser.add_argument("--model", default="/models/qwen3-14b-q4", help="基础模型路径")
    parser.add_argument("--data", default="./data/training/train_full.jsonl", help="训练数据")
    parser.add_argument("--val-data", default="", help="验证数据（不传则自动分割）")
    parser.add_argument("--output", default="./models/adapters/full-v1", help="输出目录")

    # 训练超参数
    parser.add_argument("--epochs", type=int, default=2, help="训练轮数")
    parser.add_argument("--batch-size", type=int, default=1, help="每卡 batch size")
    parser.add_argument("--grad-accum", type=int, default=8, help="梯度累积步数")
    parser.add_argument("--lr", type=float, default=1e-5, help="学习率（全参比 LoRA 小 10x）")
    parser.add_argument("--warmup-ratio", type=float, default=0.03, help="预热比例")
    parser.add_argument("--max-seq-len", type=int, default=2048, help="最大序列长度")
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--grad-clip", type=float, default=1.0)

    # 分布式策略
    parser.add_argument("--method", choices=["deepspeed", "fsdp"], default="deepspeed")
    parser.add_argument("--zero-stage", type=int, default=3, help="DeepSpeed ZeRO 阶段")
    parser.add_argument("--fp16", action="store_true", help="使用 FP16（默认 BF16）")

    # 蒸馏
    parser.add_argument("--distill", action="store_true", help="启用知识蒸馏")
    parser.add_argument("--teacher-model", default="", help="教师模型路径/名称")
    parser.add_argument("--distill-temp", type=float, default=2.0, help="蒸馏温度")
    parser.add_argument("--distill-alpha", type=float, default=0.5, help="蒸馏损失权重")

    # 其他
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save-steps", type=int, default=200)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--logging-steps", type=int, default=10)

    return parser.parse_args()


# ═══════════════════════════════════════════
#  DeepSpeed 配置生成
# ═══════════════════════════════════════════

def generate_deepspeed_config(args, num_gpus: int) -> Dict[str, Any]:
    """生成 DeepSpeed ZeRO 配置"""
    config = {
        "fp16": {
            "enabled": args.fp16,
        },
        "bf16": {
            "enabled": not args.fp16,
        },
        "optimizer": {
            "type": "AdamW",
            "params": {
                "lr": args.lr,
                "betas": [0.9, 0.95],
                "eps": 1e-8,
                "weight_decay": args.weight_decay,
            },
        },
        "scheduler": {
            "type": "WarmupDecayLR",
            "params": {
                "warmup_min_lr": 0,
                "warmup_max_lr": args.lr,
                "warmup_num_steps": 0,  # 由 Trainer 自动计算
                "total_num_steps": 0,  # 由 Trainer 自动计算
            },
        },
        "zero_optimization": {
            "stage": args.zero_stage,
            "offload_optimizer": {
                "device": "cpu" if args.zero_stage >= 3 else "none",
            },
            "offload_param": {
                "device": "cpu" if args.zero_stage >= 3 else "none",
            },
            "overlap_comm": True,
            "contiguous_gradients": True,
            "sub_group_size": 1e9,
            "reduce_bucket_size": 5e7,
            "stage3_prefetch_bucket_size": 5e7,
            "stage3_param_persistence_threshold": 1e5,
            "stage3_max_live_parameters": 1e9,
            "stage3_max_reuse_distance": 1e9,
        },
        "gradient_clipping": args.grad_clip,
        "train_batch_size": args.batch_size * num_gpus * args.grad_accum,
        "train_micro_batch_size_per_gpu": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "steps_per_print": args.logging_steps,
        "wall_clock_breakdown": False,
    }

    return config


# ═══════════════════════════════════════════
#  数据准备
# ═══════════════════════════════════════════

def load_dataset(data_path: str, val_path: str = "", train_ratio: float = 0.95):
    """加载训练数据"""
    from datasets import Dataset

    items = []
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"训练数据不存在: {data_path}")

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    logger.info(f"加载训练数据: {len(items)} 条")

    # 分割验证集
    if val_path and Path(val_path).exists():
        val_items = []
        with open(val_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    val_items.append(json.loads(line))
        train_items = items
    else:
        import random
        random.seed(42)
        random.shuffle(items)
        split = int(len(items) * train_ratio)
        train_items = items[:split]
        val_items = items[split:]
        logger.info(f"自动分割: 训练 {len(train_items)} | 验证 {len(val_items)}")

    # 统一格式
    def format_item(item):
        return {
            "instruction": item.get("instruction", "请根据公司知识库回答以下问题："),
            "input": item.get("input", item.get("question", "")),
            "output": item.get("output", item.get("answer", "")),
        }

    train_ds = Dataset.from_list([format_item(i) for i in train_items])
    val_ds = Dataset.from_list([format_item(i) for i in val_items]) if val_items else None

    return train_ds, val_ds


def tokenize_function(examples, tokenizer, max_length: int):
    """Tokenize 函数"""
    prompts = []
    for instr, inp, out in zip(
        examples["instruction"], examples["input"], examples["output"]
    ):
        prompt = f"{instr}\n\n问题: {inp}\n\n回答: {out}"
        prompts.append(prompt)

    tokenized = tokenizer(
        prompts,
        truncation=True,
        max_length=max_length,
        padding=False,
    )
    tokenized["labels"] = tokenized["input_ids"].copy()
    return tokenized


# ═══════════════════════════════════════════
#  蒸馏训练（教师-学生）
# ═══════════════════════════════════════════

def setup_distillation(model, teacher_model_name: str, tokenizer, device):
    """设置知识蒸馏"""
    import torch
    from transformers import AutoModelForCausalLM

    logger.info(f"加载教师模型: {teacher_model_name}")
    teacher = AutoModelForCausalLM.from_pretrained(
        teacher_model_name,
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )
    teacher.eval()

    return teacher


def distillation_loss(
    student_logits, teacher_logits, labels, alpha: float, temp: float
):
    """计算蒸馏损失 = α * KL散度 + (1-α) * 任务损失"""
    import torch
    import torch.nn as nn

    # KL 散度损失（软标签）
    kl_loss = nn.KLDivLoss(reduction="batchmean")(
        torch.log_softmax(student_logits / temp, dim=-1),
        torch.softmax(teacher_logits / temp, dim=-1),
    ) * (temp * temp)

    # 任务损失（硬标签）
    ce_loss = nn.CrossEntropyLoss()(
        student_logits.view(-1, student_logits.size(-1)),
        labels.view(-1),
    )

    return alpha * kl_loss + (1 - alpha) * ce_loss


# ═══════════════════════════════════════════
#  主训练流程
# ═══════════════════════════════════════════

def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 检测 GPU 数量
    import torch
    num_gpus = torch.cuda.device_count()
    logger.info(f"检测到 {num_gpus} 张 GPU")
    logger.info(f"训练方法: {args.method} (ZeRO-Stage {args.zero_stage})")

    if num_gpus < 2 and args.zero_stage >= 2:
        logger.warning(f"ZeRO-Stage {args.zero_stage} 推荐多卡，当前仅 {num_gpus} 卡")

    # 1. 加载 tokenizer
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # 2. 加载模型
    from transformers import AutoModelForCausalLM
    logger.info(f"加载基础模型: {args.model}")
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )

    # 3. 加载数据
    train_ds, val_ds = load_dataset(args.data, args.val_data)
    train_ds = train_ds.map(
        lambda x: tokenize_function(x, tokenizer, args.max_seq_len),
        batched=True,
        remove_columns=train_ds.column_names,
    )
    if val_ds:
        val_ds = val_ds.map(
            lambda x: tokenize_function(x, tokenizer, args.max_seq_len),
            batched=True,
            remove_columns=val_ds.column_names,
        )

    # 4. 设置蒸馏
    teacher = None
    if args.distill and args.teacher_model:
        teacher = setup_distillation(model, args.teacher_model, tokenizer, "cuda")
        logger.info(f"蒸馏模式: α={args.distill_alpha}, T={args.distill_temp}")

    # 5. 生成 DeepSpeed 配置
    if args.method == "deepspeed":
        ds_config = generate_deepspeed_config(args, num_gpus)
        ds_config_path = output_dir / "deepspeed_config.json"
        with open(ds_config_path, "w") as f:
            json.dump(ds_config, f, indent=2)
        logger.info(f"DeepSpeed 配置: {ds_config_path}")

    # 6. 训练参数
    from transformers import TrainingArguments

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        max_grad_norm=args.grad_clip,
        bf16=not args.fp16,
        fp16=args.fp16,
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        save_steps=args.save_steps,
        save_total_limit=3,
        evaluation_strategy="steps" if val_ds else "no",
        save_strategy="steps",
        load_best_model_at_end=True if val_ds else False,
        metric_for_best_model="eval_loss" if val_ds else None,
        report_to="none",
        seed=args.seed,
        deepspeed=str(ds_config_path) if args.method == "deepspeed" else None,
        fsdp="full_shard auto_wrap" if args.method == "fsdp" else None,
    )

    # 7. 自定义 Trainer（支持蒸馏）
    from transformers import Trainer

    if teacher:
        class DistillTrainer(Trainer):
            """支持知识蒸馏的 Trainer"""

            def __init__(self, *args, teacher_model=None, distill_alpha=0.5, distill_temp=2.0, **kwargs):
                super().__init__(*args, **kwargs)
                self.teacher = teacher_model
                self.distill_alpha = distill_alpha
                self.distill_temp = distill_temp

            def compute_loss(self, model, inputs, return_outputs=False):
                import torch

                labels = inputs.get("labels")
                outputs = model(**inputs)
                student_logits = outputs.logits

                if self.teacher and labels is not None:
                    with torch.no_grad():
                        teacher_outputs = self.teacher(**inputs)
                        teacher_logits = teacher_outputs.logits

                    loss = distillation_loss(
                        student_logits, teacher_logits, labels,
                        self.distill_alpha, self.distill_temp,
                    )
                else:
                    loss = outputs.loss

                return (loss, outputs) if return_outputs else loss

        trainer = DistillTrainer(
            model=model,
            args=training_args,
            train_dataset=train_ds,
            eval_dataset=val_ds,
            tokenizer=tokenizer,
            teacher_model=teacher,
            distill_alpha=args.distill_alpha,
            distill_temp=args.distill_temp,
        )
    else:
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=train_ds,
            eval_dataset=val_ds,
            tokenizer=tokenizer,
        )

    # 8. 开始训练
    logger.info("🚀 开始全参数微调...")
    start = time.time()

    trainer.train()

    elapsed = (time.time() - start) / 60
    logger.info(f"✅ 训练完成! 耗时 {elapsed:.1f} 分钟")

    # 9. 保存
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    # 10. 保存训练信息
    info = {
        "type": "full_finetune",
        "base_model": args.model,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "grad_accum": args.grad_accum,
        "learning_rate": args.lr,
        "max_seq_len": args.max_seq_len,
        "method": args.method,
        "zero_stage": args.zero_stage,
        "distillation": args.distill,
        "teacher_model": args.teacher_model if args.distill else None,
        "train_samples": len(train_ds),
        "val_samples": len(val_ds) if val_ds else 0,
        "training_time_minutes": round(elapsed, 1),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(output_dir / "full_finetune_config.json", "w") as f:
        json.dump(info, f, indent=2)

    # 最终评估
    if val_ds:
        eval_result = trainer.evaluate()
        logger.info(f"📊 最终评估: {eval_result}")
        with open(output_dir / "eval_result.json", "w") as f:
            json.dump(eval_result, f, indent=2)

    logger.info(f"📁 模型保存至: {output_dir}")
    logger.info("🎉 全参数微调完成!")


if __name__ == "__main__":
    main()
