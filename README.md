# EchoServe — 企业级本地客服智能体系统

> **面向 B 端客服场景，当客服会话积累达到阈值，自动利用真实业务对话蒸馏优化本地模型，实现服务数据闭环自迭代。**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-green)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-blue)](https://react.dev/)

---

## 一句话介绍

EchoServe 是**企业级本地大模型客服系统**，核心差异化在于**数据闭环自迭代**：客服坐席与客户的每一次真实对话，都会自动沉淀为训练数据，通过 DPO（Direct Preference Optimization）和 LoRA 微调持续蒸馏优化本地模型，让客服越用越聪明、越答越精准——**数据不出域、模型不外流、迭代不停机**。

---

## 核心卖点

| 维度 | 传统客服系统 | EchoServe |
|------|------------|-----------|
| **数据归属** | 数据上传到第三方 SaaS 平台 | **完全本地化**，所有数据不出域 |
| **模型迭代** | 依赖厂商更新，无法定制化 | **会话自动蒸馏**，模型越用越懂业务 |
| **知识更新** | 需要人工维护 FAQ 库 | **RAG 实时检索** + **LoRA 微调内化** |
| **渠道接入** | 单一网页聊天 | 企业微信客服、WhatsApp、网页、API 多渠道 |
| **部署成本** | 按量付费、长期使用成本高 | **一次性部署**，采用常规消费级24G显存GPU |
| **安全合规** | 数据跨境传输风险 | **等保 2.0 三级**目标，审计日志完整 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        客户端渠道层                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  企业微信客服  │  │   WhatsApp  │  │   网页聊天    │             │
│  │  (AES加密)   │  │   (可选)     │  │   (React)   │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
└─────────┼─────────────────┼─────────────────┼──────────────────┘
          │                 │                 │
          └─────────────────┴─────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                        API 网关层 (FastAPI)                       │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │  认证    │ │  审计    │ │  对话    │ │ 知识库  │ │  设置   │  │
│  │  (JWT)   │ │ (日志)   │ │ (流式)   │ │ (RAG)   │ │ (管理)  │  │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘  │
└───────┼───────────┼───────────┼───────────┼───────────┼───────┘
        │           │           │           │           │
        └───────────┴───────────┴───────────┴───────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                      插件引擎层 (FiberManager)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  LLM 插件    │  │ RAG 检索插件  │  │ 渠道插件     │             │
│  │ (vLLM/Ollama)│  │ (ChromaDB)   │  │(微信/Whatsapp)│            │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              模型进化引擎 (ModelEvolvePlugin)              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │   │
│  │  │审计日志挖掘 │→│训练数据生成│→│ LoRA 微调 │→│ A/B 评估 │ │   │
│  │  │(AuditTo   │  │ (DPO/SFT) │  │ (Axolotl)│  │(Promote)│ │   │
│  │  │Training)  │  │           │  │          │  │         │ │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └────────┘ │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                        基础设施层                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  vLLM 推理   │  │  Ollama    │  │   Docker    │             │
│  │ (Qwen3-8B)  │  │  (CPU模式) │  │  (容器化)   │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 功能模块

### P0 核心功能（已上线）

| 模块 | 功能 | 状态 |
|------|------|------|
| **认证授权** | JWT Token + RBAC 权限控制（super_admin/admin/editor/user/readonly） | ✅ |
| **审计日志** | 全量操作审计、完整性校验、CSV 导出 | ✅ |
| **知识库 RAG** | 文档上传/解析/切分、向量检索、BM25 混合排序、检索测试 | ✅ |
| **对话系统** | 流式 SSE 输出、多轮会话、RAG 检索增强开关、会话历史 | ✅ |
| **企业微信客服** | AES 加密消息收发、主动回复、回调 URL 验证、CorpID/Token 配置 | ✅ |
| **WhatsApp** | 消息收发（需配置 Meta API） | ✅ |
| **用户管理** | 角色分配、API Key 管理 | ✅ |

### P1 模型管理（已上线）

| 模块 | 功能 | 状态 |
|------|------|------|
| **模型管理** | Ollama / vLLM 切换、模型列表、运行时状态监控 | ✅ |
| **数据闭环** | 审计日志 → 训练数据自动转换、会话历史挖掘 | ✅ |
| **LoRA 微调** | QLoRA 4-bit 高效微调、训练数据生成、权重导出 | ✅ |
| **DPO 训练** | 偏好反馈数据集构建、自动触发训练（阈值 50 条） | ✅ |
| **A/B 评估** | 新老模型对比评估、自动 Promote 最优模型 | ✅ |
| **监控告警** | Prometheus 指标、健康检查、系统状态面板 | ✅ |

### P2 企业认证（已上线）

| 模块 | 功能 | 状态 |
|------|------|------|
| **LDAP 集成** | 企业 AD 域账号同步 | ✅ |
| **OAuth2** | 第三方登录接入 | ✅ |
| **等保合规** | 审计日志、数据加密、访问控制 | 🔄 |

---

## 技术栈

### 后端
- **框架**: FastAPI + Pydantic v2
- **推理**: vLLM（GPU）/ Ollama（CPU）
- **向量库**: ChromaDB + Sentence-Transformers
- **检索**: BM25 (rank-bm25) + CrossEncoder 重排序
- **微调**: Axolotl (QLoRA 4-bit)
- **数据**: Pandas + NumPy
- **监控**: Prometheus Client
- **安全**: python-jose + passlib + python-dotenv

### 前端
- **框架**: React 18 + Vite 5
- **状态**: Zustand
- **样式**: Tailwind CSS
- **路由**: React Router v6
- **图标**: 原生 Emoji

### 部署
- **容器**: Docker + Docker Compose
- **反向代理**: Nginx
- **进程管理**: systemd / PM2
- **云部署**: AutoDL / 阿里云 ECS / 本地服务器

---

## 项目结构

```
EchoServe/
├── api/                          # FastAPI 后端
│   ├── main.py                   # 主入口：插件加载、路由注册
│   ├── deps.py                   # 依赖注入：权限校验、上下文获取
│   └── routers/                  # API 路由模块
│       ├── auth.py               # 认证授权（JWT + RBAC）
│       ├── audit.py              # 审计日志
│       ├── chat.py               # 对话接口（流式/非流式）
│       ├── knowledge.py          # 知识库管理
│       ├── model.py              # 模型管理
│       ├── evolve.py             # 模型进化（训练/评估/切换）
│       ├── metrics.py            # 监控指标
│       └── settings.py           # 系统设置（微信客服配置等）
│
├── core/                         # 核心框架
│   ├── context.py                # BaizeContext：插件共享上下文
│   ├── fiber.py                  # FiberManager：插件生命周期管理
│   ├── plugin.py                 # 插件基类
│   └── plugin_loader.py          # 插件发现与加载
│
├── plugins/                      # 业务插件
│   ├── auth/                     # 认证插件（用户/角色/权限）
│   ├── chat/                     # 对话插件（LLM 调用）
│   ├── knowledge/                # 知识库插件（文档管理）
│   ├── retriever/                # 检索插件（RAG 引擎）
│   ├── llm/                      # LLM 插件（vLLM/Ollama 适配）
│   ├── model_manager/            # 模型管理插件
│   ├── evolve/                   # 模型进化插件
│   │   ├── audit_to_training.py  # 审计日志 → 训练数据
│   │   ├── session_miner.py      # 会话历史挖掘
│   │   ├── dpo_trainer.py        # DPO 训练器
│   │   └── evaluator.py          # A/B 评估器
│   ├── channel_wechat_kf/        # 企业微信客服插件
│   └── channel_whatsapp/         # WhatsApp 插件
│
├── web/                          # React 前端
│   ├── src/
│   │   ├── pages/                # 页面组件
│   │   │   ├── Dashboard.jsx     # 仪表盘
│   │   │   ├── Chat.jsx          # 对话测试（含 RAG 开关）
│   │   │   ├── Knowledge.jsx     # 知识库管理
│   │   │   ├── Settings.jsx      # 系统设置
│   │   │   ├── Models.jsx        # 模型管理
│   │   │   └── Evolve.jsx        # 模型进化
│   │   ├── App.jsx               # 布局 + 侧边栏导航
│   │   └── store.js              # Zustand 全局状态
│   └── public/logo.jpg           # EchoServe LOGO
│
├── config/                       # 配置中心
│   └── settings.py               # Pydantic 配置模型
│
├── data/                         # 数据目录
│   ├── knowledge/                # 知识库文档
│   ├── audit/                    # 审计日志
│   └── training/                 # 训练数据池
│
├── scripts/                      # 运维脚本
│   ├── train_lora.py             # LoRA 微调脚本
│   └── test_wechat_kf.py         # 微信客服测试脚本
│
├── docs/                         # 文档
│   ├── DEPLOYMENT.md             # 生产部署指南
│   └── CHANGELOG.md              # 变更日志
│
├── requirements.txt              # Python 依赖
├── docker-compose.yml            # Docker 编排
├── Dockerfile                    # 后端容器镜像
└── .env.example                  # 环境变量模板
```

---

## 部署方案

### 方案 A：本地开发/测试（CPU + 16GB 内存）

适合功能验证、POC 演示，无需 GPU。

```bash
# 1. 克隆项目
git clone https://github.com/your-org/echoserve.git
cd echoserve

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env：MODEL_PATH=./models/qwen3-8b, INFERENCE_BACKEND=ollama

# 4. 启动 Ollama（CPU 模式）
docker run -d --gpus all -v ollama:/root/.ollama -p 11434:11434 ollama/ollama

# 5. 启动后端
python -m uvicorn api.main:app --host 0.0.0.0 --port 8080

# 6. 启动前端（开发模式）
cd web && npm install && npm run dev
```

### 方案 B：云端生产环境（AutoDL RTX 3090 24GB）

推荐用于正式客服场景，支持并发推理。

```bash
# 1. 租用 AutoDL RTX 3090 实例
# 2. SSH 登录服务器
ssh -p <port> root@<host>

# 3. 安装 miniconda + Python 3.10
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh

# 4. 克隆项目并安装依赖
git clone https://github.com/your-org/echoserve.git
cd echoserve
pip install -r requirements.txt

# 5. 下载模型（Qwen3-8B-Instruct，约 16GB）
# 使用 ModelScope 镜像加速
export VLLM_MODEL=/root/autodl-tmp/models/Qwen3-8B

# 6. 启动 vLLM 推理服务
python -m vllm.entrypoints.openai.api_server \
  --model $VLLM_MODEL \
  --host 0.0.0.0 \
  --port 8000 \
  --chat-template qwen3 \
  --tensor-parallel-size 1

# 7. 配置 .env 并启动 EchoServe
cp .env.example .env
# 编辑 .env：VLLM_HOST=http://localhost:8000, INFERENCE_BACKEND=vllm
python -m uvicorn api.main:app --host 0.0.0.0 --port 8080

# 8. 配置 ngrok 公网访问（企业微信客服回调需要）
ngrok http 8080 --domain=your-domain.ngrok-free.dev
```

**典型配置（当前运行环境）**:

| 组件 | 配置 |
|------|------|
| GPU | NVIDIA RTX 3090 24GB |
| 模型 | Qwen3-8B-Instruct |
| 推理框架 | vLLM |
| Embedding | bge-small-zh-v1.5 |
| 公网访问 | ngrok 固定域名 |

---

## 快速开始

### 1. 登录管理后台

访问 `http://localhost:8080`，默认账号：
- 用户名：`admin`
- 密码：请在首次登录前通过环境变量 `ECHOSEVE_ADMIN_PASSWORD` 设置

### 2. 上传知识库文档

进入「知识库」页面，上传 PDF/Word/TXT 文档，系统自动解析、切分、向量化。

### 3. 测试对话

进入「对话测试」页面，勾选「RAG 检索增强」，输入问题验证检索效果。

### 4. 配置企业微信客服

进入「系统设置 → 企业微信客服」，填写：
- CorpID：`wwxxxxxxxxxxxxxxxx`
- Token：企业微信后台生成的 Token
- EncodingAESKey：43 位随机字符串
- Secret：企业微信应用 Secret

回调 URL：`https://your-domain.ngrok-free.dev/webhook/wechat_kf`

### 5. 开启数据闭环

进入「模型进化」页面：
1. 配置训练数据阈值（默认 50 条偏好反馈自动触发 DPO）
2. 查看审计日志 → 训练数据转换状态
3. 手动或自动启动 LoRA 微调
4. A/B 评估通过后，系统自动切换最优模型

---

## 数据闭环自迭代流程

```
┌─────────────────────────────────────────────────────────────┐
│                     数据闭环自迭代流水线                      │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐           │
│  │ 客服对话  │    │ 用户反馈  │    │ 审计日志  │           │
│  │ (微信/网页)│    │ (👍/👎)  │    │ (全量记录)│           │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘           │
│       │              │              │                     │
│       └──────────────┼──────────────┘                     │
│                      ▼                                      │
│              ┌──────────────┐                              │
│              │ 数据挖掘模块  │                              │
│              │ • AuditToTrainingConverter                  │
│              │ • SessionMiner                              │
│              └──────┬───────┘                              │
│                     ▼                                       │
│              ┌──────────────┐                              │
│              │ 训练数据池     │                              │
│              │ training_pool.jsonl                         │
│              └──────┬───────┘                              │
│                     ▼                                       │
│         ┌─────────────────────┐                           │
│         │   DPO Trainer        │                           │
│         │  • 偏好对 (chosen/rejected)                      │
│         │  • QLoRA 4-bit 微调                               │
│         │  • 阈值自动触发 (默认 50 条)                       │
│         └──────────┬──────────┘                           │
│                    ▼                                        │
│         ┌─────────────────────┐                           │
│         │   A/B Evaluator      │                           │
│         │  • 新老模型对比                                     │
│         │  • 准确率提升 ≥ 2% 自动 Promote                     │
│         └──────────┬──────────┘                           │
│                    ▼                                        │
│         ┌─────────────────────┐                           │
│         │   Model Manager      │                           │
│         │  • 热切换 LoRA Adapter                            │
│         │  • 零停机更新                                       │
│         └─────────────────────┘                           │
│                                                             │
│                    ↓ 迭代完成，模型更懂业务 ↓                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 关键技术细节

### 1. 插件化架构（FiberManager）

系统采用**插件化设计**，所有核心功能（认证、对话、检索、渠道接入）均为独立插件：

- **生命周期管理**：插件支持 `on_init` → `on_start` → `on_stop` → `on_destroy` 四个阶段
- **依赖注入**：通过 `BaizeContext` 共享服务（如 LLM 实例、知识库索引）
- **热插拔**：新插件可独立开发、独立部署，不影响现有功能

### 2. RAG 检索增强

```python
# 检索流程
query → 向量检索 (ChromaDB) → BM25 重排序 → CrossEncoder 精排 → Top-K 注入 Prompt
```

- **混合检索**：向量相似度 + 关键词 BM25 加权
- **重排序**：CrossEncoder（bge-reranker）二次精排
- **上下文压缩**：按 token 预算动态截取文档片段

### 3. LoRA 微调（QLoRA 4-bit）

```python
# 关键参数
base_model: Qwen3-8B-Instruct
quantization: 4-bit (bnb_4bit)
adapter_rank: 64
learning_rate: 2e-4
batch_size: 4
max_seq_length: 2048
```

- **显存优化**：24GB RTX 3090 可流畅训练 8B 模型
- **数据格式**：Alpaca 格式（instruction + input + output）
- **训练数据**：真实客服对话 + 审计日志自动转换

### 4. 企业微信客服安全

- **消息加密**：AES-CBC 256 位加密，符合企业微信标准
- **签名验证**：Token + Timestamp + Nonce 三重校验
- **会话保持**：OpenID 级别会话状态，支持多轮对话

---

## 环境变量配置

```bash
# === 基础配置 ===
SECRET_KEY=your-jwt-secret-change-me
CORS_ORIGINS=*

# === 模型配置 ===
MODEL_PATH=/root/autodl-tmp/models/Qwen3-8B
INFERENCE_BACKEND=vllm          # 或 ollama
VLLM_HOST=http://localhost:8000

# === RAG 配置 ===
EMBEDDING_MODEL=bge-small-zh-v1.5
CHROMA_PERSIST_DIR=./data/chroma

# === 企业微信客服 ===
WECHAT_KF_CORP_ID=wwxxxxxxxxxxxxxxxx
WECHAT_KF_TOKEN=your-token
WECHAT_KF_AES_KEY=your-43-char-aes-key
WECHAT_KF_SECRET=your-secret
WECHAT_KF_WEBHOOK_PATH=/webhook/wechat_kf

# === 训练配置 ===
TRAINING_DATA_DIR=./data/training
LORA_OUTPUT_DIR=./data/lora
AUTO_TRIGGER_THRESHOLD=50       # 自动触发 DPO 的偏好反馈阈值
```

---

## 开发路线图

| 版本 | 目标 | 时间 |
|------|------|------|
| **v0.1.0** | P0 核心功能：RAG 对话 + 微信客服 + 基础管理 | ✅ 已完成 |
| **v0.2.0** | P1 模型进化：LoRA 微调 + DPO + A/B 评估 | ✅ 已完成 |
| **v0.3.0** | P2 企业认证：LDAP + OAuth2 + 等保合规 | ✅ 已完成 |
| **v0.5.0** | 智能质检：敏感词过滤、情绪识别、转人工 | 规划中 |
| **v1.0.0** | 企业级 SaaS：多租户、计费、SLA | 规划中 |

---

## 贡献指南

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交代码：`git commit -am 'Add some feature'`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

---

## 许可证

[MIT License](LICENSE)

---

## 联系与支持

- **Issues**: [GitHub Issues](https://github.com/your-org/echoserve/issues)
- **讨论**: [GitHub Discussions](https://github.com/your-org/echoserve/discussions)
- **邮箱**: support@echoserve.example.com

---

> **EchoServe — 让每一次客服对话，都成为模型进化的养分。**
