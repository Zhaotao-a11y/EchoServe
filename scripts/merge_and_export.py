"""
EchoServe - Model Merge & Export Script
=========================================
Merge LoRA adapter with base model, then export for inference.

Usage:
    # Merge LoRA weights into full model
    python scripts/merge_and_export.py \
        --lora-path ./models/lora_cs \
        --output ./models/qwen2.5-0.5b-cs-merged

    # Export to Ollama GGUF (requires llama.cpp)
    python scripts/merge_and_export.py \
        --lora-path ./models/lora_cs \
        --output ./models/qwen2.5-0.5b-cs-merged \
        --export-gguf

    # Export to vLLM format (just merge, no conversion needed)
    python scripts/merge_and_export.py \
        --lora-path ./models/lora_cs \
        --output ./models/qwen2.5-0.5b-cs-merged \
        --export-vllm
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Merge LoRA and Export Model")
    parser.add_argument("--lora-path", type=str, required=True,
                        help="Path to trained LoRA adapter")
    parser.add_argument("--output", type=str, default="./models/merged",
                        help="Output directory for merged model")
    parser.add_argument("--export-gguf", action="store_true",
                        help="Export to GGUF for Ollama (requires llama.cpp)")
    parser.add_argument("--export-vllm", action="store_true",
                        help="Export for vLLM (no conversion needed after merge)")
    parser.add_argument("--quantization", type=str, default="Q4_K_M",
                        choices=["Q4_0", "Q4_K_M", "Q5_K_M", "Q8_0", "F16"],
                        help="GGUF quantization type")
    return parser.parse_args()


def merge_lora(lora_path: str, output_dir: str):
    """Merge LoRA weights into base model"""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        logger.error("Install: pip install transformers peft torch")
        return False

    lora_path = Path(lora_path)
    output_path = Path(output_dir)

    # Load training info
    info_file = lora_path / "training_info.json"
    if info_file.exists():
        with open(info_file, "r", encoding="utf-8") as f:
            info = json.load(f)
        base_model = info.get("base_model")
        logger.info(f"Base model from training info: {base_model}")
    else:
        logger.error(f"Training info not found: {info_file}")
        logger.error("Cannot determine base model. Please check the LoRA path.")
        return False

    # Load base model
    logger.info(f"Loading base model: {base_model}")
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)

    # Load LoRA
    logger.info(f"Loading LoRA adapter: {lora_path}")
    model = PeftModel.from_pretrained(model, str(lora_path))

    # Merge
    logger.info("Merging LoRA weights into base model...")
    model = model.merge_and_unload()

    # Save
    logger.info(f"Saving merged model to: {output_path}")
    output_path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(output_path))
    tokenizer.save_pretrained(str(output_path))

    logger.info("Merge complete!")
    return True


def export_gguf(model_path: str, output_path: str, quant: str = "Q4_K_M"):
    """Export merged model to GGUF for Ollama"""
    logger.info("=" * 60)
    logger.info("Exporting to GGUF for Ollama")
    logger.info("=" * 60)

    # Check llama.cpp
    llama_dir = Path("./llama.cpp")
    if not llama_dir.exists():
        logger.info("llama.cpp not found. Downloading...")
        os.system("git clone https://github.com/ggerganov/llama.cpp.git")

    convert_script = llama_dir / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        logger.error(f"Convert script not found: {convert_script}")
        logger.error("Please clone llama.cpp: git clone https://github.com/ggerganov/llama.cpp.git")
        return False

    # Install requirements for conversion
    logger.info("Installing llama.cpp conversion requirements...")
    os.system(f"pip install -r {llama_dir / 'requirements.txt'} -q")

    # Run conversion
    gguf_output = Path(output_path) / f"model-{quant}.gguf"
    cmd = (
        f"python {convert_script} "
        f"{model_path} "
        f"--outfile {gguf_output} "
        f"--outtype {quant}"
    )
    logger.info(f"Running: {cmd}")
    result = os.system(cmd)

    if result == 0:
        logger.info(f"GGUF exported: {gguf_output}")
        logger.info("\nTo use with Ollama, create a Modelfile:")
        logger.info(f"""
# Modelfile
FROM {gguf_output}

SYSTEM "你是一个专业的智能客服助手，熟悉公司产品、政策和服务。"

PARAMETER temperature 0.7
PARAMETER top_p 0.9
""")
        return True
    else:
        logger.error("GGUF conversion failed")
        return False


def export_vllm(model_path: str):
    """vLLM can directly load merged HF model, no conversion needed"""
    logger.info("=" * 60)
    logger.info("vLLM Export")
    logger.info("=" * 60)
    logger.info(f"Merged model ready at: {model_path}")
    logger.info("vLLM can directly load this HuggingFace format model.")
    logger.info("\nExample:")
    logger.info(f"  python -m vllm.entrypoints.openai.api_server \\")
    logger.info(f"    --model {model_path} \\")
    logger.info(f"    --port 8000")
    return True


def create_ollama_modelfile(model_path: str, gguf_file: str):
    """Create Ollama Modelfile"""
    modelfile_content = f"""# EchoServe Customer Service Model
# Generated by merge_and_export.py

FROM {gguf_file}

SYSTEM """你是一个专业的智能客服助手，熟悉公司产品、政策和服务。请根据客户的问题，给出准确、简洁、有帮助的回答。保持友好、专业的语气。"""

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER num_predict 512
PARAMETER repeat_penalty 1.1

TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>"""
"""

    modelfile_path = Path(model_path) / "Modelfile"
    with open(modelfile_path, "w", encoding="utf-8") as f:
        f.write(modelfile_content)
    logger.info(f"Ollama Modelfile created: {modelfile_path}")
    logger.info(f"Import: ollama create echoseve-cs -f {modelfile_path}")


def main():
    args = parse_args()

    # 1. Merge LoRA
    success = merge_lora(args.lora_path, args.output)
    if not success:
        return 1

    # 2. Export based on target
    if args.export_gguf:
        success = export_gguf(args.output, args.output, args.quantization)
        if success:
            gguf_file = Path(args.output) / f"model-{args.quantization}.gguf"
            create_ollama_modelfile(args.output, str(gguf_file))
    elif args.export_vllm:
        export_vllm(args.output)
    else:
        logger.info("\nModel merged. Choose export format:")
        logger.info(f"  --export-gguf   : For Ollama (CPU/GPU)")
        logger.info(f"  --export-vllm   : For vLLM (GPU only)")

    logger.info("\n" + "=" * 60)
    logger.info("Done!")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
