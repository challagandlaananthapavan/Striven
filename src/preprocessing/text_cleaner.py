import re
import unicodedata


# Clean extracted text without changing its original meaning.
def clean_text(text: str) -> str:
    """Conservative cleanup. Do not 'correct' OCR spelling automatically."""
    if not text:
        return ""

    text = text.replace("\u00ad", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)

    # Join words split by a line-break hyphen.
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = [line.strip() for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()

    return cleaned


# Normalize text for keyword-based BM25 search.
def normalize_for_bm25(text: str) -> str:
    """Light normalization; preserve useful keywords and numbers."""
    text = text.lower()
    text = re.sub(r"[^\w&\-/%.]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# Calculate basic metrics to measure text quality.
def quality_metrics(text: str) -> dict:
    if not text:
        return {
            "chars": 0,
            "alpha_ratio": 0.0,
            "suspicious_ratio": 1.0
        }

    compact = re.sub(r"\s+", "", text)

    alpha = sum(
        ch.isalpha()
        for ch in compact
    )

    suspicious = sum(
        ch in "�□|~`"
        for ch in compact
    )

    n = max(len(compact), 1)

    return {
        "chars": len(text),
        "alpha_ratio": alpha / n,
        "suspicious_ratio": suspicious / n,
    }


# Decide whether extracted native PDF text is good enough to use.
def is_good_native_text(
    text: str,
    min_chars: int,
    min_alpha_ratio: float,
    max_suspicious_ratio: float
) -> bool:

    m = quality_metrics(text)

    return (
        m["chars"] >= min_chars
        and m["alpha_ratio"] >= min_alpha_ratio
        and m["suspicious_ratio"] <= max_suspicious_ratio
    )