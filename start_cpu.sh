#!/usr/bin/env bash
# EchoServe CPU Mode Launcher - ASCII only to avoid encoding issues
# For 1GB VRAM or no GPU environments

set -e

echo "========================================"
echo "EchoServe CPU Mode Setup"
echo "========================================"

# Paths
PROJECT_DIR="/d/llm_learn/OmniZee-B/OmniZee"
MODEL="qwen2.5:0.5b"
PORT=8080

# Check Ollama
echo "[Step 1/4] Checking Ollama..."

OLLAMA_EXE=""
if command -v ollama &>/dev/null; then
    OLLAMA_EXE="ollama"
    echo "[OK] Ollama found in PATH"
elif [ -f "$HOME/AppData/Local/Programs/Ollama/ollama.exe" ]; then
    OLLAMA_EXE="$HOME/AppData/Local/Programs/Ollama/ollama.exe"
    echo "[OK] Ollama found at: $OLLAMA_EXE"
elif [ -f "/c/Users/$USERNAME/AppData/Local/Programs/Ollama/ollama.exe" ]; then
    OLLAMA_EXE="/c/Users/$USERNAME/AppData/Local/Programs/Ollama/ollama.exe"
    echo "[OK] Ollama found at: $OLLAMA_EXE"
else
    echo "[WARN] Ollama not installed"
    echo "[INFO] Please download from: https://ollama.com/download/windows"
    echo "[INFO] After installation, re-run this script"
    exit 1
fi

# Check model
echo ""
echo "[Step 2/4] Checking model: $MODEL"

MODEL_LIST=$($OLLAMA_EXE list 2>/dev/null || true)
if echo "$MODEL_LIST" | grep -q "$MODEL"; then
    echo "[OK] Model $MODEL already exists"
else
    echo "[INFO] Pulling model $MODEL (approx 300MB)..."
    echo "[INFO] This may take a few minutes depending on network speed"
    $OLLAMA_EXE pull "$MODEL"
    echo "[OK] Model pulled successfully"
fi

# Start Ollama service
echo ""
echo "[Step 3/4] Starting Ollama service (CPU mode)..."

export OLLAMA_NO_GPU=1
export CUDA_VISIBLE_DEVICES=""

# Check if ollama serve is already running
OLLAMA_PID=$(ps -W | grep -i "ollama serve" | grep -v grep | awk '{print $1}' | head -1)
if [ -n "$OLLAMA_PID" ]; then
    echo "[OK] Ollama service already running (PID: $OLLAMA_PID)"
else
    echo "[INFO] Starting Ollama serve in background..."
    $OLLAMA_EXE serve > ollama_cpu.log 2>&1 &
    sleep 5
fi

# Verify Ollama is responsive
echo "[INFO] Waiting for Ollama to be ready..."
RETRY=0
MAX_RETRY=30
READY=false

while [ $RETRY -lt $MAX_RETRY ] && [ "$READY" = "false" ]; do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        READY=true
        echo "[OK] Ollama is ready (http://localhost:11434)"
    else
        RETRY=$((RETRY + 1))
        echo "[INFO] Waiting... ($RETRY/$MAX_RETRY)"
        sleep 2
    fi
done

if [ "$READY" = "false" ]; then
    echo "[ERROR] Ollama failed to start. Check ollama_cpu.log"
    exit 1
fi

# Start EchoServe
echo ""
echo "[Step 4/4] Starting EchoServe API (port $PORT)..."
echo "[INFO] Backend: Ollama CPU ($MODEL)"
echo "[WARN] CPU inference is slow (1-3 tokens/s). For dev testing only."
echo ""
echo "Login: admin / [Set via ECHOSEVE_ADMIN_PASSWORD env var]"
echo "API Docs: http://localhost:$PORT/docs"
echo ""
echo "Press Ctrl+C to stop"
echo "========================================"

cd "$PROJECT_DIR"

export MODEL_NAME="$MODEL"
export MODEL_PATH="ollama://$MODEL"
export MODEL_MAX_CTX="2048"
export VLLM_HOST="http://localhost:11434"
export JWT_SECRET="dev-local-secret-do-not-use-in-prod"
export BCRYPT_COST="10"
export LOG_LEVEL="INFO"
export CUDA_VISIBLE_DEVICES=""

python -m uvicorn api.main:app --host 0.0.0.0 --port "$PORT" --no-access-log
