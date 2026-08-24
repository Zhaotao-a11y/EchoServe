#!/bin/bash
# EchoServe V0.1.0
# 清理缓存 → 运行全部测试 → 打包 zip

set -e

PROJECT_DIR="/data/workspace/echoseve-b-mvp"
DIST_DIR="/data/workspace/dist"
VERSION="V0.1.0"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ZIP_NAME="EchoServe_${VERSION}_FINAL_${TIMESTAMP}.zip"

echo "=========================================="
echo "  EchoServe ${VERSION} 最终交付物打包"
echo "=========================================="
echo ""

# Step 1: 清理
echo "[1/4] 清理缓存和临时文件..."
cd "${PROJECT_DIR}"
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
rm -rf data/backups/* data/logs/* data/audit/* data/auth/* 2>/dev/null || true
rm -f reports/*.json reports/*.html 2>/dev/null || true
echo "  ✅ 清理完成"
echo ""

# Step 2: 运行测试
echo "[2/4] 运行全部测试套件..."
PASS_COUNT=0
FAIL_COUNT=0

for test_script in scripts/test_p0.py scripts/test_p1.py scripts/test_p2.py; do
    if [ -f "${test_script}" ]; then
        echo "  → 运行 ${test_script}..."
        if python3 "${test_script}" > /tmp/test_output.txt 2>&1; then
            PASSED=$(grep -oP '通过: \K\d+' /tmp/test_output.txt | tail -1)
            TOTAL=$(grep -oP '总计: \K\d+' /tmp/test_output.txt | tail -1)
            echo "    ✅ 通过 ${PASSED}/${TOTAL}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo "    ❌ 测试失败，查看 /tmp/test_output.txt"
            cat /tmp/test_output.txt | tail -20
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi
done
echo ""

# Step 3: 生成文件清单
echo "[3/4] 生成文件清单..."
FILE_COUNT=$(find . -type f \
    ! -path './data/*' ! -path './reports/*' \
    ! -path '*/__pycache__/*' ! -name '*.pyc' \
    | wc -l)
echo "  源文件总数: ${FILE_COUNT}"
echo ""

# Step 4: 打包
echo "[4/4] 打包 ZIP 文件..."
mkdir -p "${DIST_DIR}"

# 使用 Python zipfile 确保跨平台兼容性（无 Unix 权限位问题）
python3 << PYEOF
import zipfile
import os
import hashlib

project_dir = "${PROJECT_DIR}"
dist_dir = "${DIST_DIR}"
zip_name = "${ZIP_NAME}"

# 需要排除的目录和文件
exclude_patterns = [
    '/data/backups/', '/data/logs/', '/data/audit/', '/data/auth/',
    '/__pycache__/', '*.pyc', '*.pyo',
    '/.git/', '/.pytest_cache/',
]

def should_exclude(filepath):
    for pat in exclude_patterns:
        if pat.startswith('*'):
            if filepath.endswith(pat[1:]):
                return True
        elif pat in filepath:
            return True
    return False

zip_path = os.path.join(dist_dir, zip_name)
file_count = 0
total_size = 0

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for root, dirs, files in os.walk(project_dir):
        # 排除目录
        dirs[:] = [d for d in dirs if not d.startswith('__pycache__') and d != '.git']
        
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, project_dir)
            
            if should_exclude('/' + rel_path):
                continue
            
            # 跳过空文件
            if os.path.getsize(fpath) == 0 and fname != '.gitkeep':
                continue
            
            arcname = f"echoseve-b-mvp/{rel_path}"
            zf.write(fpath, arcname)
            file_count += 1
            total_size += os.path.getsize(fpath)

# 计算 SHA256
sha256 = hashlib.sha256()
with open(zip_path, 'rb') as f:
    for chunk in iter(lambda: f.read(8192), b''):
        sha256.update(chunk)

zip_size = os.path.getsize(zip_path)
print(f"  📦 ZIP 文件: {zip_path}")
print(f"  📊 包含文件: {file_count}")
print(f"  📏 压缩包大小: {zip_size / 1024:.1f} KB ({zip_size / 1024 / 1024:.2f} MB)")
print(f"  🔐 SHA256: {sha256.hexdigest()}")

# 写入校验文件
checksum_path = os.path.join(dist_dir, zip_name + '.sha256')
with open(checksum_path, 'w') as f:
    f.write(f"{sha256.hexdigest()}  {zip_name}\n")
print(f"  📝 校验文件: {checksum_path}")

PYEOF

echo ""
echo "=========================================="
echo "  ✅ 打包完成！"
echo "=========================================="
echo ""
echo "交付物清单："
ls -lh "${DIST_DIR}/"
echo ""
echo "部署步骤："
echo "  1. 将 ZIP 文件上传到目标服务器"
echo "  2. unzip 解压后阅读 docs/PRODUCTION_DEPLOYMENT_GUIDE.md"
echo "  3. 按文档执行部署流程"
echo ""
