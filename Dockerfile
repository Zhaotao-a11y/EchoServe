# EchoServe V0.1.0 — API 服务 Dockerfile
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建必要目录
RUN mkdir -p data/logs data/chroma data/knowledge

# 创建非 root 用户并设置目录权限
RUN groupadd -r echoseve && \
    useradd -r -g echoseve -d /app -s /sbin/nologin echoseve && \
    chown -R echoseve:echoseve /app

USER echoseve

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# 启动命令
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
