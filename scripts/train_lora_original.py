"""
EchoServe P1 — LoRA 微调训练脚本

在训练容器中运行：
    python train_lora.py

或通过 Docker Compose：
    docker compose --profile training up

环境变量：
    BASE_MODEL_PATH    基础模型路径（默认 /models/qwen3-14b-q4）
    TRAIN_DATA_PATH    训练数据路径（默认 /app/data/training/train.jsonl）
    OUTPUT_DIR         Adapter 输出目录（默认 /app/models/adapters/latest）
    LORA_R             LoRA rank（默认 8）
    LORA_ALPHA         LoRA alpha（默认 16）
    LORA_DROPOUT       Dropout（默认 0.05）
    TRAIN_EPOCHS       训练轮数（默认 3）
    TRAIN_BATCH_SIZE   Batch size（默认 2）
    TRAIN_LR           学习率（默认 2e-4）
    MAX_SEQ_LENGTH     最大序列长度（默认 2048）
"""
from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path

import torch
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForSeq2Seq,
)
from peft import LoraConfig, get_peft_model, TaskType

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("train_lora")

# ─── 配置（从环境变量读取）──────────────────────

BASE_MODEL = os.getenv("BASE_MODEL_PATH", "/models/qwen3-14b-q4")
TRAIN_DATA = os.getenv("TRAIN_DATA_PATH", "/app/data/training/train.jsonl")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/models/adapters/latest")

LORA_R = int(os.getenv("LORA_R", "8"))
LORA_ALPHA = int(os.getenv("LORA_ALPHA", "16"))
LORA_DROPOUT = float(os.getenv("LORA_DROPOUT", "0.05"))
TARGET_MODULES = os.getenv("TARGET_MODULES", "q_proj,v_proj").split(",")

EPOCHS = int(os.getenv("TRAIN_EPOCHS", "3"))
BATCH_SIZE = int(os.getenv("TRAIN_BATCH_SIZE", "2"))
GRAD_ACCUM = int(os.getenv("GRAD_ACCUM", "4"))
LR = float(os.getenv("TRAIN_LR", "2e-4"))
WARMUP_RATIO = float(os.getenv("WARMUP_RATIO", "0.1"))
MAX_SEQ = int(os.getenv("MAX_SEQ_LENGTH", "2048"))

# ─── 数据集 ────────────────────────────────────────

class AlpacaDataset(Dataset):
    """Alpaca 格式数据集"""

    def __init__(self, data_path: str, tokenizer, max_length: int = 2048):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.items = []

        path = Path(data_path)
        if not path.exists():
            raise FileNotFoundError(f"训练数据不存在: {data_path}")

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.items.append(json.loads(line))

        logger.info(f"  加载 {len(self.items)} 条训练数据")

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        item = self.items[idx]

        instruction = item.get("instruction", "")
        input_text = item.get("input", "")
        output_text = item.get("output", "")

        # 构建对话格式（适配 Qwen 等模型的 chat template）
        if input_text:
            prompt = f"{instruction}\n{input_text}"
        else:
            prompt = instruction

        # 使用 chat template
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": output_text},
        ]

        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )

        encoding = self.tokenizer(
            text,
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
            return_tensors="pt",
        )

        input_ids = encoding["input_ids"].squeeze()
        attention_mask = encoding["attention_mask"].squeeze()

        # Labels：将 prompt 部分设为 -100（不计算 loss）
        labels = input_ids.clone()
        # 找到 assistant 回答开始的位置
        # 简单方案：将 prompt 部分的 label 设为 -100
        prompt_text = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        prompt_ids = self.tokenizer(
            prompt_text,
            max_length=self.max_length,
            truncation=True,
            return_tensors="pt",
        )["input_ids"].squeeze()

        labels[:len(prompt_ids)] = -100

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }


# ─── 主训练流程 ────────────────────────────────────

def main():
    start = time.time()

    logger.info("=" * 50)
    logger.info("  EchoServe LoRA Fine-tuning")
    logger.info(f"  Base model: {BASE_MODEL}")
    logger.info(f"  Training data: {TRAIN_DATA}")
    logger.info(f"  Output: {OUTPUT_DIR}")
    logger.info("=" * 50)

    # 1. 加载 tokenizer
    logger.info("  加载 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # 2. 加载模型（4-bit 量化以节省显存）
    logger.info("  加载基础模型（4-bit 量化）...")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_type="nf4",
    )

    # 3. 配置 LoRA
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=TARGET_MODULES,
        bias="none",
    )

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # 4. 加载数据
    dataset = AlpacaDataset(TRAIN_DATA, tokenizer, max_length=MAX_SEQ)

    # 5. 训练参数
    output = Path(OUTPUT_DIR)
    output.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        warmup_ratio=WARMUP_RATIO,
        logging_steps=10,
        save_steps=50,
        save_total_limit=3,
        eval_strategy="no",
        fp16=True,
        optim="paged_adamw_8bit",
        gradient_checkpointing=True,
        report_to="tensorboard",
        logging_dir=str(output / "logs"),
    )

    # 6. 创建 Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            padding=True,
            pad_to_multiple_of=8,
        ),
    )

    # 7. 训练
    logger.info("  开始训练...")
    trainer.train()

    # 8. 保存 adapter
    adapter_path = output / "adapter"
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))

    # 9. 保存训练元信息
    meta = {
        "base_model": BASE_MODEL,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "lora_dropout": LORA_DROPOUT,
        "target_modules": TARGET_MODULES,
        "train_data": TRAIN_DATA,
        "train_samples": len(dataset),
        "epochs": EPOCHS,
        "batch_size": BATCH_SIZE,
        "learning_rate": LR,
        "training_time_minutes": round((time.time() - start) / 60, 1),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(output / "adapter_config.json", "w") as f:
        json.dump(meta, f, indent=2)

    elapsed = round((time.time() - start) / 60, 1)
    logger.info("=" * 50)
    logger.info(f"  训练完成! 耗时 {elapsed} 分钟")
    logger.info(f"  Adapter 保存至: {adapter_path}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
