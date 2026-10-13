from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
import pickle

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from src.config import (
    DENSE_CANDIDATES,
    BM25_CANDIDATES,
    RRF_K,
    RERANK_CANDIDATES,
    FINAL_CONTEXT_CHUNKS,
    NEIGHBOR_CHUNKS,
)
from src.embeddings.embedder import Embedder
from src.preprocessing.text_cleaner import normalize_for_bm25
from src.retrieval.reranker import Reranker


# Load the vector store and retrieval components.
class HybridRetriever:

    def __init__(self, vector_dir: str | Path):
        self.vector_dir = Path(vector_dir)

        self.index = faiss.read_index(
            str(self.vector_dir / "index.faiss")
        )

        with open(
            self.vector_dir / "records.json",
            "r",
            encoding="utf-8"
        ) as f:
            self.records = json.load(f)

        with open(
            self.vector_dir / "bm25.pkl",
            "rb"
        ) as f:
            self.bm25 = pickle.load(f)

        self.embedder = Embedder()
        self.reranker = Reranker()

    # Retrieve chunks using semantic vector similarity.
    def _dense(self, query: str, k: int):
        q = self.embedder.embed_query(query).reshape(1, -1)

        scores, ids = self.index.search(
            q,
            min(k, self.index.ntotal)
        )

        return [
            (int(i), float(s))
            for i, s in zip(ids[0], scores[0])
            if i >= 0
        ]

    # Retrieve chunks using keyword matching with BM25.
    def _bm25(self, query: str, k: int):
        scores = np.asarray(
            self.bm25.get_scores(
                normalize_for_bm25(query).split()
            ),
            dtype="float32"
        )

        order = np.argsort(-scores)[
            : min(k, len(scores))
        ]

        return [
            (int(i), float(scores[i]))
            for i in order
        ]

    # Combine dense and BM25 rankings using Reciprocal Rank Fusion.
    @staticmethod
    def _rrf(
        rankings: list[list[tuple[int, float]]],
        c: int = RRF_K
    ):
        fused = defaultdict(float)
        sources = defaultdict(dict)

        for source_idx, ranking in enumerate(rankings):

            for rank, (doc_id, score) in enumerate(
                ranking,
                start=1
            ):
                fused[doc_id] += 1.0 / (c + rank)

                sources[doc_id][source_idx] = {
                    "rank": rank,
                    "score": score
                }

        ordered = sorted(
            fused.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return [
            (doc_id, fused_score, sources[doc_id])
            for doc_id, fused_score in ordered
        ]

    # Add neighboring chunks to preserve surrounding context.
    def _expand_neighbors(
        self,
        selected_ids: list[int]
    ) -> list[int]:

        if NEIGHBOR_CHUNKS <= 0:
            return selected_ids

        per_source = defaultdict(dict)

        for i, rec in enumerate(self.records):
            meta = rec["metadata"]

            per_source[
                meta.get("source")
            ][
                meta.get("chunk_index", i)
            ] = i

        expanded = list(selected_ids)
        seen = set(expanded)

        for idx in list(selected_ids):

            meta = self.records[idx]["metadata"]

            source = meta.get("source")
            chunk_index = meta.get("chunk_index")

            lookup = per_source.get(source, {})

            for delta in range(
                -NEIGHBOR_CHUNKS,
                NEIGHBOR_CHUNKS + 1
            ):

                if delta == 0:
                    continue

                neighbor = lookup.get(
                    (chunk_index or 0) + delta
                )

                if neighbor is not None and neighbor not in seen:
                    expanded.append(neighbor)
                    seen.add(neighbor)

        return expanded

    # Run hybrid retrieval, reranking, and neighbor expansion.
    def search(
        self,
        query: str,
        final_k: int = FINAL_CONTEXT_CHUNKS
    ) -> list[dict]:

        # Get candidates from semantic search.
        dense = self._dense(
            query,
            DENSE_CANDIDATES
        )

        # Get candidates from keyword search.
        sparse = self._bm25(
            query,
            BM25_CANDIDATES
        )

        # Combine both retrieval rankings.
        fused = self._rrf(
            [dense, sparse]
        )

        # Select RRF candidates for reranking.
        rerank_ids = [
            doc_id
            for doc_id, _, _ in fused[
                :RERANK_CANDIDATES
            ]
        ]

        fused_map = {
            doc_id: (score, details)
            for doc_id, score, details in fused
        }

        rerank_records = []

        # Prepare RRF results for the reranker.
        for idx in rerank_ids:

            rec = dict(
                self.records[idx]
            )

            rrf_score, details = fused_map.get(
                idx,
                (0.0, {})
            )

            rec["record_id"] = idx

            rec["retrieval"] = {
                "rrf_score": rrf_score,
                "dense": details.get(0),
                "bm25": details.get(1),
                "is_neighbor": False,
            }

            rerank_records.append(rec)

        # Rank the RRF candidates using the cross-encoder.
        reranked = self.reranker.rerank(
            query,
            rerank_records,
            final_k
        )

        # Select the best chunks after reranking.
        base_ids = [
            rec["record_id"]
            for rec in reranked
        ]

        # Add neighboring chunks to the reranked results.
        candidate_ids = self._expand_neighbors(
            base_ids
        )

        reranked_map = {
            rec["record_id"]: rec
            for rec in reranked
        }

        result = []

        # Build the final context with retrieval metadata.
        for idx in candidate_ids:

            if idx in reranked_map:
                rec = dict(
                    reranked_map[idx]
                )

            else:
                rec = dict(
                    self.records[idx]
                )

                rrf_score, details = fused_map.get(
                    idx,
                    (0.0, {})
                )

                rec["record_id"] = idx

                rec["retrieval"] = {
                    "rrf_score": rrf_score,
                    "dense": details.get(0),
                    "bm25": details.get(1),
                    "reranker_score": None,
                    "is_neighbor": True,
                }

            result.append(rec)

        return result[
            :max(
                final_k,
                len(base_ids) + 2 * NEIGHBOR_CHUNKS
            )
        ]