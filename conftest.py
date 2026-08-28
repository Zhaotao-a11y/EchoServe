"""
Pytest conftest — 确保 EchoServe 根目录在 sys.path 中，
使 core.* 和 plugins.evolution.* 均可被正确导入。
"""
import sys
from pathlib import Path

# EchoServe 根目录（conftest.py 所在目录即为根目录）
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

# plugins 目录（使 evolution 可作为顶层包导入）
_plugins = str(Path(__file__).resolve().parent / "plugins")
if _plugins not in sys.path:
    sys.path.insert(0, _plugins)
