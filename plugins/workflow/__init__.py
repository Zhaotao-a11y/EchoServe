# -*- coding: utf-8 -*-
"""
EchoServe — 可视化工作流引擎插件 (Workflow Engine)

核心定位：让运营人员通过拖拽配置对话流程，替代硬编码路由逻辑。

设计原则：
    - DAG 有向无环图执行模型
    - 节点级异步执行 + 超时控制
    - 执行上下文隔离（按 session_id）
    - 向后兼容：现有硬编码路由仍可运行
"""
from .plugin import WorkflowPlugin

__all__ = ["WorkflowPlugin"]
