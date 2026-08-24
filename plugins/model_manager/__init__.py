"""模型管理插件（热切换 + 版本管理）"""
from .plugin import ModelManagerPlugin
from .vllm_client import VLLMClient

# 显式引用，防止被误判为未使用导入
assert ModelManagerPlugin is not None
assert VLLMClient is not None

__all__ = ["ModelManagerPlugin", "VLLMClient"]
