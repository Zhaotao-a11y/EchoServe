#!/bin/bash
# EchoServe P1 — 恢复脚本
#
# 使用：
#   bash scripts/restore.sh <backup_file.tar.gz>
#   bash scripts/restore.sh echoseve_backup_20260115_143000.tar.gz
#
# 示例：
#   # 列出备份内容
#   tar -tzf /path/to/backup.tar.gz
#
#   # 恢复到指定目录
#   RESTORE_DIR=/tmp/restore_test bash scripts/restore.sh backup.tar.gz

set -e

# ─── 参数检查 ────────────────────────────────
BACKUP_FILE="${1:-}"
if [ -z "${BACKUP_FILE}" ]; then
    echo "❌ 用法: bash scripts/restore.sh <backup_file.tar.gz>"
    echo ""
    echo "可用备份:"
    ls -1t ./data/backups/*.tar.gz 2>/dev/null | head -10 || echo "  (无备份文件)"
    exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
    # 尝试在 backups 目录中查找
    if [ -f "./data/backups/${BACKUP_FILE}" ]; then
        BACKUP_FILE="./data/backups/${BACKUP_FILE}"
    else
        echo "❌ 备份文件不存在: ${BACKUP_FILE}"
        exit 1
    fi
fi

RESTORE_DIR="${RESTORE_DIR:-./data/restore_temp}"
DATA_DIR="${DATA_DIR:-./data}"

echo "=============================================="
echo "  EchoServe Restore"
echo "=============================================="
echo "  备份文件: ${BACKUP_FILE}"
echo "  恢复目录: ${RESTORE_DIR}"
echo "  数据目录: ${DATA_DIR}"
echo "=============================================="

# ─── 解压备份 ────────────────────────────────
echo "[Restore] 解压备份文件..."
mkdir -p "${RESTORE_DIR}"
tar -xzf "${BACKUP_FILE}" -C "${RESTORE_DIR}/"

# 找到解压后的目录
BACKUP_FOLDER=$(ls -1 "${RESTORE_DIR}/" | head -1)
BACKUP_PATH="${RESTORE_DIR}/${BACKUP_FOLDER}"

if [ ! -d "${BACKUP_PATH}" ]; then
    echo "❌ 备份目录结构异常"
    exit 1
fi

echo "  备份内容: ${BACKUP_FOLDER}"
echo ""

# 显示备份信息
if [ -f "${BACKUP_PATH}/backup_info.json" ]; then
    echo "[Restore] 备份信息:"
    cat "${BACKUP_PATH}/backup_info.json" | python3 -m json.tool 2>/dev/null || cat "${BACKUP_PATH}/backup_info.json"
    echo ""
fi

# ─── 确认恢复 ────────────────────────────────
read -p "⚠️  确认恢复到 ${DATA_DIR}？这将覆盖现有数据 [y/N]: " CONFIRM
if [ "${CONFIRM}" != "y" ] && [ "${CONFIRM}" != "Y" ]; then
    echo "已取消"
    rm -rf "${RESTORE_DIR}"
    exit 0
fi

# ─── 1. 恢复知识库文档 ────────────────────────
echo "[Restore] 恢复知识库文档..."
if [ -d "${BACKUP_PATH}/knowledge" ]; then
    mkdir -p "${DATA_DIR}/knowledge"
    cp -r "${BACKUP_PATH}/knowledge/"* "${DATA_DIR}/knowledge/" 2>/dev/null && echo "  ✓ 知识库文档已恢复"
else
    echo "  ⚠ 备份中无知识库数据"
fi

# ─── 2. 恢复 Chroma 索引 ────────────────────────
echo "[Restore] 恢复 Chroma 向量索引..."
if [ -d "${BACKUP_PATH}/chroma" ]; then
    mkdir -p "${DATA_DIR}/chroma"
    cp -r "${BACKUP_PATH}/chroma/"* "${DATA_DIR}/chroma/" 2>/dev/null && echo "  ✓ Chroma 索引已恢复"
    echo "  ⓝ 注意: 恢复后建议重启 Chroma 容器"
else
    echo "  ⚠ 备份中无 Chroma 数据"
fi

# ─── 3. 恢复审计日志 ────────────────────────
echo "[Restore] 恢复审计日志..."
mkdir -p "${DATA_DIR}/logs"
if [ -f "${BACKUP_PATH}/logs/audit.db" ]; then
    cp "${BACKUP_PATH}/logs/audit.db" "${DATA_DIR}/logs/" && echo "  ✓ 审计数据库已恢复"
fi
if [ -f "${BACKUP_PATH}/logs/audit_export.jsonl" ]; then
    cp "${BACKUP_PATH}/logs/audit_export.jsonl" "${DATA_DIR}/logs/" && echo "  ✓ 审计导出文件已恢复"
fi
if [ -f "${BACKUP_PATH}/logs/echoseve.log" ]; then
    cp "${BACKUP_PATH}/logs/echoseve.log" "${DATA_DIR}/logs/echoseve.log.restored" && echo "  ✓ 应用日志已恢复 (→ echoseve.log.restored)"
fi

# ─── 4. 恢复用户数据 ────────────────────────
echo "[Restore] 恢复用户数据..."
if [ -f "${BACKUP_PATH}/users.sql" ]; then
    echo "  📝 检测到 PostgreSQL 导出文件"
    echo "  恢复命令: psql -h localhost -U echoseve -d echoseve -f ${BACKUP_PATH}/users.sql"
    cp "${BACKUP_PATH}/users.sql" "${DATA_DIR}/logs/users_restore.sql"
    echo "  ✓ 恢复脚本已保存至 ${DATA_DIR}/logs/users_restore.sql"
elif [ -f "${BACKUP_PATH}/users.db" ]; then
    cp "${BACKUP_PATH}/users.db" "${DATA_DIR}/users.db" && echo "  ✓ 用户数据库已恢复"
fi

# ─── 5. 恢复 LoRA Adapters ────────────────────────
echo "[Restore] 恢复 LoRA adapters..."
if [ -d "${BACKUP_PATH}/adapters" ]; then
    mkdir -p "${DATA_DIR}/../models/adapters"
    cp -r "${BACKUP_PATH}/adapters/"* "${DATA_DIR}/../models/adapters/" 2>/dev/null && echo "  ✓ LoRA adapters 已恢复"
else
    echo "  ⚠ 备份中无 adapter 数据"
fi

# ─── 6. 恢复训练数据 ────────────────────────
echo "[Restore] 恢复训练数据..."
if [ -d "${BACKUP_PATH}/training" ]; then
    mkdir -p "${DATA_DIR}/training"
    cp -r "${BACKUP_PATH}/training/"* "${DATA_DIR}/training/" 2>/dev/null && echo "  ✓ 训练数据已恢复"
fi

# ─── 7. 恢复模型配置 ────────────────────────
echo "[Restore] 恢复模型配置..."
if [ -d "${BACKUP_PATH}/models" ]; then
    mkdir -p "${DATA_DIR}/../models"
    cp -r "${BACKUP_PATH}/models/"* "${DATA_DIR}/../models/" 2>/dev/null && echo "  ✓ 模型配置已恢复"
fi

# ─── 清理 ────────────────────────────────
rm -rf "${RESTORE_DIR}"

# ─── 完成 ────────────────────────────────
echo ""
echo "=============================================="
echo "  ✅ 恢复完成!"
echo "=============================================="
echo ""
echo "📋 后续步骤:"
echo "  1. 重启服务: docker compose restart"
echo "  2. 验证数据: curl http://localhost:8080/health"
echo "  3. 检查日志: tail -f ${DATA_DIR}/logs/echoseve.log"
echo ""
