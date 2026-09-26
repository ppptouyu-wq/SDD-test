"""文本切分：纯函数，不涉及 IO。

契约来源：specs/tasks.md Task 3
    - chunk_text() 按长度切分且保留重叠，chunk_id 形如 doc_id#序号
    - 空文本不产生任何片段（proposal.md §3.3）
"""

from __future__ import annotations

from kb_search.models import Chunk, Document

DEFAULT_CHUNK_SIZE = 400
DEFAULT_OVERLAP = 80


def chunk_text(
    text: str,
    *,
    doc_id: str,
    title: str = "",
    url: str = "",
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """把正文切分为带重叠的片段。

    切分优先落在段落边界（换行）上，避免把一个句子劈成两半影响语义。
    重叠用于缓解"关键信息恰好落在切分点"导致的召回丢失。

    :raises ValueError: size 非正数，或 overlap 大于等于 size
    """
    if size <= 0:
        raise ValueError("size 必须为正数")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap 必须满足 0 <= overlap < size")

    normalized = (text or "").strip()
    if not normalized:
        return []  # 空正文不写入空片段

    chunks: list[Chunk] = []
    start = 0
    position = 0
    length = len(normalized)

    while start < length:
        end = min(start + size, length)

        # 尽量在段落/句子边界收尾
        if end < length:
            window = normalized[start:end]
            for boundary in ("\n\n", "\n", "。", "；", ". "):
                idx = window.rfind(boundary)
                if idx > size // 2:  # 边界不能太靠前，否则片段过短
                    end = start + idx + len(boundary)
                    break

        piece = normalized[start:end].strip()
        if piece:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#{position}",
                    doc_id=doc_id,
                    title=title,
                    url=url,
                    text=piece,
                    position=position,
                )
            )
            position += 1

        if end >= length:
            break
        start = max(end - overlap, start + 1)  # 保证前进，避免死循环

    return chunks


def chunk_document(document: Document, **kwargs) -> list[Chunk]:
    """按文档元信息切分。"""
    return chunk_text(
        document.content,
        doc_id=document.doc_id,
        title=document.title,
        url=document.url,
        **kwargs,
    )
