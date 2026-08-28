# EchoServe 远程 GPU 服务器部署指南

## 服务器要求

- Ubuntu 20.04+ / CentOS 8+
- Python 3.10+
- NVIDIA GPU (建议 RTX 3090/4090 / A100)
- 至少 16GB 显存（推荐 24GB+）
- 公网可访问地址（或 ngrok）
- HTTPS（微信回调强制要求）

## 部署步骤

### 1. 上传项目

```bash
# 在本地打包
cd /path/to/EchoServe
tar czvf ../EchoServe-deploy.tar.gz .

# 上传到远程服务器（替换为你的服务器地址）
scp ../EchoServe-deploy.tar.gz root@your-server-ip:/root/
```

### 2. 解压并进入目录

```bash
cd /root
tar xzvf EchoServe-deploy.tar.gz
cd EchoServe
```

### 3. 运行一键启动脚本

```bash
chmod +x start_remote.sh
./start_remote.sh
```

脚本会自动：
- 创建 Python 虚拟环境
- 安装依赖
- 清理缓存
- 启动服务

### 4. 配置微信回调

编辑 `.env` 文件，填入你的企业微信信息：

```env
WECHAT_KF_CORP_ID=你的企业微信CorpID
WECHAT_KF_SECRET=你的企业微信Secret（可选，用于主动发送消息）
WECHAT_KF_TOKEN=你的回调Token
WECHAT_KF_AES_KEY=你的EncodingAESKey
```

### 5. 公网访问方案

#### 方案 A：服务器有公网 IP + 域名（推荐生产）

1. 配置 Nginx 反向代理 + SSL 证书
2. 域名解析到服务器 IP
3. 微信后台填写：`https://your-domain.com/webhook/wechat_kf`

#### 方案 B：服务器无公网 IP（国内 GPU 平台如 AutoDL）

使用 ngrok：

```bash
# 安装 ngrok
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

# 配置 authtoken
ngrok config add-authtoken 你的authtoken

# 启动隧道（假设 EchoServe 运行在 8080）
ngrok http 8080 --url=你的固定域名
```

微信后台填写 ngrok 提供的 HTTPS 地址。

#### 方案 C：Cloudflare Tunnel（免费替代）

```bash
# 安装 cloudflared
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# 登录并创建隧道
cloudflared tunnel login
cloudflared tunnel create echoseve
cloudflared tunnel route dns echoseve your-domain.com
cloudflared tunnel run echoseve
```

## GPU 模型配置

远程服务器通常性能更强，可以加载更大的模型。

编辑 `.env`：

```env
# 模型配置（GPU 模式，使用更大的模型）
MODEL_NAME=qwen2.5:3b
OLLAMA_MODEL=qwen2.5:3b
MODEL_MAX_CTX=8192
MODEL_GPU_MEM_UTIL=0.90

# 如果使用 vLLM（性能更好，但需要 safetensors 格式）
# VLLM_HOST=http://localhost:8000
```

### 安装 Ollama（远程服务器）

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen2.5:3b
```

### 安装 vLLM（可选，性能更优）

```bash
pip install vllm

# 启动 vLLM 服务（需要 safetensors 格式模型）
python -m vllm.entrypoints.openai.api_server \
    --model /path/to/your/model \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.90
```

## 微信客服测试清单

1. [ ] 服务启动成功（`curl http://localhost:8080/health` 返回 healthy）
2. [ ] ngrok/域名 HTTPS 可访问
3. [ ] 微信后台填写回调 URL：`https://your-domain/webhook/wechat_kf`
4. [ ] 填写 Token、EncodingAESKey、CorpID
5. [ ] 点击「保存」完成验证
6. [ ] 发送测试消息，查看日志确认消息到达
7. [ ] 确认 AI 回复通过微信客服接口返回

## 常见问题

### Q: 微信回调显示「请求 URL 超时」
A: 检查防火墙和端口映射。确保微信服务器能访问到你的服务。

### Q: 微信回调显示「签名验证失败」
A: 
1. 确认 `.env` 中的 Token 和微信后台一致
2. 确认 CorpID 正确
3. 确认 AES Key 43位，不含 `=`
4. 查看日志中的具体错误信息

### Q: Ollama 模型加载慢
A: 首次拉取模型需要下载，后续会从缓存加载。使用 `ollama pull` 预下载。

### Q: GPU 显存不足
A: 使用更小参数的模型（0.5b/1.8b），或开启 4-bit 量化。

## 监控与日志

```bash
# 查看实时日志
tail -f /tmp/echoserve.log

# 查看服务状态
curl http://localhost:8080/health

# 查看 Prometheus 指标
curl http://localhost:8080/metrics
```
