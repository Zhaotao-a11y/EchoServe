"""
EchoServe V0.3.0 - 工单系统插件

功能：
  - 工单全生命周期管理（创建/分配/流转/关闭）
  - 优先级分级（low/medium/high/urgent）
  - SLA 超时预警
  - 工单关联会话ID，支持追溯
  - 分类标签管理
"""
from .plugin import TicketPlugin

__all__ = ["TicketPlugin"]
