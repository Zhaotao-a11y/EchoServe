@echo off
chcp 65001 >nul
REM EchoServe V0.1.0 — Qwen3-8B-Instruct Ollama 快速部署脚本
REM 适用：快速验证、开发测试、低并发场景
REM 要求：Ollama 已安装 (https://ollama.com/download/windows)

cd /d "D:\llm_learn\OmniZee-B\OmniZee"

REM ─── 加载客服专属配置 ────────────────────────
set MODEL_NAME=qwen3:8b
set OLLAMA_HOST=http://localhost:11434
set ECHOSEVE_PORT=8080

REM 安全配置
set JWT_SECRET=dev-local-secret-do-not-use-in-prod
set BCRYPT_COST=12

REM 日志
set LOG_LEVEL=INFO

echo [Qwen3-8B] Ollama 快速部署模式
echo [Qwen3-8B] 模型: %MODEL_NAME%
echo [Qwen3-8B] Ollama API: %OLLAMA_HOST%

REM 检查 Ollama 是否安装
where ollama >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Ollama 未安装或未加入 PATH
    echo [ERROR] 请访问 https://ollama.com/download/windows 下载安装
    exit /b 1
)

REM 检查模型是否已下载
ollama list | findstr /i "qwen3:8b" >nul
if errorlevel 1 (
    echo [Qwen3-8B] 模型未下载，正在拉取 qwen3:8b...
    ollama pull qwen3:8b
    if errorlevel 1 (
        echo [ERROR] 模型拉取失败，请检查网络连接
        exit /b 1
    )
) else (
    echo [Qwen3-8B] 模型已存在
)

REM 启动 Ollama 服务（如果未运行）
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | findstr ollama >nul
if errorlevel 1 (
    echo [Qwen3-8B] 启动 Ollama 服务...
    start /B ollama serve > ollama_service.log 2>&1
    timeout /t 3 /nobreak >nul
)

REM 验证 Ollama 服务
python -c "import urllib.request; urllib.request.urlopen('%OLLAMA_HOST%/api/tags')" 2>nul
if errorlevel 1 (
    echo [ERROR] Ollama 服务未响应，请手动启动: ollama serve
    exit /b 1
)

echo [Qwen3-8B] Ollama 服务已就绪！

REM ─── 启动 EchoServe API ──────────────────────
echo [EchoServe] 启动 API 服务 (端口 %ECHOSEVE_PORT%)...
echo [EchoServe] LLM 后端: Ollama (%OLLAMA_HOST%)

REM 创建 Ollama 专用配置覆盖
set MODEL_PATH=ollama://qwen3:8b
set MODEL_NAME=ollama-qwen3-8b
set VLLM_HOST=%OLLAMA_HOST%

python -m uvicorn api.main:app ^
    --host 0.0.0.0 ^
    --port %ECHOSEVE_PORT% ^
    --no-access-log ^
    > echoseve_ollama.log 2>&1

echo [EchoServe] 服务已关闭
