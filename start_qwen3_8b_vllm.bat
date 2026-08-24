@echo off
chcp 65001 >nul
REM EchoServe V0.1.0 — Qwen3-8B-Instruct vLLM 生产部署脚本
REM 适用：单卡 RTX 3090/4090 24GB，客服场景高并发

cd /d "D:\llm_learn\OmniZee-B\OmniZee"

REM ─── 加载客服专属配置 ────────────────────────
set MODEL_PATH=./models/qwen3-8b-instruct-q4
set MODEL_NAME=qwen3-8b-instruct-q4
set MODEL_MAX_CTX=8192
set MODEL_GPU_MEM_UTIL=0.85

REM vLLM 推理服务配置
set VLLM_HOST=http://localhost:8000
set VLLM_MAX_MODEL_LEN=8192
set VLLM_TENSOR_PARALLEL_SIZE=1
set VLLM_PREFIX_CACHE=true

REM 安全配置
set JWT_SECRET=dev-local-secret-do-not-use-in-prod
set BCRYPT_COST=12

REM 日志
set LOG_LEVEL=INFO

echo [Qwen3-8B] 启动 vLLM 推理服务...
echo [Qwen3-8B] 模型路径: %MODEL_PATH%
echo [Qwen3-8B] 最大上下文: %MODEL_MAX_CTX%
echo [Qwen3-8B] GPU 内存利用率: %MODEL_GPU_MEM_UTIL%

REM 检查 vLLM 是否安装
python -c "import vllm; print(f'vLLM version: {vllm.__version__}')" 2>nul
if errorlevel 1 (
    echo [ERROR] vLLM 未安装，请先执行: pip install vllm
    exit /b 1
)

REM 检查模型文件是否存在
if not exist "%MODEL_PATH%\config.json" (
    echo [WARN] 模型文件未找到: %MODEL_PATH%
    echo [WARN] 请先下载模型: huggingface-cli download Qwen/Qwen3-8B-Instruct-GPTQ-Int4 --local-dir %MODEL_PATH%
    echo [WARN] 或使用 Ollama: ollama pull qwen3:8b
    exit /b 1
)

REM 启动 vLLM 服务（后台）
echo [Qwen3-8B] 正在启动 vLLM 服务 (端口 8000)...
start /B python -m vllm.entrypoints.openai.api_server ^
    --model %MODEL_PATH% ^
    --quantization gptq ^
    --dtype auto ^
    --max-model-len %MODEL_MAX_CTX% ^
    --tensor-parallel-size %VLLM_TENSOR_PARALLEL_SIZE% ^
    --gpu-memory-utilization %MODEL_GPU_MEM_UTIL% ^
    --enable-prefix-caching ^
    --port 8000 ^
    > vllm_service.log 2>&1

REM 等待 vLLM 就绪（轮询 /v1/models）
echo [Qwen3-8B] 等待 vLLM 服务就绪...
set /a retry=0
:wait_loop
timeout /t 2 /nobreak >nul
python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/models')" 2>nul
if errorlevel 1 (
    set /a retry+=1
    if %retry% GTR 30 (
        echo [ERROR] vLLM 启动超时（60秒），请检查日志: vllm_service.log
        exit /b 1
    )
    echo [Qwen3-8B] 等待中... (%retry%/30)
    goto wait_loop
)

echo [Qwen3-8B] vLLM 服务已就绪！

REM ─── 启动 EchoServe API ──────────────────────
echo [EchoServe] 启动 API 服务 (端口 8080)...
python -m uvicorn api.main:app ^
    --host 0.0.0.0 ^
    --port 8080 ^
    --no-access-log ^
    > echoseve_service.log 2>&1

echo [EchoServe] 服务已关闭
