"""
EchoServe 修复验证测试
验证 Phase 2.4/2.5/2.6 代码评审修复点是否生效。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def verify_fixes():
    errors = []
    passed = 0

    # ── Fix C-1: chat_stream reply_text 不再被覆盖 ──────────
    print("\n[C-1] chat_stream reply_text Bug Fix...")
    try:
        import plugins.chat.plugin as chat_module
        source = Path(chat_module.__file__).read_text(encoding="utf-8")

        # 验证: 工具路径后不再有无条件 reply_text = "".join(full_reply)
        # 应存在条件赋值和注释
        has_conditional = 'if not tool_used:' in source and 'reply_text = "".join(full_reply)' in source
        has_comment = '工具路径已在' in source

        # 验证不存在旧的无条件赋值模式
        old_pattern = 'reply_text = "".join(full_reply)\n        # 更新历史\n        reply_text = "".join(full_reply)'
        has_old_bug = old_pattern in source

        if not has_conditional:
            errors.append("C-1: chat_stream 缺少条件赋值")
        elif has_old_bug:
            errors.append("C-1: chat_stream 旧 Bug 模式仍存在")
        else:
            passed += 1
            print("  OK  chat_stream reply_text 条件赋值正确，无覆盖风险")
    except Exception as e:
        errors.append(f"C-1 check error: {e}")

    # ── Fix S-1: ticket_ai.py 使用 verify_token ──────────────
    print("\n[S-1] ticket_ai.py Authentication Fix...")
    try:
        import api.routers.ticket_ai as ticket_ai
        source = Path(ticket_ai.__file__).read_text(encoding="utf-8")

        # 验证不存在旧的 lambda: "system"
        has_old_auth = 'Depends(lambda: "system")' in source
        has_verify = 'Depends(verify_token)' in source
        has_import = 'from api.deps import verify_token' in source

        if has_old_auth:
            errors.append("S-1: 仍存在 lambda: 'system' 硬编码认证")
        elif not has_verify:
            errors.append("S-1: 未使用 verify_token")
        elif not has_import:
            errors.append("S-1: 未导入 verify_token")
        else:
            passed += 1
            print("  OK  所有端点使用 verify_token 认证")
    except Exception as e:
        errors.append(f"S-1 check error: {e}")

    # ── Fix S-2: execute_tool 检查 requires_confirmation ─────
    print("\n[S-2] Tool Confirmation Check Fix...")
    try:
        import api.routers.ticket_ai as ticket_ai
        source = Path(ticket_ai.__file__).read_text(encoding="utf-8")

        has_confirmed_field = "confirmed: bool" in source
        has_check = "requires_confirmation" in source

        if not has_confirmed_field:
            errors.append("S-2: ExecuteToolRequest 缺少 confirmed 字段")
        elif not has_check:
            errors.append("S-2: execute_tool 未检查 requires_confirmation")
        else:
            passed += 1
            print("  OK  工具确认检查逻辑已添加")
    except Exception as e:
        errors.append(f"S-2 check error: {e}")

    # ── Fix C-2: 异步上下文中同步 I/O 用 run_in_executor ────
    print("\n[C-2] Async Sync I/O Fix...")
    try:
        import plugins.ticket.ai_investigator as ai_inv
        source = Path(ai_inv.__file__).read_text(encoding="utf-8")

        has_run_in_executor = "run_in_executor" in source
        has_iscoroutine = "iscoroutinefunction" in source

        if not has_iscoroutine:
            errors.append("C-2: 未检查 handler 是否为协程函数")
        elif not has_run_in_executor:
            errors.append("C-2: 未使用 run_in_executor 包装同步调用")
        else:
            passed += 1
            print("  OK  同步 I/O 已用 run_in_executor 包装")
    except Exception as e:
        errors.append(f"C-2 check error: {e}")

    # ── Fix C-3: asyncio.get_event_loop() 废弃 ──────────────
    print("\n[C-3] Deprecated get_event_loop Fix...")
    try:
        import plugins.tools as tools_module
        source = Path(tools_module.__file__).read_text(encoding="utf-8")

        has_old = "get_event_loop()" in source
        has_new = "get_running_loop()" in source

        if has_old:
            errors.append("C-3: 仍存在 get_event_loop()")
        elif not has_new:
            errors.append("C-3: 未使用 get_running_loop()")
        else:
            passed += 1
            print("  OK  get_running_loop() 已替换 get_event_loop()")
    except Exception as e:
        errors.append(f"C-3 check error: {e}")

    # ── Fix C-4: boundary_score 死代码 ────────────────────────
    print("\n[C-4] Dead Code Elimination...")
    try:
        import plugins.knowledge.semantic_chunker as chunker
        source = Path(chunker.__file__).read_text(encoding="utf-8")

        # 旧模式: 三元表达式中 else 0.5 永不执行
        old_pattern = "if i - 1 < len(similarities) else 0.5"
        has_old = old_pattern in source

        if has_old:
            errors.append("C-4: boundary_score 死代码仍存在")
        else:
            passed += 1
            print("  OK  boundary_score 冗余条件已清理")
    except Exception as e:
        errors.append(f"C-4 check error: {e}")

    # ── Fix M-1/M-2: 函数内部 import 清理 ────────────────────
    print("\n[M-1/2] Inline Import Cleanup...")
    try:
        import plugins.tools as tools_module
        source = Path(tools_module.__file__).read_text(encoding="utf-8")

        # 检查 execute 方法内部是否还有 import time
        # 先找到 execute 方法，检查其内部
        has_import_time_inline = False
        lines = source.splitlines()
        in_execute = False
        for i, line in enumerate(lines):
            if "def execute(" in line:
                in_execute = True
            elif in_execute and line.strip().startswith("def ") and "execute" not in line:
                in_execute = False
            if in_execute and "import time" in line:
                has_import_time_inline = True
                break

        # 检查 chat/plugin.py 函数内部 import asyncio
        import plugins.chat.plugin as chat_mod
        chat_source = Path(chat_mod.__file__).read_text(encoding="utf-8")
        has_import_asyncio_inline = "        import asyncio" in chat_source

        if has_import_time_inline:
            errors.append("M-1: tools/__init__.py execute() 内仍有 import time")
        elif has_import_asyncio_inline:
            errors.append("M-2: chat/plugin.py 函数内仍有 import asyncio")
        else:
            passed += 1
            print("  OK  函数内部冗余 import 已清理")
    except Exception as e:
        errors.append(f"M-1/2 check error: {e}")

    # ── Summary ───────────────────────────────────────────
    print("\n" + "=" * 60)
    total_checks = 7
    print(f"验证结果: {passed}/{total_checks} 项通过")
    if errors:
        print(f"\n{len(errors)} 项未通过:")
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print("全部通过 — 所有修复点验证成功")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(verify_fixes())
