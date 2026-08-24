# EchoServe LoRA 训练 - GPU 租用指南

## 本地环境限制

| 项目 | 你当前环境 | 训练需求 |
|------|-----------|---------|
| 硬件 | CPU (i7) | GPU (CUDA) |
| 内存 | 16GB | 16GB+ |
| 模型 | Ollama GGUF (仅推理) | HuggingFace safetensors (训练) |
| 依赖 | 缺少 PyTorch/Transformers/PEFT | 需要完整安装 |

**结论**：LoRA 微调必须在 GPU 环境运行，本地 CPU 仅能做数据准备验证。

---

## 推荐 GPU 租用平台

### 1. AutoDL (国内首选)
- **网址**: https://www.autodl.com
- **优势**: 价格便宜，镜像丰富，支持 Jupyter/SSH
- **推荐配置**:
  - **显卡**: RTX 3090 (24GB) 或 RTX 4090 (24GB)
  - **机型**: 1x RTX 3090 + 32GB RAM
  - **镜像**: PyTorch 2.x + CUDA 12.x (预装 transformers/peft)
  - **价格**: 约 1.5-2.5 元/小时
  - **预估成本**:
    - Qwen2.5-0.5B LoRA 训练 (2000条, 3epochs): ~30-60分钟
    - 总花费: **1-3 元**

### 2. RunPod (国际)
- **网址**: https://www.runpod.io
- **优势**: 按秒计费，支持 Serverless
- **推荐配置**:
  - GPU: RTX 3090 (24GB VRAM)
  - 价格: ~$0.3-0.5/小时

### 3. 阿里云/DLC (国内企业)
- **网址**: https://www.aliyun.com/product/bigdata/devops
- **优势**: 稳定可靠，适合企业长期使用
- **价格**: 相对较高，适合批量训练

---

## 训练前准备清单

### Step 1: 在本地完成数据准备
```bash
cd D:\llm_learn\OmniZee-B\OmniZee
python scripts/prepare_training_data.py
```
确认输出:
- `data/training/train.jsonl` (训练数据)
- `data/training/test.jsonl` (测试数据)
- `data/training/train_summary.json` (数据统计)

### Step 2: 打包上传
将以下文件上传到 GPU 服务器:
```
data/training/train.jsonl
scripts/train_lora.py
scripts/merge_and_export.py
```

### Step 3: 在 GPU 服务器安装依赖
```bash
pip install transformers peft datasets accelerate bitsandbytes
```

---

## GPU 服务器训练命令

### 快速测试 (50条样本)
```bash
python scripts/train_lora.py \
    --base-model Qwen/Qwen2.5-0.5B-Instruct \
    --train-data data/training/train.jsonl \
    --test-data data/training/test.jsonl \
    --max-samples 50 \
    --output-dir ./models/lora_cs_test \
    --epochs 1 \
    --batch-size 4 \
    --lora-r 8 \
    --bf16
```

### 完整训练 (2000条样本)
```bash
python scripts/train_lora.py \
    --base-model Qwen/Qwen2.5-0.5B-Instruct \
    --train-data data/training/train.jsonl \
    --test-data data/training/test.jsonl \
    --output-dir ./models/lora_cs \
    --epochs 3 \
    --batch-size 4 \
    --gradient-accumulation 4 \
    --lora-r 8 \
    --lora-alpha 16 \
    --learning-rate 2e-4 \
    --bf16
```

### QLoRA (显存 < 16GB)
```bash
python scripts/train_lora.py \
    --base-model Qwen/Qwen2.5-0.5B-Instruct \
    --train-data data/training/train.jsonl \
    --output-dir ./models/lora_cs \
    --epochs 3 \
    --batch-size 8 \
    --lora-r 16 \
    --load-in-4bit \
    --bf16
```

---

## 训练后导出

### 合并 LoRA 权重
```bash
python scripts/merge_and_export.py \
    --lora-path ./models/lora_cs \
    --output ./models/qwen2.5-0.5b-cs-merged
```

### 导出到 Ollama (GGUF)
```bash
python scripts/merge_and_export.py \
    --lora-path ./models/lora_cs \
    --output ./models/qwen2.5-0.5b-cs-merged \
    --export-gguf
```

---

## 硬件要求速查

| 模型 | LoRA rank | 显存需求 | 推荐显卡 |
|------|-----------|---------|---------|
| Qwen2.5-0.5B | r=8 | ~6GB | RTX 3060 12GB |
| Qwen2.5-0.5B | r=16 | ~8GB | RTX 3090 24GB |
| Qwen2.5-1.5B | r=8 | ~10GB | RTX 3090 24GB |
| Qwen2.5-7B | r=8 (QLoRA) | ~12GB | RTX 3090 24GB |

**建议**: 0.5B 模型训练很快，3090 足够。如果想训练更大模型，需要 A100 40GB 或 4090 24GB。

---

## 常见问题

**Q: 训练需要多久？**
A: Qwen2.5-0.5B + 2000条数据 + 3 epochs ≈ 30-90 分钟 (取决于 GPU)

**Q: 可以边训练边用电脑吗？**
A: 租用 GPU 服务器训练不影响本地电脑，训练完成后下载模型即可。

**Q: 训练失败怎么办？**
A: 先用 `--max-samples 50` 小批量测试，确认环境正常后再全量训练。
