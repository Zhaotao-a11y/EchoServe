"""
EchoServe V0.1.0 — 知识库管理插件（P0 增强版）

功能：
  - 文档上传（PDF/DOCX/MD/TXT，单文件 <=50MB）
  - 格式解析 + 智能切片（500-800 token，重叠50-100）
  - 双写索引（Chroma 向量 + BM25 词面）
  - 权限标记（文档级 ACL）
  - 检索测试工具
  - 知识库统计
"""
from __future__ import annotations

import json
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

logger = logging.getLogger("echoserve.knowledge")

# 最大文件大小：50MB
MAX_FILE_SIZE = 50 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt", ".text"}


class KnowledgePlugin(BaizePlugin):
    """知识库管理插件"""

    plugin_id = "core.knowledge"
    plugin_name = "知识库管理"
    plugin_version = "0.2.0"
    dependencies = ["core.retriever"]

    def __init__(self):
        self.documents: dict[str, dict[str, Any]] = {}
        self._storage_path: (Path | None) = None
        self._upload_dir: (Path | None) = None
        self._pending_index: (list[Dict] | None) = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # ─── 生命周期 ─────────────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        settings = ctx.settings
        data_dir = Path(settings.root_dir) / "data" / "knowledge"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._storage_path = data_dir / "documents.jsonl"
        self._upload_dir = data_dir / "uploads"
        self._upload_dir.mkdir(exist_ok=True)

        await self._load_from_disk()

        self.provide("knowledge_base", self)

        logger.info(
            f"[{self.plugin_id}] Initialized "
            f"({len(self.documents)} docs loaded)"
        )

    async def on_start(self, ctx: BaizeContext, fiber: Fiber):
        """启动后执行延迟的索引构建"""
        if self._pending_index:
            retriever = self.inject("retriever")
            if retriever:
                retriever.add_documents(self._pending_index)
                logger.info(
                    f"[{self.plugin_id}] Pending index applied "
                    f"({len(self._pending_index)} docs)"
                )
            self._pending_index = None

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        await self._save_to_disk()
        logger.info(f"[{self.plugin_id}] Destroyed")

    # ─── 文档上传与解析 ──────────────────────────────────

    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        metadata: (dict[str, Any] | None) = None,
        allowed_roles: (list[str] | None) = None,
    ) -> dict[str, Any]:
        """
        上传并解析文档。
        流程：保存文件 → 解析 → 切片 → 双写索引 → 持久化
        
        特殊处理：对于 Markdown 表格（如客服问答数据），
        自动逐条提取为独立文档，而非整体切片。
        """
        # 1. 校验文件大小
        if len(file_content) > MAX_FILE_SIZE:
            raise ValueError(f"文件过大: {len(file_content)} > {MAX_FILE_SIZE} bytes")

        # 2. 校验扩展名
        suffix = Path(filename).suffix.lower()
        logger.info(f"[{self.plugin_id}] Upload file: {filename}, suffix={suffix}")
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"不支持的文件类型: {suffix}，支持: {SUPPORTED_EXTENSIONS}"
            )

        # 3. 保存原始文件（防止路径穿越：仅取文件名部分）
        file_id = str(uuid.uuid4())
        safe_filename = Path(filename).name
        if safe_filename != filename:
            logger.warning(f"Filename sanitized for security: {filename!r} -> {safe_filename!r}")
        safe_name = f"{file_id}_{safe_filename}"
        file_path = self._upload_dir / safe_name
        with open(file_path, "wb") as f:
            f.write(file_content)

        try:
            # ─── 特殊处理：Markdown 表格（客服问答等结构化数据）───
            if suffix in (".md", ".markdown"):
                from plugins.knowledge.document_parser import parse_markdown_table
                table_docs = parse_markdown_table(str(file_path))
                
                if table_docs:
                    # 识别为结构化表格数据，逐条导入
                    logger.info(
                        f"[{self.plugin_id}] Detected markdown table with {len(table_docs)} rows, "
                        f"importing as individual documents"
                    )
                    
                    doc_ids = []
                    for i, doc_data in enumerate(table_docs):
                        doc_metadata = {
                            "filename": filename,
                            "filetype": "markdown_table",
                            "upload_time": datetime.now(timezone.utc).isoformat(),
                            "allowed_roles": allowed_roles or ["*"],
                            "table_row": i + 1,
                            **(metadata or {}),
                            **(doc_data.get("metadata", {})),
                        }
                        doc_id = await self.add_document(
                            content=doc_data["content"],
                            doc_id=f"{file_id}_row_{i+1}",
                            metadata=doc_metadata,
                        )
                        doc_ids.append(doc_id)
                    
                    return {
                        "total_chunks": len(table_docs),
                        "doc_ids": doc_ids,
                        "metadata": {
                            "filename": filename,
                            "filetype": "markdown_table",
                            "table_rows": len(table_docs),
                            "import_mode": "row_by_row",
                        },
                    }
            
            # ─── 常规文档处理（PDF/DOCX/TXT/普通MD）───
            # 4. 解析文档
            from plugins.knowledge.document_parser import parse_file
            parsed = parse_file(str(file_path))

            # 5. 为每个切片创建文档
            chunks = parsed["chunks"]
            base_metadata = {
                "filename": filename,
                "filetype": parsed["metadata"]["filetype"],
                "file_size": parsed["metadata"]["size"],
                "file_hash": parsed["metadata"]["file_hash"],
                "upload_time": datetime.now(timezone.utc).isoformat(),
                "allowed_roles": allowed_roles or ["*"],  # * 表示所有人可见
                **(metadata or {}),
            }

            doc_ids = []
            for i, chunk in enumerate(chunks):
                chunk_metadata = {
                    **base_metadata,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "source": f"{filename}#chunk-{i+1}",
                }
                doc_id = await self.add_document(
                    content=chunk,
                    doc_id=f"{file_id}_chunk_{i}",
                    metadata=chunk_metadata,
                )
                doc_ids.append(doc_id)

            return {
                "total_chunks": len(chunks),
                "doc_ids": doc_ids,
                "metadata": parsed["metadata"],
            }

        except Exception as e:
            # 清理临时文件
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass
            logger.error(f"[{self.plugin_id}] Upload failed: {e}")
            raise

    # ─── 文档管理 API ────────────────────────────────────

    async def add_document(
        self,
        content: str,
        doc_id: (str | None) = None,
        metadata: (dict[str, Any] | None) = None,
    ) -> str:
        """添加单条文档"""
        if not doc_id:
            doc_id = str(uuid.uuid4())

        doc = {
            "id": doc_id,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        async with self._lock:
            self.documents[doc_id] = doc

            # 实时索引（retriever 可能未注册，优雅降级）
            try:
                retriever = self.inject("retriever", None)
                if retriever:
                    retriever.add_documents([doc])
            except KeyError:
                pass

            # 持久化
            await self._append_to_disk(doc)

        return doc_id

    async def add_documents_batch(self, documents: list[dict[str, Any]]):
        """批量添加文档"""
        for doc in documents:
            doc_id = doc.get("id") or doc.get("doc_id")
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            await self.add_document(content=content, doc_id=doc_id, metadata=metadata)

    async def remove_document(self, doc_id: str) -> bool:
        """删除文档"""
        async with self._lock:
            if doc_id not in self.documents:
                return False

            del self.documents[doc_id]

            # 触发索引重建
            await self._rebuild_index()

        logger.info(f"[{self.plugin_id}] Removed doc: {doc_id}")
        return True

    async def update_document(
        self,
        doc_id: str,
        content: (str | None) = None,
        metadata: (dict[str, Any] | None) = None,
    ) -> bool:
        """更新文档"""
        async with self._lock:
            if doc_id not in self.documents:
                return False

            doc = self.documents[doc_id]
            if content is not None:
                doc["content"] = content
            if metadata is not None:
                doc["metadata"].update(metadata)
            doc["updated_at"] = datetime.now(timezone.utc).isoformat()

            await self._rebuild_index()

        logger.info(f"[{self.plugin_id}] Updated doc: {doc_id}")
        return True

    def get_document(self, doc_id: str) -> (dict[str, Any] | None):
        return self.documents.get(doc_id)

    def list_documents(
        self,
        offset: int = 0,
        limit: int = 50,
        user_role: str = "*",
    ) -> list[dict[str, Any]]:
        """分页列出文档（按权限过滤）"""
        docs = list(self.documents.values())

        # 权限过滤
        if user_role != "*":
            docs = [
                d for d in docs
                if self._check_doc_access(d, user_role)
            ]

        total = len(docs)
        paged = docs[offset:offset + limit]

        return [
            {
                "id": d["id"],
                "metadata": d.get("metadata", {}),
                "content_preview": d["content"][:200],
                "content_length": len(d["content"]),
                "created_at": d.get("created_at"),
            }
            for d in paged
        ]

    def count_documents(self) -> int:
        return len(self.documents)

    def get_all_documents(self) -> list[dict[str, Any]]:
        """获取所有文档列表"""
        return [
            {
                "id": d["id"],
                "content": d["content"],
                "metadata": d.get("metadata", {}),
            }
            for d in self.documents.values()
        ]

    def get_all_qa_pairs(self) -> list[dict[str, str]]:
        """
        从知识库提取 QA 对（适用于客服问答数据）。
        解析文档内容中的 "问题：xxx\n回答：xxx" 格式。
        """
        import re
        qa_pairs = []

        for doc in self.documents.values():
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})

            # 方式1：从内容解析 "问题：xxx\n回答：xxx"
            q_match = re.search(r'问题[:：]\s*(.+?)(?:\n|$)', content, re.DOTALL)
            a_match = re.search(r'回答[:：]\s*(.+?)(?:\n|$)', content, re.DOTALL)

            if q_match and a_match:
                q = q_match.group(1).strip()
                a = a_match.group(1).strip()
                if q and a:
                    qa_pairs.append({"question": q, "answer": a})
                    continue

            # 方式2：从 metadata 中的 intent/sub 组合成问题
            intent = metadata.get("intent", "")
            sub = metadata.get("sub", "")
            if intent and sub:
                question = f"{intent} - {sub}"
                answer = content
                qa_pairs.append({"question": question, "answer": answer})
                continue

            # 方式3：直接以内容作为问题+答案（兜底）
            if content.strip():
                lines = content.strip().split('\n', 1)
                if len(lines) >= 2:
                    qa_pairs.append({
                        "question": lines[0][:200],
                        "answer": '\n'.join(lines[1:])[:2000],
                    })
                else:
                    qa_pairs.append({
                        "question": content[:200],
                        "answer": content[:2000],
                    })

        logger.info(f"[KnowledgePlugin] Extracted {len(qa_pairs)} QA pairs from {len(self.documents)} documents")
        return qa_pairs

    def get_stats(self) -> dict[str, Any]:
        """知识库统计"""
        total_chars = sum(len(d["content"]) for d in self.documents.values())
        file_types: dict[str, int] = {}
        for d in self.documents.values():
            ft = d.get("metadata", {}).get("filetype", "unknown")
            file_types[ft] = file_types.get(ft, 0) + 1

        return {
            "total_documents": len(self.documents),
            "total_characters": total_chars,
            "total_files": len(set(
                d.get("metadata", {}).get("file_hash", "")
                for d in self.documents.values()
            )),
            "file_types": file_types,
            "storage_path": str(self._upload_dir) if self._upload_dir else "",
        }

    async def clear_all(self):
        """清空知识库"""
        self.documents.clear()
        try:
            retriever = self.inject("retriever", None)
            if retriever:
                retriever.clear()
        except KeyError:
            pass
        if self._storage_path and self._storage_path.exists():
            self._storage_path.unlink()
        # 清理上传文件
        if self._upload_dir:
            for f in self._upload_dir.glob("*"):
                if f.is_file():
                    f.unlink()
        logger.info(f"[{self.plugin_id}] Knowledge base cleared")

    async def rebuild_index(self):
        await self._rebuild_index()

    # ─── 检索测试 ────────────────────────────────────────

    async def test_retrieval(
        self,
        query: str,
        top_k: int = 5,
        user_role: str = "*",
    ) -> dict[str, Any]:
        """检索测试工具：输入问题，返回 Top-K 片段"""
        retriever = self.inject("retriever")
        if not retriever:
            raise RuntimeError("检索引擎未就绪")

        # 检索（retriever 不做 ACL 过滤，由知识层负责）
        results = await retriever.retrieve(query, top_k=top_k)

        # 知识层 ACL 过滤
        if user_role != "*":
            results = [
                r for r in results
                if self._check_doc_access(
                    {"metadata": r.get("metadata", {})},
                    user_role,
                )
            ]

        return {
            "query": query,
            "top_k": top_k,
            "results": [
                {
                    "rank": i + 1,
                    "doc_id": r.get("id", ""),
                    "score": r.get("score", 0),
                    "content": r.get("content", "")[:500],
                    "metadata": r.get("metadata", {}),
                    "source": r.get("metadata", {}).get("source", ""),
                }
                for i, r in enumerate(results)
            ],
        }

    # ─── 权限控制 ────────────────────────────────────────

    def _check_doc_access(self, doc: dict[str, Any], user_role: str) -> bool:
        """检查用户角色是否可访问该文档"""
        allowed = doc.get("metadata", {}).get("allowed_roles", ["*"])
        return "*" in allowed or user_role in allowed

    async def set_doc_permissions(
        self, doc_id: str, allowed_roles: list[str]
    ) -> bool:
        """设置文档的可见角色"""
        if doc_id not in self.documents:
            return False
        self.documents[doc_id]["metadata"]["allowed_roles"] = allowed_roles
        await self._save_to_disk()
        logger.info(f"[{self.plugin_id}] Permissions updated: {doc_id} -> {allowed_roles}")
        return True

    # ─── 内部方法 ────────────────────────────────────────

    async def _rebuild_index(self):
        """全量重建索引"""
        documents = list(self.documents.values())
        try:
            retriever = self.inject("retriever", None)
        except KeyError:
            retriever = None

        if not documents:
            if retriever:
                retriever.clear()
            return

        if retriever:
            retriever.clear()
            retriever.add_documents(documents)
            logger.info(f"[{self.plugin_id}] Index rebuilt ({len(documents)} docs)")

        await self._save_to_disk()

    async def _load_from_disk(self):
        if not self._storage_path or not self._storage_path.exists():
            return

        documents = []
        with open(self._storage_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                    self.documents[doc["id"]] = doc
                    documents.append(doc)
                except json.JSONDecodeError:
                    logger.warning(f"[{self.plugin_id}] Skipped invalid JSON line")

        if documents:
            self._pending_index = documents
            logger.info(f"[{self.plugin_id}] Pending index: {len(documents)} docs")

    async def _save_to_disk(self):
        if not self._storage_path:
            return
        with open(self._storage_path, "w", encoding="utf-8") as f:
            for doc in self.documents.values():
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    async def _append_to_disk(self, doc: dict[str, Any]):
        if not self._storage_path:
            return
        with open(self._storage_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
