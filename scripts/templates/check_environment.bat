@echo off
REM EchoServe environment check script (Windows)
echo ========================================
echo   EchoServe Environment Check
echo ========================================
echo.

set ERROR_COUNT=0

echo [1/5] Checking OS version...
ver | findstr /i "10\." >nul
if errorlevel 1 (
    echo   [X] Not Windows 10/11
    set /a ERROR_COUNT+=1
) else (
    echo   [OK] Windows 10/11 detected
)

echo.
echo [2/5] Checking Docker...
docker --version >nul 2>&1
if errorlevel 1 (
    echo   [X] Docker not installed
    set /a ERROR_COUNT+=1
) else (
    docker --version
    echo   [OK] Docker installed
)

echo.
echo [3/5] Checking NVIDIA GPU...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo   [!] NVIDIA GPU driver not detected (optional, CPU mode is slower)
) else (
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    echo   [OK] NVIDIA GPU detected
)

echo.
echo [4/5] Checking memory...
powershell -Command "$mem = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB; Write-Host ('  ' + [math]::Round($mem,1) + ' GB')"
echo   [OK] Memory check complete

echo.
echo [5/5] Checking disk space...
powershell -Command "$disk = Get-PSDrive C; Write-Host ('  C drive free: ' + [math]::Round($disk.Free,1) + ' GB')"
echo   [OK] Disk check complete

echo.
echo ========================================
if %ERROR_COUNT%==0 (
    echo   [OK] All checks passed! Ready to install EchoServe
) else (
    echo   [X] %ERROR_COUNT% issue(s) found. Please fix and retry.
)
echo ========================================
echo.
pause
