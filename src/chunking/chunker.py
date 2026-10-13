from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from src.config import CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS, MIN_CHUNK_TOKENS


@dataclass
class ChunkConfig:
    size_tokens: int = CHUNK_SIZE_TOKENS
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS
    min_tokens: int = MIN_CHUNK_TOKENS


class TokenAwareChunker:
    """Structure-aware token chunking with controlled overlap."""

    def __init__(self, tokenizer, config: ChunkConfig | None = None):
        self.tokenizer = tokenizer
        self.config = config or ChunkConfig()
        if self.config.overlap_tokens >= self.config.size_tokens:
            raise ValueError("overlap_tokens must be smaller than size_tokens")

    def _tokens(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False)

    def _decode(self, token_ids: list[int]) -> str:
        return self.tokenizer.decode(token_ids, skip_special_tokens=True).strip()

    def _split_long_text(self, text: str) -> list[str]:
        ids = self._tokens(text)
        if len(ids) <= self.config.size_tokens:
            return [text.strip()]

        step = self.config.size_tokens - self.config.overlap_tokens
        pieces: list[str] = []
        start = 0
        while start < len(ids):
            end = min(start + self.config.size_tokens, len(ids))
            piece = self._decode(ids[start:end])
            if piece:
                pieces.append(piece)
            if end >= len(ids):
                break
            start += step
        return pieces

    def chunk(self, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build chunks from semantic units while enforcing a token cap and overlap."""
        chunks: list[dict[str, Any]] = []
        current_text = ""
        current_ids: list[int] = []
        current_meta: list[dict[str, Any]] = []

        def emit_current():
            nonlocal current_text, current_ids, current_meta
            if not current_text.strip():
                return
            if len(current_ids) >= self.config.min_tokens:
                chunks.append(self._make_chunk(current_text.strip(), current_meta, len(chunks)))
            current_text, current_ids, current_meta = "", [], []

        for unit in units:
            text = (unit.get("text") or "").strip()
            if not text:
                continue
            ids = self._tokens(text)

            # A single semantic unit is larger than the target: split it with overlap.
            if len(ids) > self.config.size_tokens:
                emit_current()
                step = self.config.size_tokens - self.config.overlap_tokens
                start = 0
                while start < len(ids):
                    end = min(start + self.config.size_tokens, len(ids))
                    piece = self._decode(ids[start:end])
                    if piece and len(self._tokens(piece)) >= self.config.min_tokens:
                        meta = dict(unit)
                        meta["text"] = piece
                        chunks.append(self._make_chunk(piece, [meta], len(chunks)))
                    if end >= len(ids):
                        break
                    start += step
                continue

            if not current_text:
                current_text = text
                current_ids = ids
                current_meta = [unit]
                continue

            tentative_ids = self._tokens(current_text + "\n\n" + text)
            if len(tentative_ids) <= self.config.size_tokens:
                current_text += "\n\n" + text
                current_ids = tentative_ids
                current_meta.append(unit)
                continue

            # Emit current chunk, then seed the next chunk with overlap from it.
            previous_ids = current_ids
            previous_meta = list(current_meta)
            emit_current()

            available = max(0, self.config.size_tokens - len(ids))
            keep = min(self.config.overlap_tokens, available)
            overlap_text = self._decode(previous_ids[-keep:]) if keep > 0 else ""
            if overlap_text:
                current_text = overlap_text + "\n\n" + text
                current_ids = self._tokens(current_text)
                current_meta = [
                    {
                        "source": previous_meta[-1].get("source"),
                        "page_start": previous_meta[-1].get("page_start"),
                        "page_end": previous_meta[-1].get("page_end"),
                        "is_overlap": True,
                    },
                    unit,
                ]
            else:
                current_text = text
                current_ids = ids
                current_meta = [unit]

        emit_current()
        return chunks

    @staticmethod
    def _make_chunk(text: str, metas: list[dict[str, Any]], idx: int) -> dict[str, Any]:
        real_metas = [m for m in metas if not m.get("is_overlap")]
        if not real_metas:
            real_metas = metas
        pages = [m.get("page") for m in real_metas if m.get("page") is not None]
        page_starts = [m.get("page_start") for m in real_metas if m.get("page_start") is not None]
        page_ends = [m.get("page_end") for m in real_metas if m.get("page_end") is not None]
        source = next((m.get("source") for m in real_metas if m.get("source")), None)
        source_type = next((m.get("source_type") for m in real_metas if m.get("source_type")), None)
        sheet = next((m.get("sheet") for m in real_metas if m.get("sheet")), None)
        row_start = next((m.get("row_start") for m in real_metas if m.get("row_start") is not None), None)
        row_end = next((m.get("row_end") for m in reversed(real_metas) if m.get("row_end") is not None), None)
        metadata = {
            "source": source,
            "source_type": source_type,
            "page_start": min(pages) if pages else (min(page_starts) if page_starts else None),
            "page_end": max(pages) if pages else (max(page_ends) if page_ends else None),
            "pages": sorted(set(pages)),
            "sheet": sheet,
            "row_start": row_start,
            "row_end": row_end,
            "chunk_index": idx,
            "chunk_size_target_tokens": CHUNK_SIZE_TOKENS,
            "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
        }
        return {"text": text, "metadata": metadata}
