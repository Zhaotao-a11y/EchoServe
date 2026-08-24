@echo off
chcp 65001 >nul
REM EchoServe V0.1.0 — CPU 模式启动脚本（1GB 显存或无显卡环境）
REM 使用超轻量模型验证端到端链路，生成速度较慢（1-3 tokens/s）
REM 适用：本地开发验证、无 GPU 环境、CI/CD 测试

cd /d "D:\llm_learn\OmniZee-B\OmniZee"

REM ─── CPU 模式配置 ─────────────────────────────
set OLLAMA_HOST=http://localhost:11434
set OLLAMA_MODEL=qwen2.5:0.5b
REM 备选（质量稍好但速度更慢）：qwen2.5:1.8b / phi3:mini / gemma2:2b

REM EchoServe 配置
set ECHOSEVE_PORT=8080
set JWT_SECRET=dev-local-secret-do-not-use-in-prod
set BCRYPT_COST=10
set LOG_LEVEL=INFO

REM 禁用 GPU，强制 CPU 推理（Ollama 环境变量）
set OLLAMA_NO_GPU=1
set CUDA_VISIBLE_DEVICES=""

echo ============================================================
echo  EchoServe CPU 模式 — 轻量模型验证部署
echo ============================================================
echo [WARN] 当前为 CPU 推理模式，生成速度约 1-3 tokens/s
echo [WARN] 仅用于链路验证，不建议生产使用
echo [INFO] 模型: %OLLAMA_MODEL%
echo [INFO] 后端: Ollama (CPU)
echo ============================================================

REM 检查 Ollama
where ollama >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Ollama 未安装，请先访问 https://ollama.com/download/windows 下载
    exit /b 1
)

REM 启动 Ollama 服务（如果未运行，强制 CPU 模式）
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | findstr ollama >nul
if errorlevel 1 (
    echo [CPU] 启动 Ollama 服务（CPU 模式）...
    start /B ollama serve > ollama_cpu.log 2>&1
    timeout /t 3 /nobreak >nul
)

REM 检查模型
echo [CPU] 检查模型 %OLLAMA_MODEL% ...
ollama list | findstr /i "%OLLAMA_MODEL%" >nul
if errorlevel 1 (
    echo [CPU] 模型未找到，正在拉取 %OLLAMA_MODEL% ...
    echo [CPU] 首次下载约 300MB-1GB，请耐心等待...
    ollama pull %OLLAMA_MODEL%
    if errorlevel 1 (
        echo [ERROR] 模型拉取失败，请检查网络连接
        exit /b 1
    )
)

echo [CPU] 模型就绪: %OLLAMA_MODEL%

REM ─── 启动 EchoServe ────────────────────────────
echo [EchoServe] 启动 API 服务（端口 %ECHOSEVE_PORT%）...
echo [EchoServe] LLM 后端: Ollama CPU (%OLLAMA_MODEL%)

set MODEL_NAME=%OLLAMA_MODEL%
set MODEL_PATH=ollama://%OLLAMA_MODEL%
set MODEL_MAX_CTX=2048
set VLLM_HOST=%OLLAMA_HOST%

python -m uvicorn api.main:app ^
    --host 0.0.0.0 ^
    --port %ECHOSEVE_PORT% ^
    --no-access-log ^
    > echoseve_cpu.log 2>&1

echo [EchoServe] 服务已关闭
