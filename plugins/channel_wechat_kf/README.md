# 微信客服渠道配置指南 (WeChat Customer Service)

## 适用场景

此插件对接**微信客服**（企业微信客服模块，不是公众号客服）：
- 微信聊天窗口底部「客服」入口
- 小程序「联系客服」按钮
- 视频号/搜一搜等场景的客户咨询

## 与现有渠道的区别

| 渠道 | 微信客服 (WeChat KF) | 企业微信 (WeCom) | 公众号客服 |
|------|---------------------|------------------|-----------|
| 平台 | 微信客服（企业微信） | 企业微信 APP | 公众号/小程序 |
| API | `qyapi.weixin.qq.com` | `qyapi.weixin.qq.com` | `api.weixin.qq.com` |
| 认证 | CorpID + Secret | CorpID + Secret | AppID + AppSecret |
| 用户ID | openid | userid | openid |
| 限制 | 48小时会话窗口 | 无限制 | 48小时 |
| 消息加密 | AES + Token | AES + Token | AES + Token |
| AES 解密 | 支持 | 支持 | 可选 |
| 接入场景 | 视频号/搜一搜/网页客服 | 企业内部通讯 | 公众号菜单 |

---

## 配置步骤

### Step 1: 企业微信后台配置

**企业微信管理后台**: https://work.weixin.qq.com/wework_admin

1. **开通微信客服功能**
   - 应用管理 → 微信客服 → 开通
   - 或访问 https://work.weixin.qq.com/kf

2. **获取 CorpID 和 Secret**
   - CorpID: 我的企业 → 企业ID
   - Secret: 应用管理 → 微信客服 → 查看 Secret

3. **配置回调地址**
   - 客户联系 → 微信客服 → API → 接收消息
   - URL: `https://your-domain.com/webhook/wechat_kf`
   - Token: 自定义字符串（如 `EchoServe2026`）
   - EncodingAESKey: 随机生成（43位）
   - 消息加解密方式: 安全模式（推荐）

4. **获取客服账号 ID (open_kfid)**
   - 客户联系 → 微信客服 → 客服账号
   - 每个客服账号有一个 open_kfid，用于发送消息

### Step 2: 系统环境变量配置

在 `.env` 文件中添加以下配置：

```env
# ========== 微信客服 (WeChat KF) 配置 ==========
# 企业微信 CorpID
WECHAT_KF_CORP_ID=wwxxxxxxxxxxxxxxxx

# 微信客服应用 Secret
WECHAT_KF_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 回调 Token（用于消息签名验证）
WECHAT_KF_TOKEN=EchoServe2026

# 回调 EncodingAESKey（43位，用于 AES 消息解密）
WECHAT_KF_AES_KEY=abcdefghijklmnopqrstuvwxyz123456789ABCDEFG

# Webhook 路径（可选，默认 /webhook/wechat_kf）
WECHAT_KF_WEBHOOK_PATH=/webhook/wechat_kf
```

### Step 3: 域名与公网访问

微信客服要求服务器必须有**公网可访问的域名**，且支持 HTTPS。

**本地开发测试方案**：

#### 方案 A: 内网穿透（推荐开发测试）

使用 ngrok 或类似工具：

```bash
# 安装 ngrok
# https://ngrok.com/download

# 注册并获取 authtoken
ngrok config add-authtoken YOUR_AUTHTOKEN

# 暴露本地服务（EchoServe 默认端口 8080）
ngrok http 8080

# 输出示例：
# Forwarding  https://a1b2c3d4.ngrok.io -> http://localhost:8080
```

然后在企业微信后台配置：
- URL: `https://a1b2c3d4.ngrok.io/webhook/wechat_kf`

注意：ngrok 域名每次重启会变，仅用于开发测试。生产环境需要固定域名。

#### 方案 B: 云服务器（生产环境）

1. 购买云服务器（阿里云/腾讯云等）
2. 安装 Nginx + SSL 证书
3. 反向代理到 EchoServe:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /webhook/wechat_kf {
        proxy_pass http://localhost:8080/webhook/wechat_kf;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

4. 配置域名解析指向服务器 IP
5. 在企业微信后台填写 `https://your-domain.com/webhook/wechat_kf`

---

## 消息流程

```
用户发送消息
    │
    ▼
微信客服服务器
    │
    ▼ (POST XML，可能 AES 加密)
EchoServe /webhook/wechat_kf
    │
    ├── 验证签名（msg_signature）
    ├── AES 解密（如启用加密模式）
    ├── 解析 XML
    ├── 提取 openid + content + open_kfid
    │
    ▼
ChatPlugin.chat(use_rag=False)  ← LoRA 模型直接回复，零延迟
    │
    ▼
组装客服消息 API 请求
    │
    ▼ (POST JSON)
微信客服消息 API (qyapi.weixin.qq.com/cgi-bin/kf/send_msg)
    │
    ▼
用户收到回复
```

---

## 关键 API 说明

### 获取 access_token

```
GET https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=CORP_ID&corpsecret=SECRET
```

响应：
```json
{
  "errcode": 0,
  "errmsg": "ok",
  "access_token": "accesstoken000001",
  "expires_in": 7200
}
```

### 发送客服消息

```
POST https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token=ACCESS_TOKEN
```

请求体：
```json
{
  "touser": "openid123456",
  "open_kfid": "kfid123",
  "msgtype": "text",
  "text": { "content": "回复内容" }
}
```

---

## 错误码说明

| 错误码 | 含义 | 处理建议 |
|--------|------|----------|
| 0 | 成功 | — |
| 40001 | access_token 过期/无效 | 自动刷新重试 |
| 40003 | 无效的 openid | 检查用户是否已关注 |
| 95011 | 用户超过 48 小时未互动 | 引导用户先发送消息 |
| -1 | 系统繁忙 | 稍后重试 |

---

## 常见问题

### Q: 配置回调时提示"请求 URL 超时"？

A: 确保：
1. EchoServe 已启动且端口正确
2. ngrok/服务器已运行且公网可访问
3. 防火墙未拦截端口
4. 尝试先用浏览器访问 `https://your-url/webhook/wechat_kf` 确认能通

### Q: 收到消息但无法回复？

A: 检查：
1. `WECHAT_KF_CORP_ID` 和 `WECHAT_KF_SECRET` 是否正确
2. 查看日志中的 access_token 获取是否成功
3. open_kfid 是否正确配置（可在消息 XML 中提取）
4. 用户是否在企业微信客服的活跃会话中

### Q: 消息签名验证失败？

A:
1. 确认 `WECHAT_KF_TOKEN` 与企业微信后台配置一致
2. 确认 URL 路径一致（含末尾斜杠）
3. 检查是否有 CDN/代理修改了请求参数
4. 如果使用安全模式，确认 `WECHAT_KF_AES_KEY` 正确

### Q: AES 解密失败？

A:
1. 确认 `WECHAT_KF_AES_KEY` 是 43 位字符
2. 确认 `WECHAT_KF_CORP_ID` 正确（解密需要 CorpID）
3. 确认 wechatpy 已安装：`pip install wechatpy`
4. 检查企业微信后台的消息加密方式是否为"安全模式"

### Q: 用户 48 小时后无法收到回复？

A: 微信客服对消息发送有时间限制。解决方案：
1. 引导用户先发送任意消息触发互动窗口
2. 使用客服菜单引导用户保持活跃

---

## 测试流程

### 1. 本地启动 EchoServe

```bash
# 设置环境变量（临时）
set WECHAT_KF_CORP_ID=wwxxxxxxxxxxxxxxxx
set WECHAT_KF_SECRET=your_secret
set WECHAT_KF_TOKEN=EchoServe2026
set WECHAT_KF_AES_KEY=your_43_char_key

# 启动服务
python api/main.py
```

### 2. 启动内网穿透

```bash
ngrok http 8080
# 复制 https URL
```

### 3. 配置企业微信

- URL: `https://xxx.ngrok.io/webhook/wechat_kf`
- Token: `EchoServe2026`
- EncodingAESKey: 随机生成（与 .env 中一致）

### 4. 发送测试消息

- 在视频号/网页/搜一搜客服入口发消息
- 查看 EchoServe 日志输出
- 检查是否能收到自动回复

---

## 生产环境检查清单

- [ ] 使用 HTTPS + 有效 SSL 证书
- [ ] 固定域名（非 ngrok 临时域名）
- [ ] 配置了正确的 CorpID 和 Secret
- [ ] Token 与企业微信后台一致
- [ ] EncodingAESKey 配置正确且与后台一致
- [ ] 防火墙允许 443 端口
- [ ] 日志持久化（便于排查）
- [ ] 配置了错误告警
- [ ] 测试过多轮对话上下文保持
- [ ] 测试过转人工客服流程

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `plugins/channel_wechat_kf/plugin.py` | 微信客服渠道插件（本文件） |
| `plugins/channel_wechat/plugin.py` | 企业微信渠道插件（已有） |
| `api/main.py` | 主入口，注册插件 |
| `.env` | 配置文件 |

---

## 下一步：LoRA 训练后替换模型

当 LoRA 训练完成并导出为 Ollama 格式后：

1. 在 `.env` 中更新模型名称：
```env
MODEL_NAME=qwen2.5-0.5b-cs  # 微调后的模型
```

2. 重启 EchoServe：
```bash
taskkill /F /IM python.exe
python api/main.py
```

3. 微信客服消息将直接由微调后的模型回复，无需 RAG 检索，零延迟。
