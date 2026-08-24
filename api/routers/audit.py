"""
EchoServe V0.1.0 — 审计日志 API 路由

端点：
    GET    /api/audit/logs      查询日志（筛选/分页）
    GET    /api/audit/export    CSV 导出
    GET    /api/audit/verify    完整性校验
    GET    /api/audit/stats     日志统计
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_context, require_permission
from plugins.audit.plugin import AuditPlugin

logger = logging.getLogger("echoseve.api.audit")

router = APIRouter()


def get_audit_service(ctx=Depends(get_context)) -> AuditPlugin:
    if not ctx.has("audit_logger"):
        raise HTTPException(status_code=503, detail="审计服务未就绪")
    return ctx.inject("audit_logger")


@router.get("/audit/logs")
async def query_logs(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: Optional[str] = None,
    keyword: Optional[str] = None,
    action: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
    audit: AuditPlugin = Depends(get_audit_service),
    _: str = Depends(require_permission("audit.read")),
):
    """
    查询审计日志。
    支持按日期范围、用户、关键词、动作类型筛选。
    """
    result = audit.query(
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        keyword=keyword,
        action=action,
        offset=offset,
        limit=limit,
    )
    return result


@router.get("/audit/export")
async def export_csv(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: Optional[str] = None,
    audit: AuditPlugin = Depends(get_audit_service),
    _: str = Depends(require_permission("audit.read")),
):
    """
    导出审计日志为 CSV。
    返回文件下载。
    """
    from fastapi.responses import StreamingResponse
    import io

    csv_content = audit.export_csv(
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )

    # 添加 BOM 头方便 Excel 打开
    buffer = io.BytesIO()
    buffer.write(b"\xef\xbb\xbf")  # UTF-8 BOM
    buffer.write(csv_content.encode("utf-8"))
    buffer.seek(0)

    filename = f"audit_export_{start_date or 'all'}_{end_date or 'all'}.csv"

    return StreamingResponse(
        buffer,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit/verify")
async def verify_integrity(
    audit: AuditPlugin = Depends(get_audit_service),
    _: str = Depends(require_permission("audit.read")),
):
    """
    验证审计日志完整性。
    检查哈希链是否完整，任何篡改都会被检测。
    """
    result = audit.verify_integrity()
    return {
        "valid": result["valid"],
        "total_logs": result["total"],
        "broken_at": result["broken_at"],
        "message": "审计日志完整，未被篡改" if result["valid"] else f"检测到篡改，断裂位置: ID={result['broken_at']}",
    }


@router.get("/audit/stats")
async def audit_stats(
    audit: AuditPlugin = Depends(get_audit_service),
    _: str = Depends(require_permission("audit.read")),
):
    """审计日志统计概览"""
    # 获取全部日志做统计
    all_logs = audit.query(limit=100000)

    total = all_logs["total"]
    actions: dict = {}
    channels: dict = {}
    users: dict = {}

    for entry in all_logs["logs"]:
        act = entry.get("action", "unknown")
        actions[act] = actions.get(act, 0) + 1

        ch = entry.get("channel", "unknown")
        channels[ch] = channels.get(ch, 0) + 1

        uid = entry.get("user_id", "unknown")
        users[uid] = users.get(uid, 0) + 1

    # 计算时间范围
    logs = all_logs["logs"]
    time_range = {}
    if logs:
        timestamps = [l["timestamp"] for l in logs if "timestamp" in l]
        if timestamps:
            time_range = {
                "earliest": min(timestamps),
                "latest": max(timestamps),
            }

    return {
        "total_logs": total,
        "time_range": time_range,
        "top_actions": sorted(actions.items(), key=lambda x: -x[1])[:10],
        "channels": channels,
        "unique_users": len(users),
    }
