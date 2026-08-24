@echo off
chcp 65001 >nul
echo ================================================
echo EchoServe + ngrok 一键启动 (微信客服测试)
echo ================================================
echo.

REM 设置 Python 环境
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1
set PYTHONPATH=D:\llm_learn\OmniZee-B\OmniZee

REM 清理 Python 缓存
echo [1/4] 清理 Python 缓存...
for /f "delims=" %%d in ('dir /s /b __pycache__ 2^>nul') do @rd /s /q "%%d" 2>nul
for /f "delims=" %%f in ('dir /s /b *.pyc 2^>nul') do @del /f "%%f" 2>nul
echo [OK] 缓存已清理
echo.

REM 检查 Ollama
echo [2/4] 检查 Ollama 服务...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Ollama 未运行，请先启动 Ollama：
    echo   ollama serve
    pause
    exit /b 1
)
echo [OK] Ollama 运行中
echo.

REM 启动 EchoServe
echo [3/4] 启动 EchoServe...
echo   访问地址: http://localhost:8080
echo   登录: admin / EchoServe#Admin2026
echo.
start /B "EchoServe" cmd /c "cd /d D:\llm_learn\OmniZee-B\OmniZee && set PYTHONDONTWRITEBYTECODE=1 && set PYTHONUNBUFFERED=1 && set PYTHONPATH=D:\llm_learn\OmniZee-B\OmniZee && python -m uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload"

REM 等待 EchoServe 启动
echo [INFO] 等待 EchoServe 启动 (5秒)...
timeout /t 5 /nobreak >nul

REM 验证 EchoServe
curl -s http://localhost:8080/health >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] EchoServe 启动失败，请检查日志
    pause
    exit /b 1
)
echo [OK] EchoServe 已就绪
echo.

REM 启动 ngrok
echo [4/4] 启动 ngrok 内网穿透...
echo   等待 ngrok 生成 HTTPS 公网地址...
echo.
start /B "ngrok" cmd /c "D:\llm_learn\ngrok\ngrok.exe http http://localhost:8080"

REM 等待 ngrok 启动
timeout /t 5 /nobreak >nul

REM 获取 ngrok 公网地址
echo [INFO] 正在获取 ngrok 公网地址...
setlocal enabledelayedexpansion
set "NGROK_URL="

for /f "tokens=*" %%a in ('curl -s http://localhost:4040/api/tunnels ^| findstr /C:"public_url" ^| findstr /C:"https://"') do (
    for /f "tokens=2 delims=:" %%b in ("%%a") do (
        set "raw=%%b"
        set "raw=!raw:"=!"
        set "raw=!raw: =!"
        set "NGROK_URL=https:!raw!"
    )
)

echo.
echo ================================================
echo [SUCCESS] 所有服务已启动！
echo ================================================
echo.
echo [EchoServe]
echo   本地地址: http://localhost:8080
echo   健康检查: http://localhost:8080/health
echo.
if defined NGROK_URL (
    echo [ngrok 公网地址]
    echo   HTTPS: %NGROK_URL%
    echo.
    echo [微信客服回调 URL 配置]
    echo   URL: %NGROK_URL%/webhook/wechat_kf
    echo.
    echo   --- 复制这行到企业微信后台 ---
    echo   %NGROK_URL%/webhook/wechat_kf
    echo   -----------------------------
    echo.
    echo   Token: EchoServe2026
    echo   EncodingAESKey: 在企业微信后台随机生成即可
    echo.
) else (
    echo [ngrok] 地址获取中...
    echo   请稍等几秒后访问 http://localhost:4040 查看公网地址
    echo.
    echo [微信客服回调 URL 格式]
    echo   https://xxxx.ngrok-free.app/webhook/wechat_kf
    echo.
)

echo [注意事项]
echo   1. ngrok 免费版地址每次重启会变，需要重新配置微信后台
echo   2. 微信要求 HTTPS，ngrok 已自动提供 SSL 证书
echo   3. 按 Ctrl+C 两次分别关闭 ngrok 和 EchoServe
echo   4. ngrok 管理面板: http://localhost:4040
echo.

pause
