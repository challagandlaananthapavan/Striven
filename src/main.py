from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

from src.config import (
    ensure_dirs, PDF_DIR, EXCEL_DIR, CSV_DIR, IMAGE_DIR,
    TEXT_DIR, CHUNKS_DIR, REPORT_DIR,
    CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS,
)
from src.ocr.paddle_ocr_processor import PaddleOCRProcessor
from src.pdf_processor import PDFProcessor
from src.tabular_processor import process_excel, process_csv
from src.chunking.chunker import TokenAwareChunker, ChunkConfig
from src.embeddings.embedder import Embedder
from src.build_vector_store import build_vector_store


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def group_units_into_chunks(units: list[dict], tokenizer, source: str) -> list[dict]:
    chunker = TokenAwareChunker(
        tokenizer,
        ChunkConfig(CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS),
    )
    chunks = chunker.chunk(units)
    for c in chunks:
        c["metadata"]["source"] = source
    return chunks


def run() -> None:
    ensure_dirs()
    embedder = Embedder()
    ocr = PaddleOCRProcessor()
    pdf_processor = PDFProcessor(ocr)

    all_chunks: list[dict] = []
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "documents": [],
        "totals": {"files": 0, "pages": 0, "chunks": 0, "ocr_pages": 0},
        "errors": [],
    }

    # PDFs
    for path in sorted(PDF_DIR.glob("*.pdf")):
        item = {"file": path.name, "type": "pdf"}
        try:
            pages = pdf_processor.process(path)
            save_json(TEXT_DIR / f"{path.stem}.json", pages)
            units = []
            for page in pages:
                if page.get("text"):
                    units.append({
                        "text": page["text"],
                        "source": path.name,
                        "source_type": page["source_type"],
                        "page": page["page"],
                        "page_start": page["page"],
                        "page_end": page["page"],
                    })
            chunks = group_units_into_chunks(units, embedder.tokenizer, path.name)
            payload = {"document": path.name, "chunks": chunks}
            save_json(CHUNKS_DIR / f"{path.stem}_chunks.json", payload)
            all_chunks.extend(chunks)
            item.update({
                "pages": len(pages),
                "ocr_pages": sum(1 for p in pages if p.get("ocr_used")),
                "chunks": len(chunks),
            })
            report["totals"]["pages"] += len(pages)
            report["totals"]["ocr_pages"] += item["ocr_pages"]
            report["totals"]["chunks"] += len(chunks)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            report["errors"].append(item)
        report["documents"].append(item)

    # Images
    for path in sorted(IMAGE_DIR.iterdir()):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            continue
        item = {"file": path.name, "type": "image"}
        try:
            page = ocr.image_to_page(path)
            save_json(TEXT_DIR / f"{path.stem}.json", page)
            units = [{
                "text": page.get("text", ""),
                "source": path.name,
                "source_type": "image_ocr",
                "page": 1,
                "page_start": 1,
                "page_end": 1,
            }] if page.get("text") else []
            chunks = group_units_into_chunks(units, embedder.tokenizer, path.name)
            save_json(CHUNKS_DIR / f"{path.stem}_chunks.json", {"document": path.name, "chunks": chunks})
            all_chunks.extend(chunks)
            item["chunks"] = len(chunks)
            item["ocr_confidence"] = page.get("mean_confidence", 0.0)
            report["totals"]["ocr_pages"] += 1
            report["totals"]["chunks"] += len(chunks)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            report["errors"].append(item)
        report["documents"].append(item)

    # Excel
    for path in sorted(EXCEL_DIR.iterdir()):
        if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            continue
        item = {"file": path.name, "type": "excel"}
        try:
            units = process_excel(path)
            save_json(TEXT_DIR / f"{path.stem}.json", units)
            chunks = group_units_into_chunks(units, embedder.tokenizer, path.name)
            save_json(CHUNKS_DIR / f"{path.stem}_chunks.json", {"document": path.name, "chunks": chunks})
            all_chunks.extend(chunks)
            item["rows_as_units"] = len(units)
            item["chunks"] = len(chunks)
            report["totals"]["chunks"] += len(chunks)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            report["errors"].append(item)
        report["documents"].append(item)

    # CSV
    for path in sorted(CSV_DIR.glob("*.csv")):
        item = {"file": path.name, "type": "csv"}
        try:
            units = process_csv(path)
            save_json(TEXT_DIR / f"{path.stem}.json", units)
            chunks = group_units_into_chunks(units, embedder.tokenizer, path.name)
            save_json(CHUNKS_DIR / f"{path.stem}_chunks.json", {"document": path.name, "chunks": chunks})
            all_chunks.extend(chunks)
            item["rows_as_units"] = len(units)
            item["chunks"] = len(chunks)
            report["totals"]["chunks"] += len(chunks)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            report["errors"].append(item)
        report["documents"].append(item)

    report["totals"]["files"] = len(report["documents"])
    manifest = None
    if all_chunks:
        manifest = build_vector_store(all_chunks)

    report["vector_store"] = manifest
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_json(REPORT_DIR / "processing_report.json", report)

    print("\n=== RAG BUILD COMPLETE ===")
    print(f"Files: {report['totals']['files']}")
    print(f"Pages: {report['totals']['pages']}")
    print(f"OCR pages: {report['totals']['ocr_pages']}")
    print(f"Chunks: {report['totals']['chunks']}")
    print(f"Errors: {len(report['errors'])}")
    if manifest:
        print(f"Vector store: {manifest['vector_count']} vectors")
        print("Saved under: vector_store/")
    else:
        print("No chunks found; vector store was not created.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the complete CPU RAG knowledge base")
    parser.parse_args()
    run()
