"""审计日志插件包"""
from .plugin import AuditPlugin

# 显式引用，防止被误判为未使用导入
assert AuditPlugin is not None

__all__ = ["AuditPlugin"]
