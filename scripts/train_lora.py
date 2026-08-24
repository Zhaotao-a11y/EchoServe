"""
EchoServe - LoRA Fine-tuning Script (Standalone)
==================================================
Train LoRA adapter on customer service QA dataset.
Works on both CPU (test mode) and GPU (production).

Usage:
    # CPU test (very slow, for debugging only)
    python scripts/train_lora.py --cpu-test --batch-size 1 --max-samples 50

    # GPU training (rent from AutoDL/RunPod)
    python scripts/train_lora.py --base-model Qwen/Qwen2.5-0.5B-Instruct \
        --output-dir ./models/lora_cs --epochs 3 --batch-size 4 --lora-r 8

Requirements:
    pip install transformers peft datasets accelerate bitsandbytes
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("training.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="EchoServe LoRA Training")

    # Data
    parser.add_argument("--train-data", type=str, default="data/training/train.jsonl")
    parser.add_argument("--test-data", type=str, default="data/training/test.jsonl")
    parser.add_argument("--max-samples", type=int, default=None,
                        help="Limit samples for quick test")

    # Model
    parser.add_argument("--base-model", type=str, default="Qwen/Qwen2.5-0.5B-Instruct",
                        help="HuggingFace model name or local path")
    parser.add_argument("--output-dir", type=str, default="./models/lora_cs")

    # LoRA config
    parser.add_argument("--lora-r", type=int, default=8, help="LoRA rank (4-64)")
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--target-modules", type=str, default="q_proj,k_proj,v_proj,o_proj",
                        help="Comma-separated target modules")

    # Training
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--warmup-steps", type=int, default=100)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--eval-steps", type=int, default=500)

    # Hardware
    parser.add_argument("--cpu-test", action="store_true",
                        help="Run on CPU with tiny batch (debug only)")
    parser.add_argument("--bf16", action="store_true", help="Use bf16 (Ampere GPU+)")
    parser.add_argument("--fp16", action="store_true", help="Use fp16")
    parser.add_argument("--load-in-8bit", action="store_true", help="8-bit quantization")
    parser.add_argument("--load-in-4bit", action="store_true", help="4-bit quantization (QLoRA)")

    return parser.parse_args()


def load_jsonl(path: str, max_samples: Optional[int] = None) -> List[Dict]:
    """Load Alpaca-format JSONL"""
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if max_samples and i >= max_samples:
                break
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Skip invalid JSON at line {i+1}")
    logger.info(f"Loaded {len(data)} samples from {path}")
    return data


def format_alpaca_prompt(instruction: str, input_text: str, output: str) -> str:
    """Format as Qwen2.5 chat template compatible prompt"""
    if input_text:
        prompt = f"<|im_start|>system\n{instruction}\n<|im_end|>\n<|im_start|>user\n{input_text}\n<|im_end|>\n<|im_start|>assistant\n"
    else:
        prompt = f"<|im_start|>system\n{instruction}\n<|im_end|>\n<|im_start|>user\n{instruction}\n<|im_end|>\n<|im_start|>assistant\n"
    full = prompt + output + "<|im_end|>"
    return full


def prepare_dataset(data: List[Dict], tokenizer, max_length: int = 1024):
    """Prepare dataset for training"""
    from datasets import Dataset

    def tokenize_example(example):
        instruction = example.get("instruction", "")
        input_text = example.get("input", "")
        output = example.get("output", "")

        prompt = format_alpaca_prompt(instruction, input_text, output)

        result = tokenizer(
            prompt,
            truncation=True,
            max_length=max_length,
            padding="max_length",
        )
        result["labels"] = result["input_ids"].copy()
        return result

    dataset = Dataset.from_list(data)
    tokenized = dataset.map(
        tokenize_example,
        remove_columns=dataset.column_names,
        desc="Tokenizing",
    )
    return tokenized


def train(args):
    logger.info("=" * 60)
    logger.info("EchoServe LoRA Training")
    logger.info("=" * 60)

    # 1. Check dependencies
    try:
        import torch
        import transformers
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            TrainingArguments,
            Trainer,
            DataCollatorForSeq2Seq,
        )
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from datasets import Dataset
        logger.info(f"PyTorch: {torch.__version__} | CUDA available: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            logger.info(f"GPU: {torch.cuda.get_device_name(0)}")
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        logger.error("Install: pip install transformers peft datasets accelerate bitsandbytes")
        return 1

    # 2. Load data
    train_data = load_jsonl(args.train_data, args.max_samples)
    test_data = load_jsonl(args.test_data, args.max_samples // 5 if args.max_samples else None)

    if not train_data:
        logger.error("No training data found! Run: python scripts/prepare_training_data.py")
        return 1

    # 3. Load tokenizer
    logger.info(f"Loading tokenizer: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    # 4. Load model
    logger.info(f"Loading model: {args.base_model}")
    load_kwargs = {
        "trust_remote_code": True,
        "torch_dtype": torch.bfloat16 if args.bf16 else (torch.float16 if args.fp16 else torch.float32),
    }

    if args.load_in_4bit:
        logger.info("Using 4-bit quantization (QLoRA)")
        load_kwargs["load_in_4bit"] = True
        load_kwargs["bnb_4bit_compute_dtype"] = torch.bfloat16 if args.bf16 else torch.float16
        load_kwargs["bnb_4bit_quant_type"] = "nf4"
        load_kwargs["bnb_4bit_use_double_quant"] = True
    elif args.load_in_8bit:
        logger.info("Using 8-bit quantization")
        load_kwargs["load_in_8bit"] = True

    if args.cpu_test:
        logger.info("CPU test mode: using float32")
        load_kwargs["torch_dtype"] = torch.float32
        load_kwargs["device_map"] = "cpu"
    else:
        load_kwargs["device_map"] = "auto"

    model = AutoModelForCausalLM.from_pretrained(args.base_model, **load_kwargs)

    # 5. Prepare for training (quantization)
    if args.load_in_4bit or args.load_in_8bit:
        logger.info("Preparing model for k-bit training")
        model = prepare_model_for_kbit_training(model)

    # 6. LoRA config
    target_modules = args.target_modules.split(",")
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=target_modules,
        bias="none",
        task_type="CAUSAL_LM",
    )

    logger.info(f"LoRA config: r={args.lora_r}, alpha={args.lora_alpha}, "
                f"dropout={args.lora_dropout}, target={target_modules}")

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # 7. Prepare datasets
    logger.info("Preparing datasets...")
    train_dataset = prepare_dataset(train_data, tokenizer, args.max_length)
    eval_dataset = prepare_dataset(test_data, tokenizer, args.max_length) if test_data else None

    # 8. Training arguments
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.learning_rate,
        warmup_steps=args.warmup_steps,
        logging_steps=10,
        save_steps=args.save_steps,
        eval_steps=args.eval_steps,
        evaluation_strategy="steps" if eval_dataset else "no",
        save_strategy="steps",
        load_best_model_at_end=True if eval_dataset else False,
        metric_for_best_model="eval_loss" if eval_dataset else None,
        greater_is_better=False,
        bf16=args.bf16,
        fp16=args.fp16 and not args.bf16,
        remove_unused_columns=False,
        report_to=["none"],
        seed=42,
    )

    # 9. Data collator
    data_collator = DataCollatorForSeq2Seq(
        tokenizer=tokenizer,
        model=model,
        padding=True,
        return_tensors="pt",
    )

    # 10. Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=data_collator,
        tokenizer=tokenizer,
    )

    # 11. Train
    logger.info("Starting training...")
    start_time = time.time()
    try:
        trainer.train()
    except KeyboardInterrupt:
        logger.info("Training interrupted by user")
        logger.info("Saving checkpoint...")
        trainer.save_model(str(output_dir / "interrupted"))
        return 0

    elapsed = time.time() - start_time
    logger.info(f"Training completed in {elapsed / 60:.1f} minutes")

    # 12. Save final model
    logger.info(f"Saving LoRA adapter to {output_dir}")
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    # Save training info
    info = {
        "base_model": args.base_model,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "training_time_minutes": round(elapsed / 60, 1),
        "output_dir": str(output_dir),
    }
    with open(output_dir / "training_info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info("Training complete! Next: merge LoRA weights with base model")
    logger.info(f"Run: python scripts/merge_and_export.py --lora-path {output_dir}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(train(args))
