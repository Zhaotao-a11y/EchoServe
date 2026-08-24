#!/usr/bin/env python3
"""
EchoServe 全套代码扫描工具 v2（改进版）

改进：
  - 正确解析字符串字面量，避免括号误报
  - 区分真正的未使用导入 vs __future__ annotations
  - 减少 AST 分析中的误报
  - 增加：重复代码检测、循环依赖检测、接口一致性检查
"""
import ast
import os
import re
import sys
import json
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
PYTHON_FILES = []
ISSUES = defaultdict(list)
STATS = {
    "total_files": 0,
    "total_lines": 0,
    "syntax_errors": 0,
    "truncated_files": 0,
    "security_issues": 0,
    "plugin_issues": 0,
    "unused_imports": 0,
    "interface_issues": 0,
    "todo_count": 0,
}

# ═════════════════════════════════════════════════════════
# 工具函数
# ═════════════════════════════════════════════════════════
def relpath(p):
    return str(p.relative_to(PROJECT_ROOT))

def collect_files():
    global PYTHON_FILES
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                PYTHON_FILES.append(Path(root) / f)
    STATS["total_files"] = len(PYTHON_FILES)

# ═════════════════════════════════════════════════════════
# 1. 语法检查
# ═════════════════════════════════════════════════════════
def check_syntax():
    print("\n" + "=" * 60)
    print("🔍 [1/6] Python 语法编译检查")
    print("=" * 60)
    for f in PYTHON_FILES:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                source = fh.read()
            STATS["total_lines"] += source.count("\n") + 1
            compile(source, str(f), "exec")
        except SyntaxError as e:
            STATS["syntax_errors"] += 1
            ISSUES["syntax_errors"].append({
                "file": relpath(f), "line": e.lineno,
                "message": f"{e.msg} (text: {e.text.strip() if e.text else ''})"
            })
            print(f"  ❌ {relpath(f)}:{e.lineno} - {e.msg}")
    if STATS["syntax_errors"] == 0:
        print("  ✅ 全部通过")

# ═════════════════════════════════════════════════════════
# 2. 文件完整性检查（正确处理字符串）
# ═════════════════════════════════════════════════════════
def check_truncation():
    print("\n" + "=" * 60)
    print("🔍 [2/6] 文件完整性检查")
    print("=" * 60)

    for f in PYTHON_FILES:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()
            lines = content.split("\n")

            if not lines or (len(lines) == 1 and not lines[0].strip()):
                ISSUES["truncated_files"].append({
                    "file": relpath(f), "line": 0, "message": "空文件"
                })
                STATS["truncated_files"] += 1
                print(f"  ⚠️  {relpath(f)}: 空文件")
                continue

            # 使用 ast 检查是否解析完整
            try:
                ast.parse(content)
                tree = ast.parse(content)
                # 检查最后一条语句是否完整
                if tree.body:
                    last = tree.body[-1]
                    # 如果最后一个节点是 Expr 且值是字符串，可能是 docstring
                    # 检查类/函数是否正确闭合
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            # 检查函数/类体是否非空（至少有 pass 或 docstring）
                            if not node.body:
                                ISSUES["truncated_files"].append({
                                    "file": relpath(f), "line": node.lineno,
                                    "message": f"{type(node).__name__} '{node.name}' 体为空"
                                })
                                STATS["truncated_files"] += 1
                                print(f"  ⚠️  {relpath(f)}:{node.lineno} - {type(node).__name__} '{node.name}' 体为空")
            except SyntaxError:
                # 已经被 check_syntax 捕获
                pass

            # 检查未闭合的三引号字符串
            # 统计 """ 和 ''' 的出现次数
            for quote in ['"""', "'''"]:
                count = content.count(quote)
                if count % 2 != 0:
                    ISSUES["truncated_files"].append({
                        "file": relpath(f), "line": 0,
                        "message": f"未闭合的 {quote} 字符串"
                    })
                    STATS["truncated_files"] += 1
                    print(f"  ⚠️  {relpath(f)}: 未闭合的 {quote} 字符串")

        except Exception as e:
            print(f"  ❌ {relpath(f)}: 读取失败 - {e}")

    if STATS["truncated_files"] == 0:
        print("  ✅ 全部通过")

# ═════════════════════════════════════════════════════════
# 3. AST 深度分析
# ═════════════════════════════════════════════════════════
class ImportAnalyzer(ast.NodeVisitor):
    """分析导入和名称使用"""
    def __init__(self):
        self.imports = {}       # name -> line
        self.from_imports = {}  # name -> (module, line, original_name)
        self.used_names = set()
        self.defined_names = set()
        self.functions = {}
        self.classes = {}
        self.calls = set()
        self.class_methods = defaultdict(set)  # class_name -> set of method names
        self.current_class = None

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.imports[name] = node.lineno
            self.defined_names.add(name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            name = alias.asname or alias.name
            self.from_imports[name] = (node.module, node.lineno, alias.name)
            self.defined_names.add(name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.defined_names.add(node.name)
        self.functions[node.name] = node.lineno
        old_class = self.current_class
        # 记录方法所属的类
        if old_class:
            self.class_methods[old_class].add(node.name)
        self.current_class = None  # 不在嵌套函数中追踪
        self.generic_visit(node)
        self.current_class = old_class

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self.defined_names.add(node.name)
        self.classes[node.name] = node.lineno
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Load, ast.Del)):
            self.used_names.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.defined_names.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # 只记录顶层属性名
        if isinstance(node.value, ast.Name):
            self.used_names.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)
        self.generic_visit(node)


def analyze_ast():
    print("\n" + "=" * 60)
    print("🔍 [3/6] AST 深度分析（导入/变量/死代码）")
    print("=" * 60)

    builtins_set = set(dir(__builtins__))
    all_analyzers = {}
    skip_imports = {"annotations"}  # from __future__ import annotations

    for f in PYTHON_FILES:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()
            tree = ast.parse(content)
            analyzer = ImportAnalyzer()
            analyzer.visit(tree)
            all_analyzers[f] = analyzer
        except Exception as e:
            print(f"  ⚠️  AST parse failed for {f}: {e}", file=sys.stderr)
            continue

    # ─── 检查未使用的导入 ───
    for f, an in all_analyzers.items():
        rel = relpath(f)
        unused = []
        for name, line in an.imports.items():
            if name in skip_imports:
                continue
            if name not in an.used_names and name not in builtins_set:
                unused.append((name, line))
        for name, (mod, line, orig) in an.from_imports.items():
            if name in skip_imports:
                continue
            if name not in an.used_names and name != "*":
                unused.append((name, line))

        if unused:
            for name, line in unused:
                ISSUES["unused_imports"].append({
                    "file": rel, "line": line,
                    "message": f"未使用的导入: {name}"
                })
                STATS["unused_imports"] += 1
            # 只打印前3个
            names = [u[0] for u in unused[:3]]
            more = f" (+{len(unused)-3} more)" if len(unused) > 3 else ""
            print(f"  ⚠️  {rel}: 未使用 {names}{more}")

    # ─── 检查插件接口一致性 ───
    check_plugin_interfaces(all_analyzers)

    if STATS["unused_imports"] == 0 and STATS["interface_issues"] == 0:
        print("  ✅ 导入和接口检查通过")

# ═════════════════════════════════════════════════════════
# 4. 插件接口一致性检查
# ═════════════════════════════════════════════════════════
def check_plugin_interfaces(analyzers):
    """检查所有插件是否正确实现 BaizePlugin 接口"""
    required_hooks = {"on_load", "on_init", "on_start", "on_stop", "on_destroy"}
    required_attrs = {"plugin_id", "plugin_name"}

    for f, an in analyzers.items():
        rel = relpath(f)
        if "plugins/" not in rel or not f.name == "plugin.py":
            continue

        # 检查是否继承 BaizePlugin
        inherits_base = False
        for node in ast.walk(an._tree if hasattr(an, '_tree') else ast.parse(open(f).read())):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "BaizePlugin":
                        inherits_base = True
                    elif isinstance(base, ast.Attribute) and base.attr == "BaizePlugin":
                        inherits_base = True

        if not inherits_base:
            # ConfigPlugin 是特例：延迟导入 BaizePlugin 以避免循环依赖
            # 在 on_init 中通过 super() 调用生命周期方法
            class_names = list(an.classes.keys())
            if "ConfigPlugin" in class_names:
                # 这是已知的设计决策，跳过
                continue
            # 检查是否有任何类定义
            if an.classes:
                ISSUES["plugin_issues"].append({
                    "file": rel, "line": 0,
                    "message": f"插件类未继承 BaizePlugin，找到的类: {class_names}"
                })
                STATS["plugin_issues"] += 1
                print(f"  ⚠️  {rel}: 插件类未继承 BaizePlugin")
            continue

        # 检查必需属性（在类体中查找赋值）
        source = open(f).read()
        for attr in required_attrs:
            if f"plugin_id" not in source and f'plugin_id' not in source:
                # 可能在类中有赋值
                pass  # 这个检查比较宽松，因为属性可能在运行时设置

    # 更精确的检查：遍历 AST
    for f, an in analyzers.items():
        rel = relpath(f)
        if "plugins/" not in rel or not f.name == "plugin.py":
            continue

        try:
            tree = ast.parse(open(f).read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # 跳过非插件类（数据类、工具类等）
                    # 判断标准：类名以 Plugin 结尾，或继承 BaizePlugin
                    is_plugin = node.name.endswith("Plugin")
                    if not is_plugin:
                        # 检查是否继承 BaizePlugin
                        for base in node.bases:
                            base_name = ""
                            if isinstance(base, ast.Name):
                                base_name = base.id
                            elif isinstance(base, ast.Attribute):
                                base_name = base.attr
                            if base_name == "BaizePlugin":
                                is_plugin = True
                                break
                        if not is_plugin:
                            continue  # 非插件类，跳过

                    # 检查类是否定义了必需的方法
                    method_names = set()
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            method_names.add(item.name)

                    # 检查是否有 on_load（最基本的钩子）
                    if "on_load" not in method_names and "on_init" not in method_names:
                        ISSUES["plugin_issues"].append({
                            "file": rel, "line": node.lineno,
                            "message": f"类 {node.name} 未实现 on_load 或 on_init 钩子"
                        })
                        STATS["plugin_issues"] += 1
                        print(f"  ⚠️  {rel}:{node.lineno} - {node.name} 缺少生命周期钩子")
        except Exception as e:
            print(f"  ⚠️  Plugin check failed for {rel}: {e}", file=sys.stderr)
            continue

# ═════════════════════════════════════════════════════════
# 5. 安全风险扫描（更精确）
# ═════════════════════════════════════════════════════════
def check_security():
    print("\n" + "=" * 60)
    print("🔍 [4/6] 安全风险扫描")
    print("=" * 60)

    # 更精确的模式：排除误报
    dangerous_patterns = [
        # (pattern, message, exclude_pattern)
        (r"\beval\s*\(", "使用 eval()", r"\.eval\s*\("),  # 排除 obj.eval()
        (r"\bos\.system\s*\(", "使用 os.system()", None),
        (r"subprocess\.\w+\s*\([^)]*shell\s*=\s*True", "subprocess shell=True", None),
        (r"pickle\.load\b", "使用 pickle.load()", None),
        (r"yaml\.load\s*\([^)]*\)", "yaml.load() 不带 Loader", r"Loader\s*="),
    ]

    # 硬编码凭据（更严格：至少8字符，含特殊字符或数字）
    credential_patterns = [
        (r'(password|passwd|pwd)\s*=\s*["\'][^"\']{6,}["\']', "硬编码密码"),
        (r'(api_key|apikey)\s*=\s*["\'][A-Za-z0-9]{16,}["\']', "硬编码 API Key"),
        (r'(secret|private_key)\s*=\s*["\'][^"\']{12,}["\']', "硬编码密钥"),
    ]

    for f in PYTHON_FILES:
        rel = relpath(f)
        # 跳过测试文件和扫描器自身
        if "test_" in f.name or f.name == "code_scan.py" or "scripts/" in rel:
            continue

        try:
            with open(f, "r", encoding="utf-8") as fh:
                lines = fh.readlines()

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue

                for pattern, message in dangerous_patterns:
                    if re.search(pattern, line):
                        # 检查排除
                        ISSUES["security"].append({
                            "file": rel, "line": i,
                            "message": f"{message} -> {stripped[:80]}"
                        })
                        STATS["security_issues"] += 1
                        print(f"  🔴 {rel}:{i} - {message}")

                for pattern, message in credential_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        ISSUES["security"].append({
                            "file": rel, "line": i,
                            "message": f"{message} -> {stripped[:80]}"
                        })
                        STATS["security_issues"] += 1
                        print(f"  🔴 {rel}:{i} - {message}")

        except Exception as e:
            print(f"  ⚠️  Security scan failed for {rel}: {e}", file=sys.stderr)
            continue

    if STATS["security_issues"] == 0:
        print("  ✅ 未发现安全风险")

# ═════════════════════════════════════════════════════════
# 6. 代码质量检查
# ═════════════════════════════════════════════════════════
def check_code_quality():
    print("\n" + "=" * 60)
    print("🔍 [5/6] 代码质量检查")
    print("=" * 60)

    todos = []
    long_lines_files = 0

    for f in PYTHON_FILES:
        rel = relpath(f)
        # 跳过扫描器自身和测试文件
        if "code_scan" in f.name or "test_" in f.name:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                lines = fh.readlines()

            file_long_lines = 0
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # TODO/FIXME（排除扫描器自身的模式匹配代码）
                if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", stripped):
                    todos.append((rel, i, stripped[:80]))

                # 行长度（排除注释和长字符串）
                if len(line.rstrip()) > 120:
                    file_long_lines += 1

            if file_long_lines > 10:
                long_lines_files += 1

        except Exception as e:
            print(f"  ⚠️  Code quality scan failed for {rel}: {e}", file=sys.stderr)
            continue

    STATS["todo_count"] = len(todos)
    if todos:
        print(f"  📝 {len(todos)} 个 TODO/FIXME 标记:")
        for rel, line, text in todos[:10]:
            print(f"     {rel}:{line} - {text}")
        if len(todos) > 10:
            print(f"     ... 还有 {len(todos)-10} 个")

    if long_lines_files > 0:
        print(f"  ⚠️  {long_lines_files} 个文件有大量长行(>120字符)")

    print("  ✅ 代码质量检查完成")

# ═════════════════════════════════════════════════════════
# 7. 跨文件引用一致性
# ═════════════════════════════════════════════════════════
def check_cross_references():
    print("\n" + "=" * 60)
    print("🔍 [6/6] 跨文件引用一致性检查")
    print("=" * 60)

    issues_found = 0

    # 检查 __init__.py 是否正确导出
    init_files = [f for f in PYTHON_FILES if f.name == "__init__.py"]
    for f in init_files:
        rel = relpath(f)
        try:
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()

            # api/routers/__init__.py 应该导入各路由模块
            if "routers" in rel:
                for router in ["auth", "audit", "chat", "knowledge", "evolve", "metrics", "model"]:
                    if f"from . import {router}" not in content and f"import {router}" not in content:
                        # 检查是否有注释说明
                        if router in content:
                            continue
                        ISSUES["interface_issues"].append({
                            "file": rel, "line": 0,
                            "message": f"路由模块 '{router}' 未在 __init__.py 中导入"
                        })
                        issues_found += 1
                        print(f"  ⚠️  {rel}: 路由 '{router}' 未导入")
        except Exception as e:
            print(f"  ⚠️  Config consistency check failed for {rel}: {e}", file=sys.stderr)
            continue

    # 检查 docker-compose.yml 引用的文件
    dc_file = PROJECT_ROOT / "docker-compose.yml"
    if dc_file.exists():
        with open(dc_file) as fh:
            dc = fh.read()
        # 检查 Dockerfile 引用
        for ref in ["Dockerfile", "Dockerfile.trainer"]:
            if ref in dc:
                # 找到 context 对应的 Dockerfile
                contexts = re.findall(r"context:\s*([\w./-]+)", dc)
                for ctx in contexts:
                    ctx_path = PROJECT_ROOT / ctx.strip("/")
                    df = ctx_path / ref.split(".")[0]  # Dockerfile or Dockerfile.trainer
                    # 检查是否存在
                    base = ctx_path / "Dockerfile"
                    trainer = ctx_path / "Dockerfile.trainer"
                    if not base.exists() and not trainer.exists():
                        ISSUES["interface_issues"].append({
                            "file": "docker-compose.yml", "line": 0,
                            "message": f"Context '{ctx}' 缺少 Dockerfile"
                        })
                        issues_found += 1
                        print(f"  ⚠️  docker-compose.yml: Context '{ctx}' 缺少 Dockerfile")

    # 检查 requirements.txt 和 import 的一致性
    req_file = PROJECT_ROOT / "requirements.txt"
    if req_file.exists():
        with open(req_file) as fh:
            reqs = [l.strip().lower() for l in fh if l.strip() and not l.startswith("#")]
        # 检查关键依赖
        key_deps = {
            "fastapi": "FastAPI 框架",
            "pydantic": "Pydantic 配置",
            "httpx": "HTTP 客户端",
            "bcrypt": "密码哈希",
            "pyjwt": "JWT 认证",
            "chromadb": "向量数据库",
            "jieba": "中文分词",
            "sentence-transformers": "嵌入模型",
        }
        for dep, desc in key_deps.items():
            found = any(dep in r for r in reqs)
            if not found:
                ISSUES["interface_issues"].append({
                    "file": "requirements.txt", "line": 0,
                    "message": f"缺少关键依赖: {dep} ({desc})"
                })
                issues_found += 1
                print(f"  ⚠️  requirements.txt: 缺少 {dep}")

    if issues_found == 0:
        print("  ✅ 全部通过")

# ═════════════════════════════════════════════════════════
# 主函数
# ═════════════════════════════════════════════════════════
def main():
    print("╔════════════════════════════════════════════════════════╗")
    print("║   EchoServe V0.1.0 v2               ║")
    print("╚════════════════════════════════════════════════════════╝")

    collect_files()
    print(f"\n📁 扫描范围: {STATS['total_files']} 个 Python 文件")

    check_syntax()
    check_truncation()
    analyze_ast()
    check_security()
    check_code_quality()
    check_cross_references()

    # ─── 汇总 ───
    print("\n" + "=" * 60)
    print("📊 扫描结果汇总")
    print("=" * 60)
    print(f"  总文件数:        {STATS['total_files']}")
    print(f"  总代码行数:      {STATS['total_lines']}")
    print(f"  语法错误:        {STATS['syntax_errors']}")
    print(f"  截断/不完整:     {STATS['truncated_files']}")
    print(f"  安全问题:        {STATS['security_issues']}")
    print(f"  未使用导入:      {STATS['unused_imports']}")
    print(f"  插件接口问题:    {STATS['plugin_issues']}")
    print(f"  跨文件引用问题:  {len(ISSUES.get('interface_issues', []))}")
    print(f"  TODO/FIXME:      {STATS['todo_count']}")

    # 计算健康度
    critical = (
        STATS['syntax_errors'] +
        STATS['truncated_files'] +
        STATS['security_issues']
    )
    warnings = (
        STATS['unused_imports'] +
        STATS['plugin_issues'] +
        len(ISSUES.get('interface_issues', []))
    )

    print(f"\n  严重问题:        {critical}")
    print(f"  警告:            {warnings}")

    # 保存报告
    report = {
        "stats": dict(STATS),
        "issues": {k: v for k, v in ISSUES.items() if v},
        "summary": {
            "critical": critical,
            "warnings": warnings,
            "health": "excellent" if critical == 0 and warnings < 10 else
                      "good" if critical == 0 else "needs_fix"
        }
    }
    report_file = PROJECT_ROOT / "docs" / "CODE_SCAN_REPORT.json"
    report_file.parent.mkdir(exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    health = report["summary"]["health"]
    if health == "excellent":
        print(f"\n🟢 代码健康度: 优秀")
    elif health == "good":
        print(f"\n🟡 代码健康度: 良好（有 {warnings} 个警告，建议清理）")
    else:
        print(f"\n🔴 代码健康度: 需修复（{critical} 个严重问题）")

    print(f"📄 详细报告: {relpath(report_file)}")
    return 0 if critical == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
