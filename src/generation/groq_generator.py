from __future__ import annotations

import os
from typing import Any

from groq import Groq

from src.config import GROQ_MODEL, GROQ_TEMPERATURE, GROQ_MAX_TOKENS


SYSTEM_PROMPT = """You are a grounded enterprise RAG assistant.
Use ONLY the supplied retrieved context to answer the user's question.
Do not invent facts, policies, numbers, names, dates, or sources.
If the retrieved context does not contain enough information, say that the available documents do not provide enough information.
Keep the answer clear and direct.
After the answer, provide a Sources section using the supplied source labels.
"""


# Generate answers using the retrieved context and Groq.
class GroqGenerator:

    # Initialize the Groq client and model.
    def __init__(
        self,
        api_key: str | None = None,
        model: str = GROQ_MODEL
    ):
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to the project .env file "
                "or provide it in the Streamlit app."
            )

        self.client = Groq(
            api_key=key
        )

        self.model = model

    # Convert retrieved chunks into context for the LLM.
    @staticmethod
    def build_context(
        records: list[dict[str, Any]]
    ) -> str:

        pieces = []

        for n, rec in enumerate(
            records,
            start=1
        ):
            m = rec["metadata"]

            location = []

            if m.get("page_start") is not None:
                location.append(
                    f"page {m['page_start']}-{m['page_end']}"
                )

            if m.get("sheet"):
                location.append(
                    f"sheet {m['sheet']}"
                )

            if m.get("row_start") is not None:
                location.append(
                    f"rows {m['row_start']}-{m['row_end']}"
                )

            loc = (
                ", ".join(location)
                if location
                else "location not specified"
            )

            source_label = (
                f"[{n}] {m.get('source')} ({loc})"
            )

            pieces.append(
                f"SOURCE {source_label}\n{rec['text']}"
            )

        return "\n\n---\n\n".join(
            pieces
        )

    # Send the question and retrieved context to Groq and return the answer.
    def answer(
        self,
        query: str,
        records: list[dict[str, Any]]
    ) -> dict:

        # Build the context from the retrieved chunks.
        context = self.build_context(
            records
        )

        prompt = (
            f"USER QUESTION:\n{query}\n\n"
            f"RETRIEVED CONTEXT:\n{context}"
        )

        # Generate the final answer using the Groq LLM.
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=GROQ_TEMPERATURE,
            max_tokens=GROQ_MAX_TOKENS,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
        )

        # Return the answer, model information, and source metadata.
        return {
            "answer": response.choices[0].message.content,
            "model": self.model,
            "sources": [
                {
                    "source": r["metadata"].get("source"),
                    "page_start": r["metadata"].get("page_start"),
                    "page_end": r["metadata"].get("page_end"),
                    "sheet": r["metadata"].get("sheet"),
                    "row_start": r["metadata"].get("row_start"),
                    "row_end": r["metadata"].get("row_end"),
                    "rrf_score": r.get(
                        "retrieval",
                        {}
                    ).get("rrf_score"),
                    "is_neighbor": r.get(
                        "retrieval",
                        {}
                    ).get(
                        "is_neighbor",
                        False
                    ),
                }
                for r in records
            ],
        }