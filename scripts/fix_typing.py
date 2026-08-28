"""m-10: Replace old-style typing imports with modern Python 3.10+ syntax.

Replaces:
  - dict[ -> dict[
  - list[ -> list[
  - tuple[ -> tuple[
  - set[ -> set[
  - (X | None) -> X | None (bracket-matching)
  - Removes Dict/List/Optional/Tuple/Set from `from typing import ...` lines

Only processes .py files. Skips files without `from __future__ import annotations`.
"""
import re
import sys
from pathlib import Path


def find_matching_bracket(text: str, start: int) -> int:
    """Find the matching closing bracket for the opening bracket at `start`."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                return i
    return -1


def replace_optional(text: str) -> str:
    """Replace (X | None) with X | None using bracket matching."""
    result = []
    i = 0
    while i < len(text):
        # Check for Optional[
        if text[i:i+9] == '(':
            # Find matching  | None)
            end = find_matching_bracket(text, i + 8)
            if end == -1:
                result.append(text[i])
                i += 1
                continue
            # Extract inner content
            inner = text[i+9:end]
            # Replace with inner | None
            result.append(f'({inner} | None)')
            i = end + 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


def fix_typing_imports(content: str) -> str:
    """Fix typing imports in a single file."""
    if 'from __future__ import annotations' not in content:
        return content

    lines = content.split('\n')
    new_lines = []

    for line in lines:
        # Handle `from typing import ...` lines
        if line.strip().startswith('from typing import'):
            # Parse the imports
            m = re.match(r'^(\s*)from typing import (.+)$', line)
            if not m:
                new_lines.append(line)
                continue

            indent = m.group(1)
            imports_str = m.group(2)

            # Handle parenthesized imports
            if imports_str.startswith('('):
                # Multi-line import - find closing paren
                # For now, just handle single-line
                imports_str = imports_str.strip('()')

            # Split imports
            imports = [imp.strip() for imp in imports_str.split(',')]

            # Remove old-style imports
            old_style = {'Dict', 'List', 'Optional', 'Tuple', 'Set'}
            remaining = [imp for imp in imports if imp not in old_style]

            if remaining:
                new_lines.append(f'{indent}from typing import {", ".join(remaining)}')
            # If no remaining imports, skip the line entirely
        else:
            new_lines.append(line)

    content = '\n'.join(new_lines)

    # Replace usage patterns
    content = content.replace('dict[', 'dict[')
    content = content.replace('list[', 'list[')
    content = content.replace('tuple[', 'tuple[')
    content = content.replace('set[', 'set[')

    # Replace (X | None) with (X | None) - handle nested brackets
    content = replace_optional(content)

    return content


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    changed = 0

    for py_file in root.rglob('*.py'):
        # Skip __pycache__, .git, venv
        if any(part in {'__pycache__', '.git', '.venv', 'venv', 'node_modules', 'dist'}
               for part in py_file.parts):
            continue

        try:
            content = py_file.read_text(encoding='utf-8')
        except Exception:
            try:
                content = py_file.read_text(encoding='gbk')
            except Exception:
                continue

        if 'from __future__ import annotations' not in content:
            continue

        # Check if there's anything to fix
        if not any(pattern in content for pattern in
                   ['dict[', 'list[', 'tuple[', 'set[', '(',
                    'from typing import' | None)):
            continue

        new_content = fix_typing_imports(content)

        if new_content != content:
            py_file.write_text(new_content, encoding='utf-8')
            changed += 1
            print(f"  Fixed: {py_file}")

    print(f"\nTotal files changed: {changed}")


if __name__ == '__main__':
    main()
