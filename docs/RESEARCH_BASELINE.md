# Research basis for the current baseline

This project uses the following sources to justify the implementation choices:

1. BAAI `bge-small-en-v1.5` model card: 384-dimensional embeddings, 512-position model configuration, retrieval use, query instruction guidance.
   https://huggingface.co/BAAI/bge-small-en-v1.5

2. PaddleOCR 3.x OCR documentation: CPU execution, `predict()`, `enable_mkldnn`, CPU thread settings, and OCR language/device configuration.
   https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html

3. Groq model catalog: current production model IDs and context/completion limits.
   https://console.groq.com/docs/models

4. FAISS getting started: nearest-neighbor vector indexing and search.
   https://github.com/facebookresearch/faiss/wiki/getting-started

5. rank-bm25 PyPI documentation: BM25Okapi corpus indexing and ranking.
   https://pypi.org/project/rank-bm25/

6. PaddleOCR community discussion of the PIR/oneDNN error: multiple users reported successful combinations with matched PaddleOCR/PaddlePaddle versions; this project additionally disables MKL-DNN and PIR to avoid the earlier Colab CPU crash.
   https://github.com/PaddlePaddle/PaddleOCR/discussions/17350

Important: these sources support the architecture and baseline configuration. Final chunk size, overlap, retrieval K, and reranking settings should be validated on the project's own GT evaluation set.
