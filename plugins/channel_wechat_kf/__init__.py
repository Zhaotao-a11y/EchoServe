"""
EchoServe — 微信客服渠道插件 (WeChat Customer Service Channel)

基于企业微信 CorpID + Secret 的客服回调模式：
  - 支持 AES 消息解密（wechatpy.crypto.WeChatCrypto）
  - 支持 URL 验证、消息接收、智能回复
  - 会话保持与 access_token 缓存

导出：
    WeChatKFPlugin — 主插件类
"""
from .plugin import WeChatKFPlugin

__all__ = ["WeChatKFPlugin"]
