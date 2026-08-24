# EchoServe V0.1.0

> **密级**：内部使用  
> **版本**：V1.0  
> **日期**：2026-08-20  
> **适用版本**：EchoServe V0.1.0 (P0+P1+P2 完整版)  
> **状态**：GA（General Availability）

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [硬件与系统要求](#2-硬件与系统要求)
3. [操作系统准备](#3-操作系统准备)
4. [GPU 驱动与 CUDA 安装](#4-gpu-驱动与-cuda-安装)
5. [Docker 环境部署](#5-docker-环境部署)
6. [项目部署（一键启动）](#6-项目部署一键启动)
7. [模型准备](#7-模型准备)
8. [SSL 证书配置](#8-ssl-证书配置)
9. [渠道配置](#9-渠道配置)
10. [企业认证配置（LDAP/OAuth）](#10-企业认证配置ldapoauth)
11. [监控与告警](#11-监控与告警)
12. [备份与恢复策略](#12-备份与恢复策略)
13. [升级与回滚](#13-升级与回滚)
14. [故障排查手册](#14-故障排查手册)
15. [性能调优指南](#15-性能调优指南)
16. [安全加固清单](#16-安全加固清单)
17. [等保 2.0 三级合规](#17-等保-20-三级合规)
18. [快速部署检查清单](#18-快速部署检查清单)

---

## 1. 系统架构总览

### 1.1 架构图

```
                        ┌─────────────────────────────────────────┐
                        │           Nginx 反向代理 (443)           │
                        │   SSL 终止 / 负载均衡 / 静态资源缓存    │
                        └────────────┬────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            ┌──────────────┐  ┌───────────┐  ┌──────────────┐
            │ Web 管理后台  │  │ FastAPI   │  │ WebSocket   │
            │ (React SPA)  │  │ REST API  │  │ /ws/chat    │
            └──────────────┘  └─────┬─────┘  └──────┬───────┘
                                     │                │
                        ┌────────────┼────────────────┤
                        ▼            ▼                ▼
                ┌──────────────────────────────────────────┐
                │         EchoServe 插件化核心运行时       │
                │  ┌────────┐ ┌────────┐ ┌────────┐     │
                │  │ 认证   │ │ 审计   │ │ 知识库 │ ... │
                │  └────────┘ └────────┘ └────────┘     │
                │  ┌────────┐ ┌────────┐ ┌────────┐     │
                │  │ 检索   │ │ 对话   │ │ 模型   │ ... │
                │  └────────┘ └────────┘ └────────┘     │
                └──────┬─────────────┬─────────────┬──────┘
                       │             │             │
            ┌──────────┤    ┌───────┤    ┌───────┤
            ▼          ▼    ▼       ▼    ▼       ▼
     ┌─────────┐ ┌──────────┐ ┌──────┐ ┌──────────┐
     │ vLLM    │ │ Chroma   │ │ PG   │ │ Redis    │
     │ (GPU)   │ │ (向量)   │ │ DB   │ │ (缓存)  │
     └─────────┘ └──────────┘ └──────┘ └──────────┘
```

### 1.2 服务依赖关系

| 服务 | 依赖 | 启动顺序 | 健康检查端点 |
|------|------|----------|--------------|
| postgres | — | 1 | `pg_isready` |
| redis | — | 1 | `redis-cli ping` |
| chroma | — | 2 | `GET /api/v1/heartbeat` |
| vllm | GPU驱动 | 2 | `GET /health` |
| api | 全部上述 | 3 | `GET /health` |
| nginx | api | 4 | `GET /health` |
| prometheus | api | 3 | `GET /-/ready` |
| grafana | prometheus | 4 | `GET /api/health` |

### 1.3 数据流

1. **用户提问** → Nginx → FastAPI → 认证中间件 → ChatPlugin
2. **检索** → RetrieverPlugin（BM25 + Chroma 向量 → RRF 融合 → Cross-Encoder 重排序）
3. **生成** → LLMPlugin（vLLM 本地推理，Prefix Cache 加速）
4. **审计** → AuditPlugin（链式哈希记录，不可篡改）
5. **回复** → WebSocket 流式推送 → 前端打字机效果

---

## 2. 硬件与系统要求

### 2.1 最低配置（RTX 4090 48GB）

| 组件 | 规格 | 说明 |
|------|------|------|
| GPU | NVIDIA RTX 4090 / 48GB GDDR6X | 单卡即可运行 Qwen3-14B Q4 |
| CPU | 8 核以上（推荐 AMD EPYC / Intel Xeon） | 推理主要为 GPU 计算 |
| 内存 | 64GB DDR4 ECC | 模型加载 + 系统 + 缓存 |
| 系统盘 | 200GB NVMe SSD | 系统 + Docker 镜像 + 依赖 |
| 数据盘 | 1TB NVMe SSD | 知识库 + 模型 + 日志 + 备份 |
| 网络 | 千兆以太网 | 内网隔离，仅开放 443/80 |
| 电源 | 850W 80+ Gold 以上 | RTX 4090 满载约 450W |
| 操作系统 | Ubuntu 22.04 LTS | 内核 5.15+ |

### 2.2 推荐配置（A100）

| 组件 | 规格 | 说明 |
|------|------|------|
| GPU | NVIDIA A100 40GB / 80GB（单卡或双卡） | 更高吞吐，支持更大并发 |
| CPU | 16 核以上 | |
| 内存 | 128GB DDR4 ECC | |
| 系统盘 | 500GB NVMe SSD | |
| 数据盘 | 2TB NVMe SSD（RAID 1 可选） | 数据冗余 |
| 网络 | 万兆以太网 | 高并发场景 |

### 2.3 磁盘分区建议

```
/           200GB  SSD  (系统 + Docker 镜像)
/data       1TB+   NVMe (应用数据)
  /data/echoseve-b/       项目根目录
  /data/echoseve-b/models/  模型文件 (8-9GB per model)
  /data/echoseve-b/data/    运行时数据
    /knowledge/              知识库文档
    /chroma/                 向量索引
    /audit/                  审计日志 (append-only)
    /auth/                   用户数据
    /logs/                   运行日志
    /backups/                备份文件
    /training/               训练数据和报告
```

---

## 3. 操作系统准备

### 3.1 系统更新与基础软件

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    curl wget git vim htop iotop \
    build-essential pkg-config \
    ca-certificates gnupg lsb-release \
    software-properties-common \
    unzip zip tar \
    net-tools dnsutils \
    jq tree
```

### 3.2 创建专用用户

```bash
# 创建 echoseve 用户（不允许 SSH 密码登录）
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
net.ipv4.tcp_keepalive_time = 600

# 内存
vm.swappiness = 10
vm.overcommit_memory = 1
vm.max_map_count = 262144

# 文件系统
fs.file-max = 655350
EOF

sudo sysctl -p /etc/sysctl.d/99-echoseve.conf
```

### 3.4 时间同步

```bash
sudo apt install -y chrony
sudo systemctl enable --now chrony
chronyc tracking
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

### 4.2 安装 CUDA Toolkit 12.1

```bash
wget https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_530.30.02_linux.run
sudo sh cuda_12.1.0_530.30.02_linux.run --silent --toolkit

# 配置环境变量（写入 /etc/profile.d/ 全局生效）
sudo tee /etc/profile.d/cuda.sh << 'EOF'
export PATH=/usr/local/cuda-12.1/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda-12.1/lib64:$LD_LIBRARY_PATH
EOF

source /etc/profile.d/cuda.sh
nvcc --version
```

### 4.3 安装 NVIDIA Container Toolkit

```bash
# 添加仓库
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
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

# 安装 Docker（官方脚本）
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
  "restart-policy": "unless-stopped",
  "max-concurrent-downloads": 10,
  "max-concurrent-uploads": 5
}
EOF

sudo systemctl restart docker
```

### 5.3 磁盘空间监控

```bash
# Docker 日志轮转
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

# 定时清理 Docker 资源（每周日 4 点）
echo "0 4 * * 0 docker system prune -af --volumes" | sudo crontab -
```

---

## 6. 项目部署（一键启动）

### 6.1 上传与解压

```bash
# 切换到专用用户
su - echoseve
cd /data/echoseve-b

# 上传并解压（通过 scp / sftp 将 EchoServe_V0.1.0.zip 传到服务器）
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
AUDITOR_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

# 写入配置
sed -i "s|JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" .env
sed -i "s|DB_PASSWORD=.*|DB_PASSWORD=${DB_PASSWORD}|" .env
sed -i "s|GRAFANA_PASSWORD=.*|GRAFANA_PASSWORD=${GRAFANA_PASSWORD}|" .env
sed -i "s|AUDITOR_PASSWORD=.*|AUDITOR_PASSWORD=${AUDITOR_PASSWORD}|" .env

# 设置文件权限（仅所有者可读）
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

# 权限设置
chmod -R 755 data/
chmod -R 700 data/audit/    # 审计日志仅所有者可访问
chmod -R 700 data/auth/     # 认证数据仅所有者可访问
chmod -R 700 data/backups/  # 备份仅所有者可访问
```

### 6.4 一键启动

```bash
# 首次启动（构建镜像 + 拉取依赖）
docker compose up -d --build

# 查看启动进度
docker compose ps

# 跟踪 API 日志
docker compose logs -f api

# 等待所有服务健康（每 5 秒检查一次）
watch -n 5 'docker compose ps'
# 所有服务状态变为 "healthy" 即可
```

### 6.5 验证部署

```bash
# 健康检查
curl https://localhost/health | jq
# 预期：所有插件状态为 "started"

# 就绪检查
curl https://localhost/ready | jq

# API 文档（浏览器打开）
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
USER_ID="<上一步返回的 user_id>"

curl -X PUT https://localhost/api/users/${USER_ID}/password \
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

# 验证模型可加载（启动 vLLM 后检查）
curl http://localhost:8000/v1/models | jq
```

### 7.3 模型热切换

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

# 先停止 nginx（certbot standalone 模式需要 80 端口）
docker compose stop nginx

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
cat your-server.crt intermediate.crt > \
    /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.crt

# 重启 Nginx
docker compose restart nginx
```

### 8.3 自动续期（Let's Encrypt）

```bash
# 添加 crontab（每天凌晨 3 点检查续期）
echo "0 3 * * * /usr/bin/certbot renew --quiet && \
cp /etc/letsencrypt/live/echoseve.yourcompany.com/fullchain.pem \
    /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.crt && \
cp /etc/letsencrypt/live/echoseve.yourcompany.com/privkey.pem \
    /data/echoseve-b/echoseve-b-mvp/nginx/certs/server.key && \
chown echoseve:echoseve /data/echoseve-b/echoseve-b-mvp/nginx/certs/* && \
docker compose -f /data/echoseve-b/echoseve-b-mvp/docker-compose.yml restart nginx" | \
sudo crontab -
```

---

## 9. 渠道配置

### 9.1 企业微信配置

#### 创建企业微信应用

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」→「创建应用」
3. 记录以下信息：
   - **Corp ID**（企业 ID）
   - **Agent ID**（应用 ID）
   - **Secret**（应用密钥）

#### 配置 EchoServe

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

#### 配置接收消息 URL

1. 在企业微信应用设置中，找到「接收消息」→「设置 API 接收」
2. URL 填写：`https://echoseve.yourcompany.com/webhook/wechat`
3. Token 和 AES Key 与 .env 中保持一致
4. 点击「保存」，企业微信会发送验证请求

#### 验证连接

```bash
curl https://localhost/api/channel/status \
    -H "Authorization: Bearer ${TOKEN}" | jq

# 在企业微信中向应用发送消息，查看日志
docker compose logs -f api | grep wechat
```

### 9.2 WhatsApp 配置（可选）

```bash
# 编辑 .env
cat >> .env << 'EOF'
WHATSAPP_ENABLED=true
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_ACCESS_TOKEN=your-permanent-access-token
WHATSAPP_VERIFY_TOKEN=your-custom-verify-token
WHATSAPP_APP_SECRET=your-app-secret
EOF

docker compose restart api
```

**注意事项**：
- WhatsApp Business API 有速率限制（每号码 80 条/分钟）
- 需要公网 HTTPS 地址
- 消息内容需符合 Meta 商业政策

---

## 10. 企业认证配置（LDAP/OAuth）

### 10.1 LDAP 配置（Active Directory）

```bash
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

docker compose restart api

# 手动同步用户
curl -X POST https://localhost/api/auth/ldap/sync \
    -H "Authorization: Bearer ${TOKEN}"
```

### 10.2 OAuth2 配置（以 Azure AD 为例）

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

docker compose restart api
```

### 10.3 认证降级链

EchoServe 支持认证链降级：

```
用户请求 → OAuth2/LDAP（企业认证）
         ↓ 失败
       JWT 本地认证
         ↓ 失败
       API Key 认证
         ↓ 失败
       拒绝访问 (401)
```

---

## 11. 监控与告警

### 11.1 启动监控栈

```bash
# 启动 Prometheus + Grafana
docker compose --profile monitoring up -d

# 验证
curl http://localhost:9090/-/ready    # Prometheus
curl http://localhost:3001/api/health  # Grafana
```

### 11.2 访问 Grafana 仪表盘

1. 浏览器打开 `https://echoseve.yourcompany.com:3001`
2. 登录：admin / `${GRAFANA_PASSWORD}`
3. 自动加载 EchoServe 仪表盘

### 11.3 关键监控指标与告警阈值

| 指标 | 告警阈值 | 说明 | 处置建议 |
|------|----------|------|----------|
| GPU 利用率 | > 95% 持续 5min | 可能需要扩容 | 降低并发或升级硬件 |
| GPU 显存使用率 | > 90% | 接近 OOM 风险 | 降低 max_model_len |
| API QPS | > 12（单卡 4090） | 超过并发上限 | 启用排队或扩容 |
| 首 Token 延迟 P95 | > 3s | 性能退化 | 检查 Prefix Cache 命中率 |
| 检索延迟 P95 | > 500ms | 索引可能需要优化 | 重建索引或增加资源 |
| 错误率 | > 1% | 系统异常 | 查看错误日志 |
| 磁盘使用率 | > 80% | 需要清理或扩容 | 清理旧备份/日志 |
| 内存使用率 | > 85% | 可能需要增加 Swap | 检查内存泄漏 |

### 11.4 告警规则配置

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

      - alert: AuditLogIntegrityBreach
        expr: echoseve_audit_integrity_valid == 0
        for: 0m
        annotations:
          summary: "审计日志完整性被破坏！"
          severity: critical
```

### 11.5 告警通知方式

```yaml
# monitoring/alertmanager.yml
route:
  receiver: 'admin-email'
  routes:
    - match:
        severity: critical
      receiver: 'sms-alert'

receivers:
  - name: 'admin-email'
    email_configs:
      - to: 'admin@yourcompany.com'
        from: 'alert@yourcompany.com'
        smarthost: 'smtp.yourcompany.com:587'
        auth_username: 'alert@yourcompany.com'
        auth_password: '${SMTP_PASSWORD}'

  - name: 'sms-alert'
    webhook_configs:
      - url: 'https://sms-gateway.company.com/send'
```

---

## 12. 备份与恢复策略

### 12.1 备份内容矩阵

| 内容 | 频率 | 保留 | 方法 | 存储位置 |
|------|------|------|------|----------|
| 知识库文档 | 每天 03:00 | 30 天 | JSONL 导出 | 本地 + 异地 |
| Chroma 向量索引 | 每天 03:00 | 30 天 | 目录打包 | 本地 + 异地 |
| 审计日志 | 每天 03:00 | 90 天 | SQLite dump | 本地 + 异地 |
| 用户数据 | 每天 03:00 | 30 天 | PostgreSQL dump | 本地 + 异地 |
| LoRA Adapters | 每次训练后 | 10 个 | 目录复制 | 本地 |
| 训练数据 | 每周日 04:00 | 12 周 | JSONL 复制 | 本地 |
| 模型配置 | 每周日 04:00 | 4 周 | 文件复制 | 本地 |
| 全量备份 | 每周日 02:00 | 8 周 | tar.gz | 本地 + 异地 |

### 12.2 配置自动备份

```bash
# 每日增量备份（凌晨 3 点）
echo "0 3 * * * cd /data/echoseve-b/echoseve-b-mvp && bash scripts/backup.sh >> data/logs/backup.log 2>&1" | crontab -

# 每周全量备份（含模型配置，每周日 2 点）
echo "0 2 * * 0 cd /data/echoseve-b/echoseve-b-mvp && INCLUDE_MODELS=true bash scripts/backup.sh >> data/logs/backup.log 2>&1" | crontab -

# 查看现有 cron 任务
crontab -l
```

### 12.3 手动备份

```bash
cd /data/echoseve-b/echoseve-b-mvp

# 标准备份
bash scripts/backup.sh

# 包含模型权重（文件很大，谨慎使用）
INCLUDE_MODELS=true bash scripts/backup.sh

# 查看备份
ls -lh data/backups/
```

### 12.4 恢复

```bash
# 列出可用备份
ls -lh data/backups/*.tar.gz

# 恢复指定备份
bash scripts/restore.sh --file data/backups/echoseve_backup_20260820_030000.tar.gz

# 恢复后验证
curl https://localhost/health
curl https://localhost/api/knowledge/stats
curl https://localhost/api/audit/verify -H "Authorization: Bearer ${TOKEN}"
```

### 12.5 异地备份

```bash
# 方式一：rsync 到备份服务器
rsync -avz --delete data/backups/ backup-server:/backup/echoseve-b/

# 方式二：上传到对象存储（如 MinIO / S3）
pip install awscli
aws s3 sync data/backups/ s3://company-backup/echoseve-b/ \
    --endpoint-url https://s3.company.com

# 方式三：定期磁带归档（合规要求）
tar czf /mnt/tape/echoseve_$(date +%Y%m%d).tar.gz data/backups/
```

---

## 13. 升级与回滚

### 13.1 升级前准备

```bash
# 1. 创建升级前备份
bash scripts/backup.sh

# 2. 记录当前版本
curl https://localhost/health | jq '.version'

# 3. 导出当前配置
cp .env .env.backup.$(date +%Y%m%d)

# 4. 导出插件状态
curl https://localhost/health | jq '.plugins' > reports/plugins_pre_upgrade.json
```

### 13.2 在线升级（不停机）

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

### 13.3 回滚

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

## 14. 故障排查手册

### 14.1 常见问题速查

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
| 证书过期 | Let's Encrypt 未自动续期 | `openssl x509 -in cert.pem -noout -dates` | 手动续期 |
| WebSocket 断开 | Nginx 超时配置过短 | `docker compose logs nginx` | 增加 proxy_read_timeout |
| Prefix Cache 命中率低 | 请求前缀不一致 | 检查监控指标 | 统一 system prompt |

### 14.2 日志查看

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

# Docker 容器资源使用
docker stats --no-stream
```

### 14.3 健康检查脚本

```bash
#!/bin/bash
# scripts/health_check.sh

echo "=== EchoServe Health Check ==="
echo "Time: $(date)"
echo ""

# 1. Docker 服务状态
echo "--- Docker Services ---"
docker compose ps --format "table {{.Name}}\t{{.State}}\t{{.Health}}"
echo ""

# 2. API 健康检查
echo "--- API Health ---"
curl -ks https://localhost/health | jq .
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
curl -ks https://localhost/api/audit/verify \
    -H "Authorization: Bearer ${TOKEN}" | jq .
echo ""

# 7. 插件状态
echo "--- Plugin Status ---"
curl -ks https://localhost/health | jq '.plugins'
```

### 14.4 紧急恢复

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

# 6. 验证
curl -ks https://localhost/health | jq
```

---

## 15. 性能调优指南

### 15.1 vLLM 调优

```yaml
# docker-compose.yml 中 vllm 服务环境变量
environment:
  # 提高显存利用率（默认 0.90）
  - GPU_MEMORY_UTILIZATION=0.92
  
  # 如果不需要 32K，降低可提升吞吐
  - MAX_MODEL_LEN=16384
  
  # 多卡时改为 2
  - TENSOR_PARALLEL_SIZE=1
  
  # 保持开启（大幅提升重复请求性能）
  - ENABLE_PREFIX_CACHING=true
  
  # 最大并发序列数
  - MAX_NUM_SEQS=16
  
  # 批处理 token 数
  - MAX_BATCH_TOKENS=8192
  
  # KV Cache 精度（fp8 可节省显存）
  - KV_CACHE_DTYPE=fp8
```

### 15.2 检索调优

```bash
# 在 .env 中调整检索参数
RETRIEVAL_TOP_K=15           # 提高召回率（默认 10）
RRF_K=60                     # RRF 参数，通常 50-60
BM25_WEIGHT=0.35             # BM25 权重（默认 0.4）
VECTOR_WEIGHT=0.65           # 向量权重（默认 0.6）
RERANK_ENABLED=true           # 保持开启
RERANK_TOP_N=20              # 重排序候选数
RERANK_FINAL_K=5             # 最终返回数
EMBEDDING_BATCH_SIZE=32      # Embedding 批处理大小
```

### 15.3 并发与限流

```nginx
# nginx/nginx.conf 中添加限流配置
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=chat:10m rate=5r/s;

location /api/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://api_backend;
}

location /api/chat {
    limit_req zone=chat burst=10 nodelay;
    proxy_pass http://api_backend;
    proxy_read_timeout 300s;  # 长连接超时
}

location /ws/chat {
    proxy_pass http://api_backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

### 15.4 缓存策略

```bash
# Redis 缓存配置
# docker-compose.yml 中 redis 服务添加：
command: redis-server \
    --maxmemory 2gb \
    --maxmemory-policy allkeys-lru \
    --save 900 1 \
    --save 300 10 \
    --save 60 10000
```

### 15.5 知识库规模建议

| 知识库规模 | 推荐配置 | 注意事项 |
|------------|----------|----------|
| < 10,000 文档 | RTX 4090 + Chroma | 默认配置即可 |
| 10,000-100,000 文档 | RTX 4090 + Chroma + PGVector | 考虑分片 |
| 100,000-1,000,000 文档 | A100 + Chroma + Elasticsearch | BM25 迁移到 ES |
| > 1,000,000 文档 | A100 x2 + Milvus + ES | 需要分布式方案 |

### 15.6 性能基线测试

```bash
# 使用 ab 进行压力测试
sudo apt install -y apache2-utils

# 测试 API 端点
ab -n 1000 -c 10 -H "Authorization: Bearer ${TOKEN}" \
    https://localhost/api/knowledge/stats

# 测试对话端点（流式）
for i in $(seq 1 50); do
    curl -N -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"query": "测试问题"}' \
        https://localhost/api/chat &
done
wait
```

---

## 16. 安全加固清单

### 16.1 系统层

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
findtime = 600
EOF
sudo systemctl enable --now fail2ban
```

### 16.2 网络层

```bash
# 防火墙规则
sudo ufw reset
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 仅开放必要端口
sudo ufw allow 2222/tcp    # SSH（非标准端口）
sudo ufw allow from 10.0.0.0/8 to any port 443   # 内网 HTTPS
sudo ufw allow from 172.16.0.0/12 to any port 443
sudo ufw allow from 192.168.0.0/16 to any port 443

# 禁用 ping（可选）
sudo ufw deny icmp

sudo ufw enable
```

### 16.3 应用层安全验证

| 检查项 | 命令/方法 | 预期结果 |
|--------|-----------|----------|
| HTTPS 强制 | `curl -I http://localhost` | 301 跳转到 HTTPS |
| JWT 过期 | 等待 8 小时后请求 | 返回 401 |
| 密码复杂度 | 尝试弱密码注册 | 被拒绝 |
| 登录限流 | 连续 5 次错误密码 | 账户锁定 30 分钟 |
| API Key 限流 | 高频请求 | 返回 429 |
| 审计日志完整性 | `curl /api/audit/verify` | `{"valid": true}` |
| 文件上传限制 | 上传 >50MB 文件 | 被拒绝 |
| SQL 注入测试 | 在参数中注入 SQL | 被参数化查询拦截 |
| XSS 防护 | 注入 `<script>` 标签 | 被转义输出 |
| CSRF 防护 | 跨域 POST 请求 | 被 CORS 策略拒绝 |

### 16.4 数据层安全

```bash
# 数据库访问控制
sudo -u postgres psql -c "
REVOKE ALL ON DATABASE echoseve FROM public;
CREATE USER echoseve_app WITH PASSWORD '${DB_PASSWORD}';
GRANT CONNECT ON DATABASE echoseve TO echoseve_app;
GRANT USAGE ON SCHEMA public TO echoseve_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO echoseve_app;
"

# 审计日志只读权限（独立账号）
sudo -u postgres psql -c "
CREATE USER auditor WITH PASSWORD '${AUDITOR_PASSWORD}';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO auditor;
"

# 数据库加密（静态数据加密）
sudo -u postgres psql -c "
ALTER SYSTEM SET ssl=on;
ALTER SYSTEM SET ssl_cert_file='/etc/ssl/certs/ssl-cert-snakeoil.pem';
ALTER SYSTEM SET ssl_key_file='/etc/ssl/private/ssl-cert-snakeoil.key';
"
sudo systemctl restart postgresql
```

### 16.5 定期安全扫描

```bash
# 安装 Lynis（安全审计工具）
sudo apt install -y lynis
sudo lynis audit system

# 依赖漏洞扫描
pip install safety
cd /data/echoseve-b/echoseve-b-mvp
safety check -r requirements.txt

# Docker 镜像漏洞扫描
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    aquasec/trivy image echoseve-api:latest
```

---

## 17. 等保 2.0 三级合规

### 17.1 技术层面检查项（27/31 项可自动化）

| 等保大类 | 检查项数 | 覆盖情况 | 检查方式 |
|----------|----------|----------|----------|
| 安全物理环境 | 3 项 | ⚠️ 需客户现场确认 | 机房门禁/监控/消防 |
| 安全通信网络 | 4 项 | ✅ 全部覆盖 | TLS 1.2+ / 防火墙 / 隔离 |
| 安全区域边界 | 5 项 | ✅ 全部覆盖 | Nginx ACL / 限流 / WAF |
| 安全计算环境 | 8 项 | ✅ 全部覆盖 | 认证/审计/权限/加密 |
| 安全管理中心 | 4 项 | ✅ 全部覆盖 | 监控/告警/日志/审计 |
| 安全管理制度 | 3 项 | ⚠️ 需客户配合 | 制度文档/流程规范 |
| 安全管理机构 | 2 项 | ⚠️ 需客户配合 | 组织架构/人员职责 |
| 安全建设管理 | 2 项 | ✅ 覆盖 | 变更/备份流程 |
| **合计** | **31 项** | **技术 27 项可自动化** | |

### 17.2 运行合规检查

```bash
# 方式一：通过 API
curl https://localhost/api/compliance/check \
    -H "Authorization: Bearer ${TOKEN}" | jq

# 方式二：命令行
cd /data/echoseve-b/echoseve-b-mvp
python scripts/compliance_check.py

# 方式三：Docker 容器执行
docker compose exec api python scripts/compliance_check.py
```

### 17.3 生成合规报告

```bash
# 报告自动生成到 reports/ 目录
ls reports/compliance_report_*.html    # 可视化报告（浏览器打开）
ls reports/compliance_report_*.json    # 机器可读格式

# 查看评分
cat reports/compliance_report_*.json | jq '{overall_score, grade, summary}'

# 查看改进建议
cat reports/compliance_report_*.json | jq '.recommendations[]'
```

### 17.4 合规检查项详情

| 编号 | 检查项 | 检查方法 | 自动/手动 |
|------|--------|----------|-----------|
| C1 | HTTPS 强制跳转 | `curl -I http://localhost` → 301 | 自动 |
| C2 | TLS 版本 ≥ 1.2 | `openssl s_client -connect localhost:443` | 自动 |
| C3 | JWT Token 有效期 ≤ 8h | 检查 .env 中 JWT_EXPIRATION | 自动 |
| C4 | 密码 bcrypt cost ≥ 12 | 检查认证插件配置 | 自动 |
| C5 | 登录失败锁定 | 连续 5 次失败后尝试登录 | 自动 |
| C6 | 审计日志 append-only | 尝试写入审计数据库 | 自动 |
| C7 | 审计日志链式哈希 | `curl /api/audit/verify` | 自动 |
| C8 | 审计日志保留 ≥ 90 天 | 检查备份策略 | 自动 |
| C9 | 文档级 ACL | 用不同角色登录测试 | 自动 |
| C10 | API Key 限流 | 高频请求测试 | 自动 |
| C11 | 文件上传大小限制 | 上传 >50MB 文件 | 自动 |
| C12 | SQL 注入防护 | 注入测试 | 自动 |
| C13 | XSS 防护 | 脚本注入测试 | 自动 |
| C14 | CSRF 防护 | 跨域请求测试 | 自动 |
| C15 | 敏感数据加密存储 | 检查数据库字段 | 自动 |
| C16 | 防火墙规则 | `ufw status` | 自动 |
| C17 | SSH 密钥登录 | 检查 sshd_config | 自动 |
| C18 | fail2ban 运行 | `systemctl status fail2ban` | 自动 |
| C19 | Docker 日志轮转 | 检查 logrotate | 自动 |
| C20 | 备份策略生效 | 检查 cron + 备份文件 | 自动 |
| C21 | 监控告警配置 | 检查 Prometheus rules | 自动 |
| C22 | GPU 资源隔离 | `nvidia-smi` 查看进程 | 自动 |
| C23 | 数据库独立账号 | 检查 PG 角色 | 自动 |
| C24 | 审计日志独立账号 | 检查 PG 角色 | 自动 |
| C25 | .env 文件权限 600 | `ls -la .env` | 自动 |
| C26 | 数据目录权限 700 | `ls -la data/audit/` | 自动 |
| C27 | 证书有效期 ≥ 30 天 | `openssl x509 -dates` | 自动 |
| C28 | 机房物理安全 | 现场查看 | 手动 |
| C29 | 消防系统 | 现场查看 | 手动 |
| C30 | 安全管理制度文档 | 检查文档 | 手动 |
| C31 | 安全管理组织架构 | 检查文件 | 手动 |

---

## 18. 快速部署检查清单

```
□ 硬件到位（RTX 4090 48GB / A100）
□ 操作系统安装完成（Ubuntu 22.04 LTS）
□ 系统更新完成（apt upgrade）
□ NVIDIA 驱动安装（≥ 535）
□ CUDA 12.1 安装
□ NVIDIA Container Toolkit 安装
□ Docker 24.0+ 安装
□ Docker Compose v2+ 安装
□ Docker 配置优化（daemon.json）
□ 系统参数调优（sysctl / limits）
□ 专用用户创建（echoseve）
□ 项目文件上传并解压
□ .env 文件配置完成（随机密钥生成）
□ 模型文件就位（Qwen3-14B Q4）
□ SSL 证书配置完成
□ Docker Compose 启动成功
□ /health 返回 healthy
□ /ready 返回所有插件 started
□ 默认管理员密码已修改
□ 知识库文档已上传
□ 端到端对话测试通过
□ 监控栈启动并验证
□ Grafana 仪表盘可访问
□ 告警规则配置完成
□ 备份 cron 配置完成
□ 备份恢复演练通过
□ 等保合规检查通过（≥ 80 分）
□ 防火墙规则配置完成
□ SSH 加固完成
□ fail2ban 安装并运行
□ 企业微信/WhatsApp 渠道验证通过（如启用）
□ LDAP/OAuth 认证验证通过（如启用）
□ 审计日志完整性验证通过
□ 性能基线测试完成
□ 安全扫描完成（无高危漏洞）
```

---

## 附录 A：技术支持信息

| 渠道 | 方式 |
|------|------|
| 日志收集 | `docker compose logs > echoseve_logs.txt 2>&1` |
| 系统信息 | `nvidia-smi && df -h && free -h && docker compose ps` |
| 配置导出 | `cat .env \| grep -v PASSWORD \| grep -v SECRET` |
| 版本信息 | `curl https://localhost/health \| jq` |
| 健康检查 | `bash scripts/health_check.sh` |
| 合规报告 | `python scripts/compliance_check.py` |

## 附录 B：版本兼容性矩阵

| 组件 | 版本 | 最低要求 | 推荐版本 |
|------|------|----------|----------|
| NVIDIA Driver | 535+ | 470 | 550 |
| CUDA | 12.1 | 11.8 | 12.1 |
| Docker | 24.0+ | 20.10 | 25.0 |
| Docker Compose | v2.20+ | v2.0 | v2.24 |
| PostgreSQL | 15 | 13 | 16 |
| Redis | 7 | 6 | 7.2 |
| Nginx | 1.24+ | 1.20 | 1.26 |
| Python | 3.10+ | 3.8 | 3.12 |
| Node.js | 18+ | 16 | 20 |
| Ubuntu | 22.04 LTS | 20.04 | 22.04 |

---

**文档版本**：V1.0  
**最后更新**：2026-08-20  
**适用版本**：EchoServe V0.1.0 (P0+P1+P2 完整版)  
**密级**：内部使用  
**免责声明**：本文档仅供参考，实际部署请根据客户环境调整。
