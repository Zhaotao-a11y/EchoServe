# EchoServe CPU 模式 — 一键部署脚本（Windows）
# 适用：1GB 显存或无独显环境，用于链路验证
# 功能：下载安装 Ollama → 拉取轻量模型 → 启动服务

$ErrorActionPreference = "Stop"

$OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
$OLLAMA_EXE = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$MODEL = "qwen2.5:0.5b"
$PROJECT_DIR = "D:\llm_learn\OmniZee-B\OmniZee"
$ECHOSEVE_PORT = 8080

function Write-Header ($msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Write-Info ($msg) {
    Write-Host "[INFO] $msg" -ForegroundColor Green
}

function Write-Warn ($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-Error ($msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

# ─── 检查 Ollama ──────────────────────────────────
Write-Header "步骤 1/4: 检查 Ollama 安装"

if (Test-Path $OLLAMA_EXE) {
    Write-Info "Ollama 已安装: $OLLAMA_EXE"
    $ollamaVersion = & $OLLAMA_EXE --version 2>$null
    Write-Info "版本: $ollamaVersion"
} else {
    Write-Warn "Ollama 未安装，准备下载..."

    $tempExe = "$env:TEMP\OllamaSetup.exe"

    Write-Info "正在下载 Ollama (约 300MB)..."
    try {
        Invoke-WebRequest -Uri $OLLAMA_URL -OutFile $tempExe -UseBasicParsing
        Write-Info "下载完成"
    } catch {
        Write-Error "下载失败: $_"
        Write-Info "请手动下载: https://ollama.com/download/windows"
        exit 1
    }

    Write-Info "正在安装 Ollama..."
    Start-Process -FilePath $tempExe -Wait
    Remove-Item $tempExe -ErrorAction SilentlyContinue

    # 刷新 PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")

    if (-not (Test-Path $OLLAMA_EXE)) {
        Write-Error "安装后仍未找到 Ollama，请检查安装路径"
        exit 1
    }
    Write-Info "Ollama 安装成功"
}

# ─── 检查模型 ─────────────────────────────────────
Write-Header "步骤 2/4: 检查模型 $MODEL"

$ollamaModels = & $OLLAMA_EXE list 2>$null
if ($ollamaModels -match $MODEL) {
    Write-Info "模型 $MODEL 已存在"
} else {
    Write-Warn "模型未找到，正在拉取 $MODEL (约 300MB)..."
    Write-Warn "这可能需要几分钟，取决于网络速度"
    & $OLLAMA_EXE pull $MODEL
    if ($LASTEXITCODE -ne 0) {
        Write-Error "模型拉取失败"
        exit 1
    }
    Write-Info "模型拉取完成"
}

# ─── 启动 Ollama 服务 ──────────────────────────
Write-Header "步骤 3/4: 启动 Ollama 服务 (CPU 模式)"

$ollamaProcess = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaProcess) {
    Write-Info "启动 Ollama 服务..."
    $env:OLLAMA_NO_GPU = "1"
    $env:CUDA_VISIBLE_DEVICES = ""

    # 后台启动 ollama serve
    $ollamaJob = Start-Job -ScriptBlock {
        param($exe)
        & $exe serve
    } -ArgumentList $OLLAMA_EXE

    Start-Sleep -Seconds 3
    Write-Info "Ollama 服务已启动 (CPU 模式)"
} else {
    Write-Info "Ollama 服务已在运行"
}

# 验证服务
$maxRetry = 10
$retry = 0
$ready = $false
while ($retry -lt $maxRetry -and -not $ready) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -Method GET -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            $ready = $true
        }
    } catch {
        Start-Sleep -Seconds 1
        $retry++
    }
}

if (-not $ready) {
    Write-Error "Ollama 服务未响应，请检查日志"
    exit 1
}
Write-Info "Ollama 服务就绪 (http://localhost:11434)"

# ─── 启动 EchoServe ─────────────────────────────
Write-Header "步骤 4/4: 启动 EchoServe API (端口 $ECHOSEVE_PORT)"

Set-Location $PROJECT_DIR

# 设置环境变量
$env:MODEL_NAME = $MODEL
$env:MODEL_PATH = "ollama://$MODEL"
$env:MODEL_MAX_CTX = "2048"
$env:VLLM_HOST = "http://localhost:11434"
$env:JWT_SECRET = "dev-local-secret-do-not-use-in-prod"
$env:BCRYPT_COST = "10"
$env:LOG_LEVEL = "INFO"
$env:CUDA_VISIBLE_DEVICES = ""

Write-Warn "CPU 模式推理速度较慢 (1-3 tokens/s)，仅用于链路验证"
Write-Info "模型: $MODEL"
Write-Info "后台地址: http://localhost:$ECHOSEVE_PORT"
Write-Info "API 文档: http://localhost:$ECHOSEVE_PORT/docs"
Write-Info ""
Write-Info "登录账号: admin / EchoServe#Admin2026"
Write-Info ""
Write-Info "按 Ctrl+C 停止服务"
Write-Host "============================================================" -ForegroundColor Cyan

# 启动 EchoServe
try {
    python -m uvicorn api.main:app --host 0.0.0.0 --port $ECHOSEVE_PORT --no-access-log
} catch {
    Write-Error "EchoServe 启动失败: $_"
    Write-Info "请检查: 1) Python 环境 2) 依赖安装 (pip install -r requirements.txt)"
    exit 1
}
