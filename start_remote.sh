#!/bin/bash
# EchoServe 远程服务器一键启动脚本
# Usage: ./start_remote.sh

set -e

echo "=== EchoServe Remote Startup ==="

# 1. 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# 2. 安装依赖（如果未安装）
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

# 3. 清理缓存
echo "Clearing cache..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true

# 4. 设置环境变量
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

# 5. 检查 .env
if [ ! -f ".env" ]; then
    echo "WARNING: .env not found, copying from template..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
    fi
fi

# 6. 检查端口
PORT=8080
if lsof -i :$PORT &> /dev/null || netstat -tuln | grep -q ":$PORT "; then
    echo "WARNING: Port $PORT is already in use"
    read -p "Kill existing process? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        fuser -k $PORT/tcp 2>/dev/null || true
    fi
fi

# 7. 启动服务
echo "Starting EchoServe on port $PORT..."
echo "API: http://0.0.0.0:$PORT"
echo "Docs: http://0.0.0.0:$PORT/docs"
echo ""

python3 -m uvicorn api.main:app --host 0.0.0.0 --port $PORT --workers 1
