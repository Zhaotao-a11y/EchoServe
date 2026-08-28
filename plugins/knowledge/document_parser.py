"""
EchoServe V0.1.0 — 文档解析器

支持格式：PDF / DOCX / MD / TXT
功能：格式解析 → 智能切片（500-800 token，重叠50-100 token）→ 元数据提取
特殊功能：Markdown 表格自动识别（客服问答数据逐条提取）
"""
from __future__ import annotations

import re
import json
import hashlib
import logging
from typing import Any
from pathlib import Path

logger = logging.getLogger("echoserve.knowledge.parser")

# ─── 切片配置 ─────────────────────────────────────────────

DEFAULT_CHUNK_TOKENS = 600       # 每块目标 token 数
DEFAULT_CHUNK_OVERLAP = 80      # 块间重叠 token 数
MIN_CHUNK_TOKENS = 100           # 最小块大小
MAX_CHUNK_TOKENS = 1000          # 最大块大小

# 粗略 token 估算：中文约 1.5 字符/token，英文约 4 字符/token
def estimate_tokens(text: str) -> int:
    """粗略估算 token 数"""
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def chunk_text(
    text: str,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """
    将文本按 token 数智能切片。
    优先按段落/句子切分，避免切断语义。
    """
    if not text.strip():
        return []

    # 先按段落分割
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks = []
    current_chunk = ""
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        # 如果单个段落就超过最大块大小，需要进一步拆分
        if para_tokens > MAX_CHUNK_TOKENS:
            # 先保存当前累积的块
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
                current_tokens = 0

            # 按句子拆分大段落
            sentences = _split_sentences(para)
            for sent in sentences:
                sent_tokens = estimate_tokens(sent)
                if current_tokens + sent_tokens > chunk_tokens and current_chunk:
                    chunks.append(current_chunk.strip())
                    # 保留重叠部分
                    overlap_text = _get_overlap(current_chunk, overlap_tokens)
                    current_chunk = overlap_text + sent
                    current_tokens = estimate_tokens(current_chunk)
                else:
                    current_chunk += " " + sent if current_chunk else sent
                    current_tokens += sent_tokens
            continue

        # 正常段落处理
        if current_tokens + para_tokens > chunk_tokens and current_chunk:
            chunks.append(current_chunk.strip())
            # 保留重叠部分
            overlap_text = _get_overlap(current_chunk, overlap_tokens)
            current_chunk = overlap_text + "\n\n" + para
            current_tokens = estimate_tokens(current_chunk)
        else:
            current_chunk += "\n\n" + para if current_chunk else para
            current_tokens += para_tokens

    # 保存最后一块
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # 合并过小的块
    return _merge_small_chunks(chunks)


def _split_sentences(text: str) -> list[str]:
    """按句子切分（支持中英文标点）"""
    # 中文句子结束符：。！？；
    # 英文句子结束符：. ! ? ;
    pattern = r'(?<=[。！？；.!?;])\s*'
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def _get_overlap(text: str, overlap_tokens: int) -> str:
    """获取块末尾的重叠文本"""
    # 简单策略：取最后 N 个字符（粗略估计）
    overlap_chars = int(overlap_tokens * 1.5)  # 中文字符约 1.5 字符/token
    if len(text) <= overlap_chars:
        return text
    return text[-overlap_chars:]


def _merge_small_chunks(chunks: list[str]) -> list[str]:
    """合并过小的块"""
    if not chunks:
        return chunks

    result = []
    current = chunks[0]

    for chunk in chunks[1:]:
        if estimate_tokens(current) < MIN_CHUNK_TOKENS:
            current += "\n\n" + chunk
        else:
            result.append(current)
            current = chunk

    result.append(current)
    return result


# ─── 文档解析 ───────────────────────────────────────────

def _read_text_file(file_path: str) -> str:
    """读取文本文件（自动检测编码）"""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read().strip()
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


def parse_text(file_path: str) -> str:
    """解析纯文本文件"""
    return _read_text_file(file_path)


def parse_markdown(file_path: str) -> str:
    """解析 Markdown 文件（直接读取，保留结构）"""
    return _read_text_file(file_path)


def parse_markdown_table(file_path: str) -> list[dict[str, Any]]:
    """
    解析 Markdown 表格文件，提取结构化问答数据。
    
    自动检测文件编码（UTF-8 / GBK / GB2312）。
    识别标志：包含 | 分隔的表格头，且有 query + expected_reply 列。
    返回：list[{"content": str, "metadata": dict}]
    
    支持文件中有多个表格，会合并所有匹配的数据。
    适用场景：客服问答模板、FAQ 清单等结构化 .md 表格。
    """
    text = parse_markdown(file_path)
    lines = [l for l in text.split("\n")]
    
    all_documents = []
    
    # 遍历所有行，查找所有表格
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # 查找表格分隔行（|--+--|）
        if not (line.startswith("|") and "---" in line):
            i += 1
            continue
        
        # 找到分隔行，向前查找表头
        header_idx = i - 1
        if header_idx < 0:
            i += 1
            continue
        
        header_line = lines[header_idx].strip()
        if not header_line.startswith("|"):
            i += 1
            continue
        
        # 解析表头
        headers = [h.strip() for h in header_line.strip("|").split("|")]
        headers = [h for h in headers if h]  # 过滤空列
        
        if not headers:
            i += 1
            continue
        
        # 查找关键列位置
        query_col = None
        reply_col = None
        intent_col = None
        domain_col = None
        sub_col = None
        
        for idx, h in enumerate(headers):
            h_lower = h.lower()
            if h_lower in ("query", "question", "问题", "query_text", "user_query"):
                query_col = idx
            elif h_lower in ("expected_reply", "answer", "回复", "答案", "reply", "response", "expected_answer"):
                reply_col = idx
            elif h_lower in ("intent", "意图", "意图标签"):
                intent_col = idx
            elif h_lower in ("domain", "领域", "一级领域", "category"):
                domain_col = idx
            elif h_lower in ("sub", "子类", "sub_intent", "二级领域", "sub_domain"):
                sub_col = idx
        
        # 如果没有 query + expected_reply 列，则不是客服数据表
        if query_col is None or reply_col is None:
            i += 1
            continue
        
        # 解析数据行（从分隔行下一行开始）
        i += 1
        while i < len(lines):
            line = lines[i].strip()
            
            # 表格结束：空行或非 | 开头
            if not line or not line.startswith("|"):
                break
            
            # 忽略分隔行
            if "---" in line and line.replace("|", "").replace("-", "").replace(" ", "") == "":
                i += 1
                continue
            
            cells = [c.strip() for c in line.strip("|").split("|")]
            # 保留空单元格以维持索引对齐
            # cells = [c for c in cells if c]  ← 不要过滤空列，会破坏索引
            
            # 确保列数足够
            max_col = max([query_col, reply_col] + 
                         ([intent_col] if intent_col is not None else []) +
                         ([domain_col] if domain_col is not None else []) +
                         ([sub_col] if sub_col is not None else []))
            
            if len(cells) <= max_col:
                i += 1
                continue
            
            query = cells[query_col] if query_col < len(cells) else ""
            reply = cells[reply_col] if reply_col < len(cells) else ""
            
            # 忽略空数据行
            if not query or not reply:
                i += 1
                continue
            
            # 组装 content
            content = f"问题：{query}\n回答：{reply}"
            
            # 组装 metadata
            metadata = {
                "source": "customer_service_table",
                "table_header": ",".join(headers[:5]),
            }
            
            if intent_col is not None and intent_col < len(cells):
                val = cells[intent_col].strip()
                if val:
                    metadata["intent"] = val
            if domain_col is not None and domain_col < len(cells):
                val = cells[domain_col].strip()
                if val:
                    metadata["domain"] = val
            if sub_col is not None and sub_col < len(cells):
                val = cells[sub_col].strip()
                if val:
                    metadata["sub"] = val
            
            all_documents.append({
                "content": content,
                "metadata": metadata,
            })
            
            i += 1
        
        # 继续查找下一个表格
        continue
    
    if all_documents:
        logger.info(f"[document_parser] Parsed {len(all_documents)} Q&A pairs from {file_path}")
    
    return all_documents


# ─── PDF / DOCX 解析（按需导入，减少启动开销）───

def parse_pdf(file_path: str) -> str:
    """解析 PDF 文件"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        text = "\n\n".join(page.get_text() for page in doc)
        doc.close()
        return text.strip()
    except ImportError:
        logger.error("PyMuPDF not installed. Run: pip install PyMuPDF")
        raise
    except Exception as e:
        logger.error(f"PDF parse error: {e}")
        raise


def parse_docx(file_path: str) -> str:
    """解析 DOCX 文件"""
    try:
        from docx import Document
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()
    except ImportError:
        logger.error("python-docx not installed. Run: pip install python-docx")
        raise
    except Exception as e:
        logger.error(f"DOCX parse error: {e}")
        raise


def parse_file(file_path: str) -> dict[str, Any]:
    """
    通用文档解析入口。
    
    返回：{
        "chunks": list[str],       # 切片列表
        "metadata": {              # 文件元数据
            "filetype": str,
            "size": int,
            "file_hash": str,
        }
    }
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    file_size = path.stat().st_size
    
    # 计算文件哈希
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    file_hash = sha256.hexdigest()[:16]
    
    # 解析文本
    if suffix == ".pdf":
        text = parse_pdf(file_path)
        filetype = "pdf"
    elif suffix in (".docx", ".doc"):
        text = parse_docx(file_path)
        filetype = "docx"
    elif suffix in (".md", ".markdown"):
        text = parse_markdown(file_path)
        filetype = "markdown"
    elif suffix in (".txt", ".text"):
        text = parse_text(file_path)
        filetype = "text"
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    
    # 切片
    chunks = chunk_text(text)
    
    return {
        "chunks": chunks,
        "metadata": {
            "filetype": filetype,
            "size": file_size,
            "file_hash": file_hash,
        },
    }
