@echo off
chcp 65001 >nul
echo ================================================
echo EchoServe + 微信客服 ngrok 一键启动
echo ================================================
echo.

REM 进入项目目录
cd /d D:\llm_learn\OmniZee-B\OmniZee

REM 设置 Python 环境
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1
set PYTHONPATH=D:\llm_learn\OmniZee-B\OmniZee

REM Step 1: 清理 Python 缓存
echo [1/6] 清理 Python 缓存...
for /f "delims=" %%d in ('dir /s /b __pycache__ 2^>nul') do @rd /s /q "%%d" 2>nul
for /f "delims=" %%f in ('dir /s /b *.pyc 2^>nul') do @del /f "%%f" 2>nul
echo [OK] 缓存已清理
echo.

REM Step 2: 检查 Ollama
echo [2/6] 检查 Ollama 服务...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Ollama 未运行，正在启动...
    start /B "" "ollama" serve > nul 2>&1
    timeout /t 5 /nobreak >nul
) else (
    echo [OK] Ollama 运行中
)
echo.

REM Step 3: 检查 ngrok
echo [3/6] 检查 ngrok...
if not exist "D:\llm_learn\ngrok\ngrok.exe" (
    echo [ERROR] ngrok 未找到！请确认 D:\llm_learn\ngrok\ngrok.exe 存在
    pause
    exit /b 1
)
echo [OK] ngrok 已就绪
echo.

REM Step 4: 启动 EchoServe
echo [4/6] 启动 EchoServe...
echo   本地地址: http://localhost:8080
echo   登录: admin / EchoServe#Admin2026
echo.
start /B "EchoServe" cmd /c "cd /d D:\llm_learn\OmniZee-B\OmniZee && set PYTHONDONTWRITEBYTECODE=1 && set PYTHONUNBUFFERED=1 && set PYTHONPATH=D:\llm_learn\OmniZee-B\OmniZee && python -m uvicorn api.main:app --host 0.0.0.0 --port 8080"

echo [INFO] 等待 EchoServe 启动...
timeout /t 8 /nobreak >nul

REM 验证 EchoServe
curl -s http://localhost:8080/health >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARN] EchoServe 可能还在启动中，继续等待...
    timeout /t 5 /nobreak >nul
) else (
    echo [OK] EchoServe 已就绪
)
echo.

REM Step 5: 启动 ngrok
echo [5/6] 启动 ngrok 内网穿透...
echo   域名: https://eaten-earthling-garlic.ngrok-free.dev
echo   本地目标: http://localhost:8080
echo.
start /B "ngrok" cmd /c "cd /d D:\llm_learn\ngrok && ngrok.exe http http://localhost:8080 --url https://eaten-earthling-garlic.ngrok-free.dev"

echo [INFO] 等待 ngrok 启动...
timeout /t 5 /nobreak >nul
echo [OK] ngrok 已启动
echo.

REM Step 6: 验证连通性
echo [6/6] 验证公网连通性...
curl -s https://eaten-earthling-garlic.ngrok-free.dev/health >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARN] 公网验证未通过，等待几秒再试...
    timeout /t 5 /nobreak >nul
    curl -s https://eaten-earthling-garlic.ngrok-free.dev/health >nul 2>&1
)

echo.
echo ================================================
if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] 全部就绪！
) else (
    echo [WARN] 服务已启动，公网验证待确认
)
echo ================================================
echo.
echo [EchoServe]
echo   本地: http://localhost:8080
echo   健康: http://localhost:8080/health
echo   管理: http://localhost:8080/admin
echo   登录: admin / EchoServe#Admin2026
echo.
echo [ngrok 公网]
echo   https://eaten-earthling-garlic.ngrok-free.dev
echo.
echo [微信客服回调 URL]
echo   https://eaten-earthling-garlic.ngrok-free.dev/webhook/wechat_kf
echo.
echo [Token] Tgl6P
echo [AESKey] 3fv1Xk86RveoVFwe92mFIwRoMle54bYDXFYl6ObvNQe
echo.
echo [重要]
echo   1. 去企业微信后台点击 [完成] 按钮
echo   2. 如果提示 ^'openapi回调地址请求不通过^'，稍等几秒再点
echo   3. 两个窗口必须保持开启（EchoServe + ngrok）
   4. 关闭任一个窗口 = 服务停止
echo.
echo [故障排查]
echo   ngrok 管理面板: http://localhost:4040
echo   EchoServe 日志: 查看 EchoServe 窗口
echo.

pause
