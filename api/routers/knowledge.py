"""
EchoServe V0.1.0 — 知识库 API 路由（P0 完整版）

端点：
    GET    /api/knowledge           列出文档（分页 + 权限过滤）
    POST   /api/knowledge           添加文档
    POST   /api/knowledge/upload    文件上传（PDF/DOCX/MD/TXT）
    POST   /api/knowledge/update-file 增量更新文件（hash检测+增量索引）
    PUT    /api/knowledge/{id}     更新文档
    DELETE /api/knowledge/{id}     删除文档
    POST   /api/knowledge/ingest   批量导入（JSONL）
    POST   /api/knowledge/rebuild  重建索引
    DELETE /api/knowledge/all      清空知识库
    GET    /api/knowledge/stats     知识库统计
    GET    /api/knowledge/test      检索测试
    POST   /api/knowledge/{id}/acl 设置文档权限
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from api.deps import get_knowledge_base, verify_token, require_permission
from plugins.knowledge.plugin import KnowledgePlugin

logger = logging.getLogger("echoserve.api.knowledge")

router = APIRouter()

# ─── 文件上传安全校验常量 ──────────────────────────────
_ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".doc", ".md", ".markdown", ".txt", ".jsonl",
})
_MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


def _sanitize_filename(filename: str) -> str:
    """清理文件名：去除路径前缀、替换危险字符，防止路径穿越。"""
    # 仅保留 basename，去掉任何目录前缀
    safe_name = os.path.basename(filename)
    # 替换危险字符（保留中文、字母、数字、点、下划线、连字符）
    safe_name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", safe_name)
    # 防止空文件名
    if not safe_name or safe_name.startswith("."):
        safe_name = f"upload_{safe_name}" if safe_name else "upload_unknown"
    return safe_name


def _validate_upload_file(file: UploadFile, content: bytes) -> str:
    """校验上传文件：扩展名白名单、大小限制。返回清理后的安全文件名。"""
    raw_filename = file.filename or "unknown"
    safe_filename = _sanitize_filename(raw_filename)

    # 扩展名白名单校验
    ext = Path(safe_filename).suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}，仅支持: {', '.join(sorted(_ALLOWED_UPLOAD_EXTENSIONS))}",
        )

    # 文件大小校验
    if len(content) > _MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大: {len(content) / 1024 / 1024:.1f}MB，上限 {_MAX_UPLOAD_SIZE_BYTES / 1024 / 1024:.0f}MB",
        )

    return safe_filename


# ─── 请求模型 ─────────────────────────────────────

class AddDocumentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    doc_id: str | None = None
    metadata: dict[str, Any] | None = None


class UpdateDocumentRequest(BaseModel):
    content: str | None = None
    metadata: dict[str, Any] | None = None


class SetACLRequest(BaseModel):
    allowed_roles: list[str] = Field(..., description="允许访问的角色列表，['*']表示所有人")


# ─── 辅助函数 ─────────────────────────────────────

def get_kb(
    kb: KnowledgePlugin = Depends(get_knowledge_base),
    user_id: str = Depends(verify_token),
) -> tuple[KnowledgePlugin, str]:
    """获取知识库实例和当前用户角色"""
    from api.deps import get_context
    ctx = get_context()
    auth = ctx.inject("auth_service", None)
    role = "*"
    if auth:
        user = auth.get_user(user_id)
        if user:
            role = user.get("role", "*")
    return kb, role


# ─── 文档管理端点 ────────────────────────────────

@router.get("/knowledge")
async def list_documents(
    offset: int = 0,
    limit: int = 50,
    kb_role: tuple = Depends(get_kb),
):
    """列出知识库文档"""
    kb, role = kb_role
    docs = kb.list_documents(offset=offset, limit=limit, user_role=role)
    return {
        "total": kb.count_documents(),
        "offset": offset,
        "limit": limit,
        "documents": docs,
    }


@router.post("/knowledge")
async def add_document(
    request: AddDocumentRequest,
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """添加单条文档"""
    kb, _ = kb_role
    try:
        doc_id = await kb.add_document(
            content=request.content,
            doc_id=request.doc_id,
            metadata=request.metadata,
        )
        return {"status": "added", "doc_id": doc_id}
    except Exception as e:
        logger.error(f"[API] Add document error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/upload")
async def upload_file(
    file: UploadFile = File(..., description="PDF/DOCX/MD/TXT/JSONL 文件，≤50MB"),
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """
    文件上传端点。
    支持 PDF / DOCX / MD / TXT / JSONL：
      - MD 表格自动识别客服问答数据，逐条导入
      - JSONL 走批量导入逻辑
      - 其他文件自动解析、切片、索引
    """
    kb, role = kb_role

    # 读取文件内容
    content = await file.read()

    # 安全校验：扩展名白名单 + 文件大小限制 + 文件名清理
    filename = _validate_upload_file(file, content)

    # ─── JSONL 文件走批量导入 ─────────────────────
    if filename.lower().endswith('.jsonl'):
        try:
            text = content.decode("utf-8")
            documents = []
            line_num = 0

            for line in text.strip().split("\n"):
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                    if "content" not in doc:
                        raise ValueError(f"Line {line_num}: missing 'content'")
                    documents.append(doc)
                except json.JSONDecodeError as e:
                    raise HTTPException(status_code=400, detail=f"Invalid JSON at line {line_num}: {e}")

            if not documents:
                raise HTTPException(status_code=400, detail="No valid documents found")

            await kb.add_documents_batch(documents)
            return {
                "status": "ingested",
                "filename": filename,
                "total": len(documents),
                "kb_size": kb.count_documents(),
                "metadata": {"import_mode": "jsonl_batch"},
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[API] JSONL ingest error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ─── 常规文件上传（PDF/DOCX/MD/TXT）───────────
    try:
        result = await kb.upload_file(
            file_content=content,
            filename=filename,
            allowed_roles=[role] if role != "*" else ["*"],
        )
        return {
            "status": "uploaded",
            "filename": filename,
            "total_chunks": result["total_chunks"],
            "doc_ids": result["doc_ids"],
            "metadata": result["metadata"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[API] Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/knowledge/{doc_id}")
async def update_document(
    doc_id: str,
    request: UpdateDocumentRequest,
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """更新文档"""
    kb, _ = kb_role
    success = await kb.update_document(
        doc_id=doc_id,
        content=request.content,
        metadata=request.metadata,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "updated", "doc_id": doc_id}


@router.post("/knowledge/update-file")
async def update_file(
    file: UploadFile = File(..., description="更新文件：内容变更时自动重建索引，无变更则跳过"),
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """
    增量更新文件端点。

    基于 content_hash 检测文件是否变更：
    - 内容未变 → 跳过，返回 content_unchanged
    - 内容变更 → 删除旧 chunks → 重新解析+切片+索引（增量，非全量重建）
    """
    kb, role = kb_role

    content = await file.read()
    filename = _validate_upload_file(file, content)

    try:
        result = await kb.update_file(
            file_content=content,
            filename=filename,
            allowed_roles=[role] if role != "*" else ["*"],
        )
        return {
            "status": "updated" if result.get("updated") else "skipped",
            "filename": filename,
            "total_chunks": result.get("total_chunks", 0),
            "doc_ids": result.get("doc_ids", []),
            "reason": result.get("reason", ""),
            "previous_doc_count": result.get("previous_doc_count", 0),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[API] Update file error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/knowledge/{doc_id}")
async def delete_document(
    doc_id: str,
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.delete")),
):
    """删除文档"""
    kb, _ = kb_role
    success = await kb.remove_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "doc_id": doc_id}


@router.post("/knowledge/ingest")
async def ingest_jsonl(
    file: UploadFile = File(..., description="JSONL 文件"),
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """批量导入 JSONL 文件"""
    kb, _ = kb_role
    content = await file.read()

    # 安全校验：扩展名白名单 + 文件大小限制 + 文件名清理
    filename = _validate_upload_file(file, content)

    text = content.decode("utf-8")
    documents = []
    line_num = 0

    for line in text.strip().split("\n"):
        line_num += 1
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
            if "content" not in doc:
                raise ValueError(f"Line {line_num}: missing 'content'")
            documents.append(doc)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON at line {line_num}: {e}")

    if not documents:
        raise HTTPException(status_code=400, detail="No valid documents found")

    await kb.add_documents_batch(documents)
    return {"status": "ingested", "total": len(documents), "kb_size": kb.count_documents()}


@router.post("/knowledge/rebuild")
async def rebuild_index(
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """重建全部索引"""
    kb, _ = kb_role
    await kb.rebuild_index()
    return {"status": "rebuilt", "kb_size": kb.count_documents()}


@router.delete("/knowledge/all")
async def clear_knowledge(
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.delete")),
):
    """清空整个知识库"""
    kb, _ = kb_role
    await kb.clear_all()
    return {"status": "cleared"}


# ─── 知识库统计 ────────────────────────────────────

@router.get("/knowledge/stats")
async def knowledge_stats(
    kb_role: tuple = Depends(get_kb),
):
    """知识库统计信息"""
    kb, _ = kb_role
    return kb.get_stats()


# ─── 检索测试 ──────────────────────────────────────

@router.get("/knowledge/test")
async def test_retrieval(
    query: str = "",
    top_k: int = 5,
    kb_role: tuple = Depends(get_kb),
):
    """
    检索测试工具：输入问题，返回 Top-K 片段。
    用于调试 RAG 效果。
    """
    if not query.strip():
        raise HTTPException(status_code=400, detail="query 参数不能为空")

    kb, role = kb_role
    try:
        result = await kb.test_retrieval(query=query, top_k=top_k, user_role=role)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 文档权限设置 ──────────────────────────────────

@router.post("/knowledge/{doc_id}/acl")
async def set_document_acl(
    doc_id: str,
    request: SetACLRequest,
    kb_role: tuple = Depends(get_kb),
    _: str = Depends(require_permission("kb.write")),
):
    """设置文档的可见角色"""
    kb, _ = kb_role
    success = await kb.set_doc_permissions(doc_id, request.allowed_roles)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "acl_updated", "doc_id": doc_id, "allowed_roles": request.allowed_roles}
