@echo off
chcp 65001 >nul
echo ========================================
echo EchoServe Clean Start (After Reboot)
echo ========================================
echo.

REM 设置环境变量禁止 Python 字节码缓存
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1

REM 加载本地环境变量
if exist ".env" (
    for /f "usebackq tokens=* delims=" %%a in ("D:\llm_learn\OmniZee-B\OmniZee\.env") do (
        echo %%a | findstr "^#" >nul || set "%%a"
    )
)

REM 进入项目目录
cd /d "D:\llm_learn\OmniZee-B\OmniZee"

REM 清理 Python 缓存（关键！）
echo [Step 1/5] Cleaning Python cache...
for /f "delims=" %%d in ('dir /s /b __pycache__ 2^>nul') do @rd /s /q "%%d" 2>nul
for /f "delims=" %%f in ('dir /s /b *.pyc 2^>nul') do @del /f "%%f" 2>nul
echo [OK] Cache cleaned

REM 检查 vLLM
echo.
echo [Step 2/5] Checking vLLM...
curl -s http://localhost:8000/v1/models >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] vLLM already running at :8000
    goto :start_echoseve
)

REM vLLM 未运行，尝试启动
echo [INFO] Starting vLLM with qwen2.5:0.5b...
start /B "" cmd /c "python -m vllm.entrypoints.openai.api_server --model qwen2.5:0.5b --port 8000 --dtype float16 --max-model-len 4096 --gpu-memory-utilization 0.5 > vllm.log 2>&1"
echo [INFO] Waiting for vLLM to start...
timeout /t 10 /nobreak >nul

:wait_vllm
curl -s http://localhost:8000/v1/models >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Waiting for vLLM...
    timeout /t 3 /nobreak >nul
    goto wait_vllm
)
echo [OK] vLLM ready

:start_echoseve
echo.
echo [Step 3/5] Starting EchoServe...
echo Login: admin / EchoServe#Admin2026
echo API: http://localhost:8080
echo.

python -m uvicorn api.main:app --host 0.0.0.0 --port 8080

pause
    exit /b 1
)
echo [OK] Ollama found

REM 检查模型
echo.
echo [Step 3/4] Checking model...
%OLLAMA_EXE% list | findstr "qwen2.5:0.5b" >nul
if %ERRORLEVEL% neq 0 (
    echo [INFO] Pulling model qwen2.5:0.5b...
    %OLLAMA_EXE% pull qwen2.5:0.5b
)
echo [OK] Model ready

REM 启动 Ollama（CPU 模式）
echo.
echo [Step 4/4] Starting services...
set OLLAMA_NO_GPU=1
set CUDA_VISIBLE_DEVICES=

REM 检查 Ollama 是否已在运行
tasklist | findstr "ollama.exe" >nul
if %ERRORLEVEL% neq 0 (
    start /B "" %OLLAMA_EXE% serve > ollama_cpu.log 2>&1
    echo [INFO] Ollama starting...
    timeout /t 5 /nobreak >nul
) else (
    echo [OK] Ollama already running
)

REM 等待 Ollama 就绪
echo [INFO] Waiting for Ollama...
:wait_ollama
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% neq 0 (
    timeout /t 2 /nobreak >nul
    goto wait_ollama
)
echo [OK] Ollama ready

REM 启动 EchoServe
echo.
echo [INFO] Starting EchoServe...
echo Login: admin / EchoServe#Admin2026
echo API: http://localhost:8080
echo.

python -m uvicorn api.main:app --host 0.0.0.0 --port 8080

pause
