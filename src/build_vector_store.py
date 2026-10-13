from __future__ import annotations

import json
import pickle
from pathlib import Path
from datetime import datetime, timezone

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from src.config import VECTOR_DIR, EMBEDDING_MODEL
from src.embeddings.embedder import Embedder
from src.preprocessing.text_cleaner import normalize_for_bm25


# Build embeddings and search indexes from all chunks.
def build_vector_store(chunk_records: list[dict]) -> dict:

    VECTOR_DIR.mkdir(parents=True, exist_ok=True)

    if not chunk_records:
        raise RuntimeError("No chunks were produced. Vector store cannot be built.")

    embedder = Embedder()
    texts = [r["text"] for r in chunk_records]

    # Convert each chunk into a numerical embedding vector.
    embeddings = embedder.embed_documents(texts, batch_size=32)

    # Build and save the FAISS vector index for semantic search.
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    faiss.write_index(index, str(VECTOR_DIR / "index.faiss"))

    # Save the raw embedding vectors and chunk metadata.
    np.save(VECTOR_DIR / "embeddings.npy", embeddings)

    with open(VECTOR_DIR / "records.json", "w", encoding="utf-8") as f:
        json.dump(chunk_records, f, ensure_ascii=False, indent=2)

    # Build and save the BM25 index for keyword search.
    tokenized = [normalize_for_bm25(t).split() for t in texts]

    bm25 = BM25Okapi(tokenized)

    with open(VECTOR_DIR / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f)

    # Create metadata describing the generated vector store.
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "documents_model": EMBEDDING_MODEL,
        "vector_dimension": int(embeddings.shape[1]),
        "vector_count": int(len(chunk_records)),
        "faiss_index": "IndexFlatIP with normalized vectors",
        "bm25": "BM25Okapi",
    }

    # Save the vector-store metadata.
    with open(VECTOR_DIR / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest