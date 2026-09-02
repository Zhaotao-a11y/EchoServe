# -*- coding: utf-8 -*-
"""
EchoServe — Source Citation Manager (Phase 2.4)

Enhances RAG retrieval results with proper source citation metadata
and builds LLM system prompts that instruct the AI to cite sources.

Features:
    1. Enriches each retrieved document with citation info (doc_name, chunk_index, paragraph_range)
    2. Generates numbered reference list for the LLM prompt
    3. Builds a citation-aware system prompt
    4. Post-processes AI responses to normalize citation format
"""
from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger("echoserve.knowledge.citation")


# ─── Citation Builder ───────────────────────────────────

class SourceCitationManager:
    """
    Manages source citation for RAG results.

    Transforms raw retrieval results into citation-ready format
    and builds the reference list for LLM system prompts.
    """

    def __init__(self):
        self._citation_format = "[参考{idx}]"

    def enrich_results(
        self,
        retrieved_docs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Enrich retrieval results with citation metadata.

        Adds:
        - citation_id: "[参考1]" etc.
        - citation_index: 1-based index
        - source_name: document filename
        - source_location: chunk/paragraph info

        Args:
            retrieved_docs: Raw retrieval results

        Returns:
            Enriched docs with citation metadata
        """
        enriched = []
        for i, doc in enumerate(retrieved_docs):
            metadata = doc.get("metadata", {})

            # Extract source name
            source_name = metadata.get("filename", metadata.get("source", "未知来源"))
            if "#" in source_name:
                source_name = source_name.split("#")[0]

            # Build source location string
            chunk_idx = metadata.get("chunk_index", "")
            para_range = metadata.get("paragraph_range", "")

            location_parts = []
            if chunk_idx != "":
                location_parts.append(f"第{chunk_idx + 1}段")
            if para_range and isinstance(para_range, list) and len(para_range) >= 2:
                start, end = para_range
                if start == end:
                    location_parts.append(f"段落{start + 1}")
                else:
                    location_parts.append(f"段落{start + 1}-{end + 1}")

            source_location = "，".join(location_parts) if location_parts else ""

            # Build citation
            citation_idx = i + 1
            citation_id = self._citation_format.format(idx=citation_idx)

            enriched_doc = {
                **doc,
                "citation_id": citation_id,
                "citation_index": citation_idx,
                "source_name": source_name,
                "source_location": source_location,
            }

            # Update metadata
            enriched_doc["metadata"] = {
                **metadata,
                "citation_id": citation_id,
                "citation_index": citation_idx,
                "source_name": source_name,
                "source_location": source_location,
            }

            enriched.append(enriched_doc)

        return enriched

    def build_reference_list(
        self,
        enriched_docs: list[dict[str, Any]],
    ) -> str:
        """
        Build a numbered reference list for the LLM system prompt.

        Format:
            [参考1] (来源: faq.pdf, 第3段) 内容...
            [参考2] (来源: manual.docx, 第1-2段) 内容...

        Args:
            enriched_docs: Enriched retrieval results

        Returns:
            Formatted reference list string
        """
        if not enriched_docs:
            return ""

        parts = []
        for doc in enriched_docs:
            citation_id = doc.get("citation_id", "")
            source_name = doc.get("source_name", "未知来源")
            source_location = doc.get("source_location", "")
            content = doc.get("content", "")

            # Build reference line
            ref_header = f"{citation_id}"
            if source_location:
                ref_header += f" (来源: {source_name}，{source_location})"
            else:
                ref_header += f" (来源: {source_name})"

            parts.append(f"{ref_header}\n{content}")

        return "\n\n".join(parts)

    def build_citation_prompt(
        self,
        enriched_docs: list[dict[str, Any]],
    ) -> str:
        """
        Build a system prompt that instructs the AI to cite sources.

        Args:
            enriched_docs: Enriched retrieval results

        Returns:
            System prompt string with citation instructions
        """
        reference_list = self.build_reference_list(enriched_docs)

        if not reference_list:
            return (
                "你是一个专业的智能客服助手。"
                "请根据用户的问题，给出准确、简洁、有帮助的回答。"
                "如果知识库中没有相关信息，请诚实地告知用户，不要编造答案。"
                "回答时保持友好、专业的语气。"
            )

        return (
            "你是一个专业的智能客服助手。"
            "请根据用户的问题，结合下面提供的知识库参考内容，给出准确、简洁、有帮助的回答。\n\n"
            "回答要求：\n"
            "1. 优先使用知识库参考内容回答用户问题\n"
            "2. 如果知识库中没有相关信息，请诚实告知用户，不要编造答案\n"
            "3. 引用知识库内容时，请在关键信息后标注参考编号，格式如 [参考1]、[参考2]\n"
            "4. 保持友好、专业的语气\n\n"
            f"=== 知识库参考内容 ===\n"
            f"{reference_list}\n"
            f"=== 参考内容结束 ==="
        )

    def normalize_response(
        self,
        response: str,
        enriched_docs: list[dict[str, Any]],
    ) -> str:
        """
        Post-process AI response to normalize citation format.

        Ensures:
        - Citation format is consistent: [参考1], [参考2], etc.
        - No phantom citations (references to non-existent sources)
        - Citations are properly spaced

        Args:
            response: AI response text
            enriched_docs: Enriched retrieval results

        Returns:
            Normalized response text
        """
        # Valid citation indices
        valid_indices = {doc.get("citation_index", 0) for doc in enriched_docs}

        # Normalize citation format: [参考1] -> [参考1]
        # Handle various formats: [参考 1], [ref 1], [1], [来源1]
        response = re.sub(
            r'\[(?:参考|ref|来源|reference)\s*(\d+)\]',
            lambda m: f'[参考{m.group(1)}]',
            response,
            flags=re.IGNORECASE,
        )

        # Remove phantom citations (referencing non-existent sources)
        def _check_citation(match):
            idx = int(match.group(1))
            if idx in valid_indices:
                return match.group(0)
            return ""  # Remove phantom citation

        response = re.sub(r'\[参考(\d+)\]', _check_citation, response)

        # Clean up extra spaces around citations
        response = re.sub(r'\s+\[参考', ' [参考', response)
        response = re.sub(r'\[参考(\d+)\]\s+', lambda m: f'[参考{m.group(1)}] ', response)

        # Clean up double spaces
        response = re.sub(r'  +', ' ', response)

        return response.strip()


# ─── Convenience Functions ─────────────────────────────

def enhance_rag_with_citation(
    retrieved_docs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """
    One-shot: enrich docs + build citation-aware system prompt.

    Args:
        retrieved_docs: Raw retrieval results

    Returns:
        (enriched_docs, system_prompt)
    """
    manager = SourceCitationManager()
    enriched = manager.enrich_results(retrieved_docs)
    prompt = manager.build_citation_prompt(enriched)
    return enriched, prompt


def post_process_citation(
    response: str,
    retrieved_docs: list[dict[str, Any]],
) -> str:
    """
    One-shot: normalize citation format in AI response.

    Args:
        response: AI response text
        retrieved_docs: Retrieved docs (for valid citation indices)

    Returns:
        Normalized response
    """
    manager = SourceCitationManager()
    enriched = manager.enrich_results(retrieved_docs)
    return manager.normalize_response(response, enriched)
