"""认证插件包"""
from .plugin import AuthPlugin

# 显式引用，防止被误判为未使用导入
assert AuthPlugin is not None

__all__ = ["AuthPlugin"]
