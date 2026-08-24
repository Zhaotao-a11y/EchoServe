# EchoServe V0.1.0

> 本文档面向运维工程师和系统管理员，提供从零到生产可用的完整部署流程。
> 适用版本：EchoServe V0.1.0 (P2 完整版)
> 最后更新：2026-08-20

---

## 目录

1. [硬件与系统要求](#1-硬件与系统要求)
2. [网络与端口规划](#2-网络与端口规划)
3. [操作系统准备](#3-操作系统准备)
4. [GPU 驱动与 CUDA 安装](#4-gpu-驱动与-cuda-安装)
5. [Docker 环境部署](#5-docker-环境部署)
6. [项目部署](#6-项目部署)
7. [模型准备](#7-模型准备)
8. [SSL 证书配置](#8-ssl-证书配置)
9. [企业微信配置](#9-企业微信配置)
10. [WhatsApp 配置（可选）](#10-whatsapp-配置可选)
11. [LDAP/OAuth 企业认证配置（可选）](#11-ldapoauth-企业认证配置可选)
12. [监控与告警](#12-监控与告警)
13. [备份策略](#13-备份策略)
14. [升级流程](#14-升级流程)
15. [故障排查](#15-故障排查)
16. [性能调优建议](#16-性能调优建议)
17. [安全加固清单](#17-安全加固清单)
18. [等保 2.0 三级合规检查](#18-等保-20-三级合规检查)

---

## 1. 硬件与系统要求

### 最低配置（RTX 4090 48GB）

| 组件 | 规格 | 说明 |
|------|------|------|
| GPU | NVIDIA RTX 4090 / 48GB GDDR6X | 单卡即可运行 Qwen3-14B Q4 |
| CPU | 8 核以上（推荐 AMD EPYC / Intel Xeon） | 推理主要为 GPU 计算 |
| 内存 | 64GB DDR4 ECC | 模型加载 + 系统 + 缓存 |
| 系统盘 | 200GB NVMe SSD | 系统 + Docker 镜像 + 依赖 |
| 数据盘 | 1TB NVMe SSD | 知识库 + 模型 + 日志 + 备份 |
| 网络 | 千兆以太网 | 内网隔离，仅开放 443/80 |
| 电源 | 850W 80+ Gold 以上 | RTX 4090 满载约 450W |

### 推荐配置（A100）

| 组件 | 规格 | 说明 |
|------|------|------|
| GPU | NVIDIA A100 40GB / 80GB（单卡或双卡） | 更高吞吐，支持更大并发 |
| CPU | 16 核以上 | |
| 内存 | 128GB DDR4 ECC | |
| 系统盘 | 500GB NVMe SSD | |
| 数据盘 | 2TB NVMe SSD（RAID 1 可选） | 数据冗余 |
| 网络 | 万兆以太网 | 高并发场景 |

### 操作系统

| 项目 | 要求 |
|------|------|
| 发行版 | Ubuntu 22.04 LTS（推荐）/ CentOS 8 Stream |
| 内核 | 5.15+ |
| 文件系统 | ext4 / xfs |
| 分区 | 独立 `/data` 分区（≥ 1TB） |

---

## 2. 网络与端口规划

### 对外暴露端口

| 端口 | 协议 | 用途 | 暴露对象 |
|------|------|------|----------|
| 443 | HTTPS | Web 管理后台 + API + WebSocket | 企业内部用户 |
| 80 | HTTP | 自动跳转 HTTPS | 企业内部用户 |

### 内部端口（仅 Docker 网络）

| 端口 | 服务 | 说明 |
|------|------|------|
| 8080 | EchoServe API | FastAPI 应用 |
| 8000 | vLLM 推理 | LLM 推理服务 |
| 8001 | Chroma | 向量数据库 |
| 5432 | PostgreSQL | 关系数据库 |
| 6379 | Redis | 缓存 |
| 9090 | Prometheus | 监控指标采集 |
| 3001 | Grafana | 监控仪表盘 |

### 防火墙规则

```bash
# 仅允许内部网络访问
ufw default deny incoming
ufw allow from 10.0.0.0/8 to any port 443
ufw allow from 172.16.0.0/12 to any port 443
ufw allow from 192.168.0.0/16 to any port 443
ufw allow 22/tcp  # SSH（建议改为非标准端口）
ufw enable
```

---

## 3. 操作系统准备

### 3.1 系统更新

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    curl wget git vim htop iotop \
    build-essential pkg-config \
    ca-certificates gnupg lsb-release \
    software-properties-common \
    unzip zip tar \
    net-tools dnsutils
```

### 3.2 创建专用用户

```bash
# 创建 echoseve 用户（不允��� SSH 登录）
sudo useradd -m -s /bin/bash -d /home/echoseve echoseve
sudo usermod -aG docker echoseve

# 设置目录权限
sudo mkdir -p /data/echoseve-b
sudo chown -R echoseve:echoseve /data/echoseve-b
```

### 3.3 系统参数调优

```bash
# 增加文件描述符限制
sudo tee /etc/security/limits.d/echoseve.conf << 'EOF'
echoseve soft nofile 65535
echoseve hard nofile 65535
echoseve soft nproc 65535
echoseve hard nproc 65535
EOF

# 内核参数优化
sudo tee /etc/sysctl.d/99-echoseve.conf << 'EOF'
# 网络
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 30

# 内存
vm.swappiness = 10
vm.overcommit_memory = 1

# 文件系统
fs.file-max = 655350
EOF

sudo sysctl -p /etc/sysctl.d/99-echoseve.conf
```

---

## 4. GPU 驱动与 CUDA 安装

### 4.1 安装 NVIDIA 驱动

```bash
# 检查 GPU 识别
lspci | grep -i nvidia

# 安装推荐驱动（Ubuntu 22.04）
sudo apt install -y nvidia-driver-535

# 重启
sudo reboot

# 验证
nvidia-smi
# 应看到 RTX 4090，驱动版本 ≥ 535
```

### 4.2 安装 CUDA Toolkit

```bash
# 安装 CUDA 12.1
wget https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_530.30.02_linux.run
sudo sh cuda_12.1.0_530.30.02_linux.run --silent --toolkit

# 配置环境变量
echo 'export PATH=/usr/local/cuda-12.1/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda-12.1/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

# 验证
nvcc --version
```

### 4.3 安装 NVIDIA Container Toolkit

```bash
# 添加仓库
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 安装
sudo apt update
sudo apt install -y nvidia-container-toolkit

# 配置 Docker
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 验证
sudo docker run --rm --gpus all nvidia/cuda:12.1-base nvidia-smi
```

---

## 5. Docker 环境部署

### 5.1 安装 Docker Engine

```bash
# 卸载旧版本
sudo apt remove -y docker docker-engine docker.io containerd runc

# 安装 Docker
curl -fsSL https://get.docker.com | sudo sh

# 验证
sudo docker --version       # Docker 24.0+
sudo docker compose version  # Docker Compose v2+
```

### 5.2 Docker 配置优化

```bash
sudo tee /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "5"
  },
  "storage-driver": "overlay2",
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 65535,
      "Soft": 65535
    }
  },
  "live-restore": true,
  "restart-policy": "unless-stopped"
}
EOF

sudo systemctl restart docker
```

### 5.3 磁盘空间监控

```bash
# 设置 Docker 日志轮转
sudo tee /etc/logrotate.d/docker << 'EOF'
/var/lib/docker/containers/*/*.log {
    rotate 5
    daily
    compress
    size=100M
    missingok
    delaycompress
    copytruncate
}
EOF
```

---

## 6. 项目部署

### 6.1 解压项目

```bash
# 切换到专用用户
su - echoseve
cd /data/echoseve-b

# 上传并解压
# （通过 scp / sftp 将 EchoServe_V0.1.0.zip 传到服务器）
unzip EchoServe_V0.1.0.zip
cd echoseve-b-mvp
```

### 6.2 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 生成随机密钥
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
GRAFANA_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

# 写入配置
sed -i "s|JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" .env
sed -i "s|DB_PASSWORD=.*|DB_PASSWORD=${DB_PASSWORD}|" .env
sed -i "s|GRAFANA_PASSWORD=.*|GRAFANA_PASSWORD=${GRAFANA_PASSWORD}|" .env

# 设置文件权限
chmod 600 .env
```

### 6.3 创建必要目录

```bash
mkdir -p \
    data/knowledge \
    data/chroma \
    data/audit \
    data/auth \
    data/logs \
    data/backups \
    data/training/reports \
    data/training/preferences \
    models/qwen3-14b-q4 \
    nginx/certs

chmod -R 755 data/
chmod -R 700 data/audit/
chmod -R 700 data/auth/
```

### 6.4 启动服务

```bash
# 首次启动（构建镜像）
docker compose up -d --build

# 查看启动进度
docker compose ps
docker compose logs -f api

# 等待所有服务健康
watch -n 5 'docker compose ps'
# 所有服务状态变为 "healthy" 即可
```

### 6.5 验证部署

```bash
# 健康检查
curl https://localhost/health | jq

# 预期输出：
# {
#   "status": "healthy",
#   "version": "0.1.2",
#   "plugins": {
#     "core.config": "started",
#     "security.auth": "started",
#     "security.audit": "started",
#     "core.retriever": "started",
#     "core.llm": "started",
#     "core.knowledge": "started",
#     "core.chat": "started",
#     "channel.wechat": "started",
#     "core.model": "started",
#     "core.monitoring": "started",
#     "core.evolve": "started",
#     "security.enterprise": "started",
#     "channel.whatsapp": "started"
#   }
# }

# 就绪检查
curl https://localhost/ready | jq

# API 文档
curl https://localhost/docs
```

### 6.6 初始化管理员

```bash
# 首次登录（使用默认账号）
curl -X POST https://localhost/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username": "admin", "password": "Admin@2026!"}' | jq

# 修改默认密码（强烈建议立即执行）
TOKEN="<上一步返回的 access_token>"
curl -X PUT https://localhost/api/users/<user_id>/password \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"new_password": "YourNewSecurePassword@2026!"}'
```

---

## 7. 模型准备

### 7.1 下载模型

```bash
# 方式一：从 HuggingFace 下载（需要外网访问）
cd models/
git lfs install
git clone https://huggingface.co/Qwen/Qwen3-14B-GPTQ-Int4 ./qwen3-14b-q4

# 方式二：离线拷贝（推荐生产环境）
# 在能访问外网的机器上下载后，通过 scp 传输
scp -r qwen3-14b-q4/ echoseve@server:/data/echoseve-b/echoseve-b-mvp/models/
```

### 7.2 模型文件校验

```bash
# 确认模型文件完整
ls -lh models/qwen3-14b-q4/
# 应包含：config.json, tokenizer.json, model-*.safetensors, generation_config.json

# 检查模型大小（Q4 量化约 8-9GB）
du -sh models/qwen3-14b-q4/
```

### 7.3 模型热切换测试

```bash
# 查看当前模型
curl https://localhost/api/model/status \
    -H "Authorization: Bearer ${TOKEN}" | jq

# 切换模型（如果有多个模型）
curl -X POST https://localhost/api/model/switch \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"model_path": "/models/qwen3-14b-q4"}' | jq
```

---

## 8. SSL 证书配置

### 8.1 使用 Let's Encrypt（推荐）

```bash
# 安装 certbot
sudo apt install -y certbot

# 申请证书（需要域名解析到本机）
sudo certbot certonly --standalone -d echoseve.yourcompany.com

# 复制证书到项目目录
sudo cp /etc/letsencrypt/live/echoseve.yourcompany.com/fullchain.pem \
    /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.crt
sudo cp /etc/letsencrypt/live/echoseve.yourcompany.com/privkey.pem \
    /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.key

# 设置权限
sudo chown echoseve:echoseve /data/echoseve-b/echoseve-b-mvp/nginx/certs/*
chmod 600 /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.key

# 重启 Nginx
docker compose restart nginx
```

### 8.2 使用企业内部 CA

```bash
# 将企业 CA 签发的证书放入 nginx/certs/
cp your-server.crt /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.crt
cp your-server.key /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.key

# 如果是中间 CA 签发，需要完整链
cat your-server.crt intermediate.crt > /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.crt

# 重启 Nginx
docker compose restart nginx
```

### 8.3 自动续期（Let's Encrypt）

```bash
# 添加 crontab
echo "0 3 * * * /usr/bin/certbot renew --quiet && cp /etc/letsencrypt/live/echoseve.yourcompany.com/fullchain.pem /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.crt && cp /etc/letsencrypt/live/echoseve.yourcompany.com/privkey.pem /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.key && chown echoseve:echoseve /data/echoseve-b/echoseve-b-mvp/nginx/certs/* && docker compose -f /data/echoseve-b/echoseve-b-mvp/docker-compose.yml restart nginx" | crontab -
```

---

## 9. 企业微信配置

### 9.1 创建企业微信应用

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」→「创建应用」
3. 记录以下信息：
   - **Corp ID**（企业 ID）
   - **Agent ID**（应用 ID）
   - **Secret**（应用密钥）

### 9.2 配置 EchoServe

```bash
# 编辑 .env 文件
cat >> .env << 'EOF'
WECHAT_CORP_ID=ww1234567890abcdef
WECHAT_AGENT_ID=1000001
WECHAT_SECRET=your-app-secret-here
WECHAT_TOKEN=your-custom-token
WECHAT_AES_KEY=your-encoding-aes-key
EOF

# 重启服务
docker compose restart api
```

### 9.3 配置接收消息 URL

1. 在企业微信应用设置中，找到「接收消息」→「设置 API 接收」
2. URL 填写：`https://echoseve.yourcompany.com/webhook/wechat`
3. Token 和 AES Key 与 .env 中保持一致
4. 点击「保存」，企业微信会发送验证请求

### 9.4 验证企业微信连接

```bash
# 检查 Webhook 状态
curl https://localhost/api/channel/status \
    -H "Authorization: Bearer ${TOKEN}" | jq

# 在企业微信中向应用发送消息，查看日志
docker compose logs -f api | grep wechat
```

---

## 10. WhatsApp 配置（可选）

### 10.1 申请 Meta 开发者账号

1. 注册 [Meta for Developers](https://developers.facebook.com/)
2. 创建应用 → 选择「Business」类型
3. 添加「WhatsApp」产品
4. 获取 **Phone Number ID** 和 **Access Token**

### 10.2 配置 Webhook

```bash
# 编辑 .env
cat >> .env << 'EOF'
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_ACCESS_TOKEN=your-permanent-access-token
WHATSAPP_VERIFY_TOKEN=your-custom-verify-token
WHATSAPP_APP_SECRET=your-app-secret
EOF

# 重启服务
docker compose restart api
```

### 10.3 配置 Webhook URL

在 Meta 开发者控制台：
- URL: `https://echoseve.yourcompany.com/webhook/whatsapp`
- Verify Token: 与 .env 中一致
- 订阅事件: `messages`

### 10.4 注意事项

- WhatsApp Business API 有速率限制（每号码 80 条/分钟）
- 需要公网 HTTPS 地址（可用 ngrok 做开发测试）
- 消息内容需符合 Meta 商业政策

---

## 11. LDAP/OAuth 企业认证配置（可选）

### 11.1 LDAP 配置

```bash
# 编辑 .env
cat >> .env << 'EOF'
LDAP_ENABLED=true
LDAP_SERVER=ldap://your-ad-server.company.com
LDAP_PORT=389
LDAP_BIND_DN=CN=echoseve-svc,OU=ServiceAccounts,DC=company,DC=com
LDAP_BIND_PASSWORD=service-account-password
LDAP_USER_BASE=OU=Users,DC=company,DC=com
LDAP_USER_FILTER=(&(objectClass=user)(sAMAccountName={username}))
LDAP_ATTR_USERNAME=sAMAccountName
LDAP_ATTR_EMAIL=mail
LDAP_ATTR_DISPLAY_NAME=displayName
LDAP_AUTO_CREATE_USER=true
LDAP_DEFAULT_ROLE=user
EOF

# 重启服务
docker compose restart api

# 手动同步用户
curl -X POST https://localhost/api/auth/ldap/sync \
    -H "Authorization: Bearer ${TOKEN}"
```

### 11.2 OAuth2 配置（以 Azure AD 为例）

```bash
cat >> .env << 'EOF'
OAUTH_ENABLED=true
OAUTH_PROVIDER=azure
OAUTH_CLIENT_ID=your-azure-app-client-id
OAUTH_CLIENT_SECRET=your-azure-app-secret
OAUTH_TENANT_ID=your-azure-tenant-id
OAUTH_REDIRECT_URI=https://echoseve.yourcompany.com/api/auth/oauth/callback
OAUTH_SCOPE=openid profile email
EOF

# 重启服务
docker compose restart api
```

### 11.3 用户认证降级链

EchoServe 支持认证链降级：
1. **优先**：OAuth2 / LDAP（企业认证）
2. **降级**：本地 JWT 认证
3. **兜底**：API Key 认证

---

## 12. 监控与告警

### 12.1 启动监控栈

```bash
# 启动 Prometheus + Grafana
docker compose --profile monitoring up -d

# 验证
curl http://localhost:9090/-/ready    # Prometheus
curl http://localhost:3001/api/health  # Grafana
```

### 12.2 访问 Grafana 仪表盘

1. 浏览器打开 `https://echoseve.yourcompany.com:3001`（或配置反向代理）
2. 登录：admin / `${GRAFANA_PASSWORD}`
3. 自动加载 EchoServe 仪表盘

### 12.3 关键监控指标

| 指标 | 告警阈值 | 说明 |
|------|----------|------|
| GPU 利用率 | > 95% 持续 5min | 可能需要扩容 |
| GPU 显存使用率 | > 90% | 接近 OOM 风险 |
| API QPS | > 12（单卡 4090） | 超过并发上限 |
| 首 Token 延迟 P95 | > 3s | 性能退化 |
| 检索延迟 P95 | > 500ms | 索引可能需要优化 |
| 错误率 | > 1% | 系统异常 |
| 磁盘使用率 | > 80% | 需要清理或扩容 |
| 内存使用率 | > 85% | 可能需要增加 Swap |

### 12.4 配置告警规则

```yaml
# monitoring/alert_rules.yml
groups:
  - name: echoseve_alerts
    rules:
      - alert: HighGPUUtilization
        expr: echoseve_gpu_utilization > 95
        for: 5m
        annotations:
          summary: "GPU 利用率过高"
          description: "GPU 利用率持续 5 分钟超过 95%"

      - alert: HighMemoryUsage
        expr: echoseve_memory_usage_percent > 85
        for: 3m
        annotations:
          summary: "内存使用率过高"

      - alert: HighErrorRate
        expr: rate(echoseve_requests_total{status="error"}[5m]) > 0.01
        for: 2m
        annotations:
          summary: "错误率超过 1%"

      - alert: DiskSpaceLow
        expr: echoseve_disk_usage_percent > 80
        for: 1m
        annotations:
          summary: "磁盘空间不足"
```

### 12.5 外部监控集成

```bash
# 配置 Prometheus 远程写入（可选，发送到中央监控）
cat >> monitoring/prometheus.yml << 'EOF'
remote_write:
  - url: https://prometheus-central.company.com/api/v1/write
    basic_auth:
      username: echoseve
      password: ${REMOTE_WRITE_PASSWORD}
EOF
```

---

## 13. 备份策略

### 13.1 自动备份（推荐）

```bash
# 添加 crontab（每天凌晨 3 点备份）
echo "0 3 * * * cd /data/echoseve-b/echoseve-b-mvp && bash scripts/backup.sh >> data/logs/backup.log 2>&1" | crontab -

# 包含模型配置（不含权重）
echo "0 4 * * 0 cd /data/echoseve-b/echoseve-b-mvp && INCLUDE_MODELS=true bash scripts/backup.sh >> data/logs/backup.log 2>&1" | crontab -
```

### 13.2 备份内容

| 内容 | 频率 | 保留 | 说明 |
|------|------|------|------|
| 知识库文档 | 每天 | 30 天 | JSONL 格式 |
| Chroma 向量索引 | 每天 | 30 天 | 可重建但耗时 |
| 审计日志 | 每天 | 90 天 | 合规要求 |
| 用户数据 | 每天 | 30 天 | PostgreSQL dump |
| LoRA Adapters | 每次训练后 | 10 个 | 模型进化产物 |
| 训练数据 | 每周 | 12 周 | 用于复现训练 |
| 模型配置 | 每周 | 4 周 | tokenizer + config |

### 13.3 手动备份

```bash
# 全量备份
cd /data/echoseve-b/echoseve-b-mvp
bash scripts/backup.sh

# 包含模型权重（谨慎，文件很大）
INCLUDE_MODELS=true bash scripts/backup.sh

# 查看备份
ls -lh data/backups/
```

### 13.4 恢复

```bash
# 列出可用备份
ls -lh data/backups/*.tar.gz

# 恢复指定备份
bash scripts/restore.sh --file data/backups/echoseve_backup_20260820_030000.tar.gz

# 恢复后验证
curl https://localhost/health
curl https://localhost/api/knowledge/stats
```

### 13.5 异地备份（推荐）

```bash
# 方式一：rsync 到备份服务器
rsync -avz --delete data/backups/ backup-server:/backup/echoseve-b/

# 方式二：上传到对象存储（如 MinIO / S3）
pip install awscli
aws s3 sync data/backups/ s3://company-backup/echoseve-b/ \
    --endpoint-url https://s3.company.com
```

---

## 14. 升级流程

### 14.1 升级前准备

```bash
# 1. 创建升级前备份
bash scripts/backup.sh

# 2. 记录当前版本
curl https://localhost/health | jq '.version'

# 3. 导出当前配置
cp .env .env.backup.$(date +%Y%m%d)
```

### 14.2 在线升级（不停机）

```bash
# 1. 上传新版本
# （通过 scp 将新版本 zip 传到服务器）

# 2. 解压到新目录
cd /data/echoseve-b/
unzip EchoServe_V0.2.0.zip -d echoseve-b-mvp-new

# 3. 迁移配置和数据
cp echoseve-b-mvp/.env echoseve-b-mvp-new/
cp -r echoseve-b-mvp/data/ echoseve-b-mvp-new/
cp -r echoseve-b-mvp/models/ echoseve-b-mvp-new/

# 4. 停止旧版本，启动新版本
cd echoseve-b-mvp
docker compose down

cd ../echoseve-b-mvp-new
docker compose up -d --build

# 5. 验证
curl https://localhost/health | jq
```

### 14.3 回滚

```bash
# 如果升级失败，快速回滚
cd /data/echoseve-b/echoseve-b-mvp-new
docker compose down

cd /data/echoseve-b/echoseve-b-mvp  # 旧版本
docker compose up -d

# 恢复数据
bash scripts/restore.sh --file data/backups/echoseve_backup_<upgrade-timestamp>.tar.gz
```

---

## 15. 故障排查

### 15.1 常见问题速查

| 症状 | 可能原因 | 排查方法 | 解决方案 |
|------|----------|----------|----------|
| 服务无法启动 | 端口被占用 | `ss -tlnp \| grep :8080` | 释放端口或改配置 |
| GPU 不可见 | NVIDIA 驱动未安装 | `nvidia-smi` | 安装/重装驱动 |
| 模型加载失败 | 模型文件缺失/损坏 | `ls -lh models/` | 重新下载模型 |
| 推理超时 | 显存不足 | `nvidia-smi` 查看显存 | 降低并发或上下文长度 |
| 检索结果为空 | Chroma 未初始化 | `docker compose logs chroma` | 重建索引 |
| 企业微信无响应 | Webhook 配置错误 | 检查 `WECHAT_*` 环境变量 | 核对 Corp ID/Secret/Token |
| 登录失败 | JWT_SECRET 变更 | 检查 `.env` 中 JWT_SECRET | 使用正确密钥或重新登录 |
| 审计日志报错 | 权限不足 | `ls -la data/audit/` | 修复目录权限 |
| 备份失败 | 磁盘空间不足 | `df -h` | 清理旧备份或扩容 |

### 15.2 日志查看

```bash
# API 服务日志
docker compose logs -f --tail=100 api

# vLLM 推理日志
docker compose logs -f --tail=50 vllm

# Nginx 访问日志
docker compose exec nginx tail -f /var/log/nginx/access.log

# Nginx 错误日志
docker compose exec nginx tail -f /var/log/nginx/error.log

# 应用运行日志
tail -f data/logs/echoseve.log

# 审计日志
sqlite3 data/audit/audit.db "SELECT * FROM audit_log ORDER BY id DESC LIMIT 20;"
```

### 15.3 健康检查脚本

```bash
#!/bin/bash
# scripts/health_check.sh

echo "=== EchoServe Health Check ==="
echo ""

# 1. Docker 服务状态
echo "--- Docker Services ---"
docker compose ps --format "table {{.Name}}\t{{.State}}\t{{.Health}}"
echo ""

# 2. API 健康检查
echo "--- API Health ---"
curl -s https://localhost/health | jq .
echo ""

# 3. GPU 状态
echo "--- GPU Status ---"
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv
echo ""

# 4. 磁盘空间
echo "--- Disk Usage ---"
df -h /data
echo ""

# 5. 内存使用
echo "--- Memory ---"
free -h
echo ""

# 6. 审计日志完整性
echo "--- Audit Log Integrity ---"
curl -s https://localhost/api/audit/verify \
    -H "Authorization: Bearer ${TOKEN}" | jq .
```

### 15.4 紧急恢复

```bash
# 如果系统完全无法启动
# 1. 停止所有容器
docker compose down -v

# 2. 检查磁盘空间
df -h

# 3. 清理 Docker 资源
docker system prune -a --volumes

# 4. 从备份恢复
bash scripts/restore.sh --file data/backups/<latest-backup>.tar.gz

# 5. 重新启动
docker compose up -d --build
```

---

## 16. 性能调优建议

### 16.1 vLLM 调优

```bash
# 在 docker-compose.yml 中调整 vLLM 参数
environment:
  - GPU_MEMORY_UTILIZATION=0.92    # 提高显存利用率（默认 0.90）
  - MAX_MODEL_LEN=16384            # 如果不需要 32K，降低可提升吞吐
  - TENSOR_PARALLEL_SIZE=1         # 多卡时改为 2
  - ENABLE_PREFIX_CACHING=true     # 保持开启
  - MAX_NUM_SEQS=16                # 最大并发序列数
  - MAX_BATCH_TOKENS=8192          # 批处理 token 数
```

### 16.2 检索调优

```bash
# 在 .env 中调整检索参数
RETRIEVAL_TOP_K=15           # 提高召回率（默认 10）
RRF_K=60                     # RRF 参数，通常 50-60
BM25_WEIGHT=0.35             # BM25 权重（默认 0.4）
VECTOR_WEIGHT=0.65           # 向量权重（默认 0.6）
RERANK_ENABLED=true           # 保持开启
RERANK_TOP_N=20              # 重排序候选数
RERANK_FINAL_K=5             # 最终返回数
```

### 16.3 并发与限流

```bash
# API 并发限制（在 Nginx 中配置）
# nginx/nginx.conf 中添加：
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=chat:10m rate=5r/s;

location /api/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://api_backend;
}

location /api/chat {
    limit_req zone=chat burst=10 nodelay;
    proxy_pass http://api_backend;
}
```

### 16.4 缓存策略

```bash
# Redis 缓存配置
# docker-compose.yml 中 redis 服务添加：
command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru --save 900 1
```

### 16.5 知识库规模建议

| 知识库规模 | 推荐配置 | 注意事项 |
|------------|----------|----------|
| < 10,000 文档 | RTX 4090 + Chroma | 默认配置即可 |
| 10,000-100,000 文档 | RTX 4090 + Chroma + PGVector | 考虑分片 |
| 100,000-1,000,000 文档 | A100 + Chroma + Elasticsearch | BM25 迁移到 ES |
| > 1,000,000 文档 | A100 x2 + Milvus + ES | 需要分布式方案 |

---

## 17. 安全加固清单

### 17.1 系统层

```bash
# SSH 加固
sudo tee /etc/ssh/sshd_config.d/echoseve.conf << 'EOF'
Port 2222                      # 修改默认端口
PermitRootLogin no            # 禁止 root 登录
PasswordAuthentication no     # 仅允许密钥登录
PubkeyAuthentication yes
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
sudo systemctl restart sshd

# 安装 fail2ban
sudo apt install -y fail2ban
sudo tee /etc/fail2ban/jail.d/echoseve.conf << 'EOF'
[sshd]
enabled = true
port = 2222
maxretry = 3
bantime = 3600
EOF
sudo systemctl enable --now fail2ban
```

### 17.2 应用层

| 检查项 | 命令/方法 | 预期结果 |
|--------|-----------|----------|
| HTTPS 强制 | `curl http://localhost` | 301 跳转到 HTTPS |
| JWT 过期 | 等待 8 小时后请求 | 返回 401 |
| 密码复杂度 | 尝试弱密码注册 | 被拒绝 |
| 登录限流 | 连续 5 次错误密码 | 账户锁定 30 分钟 |
| API Key 限流 | 高频请求 | 返回 429 |
| 审计日志完整性 | `curl /api/audit/verify` | `{"valid": true}` |
| 文件上传限制 | 上传 >50MB 文件 | 被拒绝 |
| SQL 注入测试 | 在参数中注入 SQL | 被参数化查询拦截 |

### 17.3 数据层

```bash
# 数据库访问控制
sudo -u postgres psql -c "
REVOKE ALL ON DATABASE echoseve FROM public;
CREATE USER echoseve_app WITH PASSWORD '${DB_PASSWORD}';
GRANT CONNECT ON DATABASE echoseve TO echoseve_app;
GRANT USAGE ON SCHEMA public TO echoseve_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO echoseve_app;
"

# 审计日志只读权限
sudo -u postgres psql -c "
CREATE USER auditor WITH PASSWORD '${AUDITOR_PASSWORD}';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO auditor;
"
```

### 17.4 网络安全

```bash
# 安装和配置 ufw
sudo ufw reset
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 仅开放必要端口
sudo ufw allow 2222/tcp    # SSH
sudo ufw allow 443/tcp     # HTTPS
sudo ufw allow from 10.0.0.0/8 to any port 443  # 仅内网

# 禁用 ping（可选）
sudo ufw deny icmp

sudo ufw enable
```

---

## 18. 等保 2.0 三级合规检查

### 18.1 运行合规检查

```bash
# 方式一：通过 API
curl https://localhost/api/compliance/check \
    -H "Authorization: Bearer ${TOKEN}" | jq

# 方式二：命令行
cd /data/echoseve-b/echoseve-b-mvp
python scripts/compliance_check.py
```

### 18.2 检查项覆盖

| 等保大类 | 检查项数 | 覆盖情况 |
|----------|----------|----------|
| 安全物理环境 | 3 项 | 需客户现场确认 |
| 安全通信网络 | 4 项 | ✅ 全部覆盖（TLS/防火墙/隔离） |
| 安全区域边界 | 5 项 | ✅ 全部覆盖（Nginx/ACL/限流） |
| 安全计算环境 | 8 项 | ✅ 全部覆盖（认证/审计/权限/加密） |
| 安全管理中心 | 4 项 | ✅ 全部覆盖（监控/告警/日志） |
| 安全管理制度 | 3 项 | 需客户配合（制度文档） |
| 安全管理机构 | 2 项 | 需客户配合（组织架构） |
| 安全建设管理 | 2 项 | ✅ 覆盖（变更/备份流程） |
| **合计** | **31 项** | **技术层面 27/31 可自动化检查** |

### 18.3 生成合规报告

```bash
# 报告自动生成到 reports/ 目录
ls reports/compliance_report_*.html
ls reports/compliance_report_*.json

# 查看评分
cat reports/compliance_report_*.json | jq '{overall_score, grade, summary}'
```

---

## 附录 A：快速部署检查清单

```
□ 硬件到位（RTX 4090 48GB / A100）
□ 操作系统安装完成（Ubuntu 22.04 LTS）
□ NVIDIA 驱动安装（≥ 535）
□ CUDA 12.1 安装
□ NVIDIA Container Toolkit 安装
□ Docker 24.0+ 安装
□ Docker Compose v2+ 安装
□ 项目文件上传并解压
□ .env 文件配置完成（JWT_SECRET / DB_PASSWORD / GRAFANA_PASSWORD）
□ 模型文件就位（Qwen3-14B Q4）
□ SSL 证书配置完成
□ Docker Compose 启动成功
□ /health 返回 healthy
□ /ready 返回所有插件 started
□ 默认管理员密码已修改
□ 知识库文档已上传
□ 端到端对话测试通过
□ 监控栈启动并验证
□ 备份 cron 配置完成
□ 等保合规检查通过
□ 防火墙规则配置完成
□ SSH 加固完成
□ fail2ban 安装并运行
```

## 附录 B：技术支持

| 渠道 | 方式 |
|------|------|
| 日志收集 | `docker compose logs > echoseve_logs.txt 2>&1` |
| 系统信息 | `nvidia-smi && df -h && free -h && docker compose ps` |
| 配置导出 | `cat .env \| grep -v PASSWORD \| grep -v SECRET` |
| 版本信息 | `curl https://localhost/health \| jq` |

---

**文档版本**：V1.0
**最后更新**：2026-08-20
**适用版本**：EchoServe V0.1.0 (P2)
**密级**：内部使用
