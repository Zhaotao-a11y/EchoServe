@echo off
chcp 65001 >nul
cd /d "D:\llm_learn\OmniZee-B\OmniZee"
set JWT_SECRET=dev-local-secret-do-not-use-in-prod
set BCRYPT_COST=12
python -m uvicorn api.main:app --host 0.0.0.0 --port 8080 --no-access-log > service.log 2>&1
echo %ERRORLEVEL% > exitcode.txt