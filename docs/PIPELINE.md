# Runtime pipeline

## Build / ingestion

1. Discover PDF, image, Excel and CSV inputs under `data/raw`.
2. For PDF pages, use PyMuPDF native text extraction first.
3. If a page is empty or fails conservative text-quality checks, render it and use PaddleOCR.
4. Images go directly through PaddleOCR.
5. Excel/CSV rows are normalized to searchable text while retaining sheet/row metadata.
6. Clean text conservatively; do not hallucinate OCR corrections.
7. Split by semantic units with a maximum target of 450 tokens and 75 token overlap.
8. Embed passages using `BAAI/bge-small-en-v1.5` on CPU.
9. Normalize embeddings and store them in FAISS `IndexFlatIP`.
10. Store BM25 index and exact chunk records alongside FAISS.

## Query / retrieval

1. Embed the user query with the BGE retrieval instruction.
2. Retrieve up to 40 candidates from FAISS.
3. Retrieve up to 40 candidates from BM25.
4. Fuse ranks with RRF constant 60.
5. Take the highest-ranked chunks and add one adjacent chunk on either side when available.
6. Send the resulting context plus the question to the configured Groq model.
7. Return the answer and source metadata (document, page/sheet/row, RRF score, neighbor flag).

The vector store is intentionally local for the Colab evaluation phase.
