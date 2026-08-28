"""
EchoServe P1 — 监控指标 API 路由

提供：
- GET /metrics → Prometheus 格式
- GET /api/monitoring/snapshot → JSON 快照
- GET /api/monitoring/dashboard → 仪表盘数据
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Response, Depends, HTTPException

from api.deps import get_context, verify_token
from core.context import BaizeContext

logger = logging.getLogger("echoserve.api.metrics")

router = APIRouter()


# ─── Prometheus 端点 ──────────────────────────────────

@router.get("/metrics")
async def prometheus_metrics(ctx: BaizeContext = Depends(get_context)):
    """
    Prometheus 指标端点。

    返回标准 Prometheus text format，可直接被 Prometheus 抓取。
    """
    monitor = ctx.inject("monitoring")
    if not monitor:
        return Response(content="# Monitoring not available\n", media_type="text/plain")

    metrics_text = monitor.get_prometheus_metrics()
    return Response(content=metrics_text, media_type="text/plain; version=0.0.4")


# ─── JSON 快照 ────────────────────────────────────────

@router.get("/api/monitoring/snapshot")
async def metrics_snapshot(ctx: BaizeContext = Depends(get_context)):
    """获取当前指标快照（JSON 格式）"""
    monitor = ctx.inject("monitoring")
    if not monitor:
        raise HTTPException(status_code=503, detail="监控插件未就绪")
    return monitor.get_snapshot()


# ─── 仪表盘数据 ────────────────────────────────────────

@router.get("/api/monitoring/dashboard")
async def dashboard_data(ctx: BaizeContext = Depends(get_context)):
    """
    返回管理后台仪表盘所需的数据。

    包含系统指标、业务指标、插件状态、训练状态。
    """
    monitor = ctx.inject("monitoring")
    if not monitor:
        raise HTTPException(status_code=503, detail="监控插件未就绪")
    return monitor.get_dashboard_data()


# ─── 手动采集触发 ──────────────────────────────────────

@router.post("/api/monitoring/collect")
async def trigger_collection(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """手动触发一次指标采集"""
    monitor = ctx.inject("monitoring")
    if not monitor:
        raise HTTPException(status_code=503, detail="监控插件未就绪")

    collector = monitor.collector
    if not collector:
        raise HTTPException(status_code=503, detail="指标收集器未就绪")

    gpu_ok = collector.collect_gpu_metrics()
    sys_ok = collector.collect_system_metrics()
    monitor._collect_business_metrics()

    return {
        "gpu_collected": gpu_ok,
        "system_collected": sys_ok,
        "business_collected": True,
    }
