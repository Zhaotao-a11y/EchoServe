#!/bin/bash
# EchoServe P1 — 备份脚本
#
# 功能：
#   - 备份知识库文档（JSONL）
#   - 备份 Chroma 向量索引
#   - 备份审计日志
#   - 备份用户数据（PostgreSQL）
#   - 备份模型 adapters
#   - 可选：备份完整模型文件
#   - 自动清理旧备份（保留最近 N 个）
#
# 使用：
#   bash scripts/backup.sh                    # 使用默认配置
#   BACKUP_DIR=/custom/path bash scripts/backup.sh
#   INCLUDE_MODELS=true bash scripts/backup.sh
#
# 定时任务（crontab）：
#   0 3 * * * cd /app && bash scripts/backup.sh >> /app/data/logs/backup.log 2>&1

set -e

# ─── 配置 ────────────────────────────────────────
BACKUP_ROOT="${BACKUP_DIR:-./data/backups}"
RETENTION="${BACKUP_RETENTION:-30}"
INCLUDE_MODELS="${INCLUDE_MODELS:-false}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="echoseve_backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_ROOT}/${BACKUP_NAME}"

# 源目录
DATA_DIR="${DATA_DIR:-./data}"
MODELS_DIR="${MODELS_DIR:-./models}"

echo "=============================================="
echo "  EchoServe Backup — ${TIMESTAMP}"
echo "=============================================="

# ─── 创建备份目录 ────────────────────────────────
mkdir -p "${BACKUP_PATH}"
echo "[Backup] 创建备份目录: ${BACKUP_PATH}"

# ─── 1. 知识库文档 ──────────────────────────────
echo "[Backup] 备份知识库文档..."
if [ -d "${DATA_DIR}/knowledge" ]; then
    mkdir -p "${BACKUP_PATH}/knowledge"
    cp -r "${DATA_DIR}/knowledge/"* "${BACKUP_PATH}/knowledge/" 2>/dev/null || true
    echo "  ✓ 知识库文档已备份"
else
    echo "  ⚠ 知识库目录不存在，跳过"
fi

# ─── 2. Chroma 向量索引 ────────────────────────
echo "[Backup] 备份 Chroma 向量索引..."
if [ -d "${DATA_DIR}/chroma" ]; then
    mkdir -p "${BACKUP_PATH}/chroma"
    cp -r "${DATA_DIR}/chroma/"* "${BACKUP_PATH}/chroma/" 2>/dev/null || true
    echo "  ✓ Chroma 索引已备份"
else
    echo "  ⚠ Chroma 目录不存在，跳过"
fi

# ─── 3. 审计日志 ────────────────────────────────
echo "[Backup] 备份审计日志..."
mkdir -p "${BACKUP_PATH}/logs"
if [ -f "${DATA_DIR}/logs/audit.db" ]; then
    cp "${DATA_DIR}/logs/audit.db" "${BACKUP_PATH}/logs/" 2>/dev/null || true
fi
if [ -f "${DATA_DIR}/logs/echoseve.log" ]; then
    cp "${DATA_DIR}/logs/echoseve.log" "${BACKUP_PATH}/logs/" 2>/dev/null || true
fi
# 导出审计日志为 JSONL
if command -v python3 &> /dev/null; then
    python3 -c "
import sqlite3, json, os
src = '${DATA_DIR}/logs/audit.db'
dst = '${BACKUP_PATH}/logs/audit_export.jsonl'
if os.path.exists(src):
    conn = sqlite3.connect(src)
    rows = conn.execute('SELECT * FROM audit_log ORDER BY id').fetchall()
    cols = [d[0] for d in conn.execute('SELECT * FROM audit_log').description]
    with open(dst, 'w') as f:
        for row in rows:
            f.write(json.dumps(dict(zip(cols, row)), ensure_ascii=False) + '\n')
    print(f'  导出 {len(rows)} 条审计记录')
    conn.close()
" 2>/dev/null || true
fi
echo "  ✓ 审计日志已备份"

# ─── 4. 用户数据（PostgreSQL）─────────────────
echo "[Backup] 备份用户数据..."
if command -v pg_dump &> /dev/null; then
    PGPASSWORD="${DB_PASSWORD:-echoseve_secure_2026}" pg_dump \
        -h localhost -U echoseve -d echoseve \
        -f "${BACKUP_PATH}/users.sql" 2>/dev/null \
        && echo "  ✓ 用户数据已导出 (users.sql)" \
        || echo "  ⚠ pg_dump 失败（数据库可能未运行）"
else
    echo "  ⚠ pg_dump 不可用，尝试 SQLite 备份..."
    if [ -f "${DATA_DIR}/users.db" ]; then
        cp "${DATA_DIR}/users.db" "${BACKUP_PATH}/users.db"
        echo "  ✓ 用户数据库已备份"
    fi
fi

# ─── 5. LoRA Adapters ──────────────────────────
echo "[Backup] 备份 LoRA adapters..."
if [ -d "${MODELS_DIR}/adapters" ]; then
    mkdir -p "${BACKUP_PATH}/adapters"
    cp -r "${MODELS_DIR}/adapters/"* "${BACKUP_PATH}/adapters/" 2>/dev/null || true
    echo "  ✓ LoRA adapters 已备份"
else
    echo "  ⚠ Adapters 目录不存在，跳过"
fi

# ─── 6. 训练数据 ────────────────────────────────
echo "[Backup] 备份训练数据..."
if [ -d "${DATA_DIR}/training" ]; then
    mkdir -p "${BACKUP_PATH}/training"
    cp -r "${DATA_DIR}/training/"* "${BACKUP_PATH}/training/" 2>/dev/null || true
    echo "  ✓ 训练数据已备份"
fi

# ─── 7. 完整模型（可选）────────────────────────
if [ "${INCLUDE_MODELS}" = "true" ]; then
    echo "[Backup] 备份完整模型文件..."
    if [ -d "${MODELS_DIR}" ]; then
        mkdir -p "${BACKUP_PATH}/models"
        # 只备份配置文件和 tokenizer（不备份大模型权重以节省空间）
        find "${MODELS_DIR}" -maxdepth 3 -name "config.json" -o \
                                         -name "tokenizer*" -o \
                                         -name "special_tokens*" -o \
                                         -name "adapter_config*" -o \
                                         -name "adapter_model*" \
                                         -exec cp --parents {} "${BACKUP_PATH}/models/" \;
        echo "  ✓ 模型配置已备份（不含权重文件）"
    fi
else
    echo "[Backup] 跳过模型文件（设置 INCLUDE_MODELS=true 启用）"
fi

# ─── 8. 写入备份元信息 ────────────────────────
cat > "${BACKUP_PATH}/backup_info.json" << EOF
{
  "timestamp": "${TIMESTAMP}",
  "version": "0.1.1",
  "components": {
    "knowledge": $([ -d "${BACKUP_PATH}/knowledge" ] && echo true || echo false),
    "chroma": $([ -d "${BACKUP_PATH}/chroma" ] && echo true || echo false),
    "audit": $([ -f "${BACKUP_PATH}/logs/audit.db" ] && echo true || echo false),
    "users": $([ -f "${BACKUP_PATH}/users.sql" ] && echo true || echo false),
    "adapters": $([ -d "${BACKUP_PATH}/adapters" ] && echo true || echo false),
    "training": $([ -d "${BACKUP_PATH}/training" ] && echo true || echo false)
  },
  "backup_size_mb": "$(du -sm "${BACKUP_PATH}" | cut -f1)"
}
EOF

# ─── 9. 打包压缩 ────────────────────────────────
echo "[Backup] 压缩备份..."
cd "${BACKUP_ROOT}"
tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}/"
rm -rf "${BACKUP_PATH}"
BACKUP_SIZE=$(du -sh "${BACKUP_NAME}.tar.gz" | cut -f1)
echo "  ✓ 备份文件: ${BACKUP_ROOT}/${BACKUP_NAME}.tar.gz (${BACKUP_SIZE})"

# ─── 10. 清理旧备份 ────────────────────────────
echo "[Backup] 清理旧备份（保留最近 ${RETENTION} 个）..."
cd "${BACKUP_ROOT}"
BACKUP_COUNT=$(ls -1 *.tar.gz 2>/dev/null | wc -l)
if [ "${BACKUP_COUNT}" -gt "${RETENTION}" ]; then
    EXCESS=$((BACKUP_COUNT - RETENTION))
    ls -1t *.tar.gz | tail -${EXCESS} | xargs -r rm -f
    echo "  已删除 ${EXCESS} 个旧备份"
fi

# ─── 完成 ────────────────────────────────────────
echo "=============================================="
echo "  ✅ 备份完成!"
echo "  文件: ${BACKUP_ROOT}/${BACKUP_NAME}.tar.gz"
echo "  大小: ${BACKUP_SIZE}"
echo "  保留: 最近 ${RETENTION} 个备份"
echo "=============================================="
