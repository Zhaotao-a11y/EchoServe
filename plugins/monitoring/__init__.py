"""监控插件（Prometheus 指标暴露）"""
from .plugin import MonitoringPlugin
from .metrics import MetricsCollector

# 显式引用，防止被误判为未使用导入
assert MonitoringPlugin is not None
assert MetricsCollector is not None

__all__ = ["MonitoringPlugin", "MetricsCollector"]
