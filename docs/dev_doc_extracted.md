EchoServe
企业级本地知识库问答系统
开发方案文档（修订版）
版本：EchoServe V0.1.0（修订版）
日期：2026-08-19
状态：修订版
运行环境：云端算力卡 NVIDIA RTX 4090 48GB
【机密】仅供内部使用
修订说明
本次修订针对原始方案（V0.1.0）的两项核心问题进行了修正：
硬件规格升级：将最低运行环境从 NVIDIA RTX 3090 24GB 升级为 NVIDIA RTX 4090 48GB，推荐配置升级为 NVIDIA A100 40/80GB。所有性能指标同步上调。
模型进化引擎离线化重构：将 LoRA 微调和全参数微调/蒸馏明确设计为离线作业，与在线推理服务完全隔离。训练完成后通过模型热切换机制加载新权重。
其余章节（RAG 混合检索、渠道对接、安全合规、Web 后端管理等）内容保持不变，未标注（修订）的章节即为原方案内容。
目 录
（完整目录见最终排版）
# 一、概述
## 1.1 产品定位
EchoServe 是一款面向企业客户的本地化知识库问答系统。系统在客户指定的云端算力环境（NVIDIA RTX 4090 48GB）上运行，所有数据不出域，为企业提供安全、可靠、高效的智能问答服务。
核心价值主张：让企业在数据不出域的前提下，用消费级显卡（RTX 4090 48GB）获得媲美云端 AI 的知识问答能力。
## 1.2 要解决的核心问题
EchoServe 聚焦四个核心目标，所有功能围绕这四个目标展开：
## 1.3 设计原则
Python 底层架构：统一使用 Python 技术栈，充分利用 AI 生态（vLLM、Transformers、Chroma、PGVector 等），参考 PI 系统的底层设计理念。
插件化架构：借鉴 DeepSeek Harness 的 Cordis 思想，核心运行时与技能插件解耦，新增能力 = 写一个插件。
渐进式模型进化：知识库从 0 到 10000+ 条，模型从纯 RAG 到 LoRA 微调再到全参数蒸馏，逐步优化。
混合检索优先：BM25 + 向量 + RRF 融合 + Cross-Encoder 重排序，作为默认检索方案。
渠道可扩展：优先支持网页和企业微信，预留 WhatsApp 等国际渠道接口。
## 1.4 产品范围
In Scope（本期包含）
纯对话式知识库问答（无工具调用、无多 Agent 协作）
知识库管理（上传、切片、索引、检索）
混合检索（BM25 + 向量 + RRF）
模型进化引擎（LoRA 微调 / 蒸馏，离线执行）
Web 后端管理（用户、知识库、模型、监控、审计）
渠道对接：网页 Live Chat + 企业微信 + WhatsApp
安全合规（认证、ACL、审计日志、HTTPS）
Docker Compose 一键部署
Out of Scope（本期不含）
多 Agent 编排与协作
工具调用（搜索、代码执行等）
在线学习 / 实时进化
C 端 Agent 平台功能
# 二、整体架构
## 2.1 架构总览
EchoServe 采用四层架构，核心层使用 Python 实现，能力层通过插件化机制动态加载，应用层适配多种渠道，基础设施层提供存储与推理能力。
## 2.2 分层说明
## 2.3 技术选型
## 2.4 运行环境说明（修订）
EchoServe V0.1.0，最低配置为 NVIDIA RTX 4090（48GB 显存），推荐配置为 NVIDIA A100（40/80GB）。 每个客户拥有独立实例，数据完全隔离，不与其他租户共享。
修订说明：原方案使用 RTX 3090 24GB 作为运行环境，经可行性分析发现 24GB 显存在运行 Qwen3-14B Q4（约占用 8-9GB）+ KV Cache（32K 上下文）+ Prefix Cache 的场景下极为紧张，多并发时必然 OOM。升级至 48GB 显存后，可稳定支持 8-12 并发，并为未来升级至 Qwen3-27B 留出空间。
硬件规格表（最低配置）
性能指标（基于 RTX 4090 48GB）
推荐配置（高性能场景）
# 三、Python 底层架构与插件化设计
## 3.1 设计理念（参考 DeepSeek Harness）
借鉴 DeepSeek Harness（dsh）的 Cordis 插件架构思想，EchoServe 的核心运行时与业务功能完全解耦。所有业务能力（对话、RAG、知识库管理、模型进化、渠道对接、Web 管理）都以插件形式存在，通过统一的 BaizeContext 注册和发现。
核心优势：新增功能 = 写一个插件；禁用功能 = 卸载一个插件；升级功能 = 替换一个插件。整个系统像乐高一样可组装。
## 3.2 BaizeContext（共享上下文容器）
BaizeContext 是插件化架构的核心——所有插件通过它注册服务、注入依赖、监听事件。它借鉴了 Cordis 的"时空可组合性"理念，支持可逆的副作用管理。
class BaizeContext:
def __init__(self, scope: str = "global"):
self.scope = scope
self._services: dict[str, Any] = {}
self._event_listeners: dict[str, list] = {}
self._effects: list[Callable] = []
self._fibers: dict[str, Fiber] = {}
def provide(self, key: str, service: Any):
"""注册服务（可逆）"""
self._services[key] = service
self._effects.append(lambda: self._services.pop(key, None))
def inject(self, key: str) -> Any:
"""依赖注入"""
if key not in self._services:
raise ServiceMissingError(f"服务 {key} 未提供")
return self._services[key]
def on(self, event: str, handler: Callable):
"""监听事件（可逆）"""
self._event_listeners.setdefault(event, []).append(handler)
self._effects.append(
lambda: self._event_listeners[event].remove(handler)
)
def teardown(self):
"""卸载：按 LIFO 执行所有可逆副作用"""
while self._effects:
cleanup = self._effects.pop()
cleanup()
关键设计：所有服务注册都附带一个"反向操作"（effect），当插件卸载时，按 LIFO 顺序执行这些反向操作，系统状态精确恢复到插件加载前的样子。这就是 Cordis 的"可逆副作用"机制。
## 3.3 插件基类
所有插件继承自统一的 BaizePlugin 基类，实现 apply() 方法完成注册逻辑。
class BaizePlugin(ABC):
plugin_id: str = ""
plugin_name: str = ""
plugin_version: str = "0.1.0"
dependencies: list[str] = []
def apply(self, ctx: BaizeContext) -> Fiber:
self.ctx = ctx
self.fiber = Fiber(self.plugin_id)
# 依赖检查：缺少依赖时静默非激活
for dep in self.dependencies:
if dep not in ctx._services:
return self.fiber
self._register(ctx, self.fiber)
return self.fiber
@abstractmethod
def _register(self, ctx: BaizeContext, fiber: Fiber):
pass
## 3.4 核心插件清单
EchoServe V0.1.0，每个插件独立开发、独立测试、可独立启停：
## 3.5 插件加载机制
插件通过 YAML 配置文件声明，系统启动时按依赖顺序自动加载：
# config/plugins.yaml
plugins:
- id: core.session
enabled: true
- id: core.retrieval
enabled: true
config:
vector_store: chroma
bm25_store: pgvector
reranker: cross-encoder/ms-marco-MiniLM-L-6-v2
top_k: 5
- id: core.knowledge
enabled: true
- id: core.model
enabled: true
config:
model_path: /models/qwen3-14b-q4
tensor_parallel: 1
max_model_len: 32768
gpu_memory_utilization: 0.90
- id: core.chat
enabled: true
- id: core.evolve
enabled: true
- id: admin.web
enabled: true
config:
host: 0.0.0.0
port: 9090
- id: channel.webchat
enabled: true
- id: channel.wechat
enabled: true
- id: channel.whatsapp
enabled: false  # V0.1.0 预留，V1.0 启用
- id: security.auth
enabled: true
- id: security.audit
enabled: true
- id: security.acl
enabled: true
# 加载顺序由依赖关系自动拓扑排序
loader = PluginLoader(ctx, "config/plugins.yaml")
loader.load_all()  # 按依赖顺序加载，缺失依赖自动跳过
## 3.6 Fiber 机制（借鉴 Cordis）
每个插件在加载时创建一个 Fiber（纤维）——它记录了该插件注册的所有服务和事件监听。当插件卸载时，Fiber 确保系统状态精确回滚。
class Fiber:
def __init__(self, plugin_id: str):
self.plugin_id = plugin_id
self.services: list[str] = []
self.effects: list[Callable] = []
def add_service(self, key: str, cleanup: Callable):
self.services.append(key)
self.effects.append(cleanup)
def dispose(self, ctx: BaizeContext):
"""按 LIFO 顺序执行所有反向操作"""
while self.effects:
cleanup = self.effects.pop()
cleanup()
for svc in self.services:
ctx._services.pop(svc, None)
设计要点：Fiber 让插件的"加载"和"卸载"完全对称。任何插件都可以安全地安装和移除，不会影响其他插件的正常运行。这是 EchoServe 实现"热插拔"能力的基础。
# 四、RAG 混合检索方案
## 4.1 设计原则
纯关键词（BM25）和纯向量检索各有致命短板。BM25 无法处理同义词和口语化表达（"登录不上" vs "认证失败"），向量检索在精确匹配（错误码、产品型号）上会翻车。EchoServe 默认采用混合检索，这是 2026 年企业 RAG 的生产标准做法。
## 4.2 检索管道
用户查询
|
+-- BM25 检索（PGVector tsvector）-------+
|                                       v
|                                 RRF 融合（k=60）
+-- 向量检索（Chroma + bge-small）-------+
v
Cross-Encoder 重排序
v
Top 5 片段
v
拼入 Prompt -> vLLM 生成答案
## 4.3 实现代码
class HybridRetriever:
def __init__(self, vector_store, bm25_index, reranker=None):
self.vector_store = vector_store  # Chroma
self.bm25_index = bm25_index    # PGVector tsvector
self.reranker = reranker         # Cross-Encoder
def retrieve(self, query: str, top_k: int = 5,
acl_filter: list = None):
# 双路并行召回（候选池放大到 100）
vec_results = self.vector_store.similarity_search(
query, k=100, filter=acl_filter)
bm25_results = self.bm25_index.search(
query, top_n=100, filter=acl_filter)
# RRF 融合
fused = self._rrf_fuse([vec_results, bm25_results], k=60)
top_m = fused[:50]
# Cross-Encoder 重排序
if self.reranker:
pairs = [(query, doc.text) for doc in top_m]
scores = self.reranker.predict(pairs)
scored = sorted(zip(scores, top_m), reverse=True)
return [doc for _, doc in scored[:top_k]]
return top_m[:top_k]
def _rrf_fuse(self, lists, k=60):
scores = defaultdict(float)
for rank_list in lists:
for rank, doc in enumerate(rank_list):
scores[doc.id] += 1 / (k + rank + 1)
sorted_docs = sorted(scores.items(), key=lambda x: x[1],
reverse=True)
return [doc_id_to_obj[d] for d, _ in sorted_docs]
## 4.4 RRF 融合公式
RRF（Reciprocal Rank Fusion）是混合检索的核心融合算法。它不使用各检索系统的原始分数（因为 BM25 分数和余弦相似度量纲不同），而是使用排名位置：
n
RRF(d) = SUM 1 / (k + rank_i(d))
i=1
其中 k 通常取 60，rank_i(d) 是文档 d 在第 i 个检索结果列表中的排名位置。RRF 完全规避了分数量纲问题，在生产系统中被广泛采用。
## 4.5 三档配置
## 4.6 知识库处理流程
文档上传：管理员通过 Web 管理后台上传 PDF/DOCX/MD/TXT 文件（单文件 <= 50MB）。
格式解析：根据文件类型调用对应解析器（PyMuPDF 解析 PDF、python-docx 解析 DOCX）。
智能切片：按段落/章节切分，每块 500-800 token，重叠 50-100 token。
向量化：使用 bge-small-zh-v1.5 生成 embedding（384 维）。
双写索引：向量写入 Chroma，文本 + tsvector 写入 PGVector。
权限标记：根据文档的 ACL 配置，写入角色/部门可见性标签。
生效通知：索引更新完成后，检索服务自动加载新索引（无需重启）。
## 4.7 对话生成流程
检索到 Top 5 片段后，拼入 Prompt 交给本地 vLLM 生成答案：
def generate_answer(query: str, ctx: BaizeContext) -> str:
# 1. 混合检索
retriever = ctx.inject("retriever")
user = ctx.inject("current_user")
chunks = retriever.retrieve(query, top_k=5,
acl_filter=user.permissions)
# 2. 拼装 Prompt
context_text = "\n\n".join([
f"[来源{i+1}] {c.text}" for i, c in enumerate(chunks)
])
prompt = f"""基于以下知识库内容回答用户问题。
如果知识库中没有相关信息，请回复"暂未找到相关信息"。
引用来源时标注 [来源N]。
知识库内容：
{context_text}
用户问题：{query}"""
# 3. 调用 vLLM 生成
model = ctx.inject("model")
answer = model.generate(prompt, max_tokens=1024,
temperature=0.3)
# 4. 记录审计日志
audit = ctx.inject("audit_logger")
audit.log(query=query, sources=[c.id for c in chunks],
answer=answer, user=user.id)
return answer
# 五、模型进化引擎（修订）
## 5.1 设计理念（修订）
模型进化引擎是 EchoServe 的核心差异化能力。为避免干扰在线推理服务的稳定性，所有训练任务（LoRA、全参数微调、蒸馏）均设计为离线作业，在独立环境中执行。 训练完成后，通过模型热切换机制将新权重加载到推理服务中。
修订说明：原方案中模型进化引擎与推理服务共享 GPU 资源，在 24GB 显存上边推理边训练是不可行的。修订后，所有训练任务作为独立 Docker 容器或离线脚本运行，通过共享存储传递模型权重，推理服务通过管理 API 热加载新模型。
## 5.2 三阶段进化路线（修订）
## 5.3 阶段一：纯 RAG（知识库 < 2000 条）
目标：零训练成本，即开即用。
检索：Chroma（bge-small-zh-v1.5）+ PGVector tsvector（BM25）+ RRF 融合。
生成：本地 vLLM 加载 Qwen3-14B Q4，Prefix Cache 默认开启。
延迟：首 token < 1.5s（短上下文），检索 < 200ms。
准确率：知识库内问题 85%+（人工评测 200 题）。
幻觉率：知识库外问题 < 5%（系统回复"暂未找到相关信息"）。
## 5.4 阶段二：LoRA 微调（修订为离线任务）
目标：在 RAG 基础上，让模型"记住"高频问题的回答模式，提升回答简洁度和风格一致性。所有 LoRA 训练作为离线任务执行，不占用推理服务的 GPU 显存。
5.4.1 数据准备
从知识库 FAQ 中提取（问题，答案）对，通过 LLM 生成同义变体扩充数据量：
class TrainingDataBuilder:
def __init__(self, kb_docs: list[dict], llm_client):
self.kb_docs = kb_docs
self.llm = llm_client
def build(self) -> list[dict]:
dataset = []
for doc in self.kb_docs:
# 原始 QA 对
dataset.append({
"instruction": "请根据公司知识库回答以下问题：",
"input": doc["question"],
"output": doc["answer"]
})
# LLM 生成 2-3 个同义变体
variants = self._generate_variants(doc["question"], n=3)
for v in variants:
dataset.append({
"instruction": "请根据公司知识库回答以下问题：",
"input": v,
"output": doc["answer"]
})
# 加入 10-20% 通用对话数据（防止灾难性遗忘）
dataset += self._load_generic_data(ratio=0.15)
return dataset  # 5000+ 条
5.4.2 LoRA 微调配置（离线执行）
修订说明：LoRA 微调作为独立脚本运行，不占用推理服务的 GPU 显存。可通过 Docker 容器启动，挂载模型和数据集路径。训练完成后将 adapter 权重保存至指定目录，由模型管理插件热加载。batch_size 从 2 提升至 4，充分利用 48GB 显存。
# LoRA 微调作为独立脚本运行，不占用推理服务的 GPU 显存
# 可通过 Docker 容器启动，挂载模型和数据集路径
# 训练完成后将 adapter 权重保存至指定目录，由模型管理插件热加载
from peft import LoraConfig, get_peft_model, TaskType
from transformers import TrainingArguments
lora_config = LoraConfig(
task_type=TaskType.CAUSAL_LM,
r=8,                    # 低秩，防止过拟合
lora_alpha=16,           # 缩放系数
target_modules=["q_proj", "v_proj"],  # 只微调注意力 Q 和 V
lora_dropout=0.05,
bias="none",
)
training_args = TrainingArguments(
output_dir="./echoseve-lora",
num_train_epochs=3,      # 小数据 3 轮足矣
per_device_train_batch_size=4,  # 48GB 显存可提高 batch size
gradient_accumulation_steps=2,
learning_rate=2e-4,
warmup_ratio=0.1,
evaluation_strategy="steps",
eval_steps=50,
load_best_model_at_end=True,
metric_for_best_model="eval_loss",
save_strategy="steps",
save_steps=50,
)
关键参数说明：r=8 限制低秩矩阵的秩，防止小数据集过拟合；target_modules 只选 q_proj 和 v_proj，改动最小；3 个 epoch 后早停，避免灾难性遗忘。训练在独立进程中执行，推理服务全程不受影响。
5.4.3 A/B 测试
微调后必须做 A/B 测试，对比 RAG-only 和 RAG+LoRA 的效果，只对高频问题有明显改善，低频问题仍靠 RAG：
class ABTester:
def __init__(self, test_set: list[dict]):
self.test_set = test_set  # 200 题 hold-out 测试集
def run(self, model_rag_only, model_rag_lora):
results = {"rag_only": [], "rag_lora": []}
for item in self.test_set:
ans_a = model_rag_only.answer(item["question"])
ans_b = model_rag_lora.answer(item["question"])
results["rag_only"].append(self._score(ans_a, item))
results["rag_lora"].append(self._score(ans_b, item))
avg_a = mean(results["rag_only"])
avg_b = mean(results["rag_lora"])
print(f"RAG-only: {avg_a:.1%}")
print(f"RAG+LoRA: {avg_b:.1%}")
print(f"提升: {(avg_b - avg_a):.1%}")
# 分桶分析：高频 vs 低频问题
self._bucket_analysis(results, self.test_set)
5.4.4 预期效果
## 5.5 阶段三：全面优化（修订为离线任务）
目标：数据量充足后，进行全面优化——全参数微调、知识蒸馏、RLHF/DPO 风格对齐。所有训练任务在专用训练环境（推荐 A100）中离线执行。
5.5.1 全参数微调（离线执行）
前提条件：需要专用 GPU 节点（如 A100 40/80GB 或多卡 RTX 4090）。训练过程中推理服务不受影响。
当知识库超过 10000 条时，可以构建 20000+ 条训练数据，进行全参数微调。需要使用 DeepSpeed ZeRO-3 或 FSDP 进行分布式训练。
# DeepSpeed 配置（多卡训练，离线执行）
deepspeed_config = {
"fp16": {"enabled": True},
"zero_optimization": {
"stage": 3,
"offload_optimizer": {"device": "cpu"},
"offload_param": {"device": "cpu"}
}
}
5.5.2 知识蒸馏（离线执行）
用更大的教师模型（如 DeepSeek V4 Pro API）生成高质量答案，蒸馏到本地 Qwen3-14B 学生模型：
教师模型（DeepSeek API）
| 对 10000+ 知识库问题生成详细答案（含推理过程）
v
学生模型（本地 Qwen3-14B）
| 学习教师的输出分布（KL 散度损失 + 任务损失）
v
蒸馏后的学生模型
5.5.3 RLHF / DPO 风格对齐（离线执行）
收集企业员工对回答的偏好数据（点赞/点踩/编辑），使用 DPO（Direct Preference Optimization）对齐企业回答风格：
class DPODataset:
def __init__(self, preferences: list[dict]):
# preferences: [{prompt, chosen_answer, rejected_answer}]
self.data = preferences
def format_for_dpo(self):
formatted = []
for item in self.data:
formatted.append({
"prompt": item["prompt"],
"chosen": item["chosen_answer"],
"rejected": item["rejected_answer"]
})
return formatted
DPO 的优势：不需要训练奖励模型，直接从偏好对学习，训练流程比 RLHF 简单得多，效果相当。训练在离线环境中完成。
5.5.4 自动化评估 Pipeline（修订执行策略）
修订说明：评估 Pipeline 不再直接触发训练，而是每周自动运行评估，生成准确率报告，并邮件通知管理员。管理员确认后，可手动启动离线训练任务。这避免了自动化训练对生产环境的意外影响。
class EvaluationPipeline:
def __init__(self, test_set: list[dict]):
self.test_set = test_set
def evaluate(self, model) -> dict:
correct = 0
for item in self.test_set:
answer = model.answer(item["question"])
score = self._judge(answer, item["expected"])
if score >= 0.8:
correct += 1
accuracy = correct / len(self.test_set)
return {"accuracy": accuracy, "total": len(self.test_set)}
def weekly_run(self):
# 定时任务：每周日凌晨运行
report = self.evaluate(current_model)
if report["accuracy"] > best_accuracy + 0.02:
# 不再自动 promote，改为通知管理员
email_admin(
f"模型准确率提升至 {report['accuracy']:.1%}，
建议启动离线训练。"
)
else:
email_admin(
f"本周评估报告：准确率 {report['accuracy']:.1%}，
无需训练。"
)
## 5.6 模型进化引擎插件接口（修订）
修订说明：模型进化插件不再直接执行训练，仅提供状态查询和触发接口。实际训练任务提交到独立的训练队列或 Docker 容器。
class ModelEvolvePlugin(BaizePlugin):
plugin_id = "core.evolve"
plugin_name = "模型进化引擎"
dependencies = ["core.model", "core.knowledge"]
def _register(self, ctx, fiber):
# 注册进化服务（仅提供状态查询和触发接口，不执行训练）
evolver = ModelEvolver(ctx)
ctx.provide("evolver", evolver)
fiber.add_service("evolver", lambda: None)
# 注册评估 Pipeline
evaluator = EvaluationPipeline(test_set=load_test_set())
ctx.provide("evaluator", evaluator)
# 注册定时任务（每周评估，不自动训练）
scheduler = ctx.inject("scheduler")
scheduler.add_job(
evaluator.weekly_run,
CronTrigger(day_of_week=0, hour=2)
)
class ModelEvolver:
def __init__(self, ctx):
self.ctx = ctx
self.kb_size = 0
def check_and_evolve(self):
kb = self.ctx.inject("knowledge_base")
self.kb_size = kb.count_documents()
if self.kb_size < 2000:
return  # 阶段一：纯 RAG
elif self.kb_size < 5000:
# 提示管理员启动离线 LoRA 训练
self._notify_admin("建议启动离线 LoRA 微调")
else:
self._notify_admin("建议启动离线全参数微调/蒸馏")
def trigger_offline_training(self, training_type: str):
"""管理员手动触发的离线训练入口"""
# 提交训练任务到独立的训练队列或 Docker 容器
...
# 六、Web 后端管理
## 6.1 架构设计
Web 后端基于 Python FastAPI 构建，提供完整的 RESTful API 和 WebSocket 接口。前端使用 React + Tailwind 构建管理后台，通过 API 与后端交互。所有管理功能以插件形式注册到 BaizeContext。
## 6.2 API 接口清单
所有 API 均需 JWT 认证（除登录/健康检查外）。
## 6.3 管理后台功能模块
6.3.1 仪表盘
GPU 利用率（实时曲线，刷新间隔 5s）
内存使用率
知识库规模（文档数、分片数、存储占用）
请求统计（QPS、P95 延迟、错误率）
缓存命中率（Prefix Cache）
模型进化状态（当前阶段、下次评估时间、训练队列状态）（修订）
渠道连接状态（网页/企业微信/WhatsApp）
6.3.2 知识库管理
拖拽上传（支持多文件批量上传）
上传进度实时显示
文档列表（标题、大小、上传时间、状态、可见角色）
文档预览（点击查看切片内容）
检索测试工具（输入问题，查看检索 Top-K 片段 + 最终回答）
权限设置（指定文档可见角色/部门）
批量删除 / 批量重新索引
6.3.3 模型管理（修订）
可用模型列表（显示模型名称、大小、量化级别、状态）
模型切换（选择已下载的模型文件，热切换无需重启）
进化历史（每次微调的时间、数据量、准确率变化曲线）
评估报告查看（测试集准确率、分桶分析、A/B 对比）
手动触发离线进化（按钮触发 LoRA 微调 / 评估，提交到训练队列）（修订）
训练状态监控（当前训练进度、预计完成时间、GPU 占用）（新增）
6.3.4 用户与权限管理
用户列表（用户名、角色、部门、最后登录时间、状态）
创建用户（用户名、初始密码、角色、部门）
角色管理（管理员 / 普通用户 / 只读用户）
LDAP/OAuth 配置（对接企业现有认证系统）
API Key 管理（为系统集成生成 API Key）
6.3.5 审计日志
日志列表（时间、用户、查询内容、回答摘要、来源文档、延迟）
筛选（按日期范围、用户、关键词）
导出 CSV（选中范围或全部）
日志保留策略（默认 90 天，可配置）
## 6.4 WebSocket 实时对话
网页 Live Chat 通过 WebSocket 实现实时双向通信，支持流式输出（打字机效果）：
# 前端（React）
const ws = new WebSocket("ws://server:8080/ws/chat");
ws.onopen = () => ws.send(JSON.stringify({token: jwtToken}));
ws.onmessage = (e) => {
const msg = JSON.parse(e.data);
if (msg.type === "token") appendText(msg.content);
if (msg.type === "done") finishMessage();
if (msg.type === "error") showError(msg.content);
};
# 后端（FastAPI）
@router.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
await websocket.accept()
token = await websocket.receive_text()
user = verify_jwt(token)
ctx.provide("current_user", user)
while True:
query = await websocket.receive_text()
# 流式生成
async for token in chat.process_stream(query, ctx):
await websocket.send_json({"type": "token", "content": token})
await websocket.send_json({"type": "done"})
## 6.5 前端技术栈
# 七、渠道对接
## 7.1 渠道总览
EchoServe V0.1.0，所有渠道消息统一汇入 BaizeContext 的对话插件处理，确保回复一致性。
## 7.2 统一消息格式
所有渠道的消息在进入对话插件前，统一转换为内部 UnifiedMessage 格式：
class UnifiedMessage:
user_id: str          # 统一用户 ID（跨渠道关联）
channel: str          # "webchat" / "wechat" / "whatsapp"
raw_content: str      # 原始消息内容
content: str          # 清洗后的文本内容
timestamp: datetime
metadata: dict       # 渠道特有信息（如手机号、微信号等）
session_id: str       # 会话 ID（同一用户可跨渠道延续）
## 7.3 网页 Live Chat
用户在网页聊天窗口输入消息。
前端通过 WebSocket 发送到后端 /ws/chat。
后端认证用户，转换为 UnifiedMessage。
调用 ChatPlugin 处理（检索 + 生成）。
流式返回 tokens，前端逐字显示（打字机效果）。
记录审计日志。
## 7.4 企业微信对接
通过企业微信客服 API 接收用户消息，处理后通过 API 回复。
class WeChatChannelPlugin(ChannelPlugin):
plugin_id = "channel.wechat"
plugin_name = "企业微信"
dependencies = ["core.chat", "security.auth"]
def _register(self, ctx, fiber):
router = ctx.inject("http_router")
# 接收企业微信回调
router.post("/webhook/wechat", self.handle_webhook)
# URL 验证（企业微信首次配置时调用）
router.get("/webhook/wechat", self.verify_url)
async def handle_webhook(self, request: Request):
body = await request.json()
msg = self._parse_wechat_msg(body)
unified = UnifiedMessage(
user_id=msg["from_user"],
channel="wechat",
content=msg["content"],
metadata={"wechat_open_id": msg["from_user"]}
)
chat = self.ctx.inject("chat")
reply = await chat.process(unified)
await self._send_wechat_reply(msg["to_user"], reply)
## 7.5 WhatsApp 对接
V0.1.0 状态：插件代码预留，配置默认 disabled。V1.0 正式启用。
WhatsApp 通过 Meta 的 WhatsApp Business API 对接。消息流程：
用户 -> WhatsApp -> Meta 云 API -> Webhook -> EchoServe -> vLLM -> 回复
## 7.6 渠道架构图
渠道接入层           EchoServe 统一会话中心
---------------------+----------------------------------
网页 Live Chat -----> | 1. 消息聚合（统一收口）        |
企业微信 ----------> | 2. 用户认证 + 权限检查         |
WhatsApp ----------> | 3. 意图识别 + 知识库检索       |
API 调用 ----------> | 4. vLLM 推理生成回复           |
| 5. 智能路由（AI / 人工）        |
| 6. 审计日志记录                |
|                                |
| ★ 所有数据在内网，不出域 ★    |
+----------------------------------
# 八、安全与合规
## 8.1 认证与密码安全
JWT 认证：无状态 Token，有效期 8 小时，支持刷新。
密码存储：bcrypt 加密（cost=12），不存明文。
密码策略：>= 8 位，含大小写字母 + 数字 + 特殊字符。
登录失败：连续 5 次失败锁定账户 30 分钟。
LDAP/OAuth：支持对接企业现有认证系统（Active Directory 等）。
API Key：为系统集成生成独立 API Key（可吊销、可限流）。
class AuthPlugin(BaizePlugin):
plugin_id = "security.auth"
plugin_name = "认证插件"
def _register(self, ctx, fiber):
# JWT 认证中间件
middleware = JWTAuthMiddleware(secret=ctx.config.jwt_secret)
ctx.provide("auth_middleware", middleware)
# 密码哈希工具
ctx.provide("password_hasher", BcryptHasher(cost=12))
# 登录限流
limiter = LoginRateLimiter(max_attempts=5, lockout=1800)
ctx.provide("login_limiter", limiter)
## 8.2 权限控制（ACL）
支持文档级访问控制，不同角色/部门的用户只能看到授权范围内的知识。
## 8.3 审计日志
审计日志记录所有用户操作和系统事件，append-only 不可篡改。
class AuditLogger:
def __init__(self, db_path: str):
self.db = sqlite3.connect(db_path)
self._init_db()
def _init_db(self):
self.db.execute("""
CREATE TABLE IF NOT EXISTS audit_log (
id INTEGER PRIMARY KEY AUTOINCREMENT,
timestamp TEXT NOT NULL,
user_id TEXT NOT NULL,
action TEXT NOT NULL,
query TEXT,
response_summary TEXT,
sources TEXT,
latency_ms INTEGER,
ip_address TEXT,
channel TEXT
)
""")
# 防篡改：创建校验表
self.db.execute("""
CREATE TABLE IF NOT EXISTS audit_integrity (
last_hash TEXT,
last_id INTEGER
)
""")
def log(self, **fields):
# 计算链式哈希（每条日志包含上一条的哈希）
prev_hash = self._get_last_hash()
current_hash = sha256(
f"{prev_hash}|{fields}|{time.time()}".encode()
).hexdigest()
self.db.execute(
"INSERT INTO audit_log (timestamp, ..., hash) VALUES (...)",
(..., current_hash)
)
self._update_last_hash(current_hash)
self.db.commit()
防篡改机制：每条审计日志记录包含上一条记录的哈希值，形成链式结构。任何对历史日志的修改都会破坏哈希链，可以被检测出来。
## 8.4 合规要求
# 九、部署方案
## 9.1 Docker Compose 一键部署
EchoServe 通过 Docker Compose 实现一键部署，所有服务（vLLM、API、管理后台、Chroma、PostgreSQL、Redis、Nginx）统一定义在 docker-compose.yml 中。
# docker-compose.yml
version: '3.8'
services:
vllm:
image: vllm/vllm-openai:latest
runtime: nvidia
volumes:
- ./models:/models
command: --model /models/qwen3-14b-q4 --port 8000 \
--max-model-len 32768 \
--gpu-memory-utilization 0.90 \
--enable-prefix-caching
deploy:
resources:
reservations:
devices:
- driver: nvidia
count: 1
capabilities: [gpu]
restart: unless-stopped
echoseve-api:
build: .
ports:
- "8080:8080"  # API
- "9090:9090"  # 管理后台
environment:
- VLLM_URL=http://vllm:8000/v1
- DB_URL=postgresql://baize:secret@postgres/baize
- REDIS_URL=redis://redis:6379/0
- JWT_SECRET=${JWT_SECRET}
volumes:
- ./data:/app/data
- ./config:/app/config
depends_on:
- vllm
- postgres
- redis
- chroma
restart: unless-stopped
chroma:
image: chromadb/chroma:latest
volumes:
- ./chroma:/chroma/chroma
restart: unless-stopped
postgres:
image: postgres:15
environment:
POSTGRES_DB: baize
POSTGRES_USER: baize
POSTGRES_PASSWORD: ${DB_PASSWORD}
volumes:
- ./postgres:/var/lib/postgresql/data
restart: unless-stopped
redis:
image: redis:7-alpine
volumes:
- ./redis:/data
restart: unless-stopped
nginx:
image: nginx:alpine
ports:
- "443:443"
volumes:
- ./nginx/nginx.conf:/etc/nginx/nginx.conf
- ./nginx/certs:/etc/nginx/certs
depends_on:
- echoseve-api
restart: unless-stopped
# 离线训练服务（按需启动，不常驻）
trainer:
build: ./trainer
runtime: nvidia
volumes:
- ./models:/models
- ./data:/app/data
environment:
- TRAINING_OUTPUT=/models/adapters
deploy:
resources:
reservations:
devices:
- driver: nvidia
count: 1
capabilities: [gpu]
profiles: ["training"]  # 仅按需启动：docker compose --profile training up trainer
restart: "no"
修订说明：新增 trainer 服务定义，使用 Docker Compose profiles 机制实现按需启动。日常运行不包含训练服务（节省 GPU 资源），需要训练时通过 docker compose --profile training up trainer 单独启动。训练输出通过共享卷 /models/adapters 传递给推理服务。
## 9.2 部署流程
环境准备：安装 Docker 24.0+ 和 NVIDIA Container Toolkit。
模型下载：将 Qwen3-14B Q4 模型文件放入 ./models/ 目录。
配置文件：编辑 config/plugins.yaml 设置插件开关和参数。
环境变量：创建 .env 文件，填入 JWT_SECRET、DB_PASSWORD 等。
一键启动：docker compose up -d。
健康检查：访问 https://server/health 确认所有组件就绪。
初始化管理员：访问 https://server/setup 创建首个管理员账号。
上传知识库：登录管理后台，上传第一批文档。
开始使用：通过网页/企业微信/API 开始对话。
## 9.3 运维工具
# 十、实施路线图
## 10.1 阶段规划
## 10.2 MVP 详细任务分解（第 1-6 周）
## 10.3 V0.1.0 详细任务分解（第 7-12 周）
## 10.4 V1.0 详细任务分解（第 13-18 周）
# 十一、验收标准
## 11.1 功能验收
## 11.2 性能验收（修订）
## 11.3 安全验收
# 十二、术语表
# 附录：修订记录
版本 | 日期 | 修订内容
V0.1.0 | 2026-08-19 | 初始版本
V0.1.0R1 | 2026-08-19 | 修订版：硬件升级至 RTX 4090 48GB；模型进化引擎改为离线任务
优先级 | 核心目标 | 说明
P0 | 纯对话 | 聚焦基于知识库的自然语言问答，不引入工具调用、多 Agent 等复杂能力
P0 | 知识库 RAG | 支持文档上传、混合检索（BM25 + 向量 + RRF）、精准答案生成
P0 | 安全合规 | 数据本地化、权限控制、审计日志、等保 2.0 合规
P0 | 稳定易用 | 一键部署、低运维、高可用，消费级显卡即可运行
层级 | 职责 | 关键技术
应用层 | 对外暴露接口，适配不同渠道 | FastAPI、WebSocket、企业微信 SDK、WhatsApp Business API
能力层 | 插件化业务逻辑，按需加载 | BaizeContext、PluginBase、Fiber
核心层 | Python 底层基础设施 | vLLM、Chroma、SQLAlchemy、Transformers
基础设施层 | 存储、推理、部署 | Docker、NVIDIA 驱动、PostgreSQL
组件 | 选型 | 说明
后端框架 | Python FastAPI | 异步高性能，自带 API 文档，适合 Web 后端管理
LLM 推理 | vLLM（本地） | 高吞吐、支持 Prefix Cache、兼容 OpenAI API 协议
基础模型 | Qwen3-14B Q4 | 48GB 显存可流畅运行 Qwen3-14B Q4，预留 Qwen3-27B Q4 升级空间
Embedding | bge-small-zh-v1.5 | 轻量高效，中文场景表现优秀
向量数据库 | Chroma | 轻量易部署，适合中小规模知识库
关系数据库 | SQLite（小型）/ PostgreSQL（中型） | 会话、用户、配置、审计日志
BM25 索引 | PGVector tsvector | 利用 PostgreSQL 全文检索能力
Web 管理前端 | React + Tailwind | 响应式管理后台，与后端 API 对接
认证 | JWT + bcrypt | 无状态认证，密码安全存储
缓存 | Redis | 会话缓存、Prefix Cache、检索结果缓存
部署 | Docker Compose | 一键部署全部组件
反向代理 | Nginx | SSL 终止、负载均衡、静态资源
硬件环境 | 云端 NVIDIA RTX 4090 48GB（最低） | 云端算力卡，数据仍限制在客户环境内；推荐 A100 40/80GB
项目 | 规格
GPU | NVIDIA RTX 4090 / 48GB GDDR6X（原为 RTX 3090 24GB）
CPU | 8 核以上（推荐 AMD EPYC / Intel Xeon）
内存 | 64GB DDR4 以上（原为 32GB）
系统盘 | 100GB SSD（系统 + Docker 镜像）
数据盘 | 1TB NVMe SSD（知识库 + 模型 + 日志 + 训练临时数据）（原为 500GB）
网络 | 内网隔离，仅开放必要端口（443/8080）
操作系统 | Ubuntu 22.04 LTS
CUDA | 12.1+
Docker | 24.0+
指标 | 规格
GPU 推理性能（单并发） | Qwen3-14B Q4：约 50-60 tok/s（原为 30-40 tok/s）
GPU 推理性能（4 并发） | Qwen3-14B Q4：约 25-35 tok/s（原为 15-20 tok/s）
最大上下文 | 32K tokens
知识库容量 | 200 万文档（Chroma + PGVector）（原为 100 万）
并发能力 | 8-12 并发（32K 上下文）/ 16-20 并发（4K 上下文）（原为 4-6 / 8-10）
Prefix Cache 命中率目标 | >= 85%（原为 >= 80%）
首 Token 延迟（P95） | < 1.5s（短上下文）（原为 < 2s）
检索延迟（P95） | < 250ms（混合检索 + 重排序）（原为 < 300ms）
项目 | 规格
GPU | NVIDIA A100 40GB / 80GB（推荐）
适用场景 | 知识库 > 5000 条时的 LoRA 微调、全参数微调/蒸馏训练
并发能力 | A100 80GB：16-24 并发（32K 上下文）
模型升级 | 可运行 Qwen3-27B Q4 甚至 Qwen3-32B Q4
插件 ID | 插件名称 | 职责 | 依赖
core.session | 会话管理插件 | 会话创建/销毁、上下文窗口管理、会话归档 | 无
core.retrieval | 检索引擎插件 | BM25 + 向量 + RRF 混合检索、Cross-Encoder 重排序 | core.config
core.knowledge | 知识库引擎插件 | 文档上传、切片、向量化、索引构建、权限过滤 | core.retrieval
core.model | 模型管理插件 | vLLM 推理、模型加载/切换、Prefix Cache 管理 | core.config
core.chat | 对话插件 | 纯对话循环（无工具）、RAG 结果拼入 Prompt、流式输出 | core.session, core.retrieval, core.model
core.evolve | 模型进化引擎插件 | LoRA 微调、数据增强、A/B 测试、评估 Pipeline（离线执行） | core.model, core.knowledge
admin.web | Web 管理后台插件 | 用户管理、知识库管理 UI、模型管理 UI、监控仪表盘、审计日志查看 | core.session, core.knowledge, core.model
channel.webchat | 网页 Live Chat 插件 | 网页聊天窗口、WebSocket 实时通信 | core.chat
channel.wechat | 企业微信插件 | 企业微信消息接收/发送、用户映射 | core.chat
channel.whatsapp | WhatsApp 插件 | WhatsApp Business API 对接、Webhook 处理 | core.chat
security.auth | 认证插件 | JWT 认证、用户注册/登录、LDAP/OAuth 对接 | 无
security.audit | 审计日志插件 | append-only 日志记录、导出 CSV | 无
security.acl | 权限控制插件 | 文档级 ACL、角色权限管理 | security.auth
配置 | 适用场景 | 组件 | 延迟目标
轻量 | 小型客户（<100 万文档） | Chroma + PGVector tsvector + 无重排序 | < 200ms
标准 | 中型客户（100-1000 万文档） | Chroma + PGVector + MiniLM 重排序 | < 300ms
高精度 | 法律/医疗/金融客户 | 标准 + ColBERT 第三路 + 领域微调重排序 | < 400ms
阶段 | 知识库规模 | 方案 | 训练环境 | 预期效果
一 | < 2000 条 | 纯 RAG（Chroma + bge-small + BM25 + RRF） | 无需训练 | >=85%
二 | 2000-5000 条 | LoRA 微调（离线执行，r=8, target=q_proj+v_proj） | 可与推理共用 GPU，但需低峰期或专用卡 | +3-5%
三 | > 10000 条 | 全参数微调/蒸馏 + DPO（离线执行） | 建议专用 A100 或同等算力 | 全面优化
指标 | RAG-only | RAG + LoRA | 变化
知识库内准确率 | 85-90% | 88-93% | +3-5%
高频问题准确率 | 88% | 93% | +5%
低频问题准确率 | 82% | 83% | +1%
回答简洁度 | 依赖 Prompt | 更自然简洁 | 明显提升
通用能力保持 | 100% | 95-98% | -2-5%
模块 | 方法 | 路径 | 说明
认证 | POST | /api/auth/login | 用户登录，返回 JWT Token
认证 | POST | /api/auth/logout | 退出登录
认证 | GET | /api/auth/me | 获取当前用户信息
仪表盘 | GET | /api/dashboard/stats | GPU/内存/请求量/缓存命中率
仪表盘 | GET | /api/dashboard/metrics | Prometheus 格式指标
知识库 | POST | /api/kb/upload | 上传文档（PDF/DOCX/MD/TXT）
知识库 | GET | /api/kb/documents | 文档列表（分页）
知识库 | DELETE | /api/kb/documents/{id} | 删除文档
知识库 | PUT | /api/kb/documents/{id} | 编辑文档元数据
知识库 | POST | /api/kb/test | 检索测试（输入问题，返回 Top-K）
知识库 | GET | /api/kb/stats | 知识库统计（文档数/分片数/大小）
模型 | GET | /api/model/list | 可用模型列表
模型 | POST | /api/model/switch | 切换模型（热切换）
模型 | GET | /api/model/status | 当前模型状态（加载中/就绪/错误）
模型 | POST | /api/model/evolve | 触发离线模型进化（LoRA/微调）（修订）
模型 | GET | /api/model/eval-report | 最新评估报告
用户 | GET | /api/users | 用户列表（管理员）
用户 | POST | /api/users | 创建用户
用户 | DELETE | /api/users/{id} | 删除用户
用户 | PUT | /api/users/{id}/role | 修改用户角色
审计 | GET | /api/audit/logs | 审计日志列表（筛选/分页）
审计 | GET | /api/audit/export | 导出 CSV
渠道 | POST | /api/channel/whatsapp/webhook | WhatsApp Webhook 接收
渠道 | GET | /api/channel/status | 各渠道连接状态
系统 | GET | /api/system/health | 健康检查（含组件状态）
系统 | POST | /api/system/backup | 一键备份
系统 | POST | /api/system/restore | 一键恢复
系统 | GET | /api/system/config | 当前配置
对话 | WebSocket | /ws/chat | 实时对话（流式输出）
组件 | 选型 | 说明
框架 | React 18 | 组件化、生态丰富
样式 | Tailwind CSS | 原子化 CSS，快速开发
状态管理 | Zustand | 轻量、简单
路由 | React Router v6 | SPA 路由
HTTP | Axios | 拦截器处理 JWT 刷新
WebSocket | 原生 WebSocket | 流式对话
图表 | Recharts | 仪表盘图表
UI 组件 | Headless UI + Heroicons | 无样式组件，Tailwind 定制
构建 | Vite | 极速 HMR
渠道 | 优先级 | 实现方式 | 状态
网页 Live Chat | P0 | WebSocket + React 组件 | V0.1.0 启用
企业微信 | P0 | 企业微信客服 API + Webhook | V0.1.0 启用
WhatsApp | P1 | WhatsApp Business API + Webhook | V0.1.0 预留，V1.0 启用
角色 | 权限 | 说明
超级管理员 | 全部权限 | 系统配置、用户管理、模型管理、知识库管理
管理员 | 管理权限（除系统配置） | 用户管理、知识库管理、审计查看
编辑者 | 知识库读写 | 上传/编辑/删除文档
普通用户 | 对话 + 知识库只读 | 提问、查看回答、查看来源
只读用户 | 对话只读 | 仅提问，不能查看管理功能
API 用户 | API 调用权限 | 通过 API Key 调用对话接口
合规项 | 措施 | 对应标准
数据本地化 | 所有数据存储在客户环境内，不传输至外部 | 个保法 / GDPR
数据加密 | 传输层 TLS 1.2+，存储层 AES-256 | 等保 2.0 三级
访问控制 | 最小权限原则，角色分级，操作审计 | 等保 2.0 三级
日志保留 | 审计日志保留 >= 90 天 | 等保 2.0 / ISO 27001
漏洞管理 | 定期安全扫描，依赖漏洞监控 | ISO 27001
隐私保护 | 不收集无关个人信息，支持数据删除权 | 个保法 / GDPR
工具 | 说明
健康检查 | GET /health 返回各组件状态（vLLM / DB / Redis / Chroma）
监控仪表盘 | Grafana + Prometheus，展示 GPU 利用率、QPS、延迟、错误率、缓存命中率
日志收集 | ELK（Elasticsearch + Logstash + Kibana）或 Loki + Grafana
备份恢复 | 一键备份知识库、配置、用户数据到指定目录（支持定时任务）
模型热切换 | 管理后台选择已下载模型，无需重启服务
优雅停机 | 收到 SIGTERM 后完成在途请求，保存状态，再退出
自动重启 | Docker restart policy = unless-stopped，崩溃后自动拉起
资源限制 | Docker cgroups 限制 CPU/内存，防止单服务耗尽资源
离线训练 | docker compose --profile training up trainer，训练完成后自动停止，不占用推理资源（新增）
阶段 | 时间 | 交付内容 | 验收标准
MVP | 第1-6周 | Python 底层架构 + 插件系统 + 纯 RAG 检索 + 网页 Live Chat + 基础管理后台 + Docker Compose 部署 | 从零到可用 <= 30 分钟；知识库内准确率 >= 85%；部署文档完整
V0.1.0 | 第7-12周 | 企业微信渠道 + 离线 LoRA 微调引擎 + 文档级 ACL + 审计日志 + 仪表盘 + 备份恢复 + 模型管理 UI | 企业微信消息收发正常；离线 LoRA A/B 测试通过；审计日志不可篡改
V1.0 | 第13-18周 | WhatsApp 渠道 + 离线全参数微调/蒸馏 + DPO + 自动化评估 Pipeline + Windows 安装包 + LDAP 集成 | WhatsApp 消息收发正常；评估 Pipeline 自动运行；等保 2.0 三级通过
周次 | 任务 | 产出
W1 | BaizeContext + Fiber + PluginLoader 实现 | 可加载/卸载插件的运行时
W1-W2 | 检索引擎（BM25 + 向量 + RRF + 重排序） | 混合检索模块，单元测试通过
W2-W3 | 知识库引擎（解析 + 切片 + 索引） | 支持 PDF/DOCX/MD/TXT 上传和检索
W3-W4 | vLLM 集成 + 对话插件（纯 RAG 生成） | 端到端对话可用（命令行测试）
W4-W5 | FastAPI 后端 + WebSocket 对话接口 | RESTful API + 流式对话
W5 | 企业微信渠道插件 | 企业微信消息收发
W5-W6 | React 管理后台（知识库 + 仪表盘 + 用户管理） | 可用 Web UI 管理全部功能
W6 | Docker Compose 部署 + 部署文档 | 一条命令启动全部服务
W6 | 集成测试 + 性能测试 + 安全测试 | 验收标准全部达标
周次 | 任务 | 产出
W7-W8 | 离线 LoRA 微调引擎（数据增强 + 训练 + A/B 测试） | 模型进化插件可用（离线模式）
W8-W9 | 文档级 ACL 权限控制 | 不同角色看到不同检索结果
W9 | 审计日志（链式哈希防篡改 + 导出） | 审计日志模块
W9-W10 | 管理后台增强（模型管理 UI + 评估报表） | 完整的 Web 管理界面
W10-W11 | 仪表盘（Grafana + Prometheus 集成） | 实时监控面板
W11 | 备份恢复功能 | 一键备份/恢复脚本
W11-W12 | Nginx 反向代理 + HTTPS + 安全加固 | 生产级安全配置
W12 | 集成测试 + 安全审计 + 性能调优 | V0.1.0 验收通过
周次 | 任务 | 产出
W13-W14 | WhatsApp Business API 对接 | WhatsApp 渠道插件启用
W14-W15 | 离线全参数微调/知识蒸馏管线 | 阶段三模型优化能力（离线）
W15-W16 | DPO 风格对齐（离线执行） | 企业回答风格对齐
W16 | 自动化评估 Pipeline（每周运行，通知管理员） | 评估调度器
W16-W17 | LDAP / OAuth 集成 | 企业认证对接
W17 | Windows MSI 安装包 | 离线安装包
W17-W18 | 等保 2.0 三级合规审计 | 合规认证
W18 | 全量测试 + 文档完善 + 发布 | EchoServe V1.0 GA
验收项 | 标准 | 测试方法
知识库内问题准确率 | >= 85%（人工评测 200 题） | 构建测试集，逐条打分
知识库外问题不胡编 | 幻觉率 <= 5% | 50 个 out-of-domain 问题测试
文档上传处理 | <= 30s（10MB PDF） | 计时测试
网页对话流式输出 | 首 token < 1.5s（P95）（修订） | 前端计时
企业微信消息收发 | 端到端延迟 < 5s | 实际对话测试
WhatsApp 消息收发 | 端到端延迟 < 10s | 实际对话测试（V1.0）
用户认证 | JWT 正常签发/验证/刷新 | 单元测试 + 手动测试
权限控制 | 不同角色只能看到授权内容 | 权限矩阵测试
审计日志 | 不可篡改，可导出 CSV | 修改日志后哈希链断裂检测
模型切换 | 切换后新对话使用新模型 | 管理后台操作测试
离线训练触发 | 管理员可手动触发，训练不影响推理（新增） | 训练期间验证推理正常
备份恢复 | 恢复后系统状态与备份点一致 | 备份-修改-恢复-验证
一键部署 | 从零到可用 <= 30 分钟 | 全新环境计时
指标 | 目标值 | 测试条件
首 Token 延迟（P95） | <= 1.5s（修订） | 短上下文 <= 4K，单并发
首 Token 延迟（P95） | <= 4s（修订） | 长上下文 32K，单并发
检索延迟（P95） | <= 250ms（修订） | 200 万文档内
每秒请求数（QPS） | >= 8（修订） | RTX 4090，Qwen3-14B Q4
并发用户数 | >= 10（修订） | 32K 上下文，稳定不 OOM
Prefix Cache 命中率 | >= 85%（修订） | 稳定运行 24h 后统计
文档上传处理 | <= 30s | 10MB PDF
系统可用性 | >= 99.5% | 排除硬件故障，30 天统计
服务重启时间 | <= 60s | 含模型加载
验收项 | 标准 | 测试方法
数据不出域 | 抓包验证无外网连接（除 WhatsApp API） | tcpdump 抓包分析
密码加密 | 数据库中无明文密码，bcrypt 哈希 | 数据库直接查询验证
JWT 有效性 | 过期 Token 被拒绝，篡改 Token 被拒绝 | 手动构造请求测试
ACL 生效 | 普通用户看不到管理员文档 | 权限矩阵测试
审计日志防篡改 | 修改历史日志后哈希链断裂 | 哈希验证脚本
HTTPS 强制 | HTTP 请求自动跳转 HTTPS | curl 测试
登录限流 | 5 次失败后锁定 30 分钟 | 暴力破解模拟
API Key 鉴权 | 无效 Key 被拒绝，吊销后失效 | API 测试
术语 | 说明
RAG | Retrieval-Augmented Generation，检索增强生成。先检索相关知识，再让 LLM 基于检索结果生成答案
BM25 | Best Matching 25，经典的词面匹配检索算法，擅长精确匹配
RRF | Reciprocal Rank Fusion，倒排融合。将多个检索结果按排名位置融合，规避分数量纲问题
LoRA | Low-Rank Adaptation，低秩适配。只训练少量参数（低秩矩阵），冻结原模型，显存占用极低
DPO | Direct Preference Optimization，直接偏好优化。从人类偏好对中学习，无需训练奖励模型
vLLM | 高性能 LLM 推理引擎，支持 Prefix Cache、连续批处理、张量并行
Prefix Cache | 前缀缓存。相同前缀的 KV Cache 可复用，降低首 Token 延迟和推理成本
Chroma | 轻量级向量数据库，适合中小规模知识库（百万级文档）
PGVector | PostgreSQL 向量扩展，支持向量检索和全文检索（tsvector）
bge-small-zh | BAAI 开源的中文 Embedding 模型，轻量高效，384 维
Cross-Encoder | 交叉编码器。对查询和文档做精细交互打分，用于重排序，效果好但速度慢
Cordis | DeepSeek Harness 使用的插件架构框架，借鉴 Koishi 的设计理念
Fiber | 纤维。Cordis 中记录插件注册状态的轻量对象，支持精确卸载
WhatsApp Business API | Meta 提供的企业级 WhatsApp 消息接口
等保 2.0 | 网络安全等级保护制度 2.0 版本，中国网络安全合规标准
JWT | JSON Web Token，无状态认证令牌
bcrypt | 密码哈希算法，自适应计算成本，抗暴力破解
TCO | Total Cost of Ownership，总拥有成本
DeepSpeed ZeRO | 微软开源的分布式训练优化库，支持 ZeRO-1/2/3 三个阶段的内存优化
知识蒸馏 | 将大模型（教师）的知识迁移到小模型（学生），在保持性能的同时降低推理成本
版本 | 日期 | 修订人 | 修订内容
V0.1.0 | 2026-08-19 | 元宝 | 初始版本。包含 Python 底层架构、插件化设计、RAG 混合检索、模型进化引擎、Web 后端管理、渠道对接（网页/企业微信/WhatsApp）、安全合规、部署方案、实施路线图、验收标准
V0.1.0R1 | 2026-08-19 | 元策 | 修订版：（1）硬件规格从 RTX 3090 24GB 升级至 RTX 4090 48GB（最低）/ A100（推荐），内存从 32GB 升级至 64GB，数据盘从 500GB 升级至 1TB，所有性能指标同步上调；（2）模型进化引擎重构为离线任务，新增 Docker Compose profiles 机制实现训练服务按需启动，评估 Pipeline 改为仅通知管理员而非自动 promote，新增训练状态监控 UI