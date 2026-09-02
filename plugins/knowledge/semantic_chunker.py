# -*- coding: utf-8 -*-
"""
EchoServe — Semantic Chunker (Phase 2.4)

Enhanced document chunking that uses semantic similarity to detect
natural topic boundaries, rather than relying solely on token count.

Strategies (layered):
    1. Paragraph/sentence boundary detection (existing, preserved)
    2. Semantic similarity scoring between adjacent sentences
    3. Dynamic boundary insertion where similarity drops sharply
    4. Overlap preservation for cross-chunk context

Usage:
    from .semantic_chunker import SemanticChunker
    chunker = SemanticChunker()
    chunks = chunker.chunk(text, source_filename="faq.pdf")

Output per chunk:
    {
        "content": str,
        "metadata": {
            "source": "faq.pdf#chunk-3",
            "chunk_index": 3,
            "paragraph_range": [2, 5],
            "token_count": 420,
            "boundary_score": 0.72,
        }
    }
"""
from __future__ import annotations

import re
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("echoserve.knowledge.semantic_chunker")

# ─── Configuration ─────────────────────────────────────

DEFAULT_TARGET_TOKENS = 600
DEFAULT_MIN_TOKENS = 100
DEFAULT_MAX_TOKENS = 1200
DEFAULT_OVERLAP_TOKENS = 80
# When adjacent sentence similarity drops below this threshold,
# insert a chunk boundary even if token budget remains.
DEFAULT_BOUNDARY_THRESHOLD = 0.5
# Minimum sentences per chunk (prevents over-fragmentation)
DEFAULT_MIN_SENTENCES_PER_CHUNK = 2


# ─── Data Models ───────────────────────────────────────

@dataclass
class Sentence:
    """A single sentence with position info"""
    text: str
    index: int           # Position in original text
    paragraph: int       # Which paragraph it belongs to
    token_estimate: int = 0
    embedding: list[float] | None = None  # Optional, for similarity-based chunking


@dataclass
class SemanticChunk:
    """A chunk produced by semantic chunking"""
    content: str
    chunk_index: int
    paragraph_range: tuple[int, int]  # (start_para, end_para)
    sentence_range: tuple[int, int]   # (start_sent_idx, end_sent_idx)
    token_count: int
    boundary_score: float = 0.0      # How distinct this chunk is from the previous
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── Token Estimation ──────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Estimate token count.
    Chinese: ~1.5 chars/token
    English: ~4 chars/token
    """
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


# ─── Semantic Chunker ───────────────────────────────────

class SemanticChunker:
    """
    Semantic-aware document chunker.

    Splits text into chunks that respect:
    1. Paragraph and sentence boundaries
    2. Semantic similarity between adjacent sentences
    3. Token budget constraints
    4. Overlap for cross-chunk context

    When sentence embeddings are not available,
    falls back to structure-based chunking (paragraph + sentence).
    """

    def __init__(
        self,
        target_tokens: int = DEFAULT_TARGET_TOKENS,
        min_tokens: int = DEFAULT_MIN_TOKENS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
        boundary_threshold: float = DEFAULT_BOUNDARY_THRESHOLD,
        min_sentences: int = DEFAULT_MIN_SENTENCES_PER_CHUNK,
    ):
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.boundary_threshold = boundary_threshold
        self.min_sentences = min_sentences

    def chunk(
        self,
        text: str,
        source: str = "",
    ) -> list[dict[str, Any]]:
        """
        Chunk text into semantic units.

        Args:
            text: Input text
            source: Source filename for metadata

        Returns:
            List of chunk dicts with content + metadata
        """
        if not text or not text.strip():
            return []

        # Step 1: Split into paragraphs
        paragraphs = self._split_paragraphs(text)
        if not paragraphs:
            return []

        # Step 2: Split paragraphs into sentences
        sentences = self._split_into_sentences(paragraphs)
        if not sentences:
            return []

        # Step 3: Compute sentence similarity (structural fallback)
        similarities = self._compute_similarities(sentences)

        # Step 4: Greedy chunking with semantic boundaries
        chunks = self._greedy_chunk(sentences, similarities)

        # Step 5: Merge small chunks
        chunks = self._merge_small(chunks)

        # Step 6: Add overlap
        chunks = self._add_overlap(chunks)

        # Step 7: Build result with metadata
        result = []
        for i, chunk in enumerate(chunks):
            chunk_meta = {
                "source": f"{source}#chunk-{i+1}" if source else f"#chunk-{i+1}",
                "chunk_index": i,
                "total_chunks": len(chunks),
                "paragraph_range": [chunk.paragraph_range[0], chunk.paragraph_range[1]],
                "sentence_range": [chunk.sentence_range[0], chunk.sentence_range[1]],
                "token_count": chunk.token_count,
                "boundary_score": round(chunk.boundary_score, 4),
            }
            result.append({
                "content": chunk.content,
                "metadata": chunk_meta,
            })

        logger.debug(
            f"SemanticChunker: {len(sentences)} sentences -> {len(result)} chunks "
            f"(avg {sum(c.token_count for c in chunks) // max(len(chunks), 1)} tokens/chunk)"
        )

        return result

    # ─── Internal Methods ────────────────────────────────

    def _split_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs"""
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        return paragraphs if paragraphs else [text.strip()]

    def _split_into_sentences(self, paragraphs: list[str]) -> list[Sentence]:
        """Split paragraphs into sentences"""
        sentences = []
        sent_idx = 0

        for para_idx, para in enumerate(paragraphs):
            # Split by sentence-ending punctuation
            pattern = r'(?<=[。！？；.!?;])\s*'
            parts = re.split(pattern, para)

            for part in parts:
                part = part.strip()
                if not part:
                    continue

                sentences.append(Sentence(
                    text=part,
                    index=sent_idx,
                    paragraph=para_idx,
                    token_estimate=estimate_tokens(part),
                ))
                sent_idx += 1

        return sentences

    def _compute_similarities(
        self,
        sentences: list[Sentence],
    ) -> list[float]:
        """
        Compute similarity between adjacent sentences.

        Without embeddings, we use structural similarity:
        - Shared word ratio (Jaccard coefficient on word sets)
        - Paragraph continuity bonus

        Returns:
            List of N-1 similarity scores (0.0-1.0)
        """
        if len(sentences) <= 1:
            return []

        similarities = []
        for i in range(len(sentences) - 1):
            s1 = sentences[i]
            s2 = sentences[i + 1]

            # Jaccard similarity on word sets
            words1 = self._tokenize(s1.text)
            words2 = self._tokenize(s2.text)

            if not words1 or not words2:
                sim = 0.3
            else:
                intersection = len(words1 & words2)
                union = len(words1 | words2)
                sim = intersection / union if union > 0 else 0.3

            # Paragraph continuity bonus
            if s1.paragraph == s2.paragraph:
                sim = min(sim + 0.2, 1.0)
            else:
                # Paragraph break reduces similarity
                sim = sim * 0.6

            similarities.append(sim)

        return similarities

    def _tokenize(self, text: str) -> set[str]:
        """
        Simple tokenization for similarity comparison.
        Chinese: character-level bigrams
        English: word-level tokens
        """
        tokens = set()

        # Chinese character bigrams
        chinese_chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
        for i in range(len(chinese_chars) - 1):
            tokens.add(chinese_chars[i] + chinese_chars[i + 1])

        # English words
        english_words = re.findall(r'[a-zA-Z]{2,}', text.lower())
        tokens.update(english_words)

        return tokens

    def _greedy_chunk(
        self,
        sentences: list[Sentence],
        similarities: list[float],
    ) -> list[SemanticChunk]:
        """
        Greedy chunking: accumulate sentences until token budget or
        semantic boundary is hit.
        """
        if not sentences:
            return []

        chunks = []
        current_sents: list[Sentence] = []
        current_tokens = 0
        current_start_para = sentences[0].paragraph
        chunk_idx = 0

        for i, sent in enumerate(sentences):
            # Check if we should start a new chunk
            should_break = False

            # Condition 1: Token budget exceeded
            if current_tokens + sent.token_estimate > self.target_tokens and current_sents:
                should_break = True

            # Condition 2: Hard max tokens
            if current_tokens + sent.token_estimate > self.max_tokens:
                should_break = True

            # Condition 3: Semantic boundary (similarity drops sharply)
            if i > 0 and i - 1 < len(similarities):
                sim = similarities[i - 1]
                if sim < self.boundary_threshold and current_tokens >= self.min_tokens:
                    # Only break if we have enough sentences
                    if len(current_sents) >= self.min_sentences:
                        should_break = True

            # Condition 4: Paragraph change (strong boundary)
            if current_sents and sent.paragraph != current_sents[-1].paragraph:
                # Paragraph change is a moderate boundary
                if current_tokens >= self.target_tokens * 0.7:
                    should_break = True

            if should_break and current_sents:
                # Create chunk from accumulated sentences
                chunk = self._build_chunk(current_sents, chunk_idx)
                chunks.append(chunk)
                chunk_idx += 1
                current_sents = []
                current_tokens = 0

            current_sents.append(sent)
            current_tokens += sent.token_estimate

        # Last chunk
        if current_sents:
            chunk = self._build_chunk(current_sents, chunk_idx)
            chunks.append(chunk)

        # Compute boundary scores
        for i in range(1, len(chunks)):
            # Boundary score = inverse of similarity at the split point
            # (higher score = more distinct boundary)
            if i - 1 < len(similarities):
                chunks[i].boundary_score = 1.0 - similarities[i - 1]
            else:
                chunks[i].boundary_score = 0.5

        if chunks:
            chunks[0].boundary_score = 1.0  # First chunk has no prior boundary

        return chunks

    def _build_chunk(self, sentences: list[Sentence], chunk_idx: int) -> SemanticChunk:
        """Build a SemanticChunk from a list of sentences"""
        content = " ".join(s.text for s in sentences)
        # For Chinese text, join without space
        if any('\u4e00' <= c <= '\u9fff' for c in content):
            content = "".join(s.text for s in sentences)

        return SemanticChunk(
            content=content.strip(),
            chunk_index=chunk_idx,
            paragraph_range=(sentences[0].paragraph, sentences[-1].paragraph),
            sentence_range=(sentences[0].index, sentences[-1].index),
            token_count=estimate_tokens(content),
        )

    def _merge_small(self, chunks: list[SemanticChunk]) -> list[SemanticChunk]:
        """Merge chunks that are too small"""
        if len(chunks) <= 1:
            return chunks

        result = []
        current = chunks[0]

        for next_chunk in chunks[1:]:
            if current.token_count < self.min_tokens:
                # Merge with next
                current = self._merge_two(current, next_chunk)
            else:
                result.append(current)
                current = next_chunk

        result.append(current)

        # Re-index
        for i, c in enumerate(result):
            c.chunk_index = i

        return result

    def _merge_two(self, a: SemanticChunk, b: SemanticChunk) -> SemanticChunk:
        """Merge two chunks"""
        content = a.content + "\n\n" + b.content
        return SemanticChunk(
            content=content,
            chunk_index=a.chunk_index,
            paragraph_range=(a.paragraph_range[0], b.paragraph_range[1]),
            sentence_range=(a.sentence_range[0], b.sentence_range[1]),
            token_count=estimate_tokens(content),
            boundary_score=a.boundary_score,
        )

    def _add_overlap(self, chunks: list[SemanticChunk]) -> list[SemanticChunk]:
        """Add overlap text to each chunk (from the end of the previous chunk)"""
        if len(chunks) <= 1 or self.overlap_tokens <= 0:
            return chunks

        overlap_chars = int(self.overlap_tokens * 1.5)  # Rough estimate

        for i in range(1, len(chunks)):
            prev_content = chunks[i - 1].content
            if len(prev_content) <= overlap_chars:
                overlap = prev_content
            else:
                overlap = prev_content[-overlap_chars:]

            # Prepend overlap
            chunks[i].content = overlap + "\n\n" + chunks[i].content
            chunks[i].token_count = estimate_tokens(chunks[i].content)

        return chunks


# ─── Convenience Functions ─────────────────────────────

def chunk_document(
    text: str,
    source: str = "",
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[dict[str, Any]]:
    """
    Convenience function: chunk a document semantically.

    Args:
        text: Document text
        source: Source filename
        target_tokens: Target tokens per chunk
        overlap_tokens: Overlap tokens between chunks

    Returns:
        List of chunk dicts with content + metadata
    """
    chunker = SemanticChunker(
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
    )
    return chunker.chunk(text, source=source)


def compute_content_hash(content: str) -> str:
    """Compute a content hash for change detection"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
