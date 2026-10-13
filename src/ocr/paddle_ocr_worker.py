from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Paddle/PaddleOCR runtime configuration
# ------------------------------------------------------------------

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")
os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")


# ------------------------------------------------------------------
# Convert Paddle/numpy objects into JSON-safe Python values.
# ------------------------------------------------------------------

def _to_python(value: Any):
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, dict):
        return {
            str(k): _to_python(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _to_python(v)
            for v in value
        ]

    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass

    return str(value)


# ------------------------------------------------------------------
# Convert PaddleOCR result object into a dictionary.
# ------------------------------------------------------------------

def _raw_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return _to_python(result)

    for attr in (
        "json",
        "to_json",
        "to_dict",
    ):
        if hasattr(result, attr):
            obj = getattr(result, attr)

            try:
                value = obj() if callable(obj) else obj

                if isinstance(value, str):
                    return json.loads(value)

                if isinstance(value, dict):
                    return _to_python(value)

            except Exception:
                continue

    converted = _to_python(result)

    return (
        converted
        if isinstance(converted, dict)
        else {}
    )


# ------------------------------------------------------------------
# Extract text and confidence values from PaddleOCR output.
# ------------------------------------------------------------------

def _extract_texts(
    result: Any,
) -> tuple[list[str], list[float]]:

    data = _raw_dict(result)

    texts = (
        data.get("rec_texts")
        or data.get("texts")
        or []
    )

    scores = (
        data.get("rec_scores")
        or data.get("scores")
        or []
    )

    texts = [
        str(x).strip()
        for x in texts
        if str(x).strip()
    ]

    if scores:
        scores = [
            float(x)
            for x in scores[:len(texts)]
        ]
    else:
        scores = [0.0] * len(texts)

    if len(scores) < len(texts):
        scores.extend(
            [0.0] * (
                len(texts) - len(scores)
            )
        )

    return texts, scores


# ------------------------------------------------------------------
# Run OCR for one image.
# ------------------------------------------------------------------

def _run_ocr(
    ocr,
    image_path: str,
) -> dict:

    results = list(
        ocr.predict(image_path)
    )

    lines: list[dict] = []
    texts: list[str] = []
    scores: list[float] = []

    for result in results:

        extracted_texts, extracted_scores = (
            _extract_texts(result)
        )

        for text, score in zip(
            extracted_texts,
            extracted_scores,
        ):

            lines.append(
                {
                    "text": text,
                    "confidence": score,
                }
            )

            texts.append(text)
            scores.append(score)

    return {
        "text": "\n".join(texts),
        "lines": lines,
        "mean_confidence": (
            sum(scores) / len(scores)
            if scores
            else 0.0
        ),
    }


# ------------------------------------------------------------------
# Atomic JSON writer.
#
# This prevents partially-written response files.
# ------------------------------------------------------------------

def _write_json_atomic(
    path: Path,
    payload: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_path.replace(path)


# ------------------------------------------------------------------
# Worker main loop.
#
# IMPORTANT:
# PaddleOCR is imported INSIDE the isolated worker process.
#
# If Paddle/PaddleX suffers a native crash, only this process dies.
# The parent RAG process remains alive and can restart the worker.
# ------------------------------------------------------------------

def main() -> None:

    ready_file_value = os.getenv(
        "RAG_OCR_READY_FILE"
    )

    ready_file = (
        Path(ready_file_value)
        if ready_file_value
        else None
    )

    lang = os.getenv(
        "RAG_OCR_LANG",
        "en",
    )

    try:
        cpu_threads = int(
            os.getenv(
                "RAG_OCR_CPU_THREADS",
                "8",
            )
        )
    except ValueError:
        cpu_threads = 8

    try:

        # Import only inside the isolated process.
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(
            lang=lang,
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
            cpu_threads=cpu_threads,
        )

        if ready_file:
            _write_json_atomic(
                ready_file,
                {
                    "ok": True,
                    "message": "PaddleOCR worker ready",
                },
            )

    except Exception as exc:

        if ready_file:
            _write_json_atomic(
                ready_file,
                {
                    "ok": False,
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    "traceback": traceback.format_exc(),
                },
            )

        return

    # --------------------------------------------------------------
    # Process requests until stdin closes.
    # --------------------------------------------------------------

    for line in sys.stdin:

        line = line.strip()

        if not line:
            continue

        try:
            request = json.loads(line)

        except Exception as exc:
            continue

        if request.get("command") == "shutdown":
            break

        image_path = request.get(
            "image_path"
        )

        response_path_value = request.get(
            "response_path"
        )

        if not image_path or not response_path_value:
            continue

        response_path = Path(
            response_path_value
        )

        try:

            result = _run_ocr(
                ocr,
                str(image_path),
            )

            payload = {
                "ok": True,
                **result,
            }

        except Exception as exc:

            # Normal Python-side PaddleOCR failure.
            payload = {
                "ok": False,
                "error": (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                "traceback": traceback.format_exc(),
            }

        _write_json_atomic(
            response_path,
            payload,
        )


if __name__ == "__main__":
    main()