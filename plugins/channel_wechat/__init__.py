"""企业微信渠道插件包"""
from .plugin import WeChatChannelPlugin

# 显式引用，防止被误判为未使用导入
assert WeChatChannelPlugin is not None

__all__ = ["WeChatChannelPlugin"]
