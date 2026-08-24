"""
EchoServe V0.1.0 — API Routers

路由模块：
  - auth:     用户认证、注册、角色管理、API Key
  - audit:    审计日志查询、导出、完整性校验
  - chat:     对话接口（流式/非流式）、会话管理
  - knowledge: 知识库管理、文档上传、检索测试
  - evolve:   模型进化引擎接口
  - metrics:  监控指标接口
  - model:    模型管理接口
  - settings: 系统设置接口（微信客服等）
"""
from . import auth, audit, chat, knowledge, evolve, metrics, model, settings

# 显式引用，防止被误判为未使用导入
assert auth is not None
assert audit is not None
assert chat is not None
assert knowledge is not None
assert evolve is not None
assert metrics is not None
assert model is not None
assert settings is not None

__all__ = ["auth", "audit", "chat", "knowledge", "evolve", "metrics", "model", "settings"]
