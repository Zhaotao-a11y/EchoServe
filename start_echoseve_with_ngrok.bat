@echo off
chcp 65001 >nul
REM EchoServe + ngrok 一键启动模板
REM 使用说明：
REM 1. 修改下方 NGROK_URL 为你的固定域名
REM 2. 确保 ngrok authtoken 已配置
REM 3. 确保 .env 中已配置企业微信相关参数

echo ================================================
echo EchoServe + ngrok 一键启动
echo ================================================
echo.

REM 配置区（使用前必须修改）
set "NGROK_URL=https://<你的-ngrok-域名>.ngrok-free.dev"
set "NGROK_EXE=D:\llm_learn\ngrok\ngrok.exe"

REM 进入项目目录
cd /d "%~dp0"

REM 设置 Python 环境
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1
set PYTHONPATH=%CD%

REM 启动 EchoServe
echo [1/3] 启动 EchoServe...
echo   访问地址: http://localhost:8080
echo   请确保 .env 文件已正确配置
echo.
start /B "EchoServe" cmd /c "set PYTHONDONTWRITEBYTECODE=1 && set PYTHONUNBUFFERED=1 && set PYTHONPATH=%CD% && python -m uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload"

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
echo [2/3] 启动 ngrok 内网穿透...
echo   域名: %NGROK_URL%
echo   本地目标: http://localhost:8080
echo.
start /B "ngrok" cmd /c "\"%NGROK_EXE%\" http http://localhost:8080 --url %NGROK_URL%"

echo [INFO] 等待 ngrok 启动...
timeout /t 5 /nobreak >nul
echo [OK] ngrok 已启动
echo.

REM 验证连通性
echo [3/3] 验证公网连通性...
curl -s %NGROK_URL%/health >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARN] 公网验证未通过，等待几秒再试...
    timeout /t 5 /nobreak >nul
    curl -s %NGROK_URL%/health >nul 2>&1
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
echo.
echo [ngrok 公网]
echo   %NGROK_URL%
echo.
echo [微信客服回调 URL]
echo   %NGROK_URL%/webhook/wechat_kf
echo.
echo [重要]
echo   1. 首次使用需修改本脚本中的 NGROK_URL
echo   2. 去企业微信后台配置回调 URL
echo   3. 两个窗口必须保持开启（EchoServe + ngrok）
echo.
echo [故障排查]
echo   ngrok 管理面板: http://localhost:4040
echo   EchoServe 日志: 查看 EchoServe 窗口
echo.

pause
    exit /b 1
)
echo [OK] Ollama 运行中
echo.

REM 启动 EchoServe
echo [3/4] 启动 EchoServe...
echo   访问地址: http://localhost:8080
echo   登录: admin / [请在首次登录后修改密码]
echo   注意: 首次启动前请设置 ECHOSEVE_ADMIN_PASSWORD 环境变量
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
