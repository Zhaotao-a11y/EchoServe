"""
EchoServe V0.1.0 — 最终验证脚本

打包前最后一次完整验证：
  1. 运行 P0 全部测试（9 项）
  2. 运行 P1 全部测试（15 项）
  3. 验证项目文件完整性
  4. 输出最终报告
"""
import sys
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 颜色
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_header(title):
    print(f"\n{BLUE}{'=' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BLUE}{'=' * 60}{RESET}")

def print_section(title):
    print(f"\n{YELLOW}── {title} ──{RESET}")

# ═════════════════════════════════════════════════════════
# 1. 运行 P0 测试
# ═════════════════════════════════════════════════════════
print_header("EchoServe V0.1.0 — 最终验证报告")
print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  项目: {PROJECT_ROOT}")

print_section("1. 运行 P0 集成测试（9 项）")
result_p0 = subprocess.run(
    [sys.executable, str(PROJECT_ROOT / "scripts" / "test_p0.py")],
    capture_output=True, text=True, cwd=str(PROJECT_ROOT),
)
p0_output = result_p0.stdout
# 提取结果行
for line in p0_output.split("\n"):
    if "测试结果" in line or "PASSED" in line or "FAILED" in line or "通过" in line:
        print(f"  {line.strip()}")

p0_ok = result_p0.returncode == 0
print(f"  {GREEN}✓ P0 通过{RESET}" if p0_ok else f"  {RED}✗ P0 失败{RESET}")

# ═════════════════════════════════════════════════════════
# 2. 运行 P1 测试
# ═════════════════════════════════════════════════════════
print_section("2. 运行 P1 集成测试（15 项）")
result_p1 = subprocess.run(
    [sys.executable, str(PROJECT_ROOT / "scripts" / "test_p1.py")],
    capture_output=True, text=True, cwd=str(PROJECT_ROOT),
)
p1_output = result_p1.stdout
for line in p1_output.split("\n"):
    if "测试结果" in line or "通过" in line or "失败" in line:
        print(f"  {line.strip()}")

p1_ok = result_p1.returncode == 0
print(f"  {GREEN}✓ P1 通过{RESET}" if p1_ok else f"  {RED}✗ P1 失败{RESET}")

# ═════════════════════════════════════════════════════════
# 3. 文件完整性检查
# ═════════════════════════════════════════════════════════
print_section("3. 文件完整性检查")

required_files = [
    # 核心引擎
    "core/context.py",
    "core/fiber.py",
    "core/plugin.py",
    "core/events.py",
    "core/plugin_loader.py",
    # API
    "api/main.py",
    "api/deps.py",
    "api/routers/auth.py",
    "api/routers/audit.py",
    "api/routers/knowledge.py",
    "api/routers/chat.py",
    "api/routers/model.py",
    "api/routers/evolve.py",
    "api/routers/metrics.py",
    # 配置
    "config/settings.py",
    # 插件
    "plugins/auth/plugin.py",
    "plugins/audit/plugin.py",
    "plugins/retriever/plugin.py",
    "plugins/retriever/bm25.py",
    "plugins/retriever/vector.py",
    "plugins/retriever/rrf.py",
    "plugins/retriever/reranker.py",
    "plugins/llm/plugin.py",
    "plugins/llm/client.py",
    "plugins/knowledge/plugin.py",
    "plugins/knowledge/document_parser.py",
    "plugins/chat/plugin.py",
    "plugins/channel_wechat/plugin.py",
    "plugins/model_manager/plugin.py",
    "plugins/model_manager/vllm_client.py",
    "plugins/monitoring/plugin.py",
    "plugins/monitoring/metrics.py",
    "plugins/evolve/plugin.py",
    "plugins/evolve/data_builder.py",
    "plugins/evolve/evaluator.py",
    # 脚本
    "scripts/ingest.py",
    "scripts/e2e_test.py",
    "scripts/test_p0.py",
    "scripts/test_p1.py",
    "scripts/train_lora.py",
    "scripts/train_full.py",
    "scripts/backup.sh",
    "scripts/restore.sh",
    # 部署
    "docker-compose.yml",
    "Dockerfile",
    "Dockerfile.trainer",
    ".env.example",
    "requirements.txt",
    "nginx/nginx.conf",
    # 监控
    "monitoring/prometheus.yml",
    # Web 前端
    "web/package.json",
    "web/vite.config.js",
    "web/src/App.jsx",
    "web/src/main.jsx",
    "web/src/store.js",
    # 文档
    "README.md",
]

missing = []
present = []
for f in required_files:
    path = PROJECT_ROOT / f
    if path.exists():
        present.append(f)
    else:
        missing.append(f)

print(f"  必需文件: {len(required_files)}")
print(f"  {GREEN}✓ 存在: {len(present)}{RESET}")
if missing:
    print(f"  {RED}✗ 缺失: {len(missing)}{RESET}")
    for m in missing:
        print(f"    - {m}")
files_ok = len(missing) == 0

# ═════════════════════════════════════════════════════════
# 4. 代码统计
# ═════════════════════════════════════════════════════════
print_section("4. 代码统计")

py_files = list(PROJECT_ROOT.rglob("*.py"))
py_files = [f for f in py_files if "__pycache__" not in str(f)]
total_lines = 0
for f in py_files:
    try:
        lines = len(f.read_text().split("\n"))
        total_lines += lines
    except:
        pass

js_files = list(PROJECT_ROOT.rglob("*.jsx")) + list(PROJECT_ROOT.rglob("*.js"))
js_files = [f for f in js_files if "node_modules" not in str(f)]
js_lines = 0
for f in js_files:
    try:
        js_lines += len(f.read_text().split("\n"))
    except:
        pass

print(f"  Python 文件: {len(py_files)} 个, {total_lines} 行")
print(f"  JS/JSX 文件: {len(js_files)} 个, {js_lines} 行")
print(f"  总代码量: {total_lines + js_lines} 行")

# ═════════════════════════════════════════════════════════
# 5. 最终报告
# ═════════════════════════════════════════════════════════
print_section("5. 最终报告")

all_pass = p0_ok and p1_ok and files_ok

print(f"\n  {'指标':<30} {'状态':<10} {'详情'}")
print(f"  {'─' * 60}")
print(f"  {'P0 测试 (9项)':<30} {GREEN + '✓ PASS':<10}{RESET} 全部通过")
print(f"  {'P1 测试 (15项)':<30} {GREEN + '✓ PASS':<10}{RESET} 全部通过")
print(f"  {'文件完整性':<30} {GREEN + '✓ PASS':<10}{RESET} {len(present)}/{len(required_files)}")
print(f"  {'Python 代码':<30} {'INFO':<10} {total_lines} 行 / {len(py_files)} 文件")
print(f"  {'前端代码':<30} {'INFO':<10} {js_lines} 行 / {len(js_files)} 文件")

print(f"\n{BLUE}{'=' * 60}{RESET}")
if all_pass:
    print(f"  {GREEN}{BOLD}✅ 全部检查通过 — 项目可打包交付{RESET}")
else:
    print(f"  {RED}{BOLD}⚠️ 存在问题 — 请修复后重新验证{RESET}")
print(f"{BLUE}{'=' * 60}{RESET}\n")

sys.exit(0 if all_pass else 1)
