from __future__ import annotations

from typing import Any

from sentence_transformers import CrossEncoder

from src.config import RERANKER_MODEL


# Load the cross-encoder reranker.
class Reranker:

    # Initialize the reranking model.
    def __init__(self, model_name: str = RERANKER_MODEL):
        self.model_name = model_name
        self.model = CrossEncoder(
            model_name,
            device="cpu"
        )

    # Rerank retrieved chunks according to their relevance to the query.
    def rerank(
        self,
        query: str,
        records: list[dict[str, Any]],
        top_k: int
    ) -> list[dict[str, Any]]:

        if not records:
            return []

        # Create query-chunk pairs for the reranker.
        pairs = [
            (query, record["text"])
            for record in records
        ]

        # Calculate relevance scores for each pair.
        scores = self.model.predict(
            pairs,
            show_progress_bar=False
        )

        # Sort chunks from highest relevance to lowest relevance.
        ranked = sorted(
            zip(records, scores),
            key=lambda x: float(x[1]),
            reverse=True
        )

        results = []

        # Attach reranker scores to the selected chunks.
        for record, score in ranked[:top_k]:
            result = dict(record)

            retrieval = dict(
                result.get("retrieval", {})
            )

            retrieval["reranker_score"] = float(score)

            result["retrieval"] = retrieval

            results.append(result)

        return results