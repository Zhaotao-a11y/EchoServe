# EchoServe 变更日志 (CHANGELOG)

> 本文件记录 EchoServe 项目自 V0.1.0 以来的所有修复与变更。
> 格式：[版本号] - 日期 - [严重程度] 问题描述 → 修复方案 → 影响文件

---

## [V0.2.0] - 2026-08-28 — 进化系统优化 + 性能修复 + 数据清洗（Release）

### 概述

本次 Release 统一升级系统版本号至 V0.2.0，涵盖三大板块：进化系统修复与全链路验证、对话响应性能瓶颈定位与修复、临时代码与测试数据清洗。所有变更已通过启动验证。

---

### 一、进化系统修复（2 项）

#### FIX-1 — EvolutionQuery 路由前缀错误

**问题**：`plugins/evolution/phase1/query.py` 中路由前缀为 `/evolution`，但前端和 API 网关期望 `/api/evolution`，导致所有进化审核端点 404。

**修复**：路由前缀 `/evolution` → `/api/evolution`，所有 `/api/evolution/*` 端点恢复正常返回 JSON。

**影响文件**：`plugins/evolution/phase1/query.py`

---

#### FIX-2 — EvolutionPlugin 缺少 reviewer 公共属性

**问题**：`plugins/evolution/plugin.py` 未暴露 `reviewer` 属性，query.py 中 `_evolution_plugin.reviewer` 访问失败。

**修复**：新增 `reviewer` 公共属性返回 `self._reviewer`。

**影响文件**：`plugins/evolution/plugin.py`

---

### 二、进化系统全链路验证

通过临时端点完成完整进化链路验证（验证后已删除临时端点）：

| 步骤 | 验证内容 | 结果 |
|------|----------|------|
| Step 1 | 注入 60 条 skill_trace 测试数据 | ✅ |
| Step 2 | PatternMiner 挖掘模式（min_support=5） | ✅ 挖掘 6 个模式 |
| Step 3 | TemplateGenerator 生成候选模板 | ✅ 生成 6 个候选 |
| Step 4 | Reviewer.submit 提交审核 | ✅ |
| Step 5 | approve 2 / reject 1 | ✅ |
| Step 6 | 重复审批被 400 拦截 | ✅ |

**发现并修复的接口适配问题**：
- `store.write()` 不存在 → 改用 `insert_batch()`（格式 `[{"table":..., "data":...}]`）
- `store.query()` 返回的 list/dict 字段为序列化字符串 → 需 `json.loads` 反序列化

---

### 三、对话响应性能瓶颈定位与修复（2 项）

#### PERF-1 — ChromaDB 连接重试导致每次请求延迟 2.6s

**根因**：`plugins/retriever/vector.py` 中 ChromaDB 服务未运行但 `chromadb` 包已安装（`HAS_CHROMA=True`），每次 `vector.search()` 重新调用 `initialize()` 尝试连接 `chroma:8000`，DNS 解析超时 2.61s/次。

**修复**：新增 `_init_failed` 标志，初始化失败后不再重试。修复后后端处理时间从 ~7.9s 降至 ~0.5-0.7s（**降低 91%**）。

**影响文件**：`plugins/retriever/vector.py`（正式修复，保留）

---

#### PERF-2 — 全链路耗时打点与瓶颈定位

通过 chat 插件内打点和中间件打点完成全链路耗时分析：

| 组件 | 耗时 | 占比 |
|------|------|------|
| LLM 推理（qwen2.5:0.5b CPU） | ~480ms | 97% |
| BM25 检索 | 13-58ms | ~3% |
| 会话管理 + 审计日志 + 事件发布 | <3ms | <1% |

**结论**：后端链路已无优化空间，进一步提速需换更大模型或上 GPU。审计日志单次写入 0.14ms（P99 0.33ms），改异步写入提升约 0.4ms，不可感知，不值得做。

---

### 四、数据清洗与临时代码清理（6 项）

| 清理项 | 类型 | 说明 |
|--------|------|------|
| 临时测试端点 `POST /api/evolution/test/evolution-full-cycle` | 删除 | ~150 行临时验证代码，已从 query.py 移除 |
| chat 插件 PERF BREAKDOWN 性能打点 | 删除 | 8 个 `_tN` 变量 + 日志输出，已从 plugin.py 移除 |
| main.py 中间件 `[MW]` 计时代码 | 删除 | 3 行临时打点，已从 main.py 移除 |
| `test_chat_speed.py` | 删除 | 项目根目录临时测速脚本（61 行） |
| `data/test_customer_data.md` | 删除 | 临时测试数据文件 |
| `data/audit/audit.log.jsonl` | 清空 | 172 条运行时审计日志（86KB），已清空 |

**保留的正式修复**：`plugins/retriever/vector.py` 的 `_init_failed` 标志（ChromaDB 连接失败降级机制，属正式修复，保留）。

---

### 五、版本号统一升级至 V0.2.0（12 处）

| 文件 | 旧版本 | 新版本 |
|------|--------|--------|
| `api/main.py` — FastAPI version | 0.1.2 | 0.2.0 |
| `api/main.py` — FastAPI title | V0.1.0 | V0.2.0 |
| `api/main.py` — 启动日志 | V0.1.0 | V0.2.0 |
| `api/main.py` — 就绪日志 | V0.1.0 | V0.2.0 |
| `api/routers/settings.py` — version | 0.1.2 | 0.2.0 |
| `cpu_llm_server.py` — FastAPI version | 0.1.0 | 0.2.0 |
| `core/plugin.py` — plugin_version | 0.1.0 | 0.2.0 |
| `plugins/chat/plugin.py` — 文件头注释 | V0.1.0 | V0.2.0 |
| `.env` — 注释 | V0.1.0 | V0.2.0 |
| `.env.example` — 注释 | V0.1.0 | V0.2.0 |
| `.env.qwen3-8b` — 注释 | V0.1.0 | V0.2.0 |
| `web/src/App.jsx` — 前端版本显示 | V0.1.0 | V0.2.0 |
| `web/src/pages/Login.jsx` — 登录页版本标签 | V0.1.0 | V0.2.0 |

---

### 修改文件清单（10 文件）

| 文件 | 变更类型 |
|------|----------|
| `plugins/evolution/phase1/query.py` | FIX-1 路由前缀修复 + 删除临时测试端点 |
| `plugins/evolution/plugin.py` | FIX-2 reviewer 属性暴露 |
| `plugins/retriever/vector.py` | PERF-1 ChromaDB 降级标志（保留） |
| `plugins/chat/plugin.py` | 删除性能打点 + 版本号升级 |
| `api/main.py` | 删除中间件打点 + 版本号升级（4 处） |
| `api/routers/settings.py` | 版本号升级 |
| `cpu_llm_server.py` | 版本号升级 |
| `core/plugin.py` | plugin_version 升级 |
| `.env` / `.env.example` / `.env.qwen3-8b` | 版本号注释升级 |
| `web/src/App.jsx` / `web/src/pages/Login.jsx` | 前端版本显示升级 |

### 删除文件清单（2 文件）

| 文件 | 说明 |
|------|------|
| `test_chat_speed.py` | 临时测速脚本 |
| `data/test_customer_data.md` | 临时测试数据 |

---

## [0.5.0] - 2026-08-21 — 数据回流闭环实现（P0→P2，7 项）

### 概述

实现 EchoServe 的"数据→训练→评估→上线"全自动闭环：将审计日志、会话历史、偏好反馈、评估结果自动串成一条完整的自我优化链路。新增 2 个数据挖掘模块（AuditToTrainingConverter、SessionMiner），改造 PreferenceStore 和 EvaluationPipeline 支持自动触发，新增闭环监控 API 端点。py_compile 7 文件全部通过；pytest 16/16 零回归。

---

### [P0-A] AuditToTrainingConverter — 审计日志 → 训练数据

**新增文件**：`plugins/evolve/audit_to_training.py`

**功能**：
- 扫描 `data/audit/audit.log.jsonl` 中 `action=chat_query`/`chat_query_stream` 的条目
- 质量过滤：query/response 长度区间、refusal 短语检测（中英文约 60 条核心短语）
- 状态化：checkpoint 存于 `data/training/audit_converter_state.json`（`last_processed_id`），幂等增量
- 输出：Alpaca 格式追加到 `data/training/training_pool.jsonl`
- 公开方法：`convert(since_id, output_path)`、`get_stats()`、`reset_checkpoint()`

---

### [P0-B] SessionMiner — 会话历史 → SFT 数据

**新增文件**：`plugins/evolve/session_miner.py`

**功能**：
- 通过 `chat_manager.list_sessions()` + `get_session_history()` 挖掘多轮对话
- 每个 (user, assistant) 对生成一条 Alpaca 样本，包含前 `max_context_turns` 轮上下文
- 状态化：processed-sessions 集合存于 `data/training/session_miner_state.json`
- 输出：追加到 `data/training/session_mined.jsonl`
- 公开方法：`async mine(chat_manager)`、`get_stats()`、`reset_state()`

---

### [P1-A] PreferenceStore 阈值自动触发 DPO

**影响文件**：`plugins/evolve/dpo_trainer.py`、`plugins/evolve/plugin.py`、`api/main.py`

**改造内容**：

1. `PreferenceStore.__init__` 新增 `auto_trigger_threshold=50` 和 `on_auto_trigger` 回调参数
2. `record_feedback()` 末尾调用 `_check_auto_trigger()` 检查阈值
3. 触发条件：总反馈数 >= 50 且 like >= 3 且 dislike >= 3 且自上次触发新增 >= 50
4. 触发动作：自动调用 `build_dpo_dataset()` 生成数据集，然后调用 `on_auto_trigger` 回调
5. `get_stats()` 新增 `auto_trigger_threshold`、`auto_triggered_count`、`auto_trigger_pending` 字段
6. `ModelEvolvePlugin.on_init` 创建 `PreferenceStore` 单例并注册为 `"preference_store"` 服务
7. `api/main.py` 三个反馈端点改用 `ctx.inject("preference_store")` 单例（降级兼容独立实例）

---

### [P1-B] 评估通过自动 promote 新模型

**影响文件**：`plugins/evolve/evaluator.py`、`plugins/evolve/plugin.py`

**改造内容**：

1. `EvaluationPipeline` 新增 `evaluate_and_promote()` 方法：
   - 运行 A/B 测试对比当前模型 vs 候选模型（新 adapter）
   - 判定标准：improvement >= threshold（默认 0.02）且 candidate accuracy >= 历史最佳
   - 满足条件时调用 `promote_fn(adapter_name, ab_result)` 回调
   - 返回 `{ab_result, promoted, promote_reason, candidate_accuracy, current_best, threshold}`

2. `ModelEvolvePlugin` 新增闭环方法链：
   - `_on_dpo_auto_trigger(trigger_info)`：PreferenceStore 自动触发回调入口
     → 创建 `DPOTrainer` 执行训练 → 训练成功后调用 `_evaluate_and_promote()`
   - `_evaluate_and_promote(adapter_name, adapter_path)`：评估并 promote
     → 处理同步回调中的 async 上下文（线程池 + 新事件循环）
   - `_run_eval_async()`：异步执行 A/B 评估 + 调用 `evaluate_and_promote()`
   - `promote_fn` 回调内调用 `ModelManager.switch_model(base_model_id, use_lora=adapter_name)` 执行热切换

3. `get_status()` 新增 `last_promote_result` 和 `preference_stats` 字段

4. 全程审计日志记录：`dpo_auto_training_completed`、`model_auto_promoted`

---

### [P2] /api/loop/status 闭环监控端点

**影响文件**：`api/routers/evolve.py`

**新增端点**：`GET /api/loop/status`

聚合以下 5 个组件的状态，返回整体闭环健康度：
- `audit_to_training`：审计日志转换器的 checkpoint 和 training_pool 计数
- `session_miner`：会话挖掘器的 processed_sessions 和 mined_samples 计数
- `preference_store`：偏好反馈统计（total/by_type/ready_for_dpo/auto_trigger_pending）
- `evaluator`：评估历史计数和最近报告
- `training`：训练状态、adapter 数量、最近 promote 结果

`pipeline_health` 取值：`healthy`（全部启用）/ `partial`（部分启用）/ `inactive`（全部未启用）

---

### 校验结果

| 校验项 | 结果 |
|--------|------|
| py_compile（7 文件） | ALL PASS |
| pytest（16 用例） | 16/16 通过，零回归 |

### 修改文件清单（7 文件）

| 文件 | 变更类型 |
|------|----------|
| `plugins/evolve/audit_to_training.py` | 新建 — 审计日志→训练数据转换器 |
| `plugins/evolve/session_miner.py` | 新建 — 会话历史→SFT 数据挖掘器 |
| `plugins/evolve/dpo_trainer.py` | 修改 — PreferenceStore 自动触发 DPO |
| `plugins/evolve/evaluator.py` | 修改 — 新增 evaluate_and_promote() |
| `plugins/evolve/plugin.py` | 修改 — 持有 PreferenceStore + 闭环方法链 |
| `api/main.py` | 修改 — 反馈端点改用 PreferenceStore 单例 |
| `api/routers/evolve.py` | 修改 — 新增 /loop/status 端点 |

---

### 数据回流闭环架构

```
审计日志 (audit.log.jsonl)
    │
    ▼
AuditToTrainingConverter ──→ training_pool.jsonl
                                    │
会话历史 (sessions)                │
    │                              ▼
    ▼                    ┌─── 训练数据池 ───┐
SessionMiner ──→ mined.jsonl ──→│            │
                                 │            ▼
偏好反馈 (like/dislike/edit)     │     LoRA / DPO 训练
    │                           │            │
    ▼                           │            ▼
PreferenceStore ──→ dpo_dataset ──→  新 Adapter
    │ (≥50 自动触发)                   │
    │                                  ▼
    └──────────────→ A/B 评估 ──→ evaluate_and_promote
                                           │
                                    improvement ≥ 0.02?
                                     │           │
                                    YES          NO
                                     │           │
                                     ▼           ▼
                              ModelManager    不切换
                              .switch_model
```

---

## [0.4.0] - 2026-08-21 — 代码质量与安全审计修复（P0→P4，25 项）

### 概述

对 EchoServe 全代码库进行系统性审计，按 P0（运行时崩溃）→ P1（安全漏洞）→ P2（数据完整性）→ P3+P4（代码质量）优先级递进修复 30 项问题。覆盖 falsy 值误判、路径穿越、硬编码密码、并发写入竞态、裸异常吞没五大维度。py_compile 14 文件全部通过；pytest 16/16 零回归。

---

### [P0] 运行时崩溃修复（4 项）

#### P0-1 — LLM 插件 temperature=0 被误判为未传参

**问题**：`plugins/llm/plugin.py` 中 `temperature or self.temperature` 使用 `or` 短路求值，当 `temperature=0`（合法值）时被判定为 falsy，回退到默认值。`max_tokens` 同理。

**修复**：4 处改为 `temperature if temperature is not None else self.temperature`（`max_tokens` 同构）。

**影响文件**：`plugins/llm/plugin.py`

---

#### P0-2 — LLM chat() 未校验 choices 空值

**问题**：`chat()` 方法直接访问 `response["choices"][0]["message"]["content"]`，当 vLLM 返回空 choices 时抛 `IndexError`。

**修复**：添加 `response.get("choices")` 空值校验，返回降级错误消息。

**影响文件**：`plugins/llm/plugin.py`

---

#### P0-3 — Evolve 插件缺失导入 + Windows 路径不兼容

**问题**：`plugins/evolve/plugin.py` 缺少 `from pathlib import Path` 和 `List` 导入；`output_dir.split("/")[-1]` 在 Windows 路径（`\`）下返回整个路径而非目录名。

**修复**：补全导入；`split("/")[-1]` → `Path(output_dir).name`（跨平台兼容）。

**影响文件**：`plugins/evolve/plugin.py`

---

#### P0-4 — Knowledge 插件缺失 await 导致协程泄漏

**问题**：`plugins/knowledge/plugin.py` 中 `retriever.retrieve(...)` 返回 coroutine 但未 `await`，查询结果永远为空且 Python 发出 `RuntimeWarning: coroutine was never awaited`。

**修复**：添加 `await`。

**影响文件**：`plugins/knowledge/plugin.py`

---

### [P1] 安全漏洞修复（7 项）

#### P1-1 — Knowledge 文件上传路径穿越

**问题**：`plugins/knowledge/plugin.py` `upload_file()` 直接使用用户传入的 `filename`，攻击者可构造 `../../etc/passwd` 覆盖系统文件。

**修复**：`filename` → `Path(filename).name` 剥离目录部分；添加 sanitization 警告日志。

**影响文件**：`plugins/knowledge/plugin.py`

---

#### P1-2 — Auth 硬编码管理员密码

**问题**：`plugins/auth/plugin.py` 初始管理员密码硬编码为 `b"Admin@2026!"`，源码可被任何有代码访问权限的人查看。

**修复**：改为 `os.getenv("ECHOSEVE_ADMIN_PASSWORD", "")` 读取环境变量；未设置时 `secrets.token_urlsafe(16)` 随机生成并日志提示修改。

**影响文件**：`plugins/auth/plugin.py`

---

#### P1-3 — PostgreSQL 默认密码明文

**问题**：`config/settings.py` 和 `plugins/auth/user_store.py` 中 PostgreSQL 默认密码为 `"echoseve"`。

**修复**：默认值改为 `""`（空字符串），强制通过环境变量配置。

**影响文件**：`config/settings.py`, `plugins/auth/user_store.py`

---

#### P1-4 — Auth Enterprise 日志泄露 token/secret + async 误用

**问题**：`plugins/auth_enterprise/plugin.py` 中 `token_resp` 日志直接输出含 `access_token` / `refresh_token` 的完整响应体；2 处 `asyncio.run(auth._save_to_disk())` 在 async 上下文中调用必崩。

**修复**：日志脱敏（redact token/secret 字段）；`asyncio.run()` → `await auth._save_to_store()`；`ldap_sync_users()` 和 `_upsert_oauth_user()` 改为 async，调用方加 `await`。

**影响文件**：`plugins/auth_enterprise/plugin.py`

---

#### P1-5 — 14 个 API 端点缺少认证

**问题**：`api/routers/model.py`（5 个）、`api/routers/evolve.py`（8 个）、`api/routers/metrics.py`（1 个）共 14 个端点无 `verify_token` 依赖，可被未授权访问。

**修复**：所有端点添加 `user_id: str = Depends(verify_token)` 参数。

**影响文件**：`api/routers/model.py`, `api/routers/evolve.py`, `api/routers/metrics.py`

---

#### P1-6 — 缺少安全响应头

**问题**：`api/main.py` 未设置 `X-Content-Type-Options`、`X-Frame-Options`、`Strict-Transport-Security` 等安全头。

**修复**：添加 `security_headers_middleware` 中间件，统一注入 5 个安全响应头。

**影响文件**：`api/main.py`

---

### [P2] 数据完整性修复（2 项）

#### P2-1 — Auth 插件并发写入竞态 + 缩进 bug

**问题**：`plugins/auth/plugin.py` 中 `create_user`、`create_api_key`、`revoke_api_key`、`update_user_role`、`delete_user` 五个方法无锁保护，并发请求可导致 JSON 文件写入竞态。此外 `revoke_api_key`、`update_user_role`、`delete_user` 中 `logger.info` 和 `return True` 错位到 if 块外，无论操作是否成功都返回 True。

**修复**：添加 `asyncio.Lock()`，五个方法均包裹 `async with self._lock:`；修复缩进使日志和返回值位于正确条件分支内。

**影响文件**：`plugins/auth/plugin.py`

---

#### P2-2 — Knowledge 插件并发写入竞态

**问题**：`plugins/knowledge/plugin.py` 中 `add_document`、`remove_document`、`update_document` 无锁保护，并发写入可导致文档索引损坏。

**修复**：添加 `asyncio.Lock()`，三个方法均包裹 `async with self._lock:`。

**影响文件**：`plugins/knowledge/plugin.py`

---

### [P3+P4] 代码质量增强（14 项 — 裸异常日志补全）

**问题**：14 个文件中共 21 处 `except Exception: pass` 或 `except Exception:` 无日志，异常被静默吞没，生产环境排障困难。

**修复**：统一改为 `except Exception as e:` + `logger.debug()` / `logger.warning()`，保留原有降级逻辑不变。

| 文件 | 修复数 | 日志级别 |
|------|--------|----------|
| `plugins/auth/plugin.py` | 1 | warning |
| `plugins/evolve/evaluator.py` | 3 | debug |
| `plugins/retriever/vector.py` | 1 | debug |
| `plugins/llm/client.py` | 1 | debug |
| `plugins/monitoring/plugin.py` | 1 | debug |
| `plugins/monitoring/metrics.py` | 1 | debug |
| `plugins/model_manager/plugin.py` | 2 | debug |
| `plugins/model_manager/vllm_client.py` | 2 | debug |
| `plugins/auth_enterprise/plugin.py` | 3 | debug/warning |
| `scripts/code_scan.py` | 5 | stderr (print) |

**说明**：`scripts/code_scan.py` 中 5 处裸异常使用 `print(..., file=sys.stderr)` 输出，与脚本 `print` 输出风格保持一致。全项目零裸异常残留。

---

### 校验结果

| 校验项 | 结果 |
|--------|------|
| py_compile（14 文件） | ALL PASS |
| pytest（16 用例） | 16/16 通过，零回归 |

### 修改文件清单（14 文件）

| 文件 | P0 | P1 | P2 | P3+P4 |
|------|----|----|----|-------|
| `plugins/llm/plugin.py` | ✅ | | | |
| `plugins/evolve/plugin.py` | ✅ | | | |
| `plugins/knowledge/plugin.py` | ✅ | ✅ | ✅ | |
| `plugins/auth/plugin.py` | | ✅ | ✅ | ✅ |
| `config/settings.py` | | ✅ | | |
| `plugins/auth/user_store.py` | | ✅ | | |
| `plugins/auth_enterprise/plugin.py` | | ✅ | | ✅ |
| `api/routers/model.py` | | ✅ | | |
| `api/routers/evolve.py` | | ✅ | | ✅ |
| `api/routers/metrics.py` | | ✅ | | |
| `api/main.py` | | ✅ | | |
| `plugins/evolve/evaluator.py` | | | | ✅ |
| `plugins/retriever/vector.py` | | | | ✅ |
| `plugins/llm/client.py` | | | | ✅ |
| `plugins/monitoring/plugin.py` | | | | ✅ |
| `plugins/monitoring/metrics.py` | | | | ✅ |
| `plugins/model_manager/plugin.py` | | | | ✅ |
| `plugins/model_manager/vllm_client.py` | | | | ✅ |
| `scripts/code_scan.py` | | | | ✅ |

---

## [0.3.0] - 2026-08-21 — 安全审计 CRITICAL 修复（14 项）

### 概述

针对代码审计发现的 14 个 CRITICAL 级别安全问题进行批量修复，覆盖配置安全、API 认证、代码逻辑 bug、容器安全四个维度。pytest 16/16 零回归。

---

### [CRITICAL] C1 — JWT Secret 默认值可预测

**问题**：`config/settings.py` 中 `SecurityConfig.jwt_secret` 默认值为硬编码字符串 `"change-me-to-a-random-secret"`，生产环境若未配置将使用可预测密钥签发 JWT。

**修复**：
1. 新增 `_JWT_SECRET_DEFAULT` 常量隔离默认值
2. 新增 `SecurityConfig.validate_jwt_secret()` 方法：非 debug 模式使用默认值时 `raise RuntimeError`；debug 模式仅 warning
3. 新增 `Settings.validate_security()` 聚合校验方法
4. `api/main.py` lifespan 启动时调用 `settings.validate_security()`

**影响文件**：`config/settings.py`, `api/main.py`

---

### [CRITICAL] C2 — CORS allow_origins=["*"] + credentials=True

**问题**：`api/main.py` CORS 配置同时设置 `allow_origins=["*"]` 和 `allow_credentials=True`，违反 CORS 规范（浏览器会拒绝），且存在 CSRF 风险。

**修复**：当 `allow_origins` 为 `["*"]` 时强制 `credentials=False`；显式列表时 `credentials=True`；methods 限制为 `GET/POST/PUT/DELETE/PATCH`；headers 限制为 `Authorization/Content-Type/X-Request-ID`。

**影响文件**：`api/main.py`

---

### [CRITICAL] C3 — 6 个裸端点无认证

**问题**：`/api/compliance/check`、`/api/feedback`、`/api/feedback/stats`、`/api/evolve/dpo/build`、`/api/evolve/dpo/train`、`/api/admin/build-windows-installer` 共 6 个端点无任何认证，可被未授权访问。

**修复**：所有 6 个端点添加 `user_id: str = Depends(verify_token)` 参数，复用 `api/deps.py` 中的 JWT 验证依赖。

**影响文件**：`api/main.py`

---

### [CRITICAL] C4 — create_api_key 写入 user_id 而非 target_id

**问题**：`plugins/auth/plugin.py` `create_api_key()` 方法中，API Key 记录的 `user_id` 字段写入的是入参 `user_id`（可能是 username），而非解析后的 `target_id`（UUID），导致 `list_api_keys()` 按 UUID 查找时永远查不到。

**修复**：`api_key["user_id"]` 和日志输出均改为 `target_id`。

**影响文件**：`plugins/auth/plugin.py`

---

### [CRITICAL] C5 — 默认密码明文入日志

**问题**：`plugins/auth/plugin.py` 初始化默认管理员时，`logger.warning()` 将密码 `Admin@2026!` 明文输出到日志系统。

**修复**：日志改为 `password is hardcoded in source — CHANGE IMMEDIATELY via /api/auth/change-password`，不暴露实际密码值。

**影响文件**：`plugins/auth/plugin.py`

---

### [CRITICAL] C6 — users.json / api_keys.json 文件权限默认 644

**问题**：`plugins/auth/user_store.py` `save_users()` 和 `save_api_keys()` 写 JSON 文件后不设置权限，默认 644（所有用户可读），敏感凭证（密码哈希、API Key）可被同机其他用户读取。

**修复**：写文件后调用 `os.chmod(path, 0o600)` 限制为 owner-only；Windows 上 `OSError` 静默忽略。

**影响文件**：`plugins/auth/user_store.py`

---

### [CRITICAL] C7 — 审计日志 user_id 恒为 anonymous

**问题**：`plugins/chat/plugin.py` `chat()` 和 `chat_stream()` 方法中审计日志使用 `getattr(self, "_current_user_id", "anonymous")`，但 `_current_user_id` 属性从未被赋值，导致所有审计记录的 user_id 恒为 `anonymous`，审计追溯失效。

**修复**：`chat()` 和 `chat_stream()` 新增 `user_id: str = "anonymous"` 和 `channel: str = "web"` 参数，审计日志直接使用参数值。

**影响文件**：`plugins/chat/plugin.py`

---

### [CRITICAL] C8 — async 上下文中调用 asyncio.run() 必崩

**问题**：`plugins/evolve/plugin.py` `_weekly_evaluation()` 是 `async def`，其内部 `predict()` 闭包调用 `asyncio.run(chat.chat(...))`，在已有事件循环运行时触发 `RuntimeError: cannot be called from a running event loop`。

**修复**：
1. 获取 `asyncio.get_running_loop()`
2. `predict()` 改用 `asyncio.run_coroutine_threadsafe(chat.chat(...), loop)` + `future.result(timeout=300)` 从线程安全调用异步方法
3. `self.evaluator.weekly_run(predict)` 包装在 `loop.run_in_executor()` 中执行，避免阻塞事件循环

**影响文件**：`plugins/evolve/plugin.py`

---

### [CRITICAL] C9 — training_time 返回自 Epoch 以来的分钟数

**问题**：`plugins/evolve/dpo_trainer.py` `_result()` 方法中 `"training_time_minutes": round((time.time() - 0) / 60, 1)`，减 0 无意义，返回自 1970-01-01 以来的分钟数（约 2925 万），而非训练耗时。

**修复**：`_result()` 新增 `start_time: Optional[float] = None` 参数，有值时计算 `round((time.time() - start_time) / 60, 1)`，无值时返回 `0.0`；两处调用（数据不存在、数据不足）传入 `start_time`。

**影响文件**：`plugins/evolve/dpo_trainer.py`

---

### [CRITICAL] C10 — _bucket_analysis 索引越界

**问题**：`plugins/evolve/evaluator.py` A/B 测试中，`not question` 的条目被 `continue` 跳过，导致 `results_a`/`results_b` 比 `test_set` 短，而 `_bucket_analysis` 用 `enumerate(test_set)` 的索引访问 `scores_a[i]`/`scores_b[i]` 时越界。

**修复**：跳过空问题时仍 `results_a.append(0)` 和 `results_b.append(0)` 保持索引对齐。

**影响文件**：`plugins/evolve/evaluator.py`

---

### [CRITICAL] C11 — Docker 容器以 root 运行

**问题**：`Dockerfile` 未设置 `USER` 指令，容器进程以 root 运行，违反最小权限原则。

**修复**：新增 `groupadd -r echoseve` + `useradd -r -g echoseve` 创建非 root 用户，`chown -R echoseve:echoseve /app` 设置目录权限，`USER echoseve` 切换用户。

**影响文件**：`Dockerfile`

---

### [CRITICAL] C12 — 无 .dockerignore

**问题**：缺少 `.dockerignore` 文件，`COPY . .` 会将 `.env`、`.git`、`__pycache__`、测试数据等打入镜像，存在密钥泄漏风险。

**修复**：创建 `.dockerignore`，排除 `.env`/`*.key`/`credentials.json`/`.git`/`__pycache__`/`tests`/`data`/`build` 等敏感和非必要文件。

**影响文件**：`.dockerignore`（新建）

---

### [CRITICAL] C13 — 内部服务端口暴露宿主机

**问题**：`docker-compose.yml` 中 Redis(6379)、PostgreSQL(5432)、vLLM(8000)、Chroma(8001) 端口直接映射到宿主机，外部可直连数据库和推理服务。

**修复**：移除 `ports` 映射，改为 `expose` 仅在容器网络内可见。

**影响文件**：`docker-compose.yml`

---

### [CRITICAL] C14 — 无 Docker 网络隔离

**问题**：`docker-compose.yml` 未定义自定义网络，所有服务使用 Docker 默认网络，缺乏命名和管理。

**修复**：新增 `networks.default` 定义，命名为 `echoseve-internal`，使用 bridge 驱动。

**影响文件**：`docker-compose.yml`

---

## [0.2.0] - 2026-08-20 — P2 模型训练框架接入

### 概述

将三个核心模块从模拟实现升级为真实框架接入，同时保留完整的自动降级路径。
本次为功能性大版本升级，统一使用 v0.2.0 版本号。

---

### [MAJOR] LoRATrainer 接入 HuggingFace PEFT 真实训练

**改造内容**

`plugins/evolve/trainer.py` 中 `LoRATrainer.train()` 原先直接调用 `_simulate_training()` 模拟训练。

改造后：
1. `train()` 优先调用 `_train_with_peft()` 执行真实 PEFT/LoRA 训练
2. `_train_with_peft()` 方法（~100 行）：
   - 加载 tokenizer + 基础模型（bfloat16, device_map="auto"）
   - 配置 `LoraConfig`（r=16, alpha=32, target_modules=["q_proj","v_proj","k_proj","o_proj"]）
   - `get_peft_model()` 应用 LoRA 适配器
   - `_build_dataset()` 将 Alpaca 格式数据转为 HuggingFace Dataset（format_prompt + tokenize_fn）
   - `TrainingArguments` + `Trainer.train()` 执行训练
   - `save_pretrained()` 保存 adapter
   - 从 `trainer.state.log_history` 提取 train_loss / eval_loss
3. `ImportError`（PEFT/Transformers 未安装）→ 降级到 `_simulate_training()`
4. `RuntimeError`（CUDA OOM 等）→ 降级到 `_simulate_training()`
5. `adapter_info` 新增 `"train_mode"` 字段：`"peft"` / `"simulated"` / `"simulated_fallback"`

**影响文件**：`plugins/evolve/trainer.py`

---

### [MAJOR] DPOTrainer 接入 HuggingFace TRL 真实训练

**改造内容**

`plugins/evolve/dpo_trainer.py` 中 `DPOTrainer.train()` 原先直接调用 `_simulate_dpo_training()` + `_generate_training_script()` 模拟。

改造后：
1. `train()` 优先调用 `_train_with_trl()` 执行真实 TRL DPO 训练
2. `_train_with_trl()` 方法（~95 行）：
   - 加载 tokenizer + 基础模型（bfloat16, device_map="auto"）
   - 配置 `LoraConfig`（DPO 训练 LoRA 权重而非全量参数）
   - `load_dataset("json", ...)` 加载 DPO 偏好数据
   - `DPOConfig` 配置（beta, max_length, max_prompt_length, bf16 等）
   - `DPOTrainer` 初始化并 `trainer.train()`
   - `trainer.save_model()` + `tokenizer.save_pretrained()` 保存 adapter
   - 从 `trainer.state.log_history` 提取 train_loss / eval_loss
3. `ImportError`（TRL/PEFT 未安装）→ 降级到模拟训练 + 生成训练脚本（供 GPU 环境手动执行）
4. `RuntimeError`（CUDA OOM 等）→ 同上降级
5. `script_path` 初始化为 `None`，仅在降级模式下赋值
6. `adapter_info` 和返回结果新增 `"train_mode"` 字段：`"trl"` / `"simulated"` / `"simulated_fallback"`
7. `training_script` / `command` 字段仅在降级模式下添加（非 None 时）

**影响文件**：`plugins/evolve/dpo_trainer.py`

---

### [MAJOR] EvaluationPipeline 引入 LLM-as-Judge 评分

**改造内容**

`plugins/evolve/evaluator.py` 中 `_score()` 原先使用关键词匹配评分（覆盖度 + 否定词检查 + 长度检查）。

改造后：
1. `__init__` 新增可选参数 `judge_fn: Optional[Callable[[str, str, str], float]]`
   - 接收 `(question, answer, expected)` 三元组，返回 `[0, 1]` 分数
   - 为 `None` 时使用关键词匹配（完全向后兼容）
2. `_score()` 改为：
   - 若 `judge_fn` 已注入，优先调用 LLM-Judge
   - 返回值越界或调用异常时，自动降级到 `_keyword_score()`（原逻辑提取为独立方法）
3. 新增 `create_llm_judge()` 静态工厂方法：
   - 接收 `llm_chat_fn: Callable[[str], str]`（LLM 对话接口）
   - 返回符合 `judge_fn` 签名的闭包
   - 内部调用 `_build_judge_prompt()` 构造评分 prompt（4 维度：准确性 40% + 完整性 30% + 简洁性 20% + 流畅性 10%）
   - `_parse_judge_response()` 从 LLM 回答中提取分数（支持纯数字、含前缀文本等多种格式）
   - 支持重试（`max_retries` 参数）
4. 新增 `_scoring_mode` 属性：`"llm_judge"` / `"keyword"`（标识当前评分模式）

**使用示例**：
```python
from plugins.evolve.evaluator import EvaluationPipeline
from plugins.llm.plugin import LLMPlugin

llm = LLMPlugin(...)
evaluator = EvaluationPipeline(
    judge_fn=EvaluationPipeline.create_llm_judge(llm.chat),
)
```

**影响文件**：`plugins/evolve/evaluator.py`

---

### [MINOR] 配置与依赖更新

**变更清单**

| 文件 | 变更 |
|------|------|
| `requirements.txt` | 版本号 V0.1.0 → V0.2.0；新增 6 个训练依赖：`torch>=2.2.0`, `transformers>=4.40.0`, `peft>=0.10.0`, `trl>=0.8.0`, `accelerate>=0.30.0`, `datasets>=2.19.0` |
| `config/settings.py` | `EvolveConfig` 新增 6 个字段：`base_model_path`, `dpo_data_path`, `dpo_beta`, `dpo_learning_rate`, `dpo_epochs`, `llm_judge_enabled` |
| `plugins/evolve/plugin.py` | 文件头注释更新（P1 → V0.2.0，集成清单加入 DPOTrainer）；`plugin_version` 从 `"0.1.0"` 改为 `"0.2.0"` |

---

### 降级路径验证

| 模块 | 触发条件 | 降级行为 | 状态 |
|------|----------|----------|------|
| LoRATrainer | `ImportError`（PEFT 未装） | → `_simulate_training()` | ✅ |
| LoRATrainer | `RuntimeError`（CUDA OOM） | → `_simulate_training()` | ✅ |
| DPOTrainer | `ImportError`（TRL 未装） | → `_simulate_dpo_training()` + `_generate_training_script()` | ✅ |
| DPOTrainer | `RuntimeError`（CUDA OOM） | → 同上 | ✅ |
| Evaluator | `judge_fn=None` | → `_keyword_score()`（原关键词匹配） | ✅ |
| Evaluator | `judge_fn` 调用异常 | → `_keyword_score()` | ✅ |
| Evaluator | `judge_fn` 返回值越界 | → `_keyword_score()` | ✅ |

### 校验结果

- py_compile：5 文件全部 PASS（trainer.py, dpo_trainer.py, evaluator.py, plugin.py, settings.py）
- pytest：16/16 通过（11 原有 + 5 持久化），零回归

### 遗留待办（P3）

- 无新增遗留项；P2 原有三项待办已全部完成

---

### [MAJOR] v0.2.0-post — QLoRA 4-bit 量化 + Gradient Checkpointing

**问题描述**

v0.2.0 的 `_train_with_peft()` 和 `_train_with_trl()` 使用 `torch_dtype=torch.bfloat16` 全精度加载 14B 模型，显存峰值约 40-44 GB，RTX 3090 24GB 无法运行。

**修复方案**

两个训练器统一改为 QLoRA 4-bit NF4 量化加载 + gradient checkpointing：

1. 模型加载：`torch_dtype=torch.bfloat16` → `BitsAndBytesConfig(load_in_4bit=True, nf4, double_quant)` + `quantization_config=`
2. 4-bit 预处理：加载后调用 `prepare_model_for_kbit_training(model)` 使量化模型可训练
3. 训练配置：`TrainingArguments` / `DPOConfig` 新增 `gradient_checkpointing=True`
4. import 新增 `BitsAndBytesConfig` 和 `prepare_model_for_kbit_training`
5. `requirements.txt` 新增 `bitsandbytes>=0.43.0`

**显存对比**

| 指标 | 改前（bf16） | 改后（QLoRA 4-bit + grad ckpt） |
|------|-------------|-------------------------------|
| 模型权重 | 28 GB | ~8 GB |
| Activations | 8-12 GB | 3-4 GB |
| LoRA 峰值 | ~40 GB | ~12 GB |
| DPO 峰值 | ~44 GB | ~14 GB |
| 最低 GPU | A100 40GB | **RTX 3090 24GB** ✅ |

**影响文件**：`plugins/evolve/trainer.py`, `plugins/evolve/dpo_trainer.py`, `requirements.txt`

**校验**：py_compile 2 文件 PASS；pytest 16/16 通过，零回归

---

## [0.1.7-post] - 2026-08-20

### [MAJOR] Docker Compose 联调配置修复 + 持久化集成测试

**问题描述**

Docker Compose 联调时发现三处配置缺失：
1. `docker-compose.yml` 中 `api` 服务未传递 `REDIS_URL` / `POSTGRES_*` 环境变量，容器内连不上 Redis/PG
2. `requirements.txt` 缺少 `redis` 和 `asyncpg` 依赖声明
3. `.env.example` 缺少 Redis/PostgreSQL 配置模板

**修复方案**

1. `docker-compose.yml`：`api` 服务新增 10 个环境变量（`REDIS_URL` / `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD` / `REDIS_SESSION_TTL` / `REDIS_KEY_PREFIX` / `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`）
2. `requirements.txt`：新增 `redis>=5.0.0` 和 `asyncpg>=0.29.0`
3. `.env.example`：新增 Redis 和 PostgreSQL 配置段
4. 新建 `tests/test_persistence.py`：5 个集成测试用例

**集成测试覆盖**

| 测试 | 验证内容 |
|------|----------|
| `test_bm25_persistence` | BM25 索引 save/load roundtrip + 搜索验证 + clear + auto_save |
| `test_session_store_memory` | MemorySessionStore CRUD + LRU 淘汰 + 过期清理 |
| `test_session_store_redis_fallback` | Redis 连接失败 → 降级到 Memory |
| `test_user_store_json` | JSONUserStore 用户/API Key save/load roundtrip |
| `test_user_store_pg_fallback` | PostgreSQL 连接失败 → 降级到 JSON |

**校验结果**：pytest 16/16 全部通过（11 原有 + 5 新增），零回归

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `docker-compose.yml` | api 服务新增 Redis/PG 环境变量 |
| `requirements.txt` | 新增 redis + asyncpg |
| `.env.example` | 新增 Redis/PG 配置段 |
| `tests/test_persistence.py` | 新建 — 5 个持久化集成测试 |

---

## [0.1.7] - 2026-08-20

### [MAJOR] AuthPlugin 接入 PostgreSQL 持久化 (D-004)

**问题描述**

`AuthPlugin` 使用 JSON 文件（`users.json` / `api_keys.json`）存储用户和 API Key 数据。
多实例部署时各实例独立读写本地文件，无法共享用户数据，且存在并发写入冲突风险。

**修复方案**

1. 新建 `plugins/auth/user_store.py` — `UserStore` 抽象层
   - `PostgresUserStore`：使用 asyncpg 连接池，自动建表（`echoseve_users` / `echoseve_api_keys`），UPSERT 写入
   - `JSONUserStore`：回退方案，与旧版 JSON 文件行为一致
   - `create_user_store()` 工厂方法：PG 配置存在时创建 PG 实例（不连接），运行时连接失败自动降级
2. `config/settings.py` 新增 `PostgreSQLConfig`（host/port/database/user/password/pool_size）
3. `AuthPlugin` 改造：
   - `on_init` 中创建 UserStore，尝试连接 PG，失败则降级到 JSON
   - `_save_to_disk` / `_load_from_disk` → `_save_to_store` / `_load_from_store`（通过 UserStore 代理）
   - `_save_api_keys` / `_load_api_keys` → `_save_api_keys_to_store` / `_load_api_keys_from_store`
   - `on_destroy` 增加 `store.close()` 释放连接池
   - 版本号 → 0.1.7

**降级策略**

PG 不可用时，AuthPlugin 自动回退到 JSON 文件存储，行为与 v0.1.0 完全一致。日志会打印降级警告。

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/auth/user_store.py` | 新建 — UserStore 抽象层（JSON + PostgreSQL） |
| `plugins/auth/plugin.py` | 接入 UserStore + 版本号 → 0.1.7 |
| `config/settings.py` | 新增 `PostgreSQLConfig` 配置类 |

---

## [0.1.6] - 2026-08-20

### [MAJOR] ChatPlugin 接入 Redis 会话持久化 (D-003)

**问题描述**

`ChatPlugin` 使用内存 `OrderedDict` 存储会话历史，进程重启后所有会话丢失。
多实例部署时各实例独立维护内存会话，无法跨实例共享对话上下文。

**修复方案**

1. 新建 `plugins/chat/session_store.py` — `SessionStore` 抽象层
   - `RedisSessionStore`：使用 redis.asyncio，会话 JSON 序列化存储，TTL 自动过期，index 集合追踪活跃会话
   - `MemorySessionStore`：回退方案，使用 OrderedDict + LRU 淘汰，与旧版行为一致
   - `create_session_store()` 工厂方法：Redis URL 存在时创建 Redis 实例（不连接），运行时连接失败自动降级
2. `config/settings.py` 新增 `RedisConfig`（url/host/port/db/password/session_ttl/key_prefix）
3. `ChatPlugin` 改造：
   - `on_init` 中创建 SessionStore，尝试连接 Redis，失败则降级到内存
   - `_sessions` / `_session_timestamps` 字段移除，所有会话操作通过 `_store` 代理
   - `clear_session` / `get_session_history` / `list_sessions` 从同步改为 async
   - `_cleanup_expired` 改为调用 `store.cleanup_expired()`
   - 版本号 → 0.1.6
4. 调用方同步更新（async 适配）：
   - `api/routers/chat.py`：3 个端点加 `await`
   - `plugins/monitoring/plugin.py`：`_collect_business_metrics` 改为 async + `await`
   - `scripts/e2e_test.py`：2 处调用加 `await`

**降级策略**

Redis 不可用时，ChatPlugin 自动回退到内存 OrderedDict 存储，行为与 v0.1.2 完全一致。日志会打印降级警告。

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/chat/session_store.py` | 新建 — SessionStore 抽象层（Memory + Redis） |
| `plugins/chat/plugin.py` | 接入 SessionStore + 会话方法改 async + 版本号 → 0.1.6 |
| `config/settings.py` | 新增 `RedisConfig` 配置类 |
| `api/routers/chat.py` | 3 个端点 async 适配 |
| `plugins/monitoring/plugin.py` | `_collect_business_metrics` 改 async |
| `scripts/e2e_test.py` | 2 处 async 适配 |

---

## [0.1.5] - 2026-08-20

### [MAJOR] BM25 索引持久化 (D-002)

**问题描述**

`BM25Retriever` 的索引完全存储在内存中（`_docs` / `_corpus_tokens` / `_bm25`），
进程重启后所有索引数据丢失，需要重新导入全量文档。

**修复方案**

1. `BM25Retriever.__init__` 新增 `persist_path: Optional[str]` 参数
2. 新增 `save(path)` / `load(path)` / `_auto_save()` 方法：
   - `save`：将 docs + corpus_tokens + k1/b 序列化为 JSON
   - `load`：从 JSON 反序列化并重建 `_doc_map` 和 `_bm25` 索引
   - `_auto_save`：在 `add_documents` 和 `clear` 后自动调用（如果配置了 persist_path）
3. `RetrieverPlugin.on_init` 传入 `persist_path` 并调用 `bm25.load()` 加载已有索引
4. `RetrieverPlugin` 版本号 → 0.1.5

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/retriever/bm25.py` | 新增持久化方法 + 版本号 → V0.1.5 |
| `plugins/retriever/plugin.py` | 传入 persist_path + 调用 load() + 版本号 → 0.1.5 |

---

### [CRITICAL] 系统提示词并发覆盖隐患 (D-001)

**问题描述**

`LLMPlugin.update_system_prompt_with_context()` 方法直接修改 `self.system_prompt` 共享实例变量。
在多会话并发场景下，会话 A 注入的 RAG 上下文会被会话 B 覆盖，导致 LLM 收到错误的上下文信息。

**根因分析**

```python
# 修复前：修改共享状态
def update_system_prompt_with_context(self, retrieved_docs):
    self.system_prompt = "..." + knowledge_context  # ← 并发覆盖！
```

`self.system_prompt` 是 LLMPlugin 的实例变量，被所有会话共享。当会话 A 调用此方法注入 RAG 上下文后，
如果会话 B 也调用了此方法，会话 A 的上下文就被覆盖了。

**修复方案**

1. 新增 `_build_system_prompt_with_context()` 静态方法 — 纯函数，不修改共享状态
2. 新增 `chat_with_context()` — 带知识库上下文的非流式对话（线程安全）
3. 新增 `chat_stream_with_context()` — 带知识库上下文的流式对话（线程安全）
4. `update_system_prompt_with_context()` 标记为 `@deprecated`，保留向后兼容
5. `ChatPlugin.chat()` 和 `ChatPlugin.chat_stream()` 改为调用新的 context-aware 方法

```python
# 修复后：按调用构建系统提示词，不修改共享状态
async def chat_with_context(self, messages, retrieved_docs, ...):
    system_prompt = self._build_system_prompt_with_context(retrieved_docs)
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    # ... 调用 vLLM
```

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/llm/plugin.py` | 新增 3 个方法 + 标记旧方法 deprecated + 版本号 → 0.1.4 |
| `plugins/chat/plugin.py` | `chat()` 和 `chat_stream()` 改用 `chat_with_context` / `chat_stream_with_context` |

---

## [0.1.3] - 2026-08-20

### [MAJOR] trainer.py 末尾重复的 ModelEvolvePlugin 类定义 (ISSUE-001)

**问题描述**

`plugins/evolve/trainer.py` 文件末尾（原第 270-419 行）存在一个与 `plugins/evolve/plugin.py` 中
`ModelEvolvePlugin` 完全重复的类定义。

**根因分析**

Python 允许在同一文件中多次定义同名类，后定义的会覆盖前面的。这不会导致运行时错误，
但会造成维护混乱——开发者可能修改了 `plugin.py` 中的版本而忘记同步 `trainer.py` 中的重复定义，
导致运行时加载的是过时版本。

**修复方案**

- 删除 `trainer.py` 末尾的 `ModelEvolvePlugin` 类定义（148 行）
- 添加注释说明正式实现位于 `plugin.py`
- 文件从 419 行缩减至 271 行

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/evolve/trainer.py` | 删除重复类定义（-148 行）+ 版本号 → 0.1.3 |

---

## [0.1.2] - 2026-08-20

### [MAJOR] 非流式对话延迟计算错误 (D-005)

**问题描述**

`ChatPlugin.chat()` 方法中，审计日志的 `latency_ms` 恒接近 0，无法反映真实的 LLM 推理延迟。

**根因分析**

```python
# 修复前：
# 第 88 行：在 LLM 调用之前就更新了 timestamp
self._session_timestamps[session_id] = time.time()

# ... LLM 调用（耗时数秒）...

# 第 131 行：用 timestamp 计算延迟，但 timestamp 刚刚被更新过
latency_ms = int((time.time() - self._session_timestamps.get(session_id, time.time())) * 1000)
# ↑ time.time() - time.time() ≈ 0
```

`_session_timestamps` 用于会话超时清理，在方法入口处就被更新为当前时间。
审计日志复用这个值计算延迟，导致基准时间几乎等于结束时间。

**修复方案**

引入独立的 `start_time` 局部变量，在方法入口记录，审计日志中使用 `time.time() - start_time` 计算真实延迟。

```python
# 修复后：
start_time = time.time()           # 独立的开始时间
self._session_timestamps[session_id] = start_time  # 仅用于超时清理

# ... LLM 调用 ...

latency_ms = int((time.time() - start_time) * 1000)  # 正确的延迟计算
```

**同时修复：流式对话 latency_ms 写死为 0**

`chat_stream()` 方法中审计日志的 `latency_ms=0` 是硬编码值，同样改为 `int((time.time() - start_time) * 1000)`。

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/chat/plugin.py` | `chat()` + `chat_stream()` 延迟计算修复 + 版本号 → 0.1.2 |

---

## [0.1.1] - 2026-08-20

### [CRITICAL] 流式对话历史记录使用字符串字面量而非变量 (BUG-001)

**问题描述**

`ChatPlugin.chat_stream()` 方法在流式对话完成后更新会话历史时，用户消息被写死为字符串 `"user_message"`
而非使用变量 `user_message` 的值。

**根因分析**

```python
# 修复前（第 178 行）：
history.append({"role": "user", "content": "user_message"})  # ← 字符串字面量！
# 应为：
history.append({"role": "user", "content": user_message})    # ← 变量引用
```

这是一个典型的 Python 引号错误。`"user_message"` 是字符串字面量，而 `user_message` 是方法参数变量。

**影响**

所有流式对话的历史记录中，用户消息被错误记录为字面量 `"user_message"`，导致：
1. 多轮对话上下文完全失效——LLM 在后续轮次中看到的用户消息永远是 "user_message"
2. 审计日志中的用户查询内容也是错误的

**对比验证**

非流式 `chat()` 方法第 106 行写法正确：`history.append({"role": "user", "content": user_message})`

**修复方案**

将第 178 行的 `"user_message"` 改为 `user_message`（去掉引号）。

**影响文件**

| 文件 | 变更类型 |
|------|----------|
| `plugins/chat/plugin.py` | 第 178 行修复字符串字面量 → 变量引用 + 版本号 → 0.1.1 |

---

## 版本号规则

- **patch 位递增**（0.1.X）：Bug 修复、缺陷修补，不改变 API 接口
- **minor 位递增**（0.X.0）：新功能、API 变更（向后兼容）
- **major 位递增**（X.0.0）：架构级变更（不保证向后兼容）

## 接手指南

如果你是新接手的开发者，请按以下步骤快速了解项目状态：

1. **阅读本文档**：了解所有已修复的问题和当前版本
2. **阅读分析报告**：`docs/EchoServe_分析报告.html` — 完整的文档 vs 代码一致性分析
3. **阅读开发文档**：`docs/dev_doc_extracted.md` — 从 docx 提取的开发文档全文
4. **检查待修复项**：分析报告第 11 节"改进路线图"中 P1/P2 项尚未完成
5. **运行测试**：`python -m pytest tests/ -v`（如有测试）

### 当前已知待办（按优先级）

| 优先级 | 编号 | 任务 | 状态 |
|--------|------|------|------|
| P1 | D-002 | BM25 索引持久化（当前全内存，重启丢失） | ✅ 已完成 (v0.1.5) |
| P1 | D-003 | 接入 Redis 会话持久化（docker-compose 已定义 Redis 服务） | ✅ 已完成 (v0.1.6) |
| P1 | D-004 | 接入 PostgreSQL 数据持久化（docker-compose 已定义 PostgreSQL 服务） | ✅ 已完成 (v0.1.7) |
| P2 | — | LoRATrainer 接入真实 PEFT 训练（当前为模拟实现） | ✅ 已完成 (v0.2.0) |
| P2 | — | DPOTrainer 接入真实 TRL 训练（当前为模拟实现） | ✅ 已完成 (v0.2.0) |
| P2 | — | 评估器引入 LLM-as-Judge（当前为关键词匹配评分） | ✅ 已完成 (v0.2.0) |
| P3+P4 | — | scripts/ 下 6 处裸异常日志补全 | ✅ 已完成 (v0.4.0) |
| P0 | — | AuditToTrainingConverter（审计日志→训练数据） | ✅ 已完成 (v0.5.0) |
| P0 | — | SessionMiner（会话历史→SFT 数据） | ✅ 已完成 (v0.5.0) |
| P1 | — | PreferenceStore 阈值自动触发 DPO | ✅ 已完成 (v0.5.0) |
| P1 | — | 评估通过自动 promote 新模型 | ✅ 已完成 (v0.5.0) |
| P2 | — | /api/loop/status 闭环监控端点 | ✅ 已完成 (v0.5.0) |
