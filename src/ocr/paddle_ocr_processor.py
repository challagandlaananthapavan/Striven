from __future__ import annotations

# ------------------------------------------------------------------
# IMPORTANT:
#
# PaddleOCR itself is NOT imported here.
#
# Actual PaddleOCR execution happens in:
#
#     src/ocr/paddle_ocr_worker.py
#
# in a separate Python process.
#
# This protects the main RAG process from native Paddle crashes.
# ------------------------------------------------------------------

import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

from pathlib import Path
from typing import Any

import fitz
from PIL import Image


# ------------------------------------------------------------------
# Environment configuration
# ------------------------------------------------------------------

os.environ.setdefault(
    "FLAGS_use_mkldnn",
    "0",
)

os.environ.setdefault(
    "FLAGS_enable_pir_api",
    "0",
)

os.environ.setdefault(
    "DISABLE_MODEL_SOURCE_CHECK",
    "True",
)


# ------------------------------------------------------------------
# Adaptive OCR retry strategy.
#
# These are SCALE FACTORS, not fixed image dimensions.
#
# Example:
#
# 2026 x 1966
#     ↓
# 1.00
# 0.85
# 0.70
# 0.55
# 0.40
# 0.30
#
# The actual dimensions are calculated automatically from the
# original image.
# ------------------------------------------------------------------

OCR_RETRY_SCALES = (
    1.00,
    0.85,
    0.70,
    0.55,
    0.40,
    0.30,
)


class OCRWorkerCrashed(RuntimeError):
    """Raised when the isolated OCR process terminates unexpectedly."""


class OCRWorkerTimeout(RuntimeError):
    """Raised when the isolated OCR process does not respond in time."""


class OCRWorkerError(RuntimeError):
    """Raised for normal worker-side OCR errors."""


class PaddleOCRProcessor:

    def __init__(
        self,
        lang: str = "en",
        cpu_threads: int = 8,
        timeout_seconds: int = 120,
        retry_scales: tuple[float, ...] = OCR_RETRY_SCALES,
        max_jobs_per_worker: int = 20,
    ):

        self.lang = lang
        self.cpu_threads = cpu_threads
        self.timeout_seconds = timeout_seconds
        self.retry_scales = tuple(
            float(x)
            for x in retry_scales
            if float(x) > 0
        )
        self.max_jobs_per_worker = max(
            1,
            int(max_jobs_per_worker),
        )

        # ----------------------------------------------------------
        # Worker state.
        # ----------------------------------------------------------

        self._worker: subprocess.Popen | None = None
        self._worker_jobs = 0

        # ----------------------------------------------------------
        # Runtime directory for worker request/response files.
        # ----------------------------------------------------------

        self._runtime_tmp = tempfile.TemporaryDirectory(
            prefix="rag_paddle_worker_"
        )

        self._runtime_dir = Path(
            self._runtime_tmp.name
        )

        # Clean worker when Python exits normally.
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    @property
    def _project_root(self) -> Path:
        return Path(
            __file__
        ).resolve().parents[2]

    def _start_worker(self) -> None:

        if (
            self._worker is not None
            and self._worker.poll() is None
        ):
            return

        self._stop_worker()

        ready_file = (
            self._runtime_dir
            / f"worker_ready_{uuid.uuid4().hex}.json"
        )

        env = os.environ.copy()

        env[
            "RAG_OCR_READY_FILE"
        ] = str(ready_file)

        env[
            "RAG_OCR_LANG"
        ] = self.lang

        env[
            "RAG_OCR_CPU_THREADS"
        ] = str(self.cpu_threads)

        env[
            "DISABLE_MODEL_SOURCE_CHECK"
        ] = "True"

        env[
            "FLAGS_use_mkldnn"
        ] = "0"

        env[
            "FLAGS_enable_pir_api"
        ] = "0"

        # --------------------------------------------------------------
        # Launch a completely separate Python process.
        # --------------------------------------------------------------

        self._worker = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "src.ocr.paddle_ocr_worker",
            ],
            cwd=str(self._project_root),
            env=env,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        start_time = time.monotonic()

        try:

            while True:

                # Worker exited before becoming ready.
                if self._worker.poll() is not None:
                    exit_code = self._worker.returncode

                    raise OCRWorkerCrashed(
                        "PaddleOCR worker exited during startup. "
                        f"Exit code: {exit_code}"
                    )

                if ready_file.exists():

                    try:
                        payload = json.loads(
                            ready_file.read_text(
                                encoding="utf-8"
                            )
                        )
                    finally:
                        ready_file.unlink(
                            missing_ok=True
                        )

                    if payload.get("ok"):
                        self._worker_jobs = 0
                        return

                    raise OCRWorkerError(
                        payload.get(
                            "error",
                            "PaddleOCR worker initialization failed.",
                        )
                    )

                if (
                    time.monotonic() - start_time
                    > self.timeout_seconds
                ):
                    raise OCRWorkerTimeout(
                        "Timed out while starting "
                        "the PaddleOCR worker."
                    )

                time.sleep(0.2)

        except Exception:
            self._stop_worker()
            raise

    def _stop_worker(self) -> None:

        worker = self._worker

        if worker is None:
            return

        try:

            if worker.poll() is None:

                try:
                    if worker.stdin:
                        worker.stdin.write(
                            json.dumps(
                                {
                                    "command": "shutdown"
                                }
                            )
                            + "\n"
                        )
                        worker.stdin.flush()

                        worker.stdin.close()

                except Exception:
                    pass

                try:
                    worker.wait(
                        timeout=5
                    )
                except subprocess.TimeoutExpired:

                    try:
                        worker.terminate()
                    except Exception:
                        pass

                    try:
                        worker.wait(
                            timeout=5
                        )
                    except subprocess.TimeoutExpired:

                        try:
                            worker.kill()
                        except Exception:
                            pass

                        try:
                            worker.wait(
                                timeout=2
                            )
                        except Exception:
                            pass

        except Exception:
            pass

        finally:
            self._worker = None
            self._worker_jobs = 0

    def _restart_worker(self) -> None:

        self._stop_worker()
        self._start_worker()

    def close(self) -> None:

        try:
            self._stop_worker()
        finally:

            try:
                self._runtime_tmp.cleanup()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Send one OCR request to the isolated worker.
    # ------------------------------------------------------------------

    def _worker_ocr(
        self,
        image_path: Path,
    ) -> dict:

        self._start_worker()

        if self._worker is None:
            raise OCRWorkerError(
                "OCR worker was not started."
            )

        if self._worker.stdin is None:
            self._stop_worker()

            raise OCRWorkerError(
                "OCR worker stdin is unavailable."
            )

        response_path = (
            self._runtime_dir
            / f"ocr_response_{uuid.uuid4().hex}.json"
        )

        request = {
            "image_path": str(
                image_path.resolve()
            ),
            "response_path": str(
                response_path.resolve()
            ),
        }

        try:

            self._worker.stdin.write(
                json.dumps(request)
                + "\n"
            )

            self._worker.stdin.flush()

        except Exception as exc:

            exit_code = (
                self._worker.poll()
                if self._worker
                else None
            )

            self._stop_worker()

            raise OCRWorkerCrashed(
                "Could not send request to OCR worker. "
                f"Exit code: {exit_code}. "
                f"Error: {exc}"
            ) from exc

        start_time = time.monotonic()

        try:

            while True:

                # ------------------------------------------------------
                # Successful worker response.
                # ------------------------------------------------------

                if response_path.exists():

                    payload = json.loads(
                        response_path.read_text(
                            encoding="utf-8"
                        )
                    )

                    response_path.unlink(
                        missing_ok=True
                    )

                    self._worker_jobs += 1

                    if not payload.get("ok"):

                        # Restart the worker after any Python-side OCR
                        # exception. This keeps worker state clean.
                        error = payload.get(
                            "error",
                            "Unknown OCR worker error.",
                        )

                        self._stop_worker()

                        raise OCRWorkerError(
                            error
                        )

                    result = {
                        "text": payload.get(
                            "text",
                            "",
                        ),
                        "lines": payload.get(
                            "lines",
                            [],
                        ),
                        "mean_confidence": float(
                            payload.get(
                                "mean_confidence",
                                0.0,
                            )
                            or 0.0
                        ),
                    }

                    # --------------------------------------------------
                    # Proactive worker recycling.
                    #
                    # This limits long-term native runtime accumulation.
                    # --------------------------------------------------

                    if (
                        self._worker_jobs
                        >= self.max_jobs_per_worker
                    ):
                        self._stop_worker()

                    return result

                # ------------------------------------------------------
                # Worker crashed before generating a response.
                # ------------------------------------------------------

                if self._worker.poll() is not None:

                    exit_code = (
                        self._worker.returncode
                    )

                    response_path.unlink(
                        missing_ok=True
                    )

                    self._stop_worker()

                    raise OCRWorkerCrashed(
                        "PaddleOCR worker terminated "
                        "without returning a result. "
                        f"Exit code: {exit_code}"
                    )

                # ------------------------------------------------------
                # Timeout / hung OCR.
                # ------------------------------------------------------

                if (
                    time.monotonic() - start_time
                    > self.timeout_seconds
                ):

                    response_path.unlink(
                        missing_ok=True
                    )

                    self._stop_worker()

                    raise OCRWorkerTimeout(
                        "PaddleOCR worker timed out."
                    )

                time.sleep(0.15)

        except (
            OCRWorkerCrashed,
            OCRWorkerTimeout,
            OCRWorkerError,
        ):
            raise

        except Exception as exc:

            response_path.unlink(
                missing_ok=True
            )

            self._stop_worker()

            raise OCRWorkerError(
                f"Unexpected OCR worker error: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Determine whether OCR returned useful text.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_usable_ocr_text(
        text: str,
    ) -> bool:

        if not text:
            return False

        text = text.strip()

        if len(text) < 2:
            return False

        return any(
            char.isalnum()
            for char in text
        )

    # ------------------------------------------------------------------
    # Build the adaptive image sizes.
    #
    # No fixed dimension like "1200 px" is used.
    # The image's own dimensions determine the generated sizes.
    # ------------------------------------------------------------------

    def _candidate_sizes(
        self,
        width: int,
        height: int,
    ) -> list[tuple[float, int, int]]:

        candidates: list[
            tuple[float, int, int]
        ] = []

        seen_sizes: set[
            tuple[int, int]
        ] = set()

        for scale in self.retry_scales:

            new_width = max(
                1,
                int(round(width * scale)),
            )

            new_height = max(
                1,
                int(round(height * scale)),
            )

            size_key = (
                new_width,
                new_height,
            )

            if size_key in seen_sizes:
                continue

            seen_sizes.add(size_key)

            candidates.append(
                (
                    scale,
                    new_width,
                    new_height,
                )
            )

        return candidates

    # ------------------------------------------------------------------
    # Normalize one image candidate.
    # ------------------------------------------------------------------

    @staticmethod
    def _save_candidate(
        source: Image.Image,
        target: Path,
        width: int,
        height: int,
    ) -> None:

        if (
            source.width == width
            and source.height == height
        ):

            source.save(
                target,
                format="PNG",
            )

            return

        resized = source.resize(
            (width, height),
            Image.Resampling.LANCZOS,
        )

        try:

            resized.save(
                target,
                format="PNG",
            )

        finally:
            resized.close()

    # ------------------------------------------------------------------
    # Adaptive OCR with isolated worker + retry strategy.
    # ------------------------------------------------------------------

    def image_to_text(
        self,
        image_path: str | Path,
    ) -> dict:

        image_path = Path(
            image_path
        )

        if not image_path.exists():

            return {
                "text": "",
                "lines": [],
                "mean_confidence": 0.0,
                "ocr_ok": False,
                "ocr_method": "paddleocr_adaptive",
                "attempts": 0,
                "attempt_history": [],
                "error": (
                    f"Image does not exist: "
                    f"{image_path}"
                ),
            }

        attempt_history: list[dict] = []

        # --------------------------------------------------------------
        # Open and standardize the original source image.
        # --------------------------------------------------------------

        try:

            with Image.open(image_path) as original:

                rgb_image = original.convert(
                    "RGB"
                )

                width = rgb_image.width
                height = rgb_image.height

                candidates = (
                    self._candidate_sizes(
                        width,
                        height,
                    )
                )

                with tempfile.TemporaryDirectory(
                    prefix="rag_ocr_attempts_"
                ) as temp_dir:

                    temp_dir = Path(
                        temp_dir
                    )

                    # --------------------------------------------------
                    # Try progressively safer image representations.
                    # --------------------------------------------------

                    for attempt_no, (
                        scale,
                        candidate_width,
                        candidate_height,
                    ) in enumerate(
                        candidates,
                        start=1,
                    ):

                        candidate_path = (
                            temp_dir
                            / (
                                f"attempt_{attempt_no}_"
                                f"{candidate_width}x"
                                f"{candidate_height}.png"
                            )
                        )

                        attempt_record = {
                            "attempt": attempt_no,
                            "scale": scale,
                            "width": candidate_width,
                            "height": candidate_height,
                            "status": "started",
                        }

                        try:

                            self._save_candidate(
                                source=rgb_image,
                                target=candidate_path,
                                width=candidate_width,
                                height=candidate_height,
                            )

                        except Exception as exc:

                            attempt_record.update(
                                {
                                    "status": "image_prepare_error",
                                    "error": (
                                        f"{type(exc).__name__}: "
                                        f"{exc}"
                                    ),
                                }
                            )

                            attempt_history.append(
                                attempt_record
                            )

                            continue

                        # --------------------------------------------------
                        # Try isolated PaddleOCR.
                        # --------------------------------------------------

                        try:

                            result = self._worker_ocr(
                                candidate_path
                            )

                            text = (
                                result.get(
                                    "text",
                                    "",
                                )
                                or ""
                            ).strip()

                            confidence = float(
                                result.get(
                                    "mean_confidence",
                                    0.0,
                                )
                                or 0.0
                            )

                            if self._is_usable_ocr_text(
                                text
                            ):

                                attempt_record.update(
                                    {
                                        "status": "success",
                                        "text_characters": len(text),
                                        "mean_confidence": confidence,
                                    }
                                )

                                attempt_history.append(
                                    attempt_record
                                )

                                return {
                                    "text": text,
                                    "lines": result.get(
                                        "lines",
                                        [],
                                    ),
                                    "mean_confidence": confidence,
                                    "ocr_ok": True,
                                    "ocr_method": (
                                        "paddleocr_adaptive"
                                    ),
                                    "attempts": attempt_no,
                                    "selected_scale": scale,
                                    "final_width": candidate_width,
                                    "final_height": candidate_height,
                                    "attempt_history": attempt_history,
                                }

                            # OCR process completed but returned
                            # unusable/empty text.
                            attempt_record.update(
                                {
                                    "status": "empty_or_unusable",
                                    "text_characters": len(text),
                                    "mean_confidence": confidence,
                                }
                            )

                        except OCRWorkerCrashed as exc:

                            attempt_record.update(
                                {
                                    "status": "worker_crash",
                                    "error": str(exc),
                                }
                            )

                            # Worker has already been stopped.
                            # Next attempt automatically starts
                            # a completely fresh worker.

                        except OCRWorkerTimeout as exc:

                            attempt_record.update(
                                {
                                    "status": "worker_timeout",
                                    "error": str(exc),
                                }
                            )

                        except OCRWorkerError as exc:

                            attempt_record.update(
                                {
                                    "status": "ocr_error",
                                    "error": str(exc),
                                }
                            )

                        except Exception as exc:

                            attempt_record.update(
                                {
                                    "status": "unexpected_error",
                                    "error": (
                                        f"{type(exc).__name__}: "
                                        f"{exc}"
                                    ),
                                }
                            )

                        attempt_history.append(
                            attempt_record
                        )

        except Exception as exc:

            return {
                "text": "",
                "lines": [],
                "mean_confidence": 0.0,
                "ocr_ok": False,
                "ocr_method": "paddleocr_adaptive",
                "attempts": len(
                    attempt_history
                ),
                "attempt_history": attempt_history,
                "error": (
                    f"Could not prepare OCR image: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }

        # --------------------------------------------------------------
        # Every attempt failed.
        #
        # IMPORTANT:
        # No failure message is inserted into OCR text.
        # The caller can preserve the original image and mark it
        # unresolved.
        # --------------------------------------------------------------

        return {
            "text": "",
            "lines": [],
            "mean_confidence": 0.0,
            "ocr_ok": False,
            "ocr_method": "paddleocr_adaptive",
            "attempts": len(
                attempt_history
            ),
            "attempt_history": attempt_history,
            "error": (
                "All adaptive OCR attempts failed."
            ),
        }

    # ------------------------------------------------------------------
    # Full PDF OCR.
    #
    # Existing behavior retained.
    # Every rendered page ultimately goes through image_to_text(),
    # so adaptive retry + worker isolation also applies here.
    # ------------------------------------------------------------------

    def pdf_to_pages(
        self,
        pdf_path: str | Path,
        dpi: int = 200,
    ) -> list[dict]:

        pdf_path = Path(
            pdf_path
        )

        output: list[dict] = []

        doc = fitz.open(
            pdf_path
        )

        try:

            with tempfile.TemporaryDirectory(
                prefix="rag_ocr_"
            ) as temp_dir:

                temp_dir = Path(
                    temp_dir
                )

                for page_idx in range(
                    len(doc)
                ):

                    page = doc[
                        page_idx
                    ]

                    pix = page.get_pixmap(
                        dpi=dpi,
                        alpha=False,
                    )

                    image_path = (
                        temp_dir
                        / (
                            f"page_{page_idx + 1}.png"
                        )
                    )

                    pix.save(
                        str(image_path)
                    )

                    result = self.image_to_text(
                        image_path
                    )

                    result.update(
                        {
                            "page": page_idx + 1,
                            "source": pdf_path.name,
                            "source_type": "pdf_ocr",
                        }
                    )

                    output.append(
                        result
                    )

        finally:
            doc.close()

        return output

    # ------------------------------------------------------------------
    # Standalone image processing.
    # ------------------------------------------------------------------

    def image_to_page(
        self,
        image_path: str | Path,
    ) -> dict:

        image_path = Path(
            image_path
        )

        result = self.image_to_text(
            image_path
        )

        result.update(
            {
                "page": 1,
                "source": image_path.name,
                "source_type": "image_ocr",
            }
        )

        return result