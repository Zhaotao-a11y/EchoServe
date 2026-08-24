@echo off
REM EchoServe stop script (Windows)
echo [INFO] Stopping EchoServe services...
cd /d "%~dp0"
docker compose -f docker\docker-compose.windows.yml down
echo [SUCCESS] EchoServe stopped.
