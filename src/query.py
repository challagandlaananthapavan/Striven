from __future__ import annotations

import argparse
import json
from dotenv import load_dotenv

from src.config import VECTOR_DIR
from src.retrieval.hybrid_retriever import HybridRetriever
from src.generation.groq_generator import GroqGenerator


# Run the RAG question-answering pipeline.
def main() -> None:

    # Load environment variables and read the user's question.
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ask the built RAG system")
    parser.add_argument("question", nargs="+", help="Question to ask")
    args = parser.parse_args()
    question = " ".join(args.question).strip()

    # Check whether the vector store is available.
    if not (VECTOR_DIR / "index.faiss").exists():
        raise RuntimeError(
            "Vector store not found. Run: python -m src.main"
        )

    # Retrieve relevant chunks using hybrid search and reranking.
    retriever = HybridRetriever(VECTOR_DIR)
    records = retriever.search(question)

    # Display the retrieved chunks and their retrieval scores.
    print("\n=== RETRIEVED CONTEXT ===")

    for i, r in enumerate(records, 1):

        m = r["metadata"]
        retrieval = r.get("retrieval", {})

        print(
            f"\n[{i}] {m.get('source')} "
            f"| pages {m.get('page_start')}–{m.get('page_end')} "
            f"| RRF={retrieval.get('rrf_score', 0):.6f} "
            f"| Reranker={retrieval.get('reranker_score')}"
        )

        preview = r["text"].replace("\n", " ")

        print(
            preview[:400]
            + ("..." if len(preview) > 400 else "")
        )

    # Generate the final answer using the retrieved context.
    generator = GroqGenerator()
    result = generator.answer(
        question,
        records
    )

    # Display the generated answer.
    print("\n=== ANSWER ===\n")
    print(result["answer"])

    # Display the source metadata.
    print("\n=== SOURCES / METADATA ===")
    print(
        json.dumps(
            result["sources"],
            indent=2,
            ensure_ascii=False
        )
    )


# Start the RAG system.
if __name__ == "__main__":
    main()