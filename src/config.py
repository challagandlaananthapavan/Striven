from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("STRIVEN_DATA_ROOT", str(ROOT)))

RAW_DIR = DATA_ROOT / "data" / "raw"
PDF_DIR = RAW_DIR / "pdfs"
EXCEL_DIR = RAW_DIR / "excel"
CSV_DIR = RAW_DIR / "csv"
IMAGE_DIR = RAW_DIR / "images"

PROCESSED_DIR = DATA_ROOT / "data" / "processed"
PAGES_DIR = PROCESSED_DIR / "pages"
TEXT_DIR = PROCESSED_DIR / "text"
CHUNKS_DIR = PROCESSED_DIR / "chunks"

VECTOR_DIR = DATA_ROOT / "vector_store"
REPORT_DIR = DATA_ROOT / "reports"
LOG_DIR = DATA_ROOT / "logs"


# Configure the embedding model.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


# Configure token-based chunking.
CHUNK_SIZE_TOKENS = 450
CHUNK_OVERLAP_TOKENS = 75
MIN_CHUNK_TOKENS = 40


# Configure hybrid retrieval.
DENSE_CANDIDATES = 40
BM25_CANDIDATES = 40
RRF_K = 60


# Configure reranking.
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_CANDIDATES = 20


# Configure final context selection.
FINAL_CONTEXT_CHUNKS = 8
NEIGHBOR_CHUNKS = 1


# Configure OCR and PDF extraction.
OCR_LANGUAGE = "en"
OCR_DPI = 200
OCR_CPU_THREADS = 8
NATIVE_MIN_CHARS = 80
NATIVE_MIN_ALPHA_RATIO = 0.30
NATIVE_MAX_SUSPICIOUS_RATIO = 0.03


# Configure Groq generation.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_TEMPERATURE = 0.0
GROQ_MAX_TOKENS = 1500


SUPPORTED_PDF = {".pdf"}

SUPPORTED_IMAGE = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

SUPPORTED_EXCEL = {
    ".xlsx",
    ".xlsm",
    ".xls",
}

SUPPORTED_CSV = {".csv"}


# Create all required project directories.
def ensure_dirs() -> None:
    for p in [
        PDF_DIR,
        EXCEL_DIR,
        CSV_DIR,
        IMAGE_DIR,
        PAGES_DIR,
        TEXT_DIR,
        CHUNKS_DIR,
        VECTOR_DIR,
        REPORT_DIR,
        LOG_DIR,
    ]:
        p.mkdir(
            parents=True,
            exist_ok=True
        )