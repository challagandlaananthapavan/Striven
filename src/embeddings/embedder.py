from __future__ import annotations
from pathlib import Path
from typing import Iterable

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import EMBEDDING_MODEL, EMBEDDING_DIM, QUERY_INSTRUCTION


class Embedder:
    def __init__(self, model_name: str = EMBEDDING_MODEL):
        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device="cpu")
        actual_dim = self.model.get_sentence_embedding_dimension()
        if actual_dim != EMBEDDING_DIM:
            raise ValueError(f"Expected {EMBEDDING_DIM} dimensions, got {actual_dim}")
        self.tokenizer = self.model.tokenizer

    def embed_documents(self, texts: Iterable[str], batch_size: int = 32) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.empty((0, EMBEDDING_DIM), dtype="float32")
        vectors = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype="float32")

    def embed_query(self, query: str) -> np.ndarray:
        q = QUERY_INSTRUCTION + query
        vector = self.model.encode(
            [q], normalize_embeddings=True, convert_to_numpy=True
        )[0]
        return np.asarray(vector, dtype="float32")
