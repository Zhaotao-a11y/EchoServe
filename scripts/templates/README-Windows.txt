# EchoServe V{version} Windows Installer Build Guide

## System Requirements

- Windows 10/11 (64-bit)
- Docker Desktop for Windows (WSL2 backend)
- NVIDIA GPU (RTX 4090 24GB+ recommended)
- 16GB+ RAM, 500GB+ SSD

## Build Steps

### Method 1: Inno Setup (Recommended)

1. Download Inno Setup: https://jrsoftware.org/isdl.php
2. Open `EchoServe.iss`
3. Menu -> Build -> Compile
4. Output: `output/EchoServe-{version}-Setup.exe`

### Method 2: NSIS

1. Download NSIS: https://nsis.sourceforge.io/
2. Right-click `EchoServe.nsi` -> Compile NSIS Script
3. Output: `output/EchoServe-{version}-Setup.exe`

## Post-Installation

1. Double-click desktop icon or run `start_echoseve.bat`
2. Wait for Docker to start (first time: 2-5 minutes)
3. Browser auto-opens https://localhost
4. Default admin: admin / Admin@2026!

## Uninstall

- Control Panel -> Uninstall -> EchoServe
- Or run `uninstall.exe`

## File List

| File | Description |
|------|-------------|
| EchoServe.iss | Inno Setup script |
| EchoServe.nsi | NSIS script (alternative) |
| start_echoseve.bat | Startup script |
| stop_echoseve.bat | Stop script |
| install_docker.bat | Docker installer wizard |
| win_service.py | Windows service wrapper |
| docker/docker-compose.windows.yml | Windows Docker orchestration |
| scripts/check_environment.bat | Environment check |
