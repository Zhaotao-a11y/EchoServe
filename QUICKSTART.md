# EchoServe V0.2.0

> 5 分钟从零到可用

---

## 前置条件

| 要求 | 说明 |
|------|------|
| GPU | NVIDIA RTX 4090 48GB（最低）/ A100（推荐） |
| 系统 | Ubuntu 22.04 LTS |
| 磁盘 | ≥ 1TB NVMe SSD（系统 200GB + 数据 800GB） |
| 内存 | ≥ 64GB |
| 网络 | 可访问 Docker Hub（首次拉镜像） |

---

## 一键部署

```bash
# 1. 上传项目
scp EchoServe_V0.2.0.zip user@server:/data/
ssh user@server

# 2. 解压
cd /data && unzip EchoServe_V0.2.0.zip && cd echoseve-b-mvp

# 3. 配置
cp .env.example .env
sed -i "s|JWT_SECRET=.*|JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')|" .env
sed -i "s|DB_PASSWORD=.*|DB_PASSWORD=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')|" .env

# 4. 放模型
mkdir -p models/qwen3-14b-q4
# 将 Qwen3-14B Q4 模型文件放入 models/qwen3-14b-q4/

# 5. 启动
docker compose up -d --build

# 6. 等待就绪
watch -n 5 'docker compose ps'
# 所有服务状态变为 healthy 即可

# 7. 验证
curl https://localhost/health | jq
```

---

## 首次使用

### 1. 登录管理后台

浏览器打开 `https://your-server-ip`

| 项 | 值 |
|---|---|
| 用户名 | `admin` |
| 密码 | `Admin@2026!` |

⚠️ **首次登录后请立即修改密码！**

### 2. 上传知识库

1. 进入「知识库管理」页面
2. 拖拽上传 PDF / DOCX / MD / TXT 文件
3. 等待索引构建完成（10MB 文件约 10-30 秒）
4. 使用「检索测试」验证效果

### 3. 开始对话

1. 进入「对话测试」页面
2. 输入问题，系统自动检索知识库并生成回答
3. 支持多轮对话，上下文自动管理

### 4. 配置企业微信（可选）

1. 在企业微信管理后台创建应用
2. 记录 Corp ID / Agent ID / Secret
3. 编辑 `.env` 填入对应值
4. 重启 `docker compose restart api`
5. 在企微后台设置接收消息 URL：`https://your-server/webhook/wechat`

---

## 常用命令速查

```bash
# 启动
docker compose up -d

# 停止
docker compose down

# 查看日志
docker compose logs -f api

# 重启单个服务
docker compose restart nginx

# 备份
bash scripts/backup.sh

# 恢复
bash scripts/restore.sh --file data/backups/xxx.tar.gz

# 等保检查
curl https://localhost/api/compliance/check | jq

# 监控
docker compose --profile monitoring up -d
# 打开 https://localhost:3001 （Grafana）

# 训练（离线 LoRA）
docker compose --profile training up -d
```

---

## 下一步

- 📖 阅读完整部署文档：`docs/DEPLOYMENT.md`
- 📋 查看版本说明：`RELEASE_NOTES.md`
- 🔧 配置企业认证（LDAP/OAuth）：见部署文档第 11 章
- 📊 配置监控告警：见部署文档第 12 章
- 🔒 安全加固：见部署文档第 17 章
