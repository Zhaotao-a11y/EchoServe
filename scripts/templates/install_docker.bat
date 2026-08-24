@echo off
REM Docker Desktop installation wizard (Windows)
echo ========================================
echo    Docker Desktop Installer
echo ========================================
echo.
echo EchoServe requires Docker Desktop to run.
echo.
echo Please choose installation method:
echo   1. Auto download and install (recommended)
echo   2. Open browser for manual download
echo   3. Cancel
echo.
set /p choice="Enter option (1/2/3): "

if "%choice%"=="1" goto auto_install
if "%choice%"=="2" goto manual_install
goto end

:auto_install
echo.
echo [INFO] Downloading Docker Desktop installer...
echo Note: This may take several minutes.
echo.
powershell -Command "& {Invoke-WebRequest -Uri 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe' -OutFile '%TEMP%\DockerDesktopInstaller.exe'}"
if errorlevel 1 (
    echo [ERROR] Download failed. Please try manual installation.
    goto manual_install
)
echo.
echo [INFO] Installing Docker Desktop (silent mode)...
start /wait "" "%TEMP%\DockerDesktopInstaller.exe" install --quiet
echo.
echo [SUCCESS] Docker Desktop installed!
echo Please reboot your computer, then run start_echoseve.bat
goto end

:manual_install
echo.
echo Please open the following URL in your browser:
echo.
echo   https://www.docker.com/products/docker-desktop/
echo.
start "" "https://www.docker.com/products/docker-desktop/"
goto end

:end
pause
