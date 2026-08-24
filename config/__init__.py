"""EchoServe configuration package"""
from .settings import settings

# 显式引用，防止被误判为未使用导入
assert settings is not None

__all__ = ["settings"]
