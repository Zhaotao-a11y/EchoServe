@echo off
REM EchoServe startup script (Windows)
setlocal EnableDelayedExpansion

echo ========================================
echo   EchoServe V{version} Starting...
echo ========================================
echo.

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not running. Please start Docker Desktop first.
    echo Attempting to start Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    timeout /t 30 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to start Docker. Please start it manually.
        pause
        exit /b 1
    )
)

REM Set environment variables
set COMPOSE_PROJECT_NAME=echoseve
set JWT_SECRET=%RANDOM%%RANDOM%%RANDOM%
set DB_PASSWORD=EchoServe_%RANDOM%

REM Start EchoServe
echo [INFO] Starting EchoServe services...
cd /d "%~dp0"
docker compose -f docker\docker-compose.windows.yml up -d

if errorlevel 1 (
    echo [ERROR] Failed to start services.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] EchoServe is starting!
echo.
echo   Admin Panel: https://localhost
echo   Health Check: http://localhost:8080/health
echo.
echo Waiting for services to be ready...
timeout /t 10 /nobreak >nul

REM Open browser
start "" "https://localhost"

echo.
echo Press any key to stop services...
pause >nul

call stop_echoseve.bat
