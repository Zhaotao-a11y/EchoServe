# EchoServe 知识库导入教程（2000条客服问答数据）

## 数据格式要求

EchoServe 知识库支持两种核心导入格式：

| 格式 | 适用场景 | 说明 |
|------|---------|------|
| **JSONL** | 结构化问答数据（推荐） | 每行一个JSON，必须含 `content` 字段 |
| **Markdown/TXT/PDF/DOCX** | 长文档上传 | 自动切片，每500-800 token一块 |

对于你的2000条客服问答（.md表格），**强烈推荐走 JSONL 批量导入**——每条问答独立索引，检索精度远高于整文件上传。

---

## 第一步：准备你的数据文件

将你的 `.md` 数据文件放到项目目录，例如：

```bash
D:\llm_learn\OmniZee-B\OmniZee\data\customer_service_data.md
```

数据格式示例（.md表格）：

```markdown
| id | intent | domain | sub | query | expected_reply |
|---|---|---|---|---|---|
| 1 | 账户相关 | account | issue | 为什么登录不了？ | 修改手机号请进入... |
| 2 | 退换货申请 | order | return | 我想换个大一号的 | 了解您的需求... |
```

---

## 第二步：转换脚本（.md → JSONL）

已为你准备好转换脚本：

**文件位置**：`scripts/md_to_jsonl.py`

使用方法：

```bash
# 1. 进入项目目录
cd D:\llm_learn\OmniZee-B\OmniZee

# 2. 执行转换（假设你的数据文件叫 customer_service_data.md）
python scripts/md_to_jsonl.py \
    --input data/customer_service_data.md \
    --output data/knowledge.jsonl

# 3. 验证输出
head -n 3 data/knowledge.jsonl
```

转换后的 JSONL 格式（每行一条）：

```json
{"content": "问题：为什么登录不了？\n回答：修改手机号请进入「账户安全 ➡ 绑定手机」，验证原手机号后即可更换。", "metadata": {"intent": "账户相关", "domain": "account", "sub": "issue", "query": "为什么登录不了？"}}
{"content": "问题：我想换个大一号的\n回答：了解您的需求。请进入「我的订单 ➡ 申请售后」...", "metadata": {"intent": "退换货申请", "domain": "order", "sub": "return", "query": "我想换个大一号的"}}
```

---

## 第三步：导入知识库（三种方式）

### 方式一：命令行脚本导入（推荐，适合批量）

```bash
# 确保服务已启动
cd D:\llm_learn\OmniZee-B\OmniZee
bash start_cpu.sh

# 新开一个终端，执行导入脚本
python scripts/ingest_knowledge.py \
    --file data/knowledge.jsonl \
    --token YOUR_JWT_TOKEN
```

### 方式二：curl 命令导入（适合调试）

```bash
# 1. 先登录获取 Token
TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"EchoServe#Admin2026"}' \
    | grep -o '"token":"[^"]*"' | cut -d'"' -f4)

# 2. 导入 JSONL
curl -X POST http://localhost:8080/api/knowledge/ingest \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@data/knowledge.jsonl"

# 3. 查看导入结果
# 返回示例：{"status": "ingested", "total": 2000, "kb_size": 2000}
```

### 方式三：Web 后台上传（适合小文件/测试）

1. 打开浏览器访问：`http://localhost:8080`
2. 登录：`admin` / `EchoServe#Admin2026`
3. 进入知识库管理页面
4. 点击「批量导入」→ 选择 `.jsonl` 文件
5. 等待索引构建完成

---

## 第四步：验证导入效果

### 4.1 查看知识库统计

```bash
curl -H "Authorization: Bearer $TOKEN" \
    http://localhost:8080/api/knowledge/stats
```

### 4.2 检索测试（关键！）

```bash
# 测试检索：输入一个用户问题，看能否召回正确的标准答案
curl -X GET "http://localhost:8080/api/knowledge/test?query=登录不上怎么办&top_k=3" \
    -H "Authorization: Bearer $TOKEN"
```

返回示例：

```json
{
  "query": "登录不上怎么办",
  "results": [
    {
      "content": "问题：为什么登录不了？...",
      "score": 0.92,
      "metadata": {"intent": "账户相关", "domain": "account"}
    }
  ]
}
```

### 4.3 对话测试

打开 Web 聊天界面，发送：`"我登录不了"`，观察模型是否使用 RAG 检索到正确的回复话术。

---

## 常见问题排查

### Q1: 导入报错 "missing 'content'"

**原因**：JSONL 中某行缺少 `content` 字段。
**解决**：检查 .md 文件中是否有空行或格式错误的行。

### Q2: 检索召回率不高

**原因**：`query` 和 `expected_reply` 拼在一起可能太长，或者用户提问方式差异大。
**优化**：
- 在 `content` 中多放几个同义问法：`"问题：登录不上/无法登录/登录失败...\n回答：..."`
- 使用 `scripts/augment_queries.py` 扩充同义问题

### Q3: 导入后对话没有变化

**原因**：ChatPlugin 可能没有启用 RAG 检索。
**检查**：
1. 确认 `plugins/chat/plugin.py` 中调用了 `inject("retriever")`
2. 确认知识库文档数量 > 0：`curl /api/knowledge/stats`

### Q4: 2000条数据太大，导入超时

**解决**：分批导入，每批500条：

```bash
# 将 JSONL 拆分成多个小文件
split -l 500 knowledge.jsonl knowledge_batch_

# 逐批导入
for f in knowledge_batch_*; do
    curl -X POST http://localhost:8080/api/knowledge/ingest \
        -H "Authorization: Bearer $TOKEN" \
        -F "file=@$f"
    sleep 2
done
```

---

## 性能指标参考

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 导入速度 | ~100条/秒 | CPU环境，BM25索引 |
| 检索延迟 | <200ms | 2000条文档规模 |
| 召回率 | >85% | Top-3能否包含正确答案 |
| 精确率 | >90% | 第一条结果是否正确 |

---

## 下一步优化（可选）

1. **意图路由**：利用 `intent/domain/sub` 标签，在检索前先做意图分类，缩小搜索范围
2. **答案排序**：对召回的多个结果，用 `domain` 匹配度做二次排序
3. **模型微调**：当 RAG 效果稳定后，可用这2000条数据做 LoRA 微调（需租GPU）

---

## 一键执行脚本

如果你想一次性完成转换+导入+验证，直接运行：

```bash
cd D:\llm_learn\OmniZee-B\OmniZee
python scripts/full_import_pipeline.py \
    --input data/customer_service_data.md \
    --host http://localhost:8080 \
    --username admin \
    --password EchoServe#Admin2026
```

这个脚本会自动：转换 → 登录获取Token → 批量导入 → 验证统计 → 测试检索
