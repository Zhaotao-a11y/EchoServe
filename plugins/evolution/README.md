# EchoServe Evolution System 开发文档

> **文档版本**: v1.0  
> **编写日期**: 2026-08-27  
> **目标读者**: 后端开发工程师、运维工程师、算法工程师  
> **关联文档**: `EchoServe_进化系统优化方案_修订版.docx`

---

## 一、项目概述

### 1.1 定位

Evolution System 是 EchoServe 智能体的**自我进化基础设施**，负责从生产对话数据中持续学习，逐步优化系统参数和技能模板。

**核心原则**：自动做"发现"和"建议"，人工做"确认"和"生效"。

### 1.2 三级进化架构

```
Phase 1: EvolutionService (数据采集中台)
    ├─ 采集所有关键事件（对话、技能执行、路由决策、用户反馈）
    ├─ 统一存储到 SQLite + JSONL 冷归档
    └─ 提供查询接口供人工分析

Phase 2: ParameterTuning (单参数 A/B 测试)
    ├─ 一次只改一个参数（如 TopK）
    ├─ 一致性哈希分流，延迟评估（t-test）
    └─ 人工确认后渐进生效

Phase 3: SkillEvolution (人工审核模式)
    ├─ 自动挖掘高频、高成功率的技能序列
    ├─ 生成候选模板，推送到人工审核台
    └─ 审核通过 → 灰度 → 全量（带自动回滚）
```

### 1.3 设计约束

- **零侵入主流程**：所有采集都是异步的，失败不阻塞对话响应
- **可逆操作**：每个进化动作都可以回滚
- **人工兜底**：Phase 2 和 Phase 3 的关键决策需要人工确认
- **量化驱动**：每个阶段都有明确的量化指标和收敛条件

---

## 二、目录结构

```
plugins/evolution/
├── __init__.py              # 插件主入口，统一导出
├── shared/                  # Phase 1-3 共享基础设施
│   ├── __init__.py
│   ├── models.py            # 数据模型（dataclass）
│   ├── metrics.py           # 指标采集器（MetricsCollector）
│   └── failover.py          # 降级管理器（FailoverManager）
├── phase1/                  # Phase 1: 数据采集中台
│   ├── __init__.py
│   ├── collector.py         # 事件采集器（EvolutionCollector）
│   ├── store.py             # 数据存储层（EvolutionStore）
│   └── query.py             # REST API 查询接口
├── phase2/                  # Phase 2: 参数调优
│   ├── __init__.py
│   ├── param_pool.py        # 参数配置池（ParamPool）
│   ├── experimenter.py      # A/B 实验器（Experimenter）
│   └── evaluator.py         # 效果评估器（Evaluator）
├── phase3/                  # Phase 3: 技能进化
│   ├── __init__.py
│   ├── pattern_miner.py     # 模式挖掘器（PatternMiner）
│   ├── template_generator.py # 候选模板生成器
│   ├── reviewer.py          # 人工审核台
│   └── template_registry.py # 模板注册表（灰度/全量/回滚）
└── tests/                   # 单元测试
    ├── test_models.py
    ├── test_collector.py
    ├── test_experimenter.py
    └── test_failover.py
```

---

## 三、共享层（Shared Layer）

### 3.1 models.py — 数据模型

所有 Phase 共享的数据结构，使用 `@dataclass` 定义。

**核心模型**：

| 模型 | 用途 | 所属 Phase |
|------|------|----------|
| `ChatLogRecord` | 对话记录 | Phase 1 |
| `SkillTraceRecord` | 技能执行链路 | Phase 1/3 |
| `FeedbackRecord` | 用户反馈 | Phase 1 |
| `RouteLogRecord` | 路由决策 | Phase 1/2 |
| `ExperimentConfig` | 实验配置 | Phase 2 |
| `EvalResult` | 实验评估结果 | Phase 2 |
| `SkillPattern` | 挖掘出的技能模式 | Phase 3 |
| `SkillTemplateCandidate` | 候选技能模板 | Phase 3 |

**枚举类型**：
- `FeedbackType`: like / dislike
- `ExperimentStatus`: pending / running / converged / failed / paused / approved / rejected
- `TemplateStatus`: draft / pending_review / approved / canary / active / disabled / rolled_back
- `DegradationLevel`: normal / level_1 / level_2 / level_3

### 3.2 metrics.py — 指标采集器

内存中的环形指标缓冲区，不依赖外部时序数据库。

**核心方法**：

```python
collector = MetricsCollector(capacity=10000)

# 记录标量
collector.record("retrieval_hit_rate", 85.5, tags={"param": "top_k"})

# 计数器
collector.increment("store_write_failure")

# 延迟统计（上下文管理器）
with collector.timer("evolution.flush_duration_ms"):
    await store.insert_batch(records)

# 查询
avg = collector.get_avg("retrieval_hit_rate", window=100)
p99 = collector.get_percentile("evolution.flush_duration_ms", 0.99)
```

### 3.3 failover.py — 降级管理器

**三级降级策略**：

| 级别 | 触发条件 | 影响范围 | 恢复方式 |
|------|---------|---------|---------|
| Level 1 | 实验指标暴跌 (>20%) | 暂停单参数实验 | 自动恢复 |
| Level 2 | 灰度模板失败率高 | 禁用灰度模板 + 暂停实验 | 人工确认恢复 |
| Level 3 | 存储写入阻塞 | EvolutionService 只读 | 必须人工恢复 |

**使用方法**：

```python
failover = FailoverManager()
failover.create_default_rules()
failover.set_notifier(async_notifier)

# 评估异常信号
await failover.evaluate_signal("experiment.metric_drop", {"drop": 0.25})

# 检查当前级别
if failover.can_run_experiment():
    await experimenter.create_experiment(...)
```

---

## 四、Phase 1: EvolutionService

### 4.1 架构

```
主客服流程 (FiberManager)
    │
    │ 发布事件
    ▼
EventBus ──→ EvolutionCollector ──→ EvolutionStore ──→ EvolutionQuery (REST API)
                │
                └─ 降级: JSONL fallback
```

### 4.2 collector.py — 事件采集器

**事件订阅列表**：

| 事件名 | 处理函数 | 记录表 |
|-------|---------|--------|
| `chat.complete` | `_on_chat_complete` | chat_log |
| `skill.execute` | `_on_skill_execute` | skill_trace |
| `user.feedback` | `_on_user_feedback` | feedback |
| `route.decision` | `_on_route_decision` | route_log |
| `system.metric` | `_on_system_metric` | system_metric |

**关键设计**：
- 批量缓冲：每 50 条或每 5 秒 flush 一次
- 异步写入：使用 `asyncio.create_task()` 后台提交
- 降级：写入失败时自动转存 JSONL，不丢数据
- 积压上限：MAX_BACKLOG = 1000，超限时强制 flush

**使用示例**：

```python
from plugins.evolution.phase1.collector import EvolutionCollector
from plugins.evolution.phase1.store import EvolutionStore

store = EvolutionStore(Path("data/evolution.db"))
await store.init()

collector = EvolutionCollector(store=store)
collector.attach_to_bus(event_bus)
await collector.start()

# 在业务代码中发布事件
bus.publish("chat.complete", {
    "session_id": "sess_001",
    "query": "怎么退货",
    "reply": "您可以...",
    "latency_ms": 1200,
    "retrieved_docs": ["doc_1", "doc_2"],
})
```

### 4.3 store.py — 数据存储层

**双层存储**：
- 热数据（最近 7 天）：SQLite，支持复杂查询
- 冷数据（7 天前）：JSONL gzip 压缩归档
- 自动清理：90 天前数据自动删除

**表结构**：

| 表名 | 核心字段 | 索引 |
|------|---------|------|
| `chat_log` | session_id, query, reply, latency_ms, timestamp | session_id, timestamp |
| `skill_trace` | trace_id, skill_id, success, error, latency_ms, timestamp | session_id, timestamp |
| `feedback` | session_id, feedback_type, timestamp | session_id, timestamp |
| `route_log` | query, top_k, bm25_weight, retrieved_count, timestamp | timestamp |
| `system_metric` | cpu, memory, gpu_util, timestamp | timestamp |
| `experiment_log` | experiment_id, param_name, status, result | experiment_id |
| `template_log` | template_id, status, rollout_percent | template_id |

**关键配置**：

```python
store = EvolutionStore(
    db_path=Path("data/evolution.db"),      # SQLite 主库
    cold_dir=Path("data/evolution/cold"),   # 冷归档目录
)
```

### 4.4 query.py — REST API

**端点列表**：

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/evolution/health` | 健康检查 |
| POST | `/evolution/chat-log` | 查询对话记录（分页） |
| POST | `/evolution/skill-stats` | 统计技能调用指标 |
| POST | `/evolution/feedback-summary` | 汇总用户反馈 |
| POST | `/evolution/route-stats` | 路由决策统计 |
| GET | `/evolution/stats` | 存储统计概览 |

**注册到 FastAPI**：

```python
from fastapi import FastAPI
from plugins.evolution.phase1.query import router as evolution_router
from plugins.evolution.phase1.query import set_store

app = FastAPI()
set_store(evolution_store)  # 注入 store
app.include_router(evolution_router)
```

---

## 五、Phase 2: ParameterTuning

### 5.1 可调参数范围

| 参数 | 当前值 | 候选值 | 评估指标 | 最少样本 |
|------|--------|--------|----------|---------|
| `RETRIEVAL_TOP_K` | 5 | [3, 7, 10] | retrieval_hit_rate | 500 |
| `BM25_WEIGHT` | 0.5 | [0.3, 0.4, 0.6, 0.7] | retrieval_hit_rate | 500 |
| `RERANK_THRESHOLD` | 0.1 | [0.0, 0.05, 0.15, 0.2] | user_nps | 500 |

**冻结参数**（不允许 A/B 测试）：
- EMBEDDING_MODEL：更换需重建知识库
- MAX_SEQ_LENGTH：与硬件强耦合
- TEMPERATURE：影响生成稳定性

### 5.2 param_pool.py — 参数配置池

```python
from plugins.evolution.phase2.param_pool import ParamPool, ParamDefinition

pool = ParamPool()
pool.register(ParamDefinition(
    name="top_k",
    description="检索TopK",
    current_value=5,
    candidate_values=[3, 7, 10],
))

# 获取当前值
value = pool.get("top_k")  # -> 5

# 快照（实验前备份）
pool.snapshot("baseline")

# 实验结束后提交新值
pool.commit_experiment("top_k", 7, "exp_v1")
```

### 5.3 experimenter.py — A/B 实验器

**分流算法**：MD5 一致性哈希

```
hash = md5(user_id + param_name + experiment_version)
bucket = int(hash, 16) % 100
group = "treatment" if bucket < traffic_percent else "control"
```

**关键特性**：
- 同一用户始终分配到同一组
- 实验版本隔离：不同实验重新分组
- 支持多参数并行实验（每个参数独立分流）

**使用示例**：

```python
from plugins.evolution.phase2.experimenter import Experimenter

experimenter = Experimenter(pool, traffic_percent=50)
exp_id = await experimenter.create_experiment(
    param_name="top_k",
    candidate_values=[3, 7, 10],
    eval_metric="retrieval_hit_rate",
)

# 为用户分配参数值
assignment = experimenter.assign_user("user_123", "top_k")
print(assignment.group)           # "control" or "treatment"
print(assignment.assigned_value)  # 5 (control) or 3/7/10 (treatment)

# 记录用户产生的指标
experimenter.record_metric("user_123", "top_k", 0.85)
```

### 5.4 evaluator.py — 效果评估器

**统计方法**：
- **Welch's t-test**：不假设等方差
- **单侧检验**：只关心 treatment 是否优于 control
- **Cohen's d 效应量**：过滤小效应
- **显著性水平**：alpha = 0.05

**收敛判定**：

| 结果 | 条件 | 后续动作 |
|------|------|---------|
| 显著提升 | p < 0.05 且 treatment > control | 生成报告 → 人工确认队列 |
| 显著下降 | p < 0.05 且 treatment < control | 自动终止，保持原参数 |
| 无显著差异 | p >= 0.05 | 继续运行，达最大样本后终止 |

**使用示例**：

```python
from plugins.evolution.phase2.evaluator import Evaluator

evaluator = Evaluator(pool, experimenter)
result = evaluator.evaluate("exp_v1")

if result and result.is_significant and result.winner == "treatment":
    # 人工确认后才生效
    print(f"建议采纳: {result.param_name} = {result.candidate_value}")
    print(f"提升幅度: {result.effect_size:.2f}")
    print(f"p-value: {result.p_value:.4f}")
```

---

## 六、Phase 3: SkillEvolution

### 6.1 前置条件

**必须满足以下所有条件才能启用 Phase 3**：
1. Phase 1 稳定运行 ≥ 3 个月
2. Phase 2 稳定运行 ≥ 2 个月
3. 累计对话数据 ≥ 10 万条
4. 有专职审核人员（每周 ≥ 4 小时）

### 6.2 pattern_miner.py — 模式挖掘器

**挖掘流程**：
1. 筛选优质执行记录（success=True, user_feedback=like）
2. 按用户问题意图聚类
3. 在每个聚类内统计技能调用序列频率
4. 提取高频序列（出现 ≥ 10 次，成功率 ≥ 90%）

**使用示例**：

```python
from plugins.evolution.phase3.pattern_miner import PatternMiner

miner = PatternMiner(store)
patterns = await miner.mine_patterns(days=30)

for p in patterns:
    print(f"意图: {p.intent}")
    print(f"序列: {' -> '.join(p.skill_sequence)}")
    print(f"出现: {p.frequency} 次, 成功率: {p.success_rate:.1%}")
    print(f"置信度: {p.confidence:.1f}")
```

### 6.3 template_generator.py — 候选模板生成器

将挖掘出的模式转化为标准化的候选模板。

**输出字段**：
- `trigger_conditions`: 触发条件列表
- `skill_sequence`: 执行技能序列
- `parameter_mapping`: 参数映射规则
- `expected_output_template`: 预期输出模板

### 6.4 reviewer.py — 人工审核台

**审核状态流转**：

```
DRAFT -> PENDING_REVIEW -> APPROVED / REJECTED / MODIFY
```

**前端界面需展示**：
- 候选模板详情（触发条件、执行序列、参数映射）
- 支撑数据（出现次数、成功率、原始记录链接）
- 模拟测试结果（在历史问题上的通过率）
- 操作按钮：[采纳并入库] [拒绝] [修改后采纳]

### 6.5 template_registry.py — 模板注册表

**状态机**：

```
APPROVED -> register() -> CANARY -> canary(10%) -> ACTIVE -> promote(100%)
                |                                              |
                |                                              v
                |                                        ROLLED_BACK <- rollback()
                v
          DISABLED <- disable()
```

**使用示例**：

```python
from plugins.evolution.phase3.template_registry import TemplateRegistry

registry = TemplateRegistry(failover_manager)

# 注册审核通过的模板
registry.register(candidate)

# 启动灰度（10% 流量）
registry.canary(template_id, rollout_percent=0.1)

# 全量激活（人工确认后）
registry.promote(template_id)

# 回滚到上一个版本
registry.rollback(template_id)

# 获取当前活跃模板
active = registry.get_active("退货咨询")
```

**自动全量触发条件**（可选）：
- 灰度期间成功率 ≥ 95%
- 灰度期间满意度不低于对照组

---

## 七、部署指南

### 7.1 环境依赖

```bash
# Python 3.11+
pip install fastapi uvicorn pydantic

# SQLite（Python 标准库已包含）
# 无需额外安装数据库
```

### 7.2 初始化

```python
from pathlib import Path
from plugins.evolution.phase1.store import EvolutionStore
from plugins.evolution.phase1.collector import EvolutionCollector
from plugins.evolution.shared.failover import FailoverManager

# 1. 创建存储层
store = EvolutionStore(
    db_path=Path("data/evolution.db"),
    cold_dir=Path("data/evolution/cold"),
)
await store.init()

# 2. 创建采集器
collector = EvolutionCollector(store=store)
collector.attach_to_bus(event_bus)
await collector.start()

# 3. 创建降级管理器
failover = FailoverManager()
failover.create_default_rules()

# 4. 注册查询 API
from plugins.evolution.phase1.query import set_store, router
set_store(store)
app.include_router(router)
```

### 7.3 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `EVOLUTION_DB_PATH` | `data/evolution.db` | SQLite 数据库路径 |
| `EVOLUTION_COLD_DIR` | `data/evolution/cold` | 冷归档目录 |
| `EVOLUTION_FALLBACK_DIR` | `data/evolution/fallback` | 降级 JSONL 目录 |
| `BATCH_SIZE` | 50 | 采集批量 flush 大小 |
| `FLUSH_INTERVAL` | 5 秒 | 定时 flush 间隔 |
| `MAX_BACKLOG` | 1000 | 积压上限 |
| `HOT_DAYS` | 7 | 热数据保留天数 |
| `CLEANUP_DAYS` | 90 | 数据总保留天数 |
| `EXPERIMENT_MIN_SAMPLES` | 500 | 实验最少样本 |
| `EXPERIMENT_MAX_SAMPLES` | 2000 | 实验最大样本 |
| `CANARY_PERCENT` | 10% | 默认灰度流量 |
| `CANARY_SUCCESS_THRESHOLD` | 85% | 灰度通过成功率 |
| `PATTERN_MIN_FREQUENCY` | 10 | 模式最少出现次数 |
| `PATTERN_MIN_SUCCESS_RATE` | 90% | 模式最少成功率 |

### 7.4 定时任务

```python
# 数据清理（每日凌晨）
async def scheduled_cleanup():
    await store.cleanup()

# 实验评估（每小时）
async def scheduled_evaluation():
    for exp_id in experimenter.list_experiments(status=ExperimentStatus.RUNNING):
        result = evaluator.evaluate(exp_id)
        if result and result.is_significant:
            # 推送报告到前端，等待人工确认
            await push_report(result)

# 降级恢复检查（每 15 分钟）
async def scheduled_recovery():
    await failover.run_recovery_checks()
```

---

## 八、测试指南

### 8.1 运行测试

```bash
# 进入测试目录
cd plugins/evolution

# 运行全部测试
python -m pytest tests/ -v

# 运行指定测试
python -m pytest tests/test_experimenter.py -v
python -m pytest tests/test_failover.py -v

# 覆盖率报告
python -m pytest tests/ --cov=. --cov-report=html
```

### 8.2 测试文件说明

| 测试文件 | 覆盖内容 | 测试数 |
|---------|---------|--------|
| `test_models.py` | 数据模型创建和序列化 | 13 |
| `test_collector.py` | 批量 flush、降级、积压处理 | 8 |
| `test_experimenter.py` | 分流一致性、t-test、回滚 | 20 |
| `test_failover.py` | 降级规则、恢复、冷却期 | 30 |

---

## 九、交接清单

### 9.1 代码交接

- [ ] 所有 Python 文件已添加 docstring 和类型提示
- [ ] 代码通过 PEP 8 检查（`ruff check .` 或 `black --check .`）
- [ ] 无硬编码密钥或敏感信息
- [ ] 单元测试覆盖率 ≥ 70%
- [ ] README.md 已更新到最新版本

### 9.2 配置交接

- [ ] 数据库路径和冷归档目录已确认
- [ ] 定时任务已配置（cleanup / evaluation / recovery）
- [ ] 告警通知渠道已配置（企业微信/邮件 webhook）
- [ ] 备份策略已确认（SQLite 文件备份）

### 9.3 运维交接

- [ ] 监控指标基线已建立（日志完整率、查询延迟等）
- [ ] 降级演练已完成（至少一次 Level 1 → Normal 恢复）
- [ ] 数据清理日志已确认正常
- [ ] 磁盘空间监控已配置（防止冷归档占满）

### 9.4 业务交接

- [ ] Phase 1 已稳定运行，数据完整率达标
- [ ] Phase 2 参数列表已和业务方确认（哪些可调、哪些冻结）
- [ ] Phase 3 审核人员已确定，审核 SOP 已培训
- [ ] 评估报告模板已确认（指标对比、统计检验结果）

---

## 十、关键设计决策

### 10.1 为什么不用外部时序数据库？

**决策**：使用 SQLite + JSONL 而非 Prometheus/InfluxDB。

**理由**：
- 降低部署复杂度，单文件可移植
- EchoServe 目标场景（中小规模客服）数据量可控
- 查询接口简单，无需复杂聚合

**权衡**：
- 大规模场景（日活 > 10 万）需迁移到专用时序数据库
- 迁移路径：EvolutionStore 接口抽象，底层可替换

### 10.2 为什么 A/B 只做单参数？

**决策**：一次只改一个参数，不做多参数联合优化。

**理由**：
- 2000 条样本不足以支撑多变量统计检验
- 单参数结果可解释性强，人工审核门槛低
- 降低实验失败时的排查复杂度

### 10.3 为什么技能模板必须人工审核？

**决策**：自动挖掘的模板必须人工审核后才能生效。

**理由**：
- 客服场景对安全/合规要求高，自动生效风险大
- 模板质量难以完全量化（用户体验、话术风格）
- 审核过程本身也是知识沉淀

---

## 十一、故障排查速查

### 11.1 采集数据丢失

**现象**：`log_completeness_rate` < 99%。

**排查**：
1. 检查 `collector.get_stats()` 中的 `write_failures`
2. 查看 fallback 目录中是否有积压的 JSONL 文件
3. 检查 SQLite 磁盘空间是否已满

**修复**：
- 手动将 JSONL 文件导入 SQLite：`scripts/reimport_fallback.py`
- 扩大磁盘空间或缩短 CLEANUP_DAYS

### 11.2 实验评估不收敛

**现象**：实验运行 7 天仍未达到显著性。

**排查**：
1. 检查样本量：`experimenter.get_assignment_stats(exp_id)`
2. 检查指标方差：评估报告中看 Cohen's d 值
3. 检查分流是否均匀：control/treatment 样本比是否接近 1:1

**修复**：
- 样本不足：延长实验周期或降低 min_samples
- 方差太大：换一个更稳定的评估指标
- 分流不均：检查 user_id 分布是否均匀

### 11.3 灰度模板异常

**现象**：灰度期间成功率 < 85%。

**排查**：
1. 检查 `template_registry.get_template(id).metrics`
2. 查看原始执行记录：`store.query("skill_trace", ...)`
3. 对比对照组同一时段的指标

**修复**：
- 自动：FailoverManager Level 2 会自动禁用
- 手动：调用 `registry.rollback(template_id)`
- 分析：查看失败案例，修改后重新审核

---

## 十二、与主应用融合（v1.0）

### 融合状态

- **插件主入口**: `plugins/evolution/plugin.py`（`EvolutionPlugin` 类）
- **注册位置**: `api/main.py` P1 插件段，`loader.register(EvolutionPlugin)`
- **依赖**: `core.events`（EventBus）
- **路由挂载**: `/evolution/*`（通过 `http_router` 注册到 FastAPI）

### 启动流程

```
1. main.py 创建 BaizeContext + http_router
2. main.py loader.register(EvolutionPlugin)  // on_load
3. main.py loader.load_all()                 // on_init（挂载 REST API）
4. main.py fiber_manager.start_all()           // on_start（订阅事件 + 启动采集器）
5. main.py app.include_router(plugin_router)   // 路由生效
```

### Context 注册的服务

| 服务名 | 类型 | 用途 |
|--------|------|------|
| `evolution` | `EvolutionPlugin` | 插件实例，公开管理 API |
| `evolution_store` | `EvolutionStore` | 数据查询（其他插件读取）|
| `evolution_collector` | `EvolutionCollector` | 手动触发采集 |
| `failover_manager` | `FailoverManager` | 降级状态查询 |
| `param_pool` | `ParamPool` | 参数配置池 |
| `experimenter` | `Experimenter` | A/B 实验器 |
| `evolution_evaluator` | `Evaluator` | 效果评估器 |
| `pattern_miner` | `PatternMiner` | 模式挖掘 |
| `template_generator` | `TemplateGenerator` | 候选模板生成 |
| `reviewer` | `Reviewer` | 人工审核台 |
| `template_registry` | `TemplateRegistry` | 模板状态机 |

### 事件订阅

EvolutionPlugin 自动订阅以下 EventBus 事件：

| 事件名 | 处理函数 | 用途 |
|--------|----------|------|
| `chat.complete` | `_on_chat_complete` | 记录对话日志 |
| `skill.execute` | `_on_skill_execute` | 记录技能执行链路 |
| `user.feedback` | `_on_user_feedback` | 记录用户反馈 |
| `route.decision` | `_on_route_decision` | 记录检索参数决策 |
| `system.metric` | `_on_system_metric` | 记录系统指标 |

> **注意**：降级到 Level 2 及以上时，事件采集自动暂停（不阻塞主流程）。

### 集成验证

```bash
cd plugins/evolution
python -m pytest tests/ -v
# 77 passed in ~0.8s
```

### 卸载方式

若需临时禁用进化系统：

1. 注释 `api/main.py` 中 `loader.register(EvolutionPlugin)`
2. 重启服务

> 数据保留在 `data/evolution.db`，不影响主系统运行。

### 12.1 事件总线集成

在 EchoServe 主流程中，关键位置发布事件：

```python
# chat complete 后
bus.publish("chat.complete", {
    "session_id": session.id,
    "query": session.query,
    "reply": session.reply,
    "retrieved_docs": [d.id for d in docs],
    "latency_ms": latency,
})

# skill execute 后
bus.publish("skill.execute", {
    "session_id": session.id,
    "skill_id": skill.id,
    "success": success,
    "error": error_msg,
    "latency_ms": latency,
})

# user feedback 时
bus.publish("user.feedback", {
    "session_id": session.id,
    "feedback_type": "like",  # or "dislike"
    "comment": user_comment,
})
```

### 12.2 数据库 Schema（完整）

见 `phase1/store.py` 中的 `_create_tables()` 方法。

### 12.3 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-08-27 | 初始版本，Phase 1-3 完整实现 |

---

> **维护者**: 赵工 (ZhaoGong)  
> **最后更新**: 2026-08-27
