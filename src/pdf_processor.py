from __future__ import annotations

from pathlib import Path
import json
import re

import fitz

from src.config import (
    OCR_DPI,
    NATIVE_MIN_CHARS,
    NATIVE_MIN_ALPHA_RATIO,
    NATIVE_MAX_SUSPICIOUS_RATIO,
    PAGES_DIR,
)
from src.preprocessing.text_cleaner import (
    clean_text,
    quality_metrics,
    is_good_native_text,
)
from src.ocr.paddle_ocr_processor import PaddleOCRProcessor


class PDFProcessor:
    def __init__(self, ocr: PaddleOCRProcessor):
        self.ocr = ocr

        # Folder used to preserve images that could not be processed.
        self.unresolved_images_dir = PAGES_DIR / "unresolved_images"
        self.unresolved_images_dir.mkdir(parents=True, exist_ok=True)

        PAGES_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _native_page(page) -> str:
        """
        Extract native/selectable PDF text using PyMuPDF.
        """
        blocks = page.get_text("blocks", sort=True)

        texts = []

        for block in blocks:
            if len(block) >= 5:
                text = block[4]

                if text and text.strip():
                    texts.append(text.strip())

        return clean_text("\n".join(texts))

    @staticmethod
    def _is_meaningful_image(image_info: dict) -> bool:
        """
        Skip extremely tiny images that are likely icons/decorative objects.

        Larger images are sent to PaddleOCR.
        """
        width = int(image_info.get("width", 0) or 0)
        height = int(image_info.get("height", 0) or 0)

        if width <= 0 or height <= 0:
            return False

        # Conservative threshold so normal figures/illustrations are processed.
        return width >= 32 and height >= 32

    def _extract_and_ocr_images(
        self,
        doc,
        page,
        pdf_path: Path,
        page_no: int,
    ) -> tuple[list[str], list[dict]]:
        """
        Extract embedded raster images from a PDF page and send them
        individually to PaddleOCR.

        Returns:
            image_texts:
                Successful OCR text extracted from images.

            image_records:
                Metadata/status for every encountered image.
        """
        image_texts: list[str] = []
        image_records: list[dict] = []

        # Prevent duplicate processing when the same xref is referenced
        # more than once on a page.
        seen_xrefs: set[int] = set()

        try:
            page_images = page.get_images(full=True)
        except Exception as exc:
            image_records.append({
                "status": "error",
                "reason": f"Could not enumerate page images: {exc}",
                "page": page_no,
            })
            return image_texts, image_records

        for image_index, image_info in enumerate(page_images, start=1):
            try:
                xref = int(image_info[0])

                if xref in seen_xrefs:
                    continue

                seen_xrefs.add(xref)

                extracted = doc.extract_image(xref)

                if not extracted:
                    image_records.append({
                        "status": "unresolved",
                        "reason": "PyMuPDF returned no image data",
                        "page": page_no,
                        "xref": xref,
                    })
                    continue

                width = extracted.get("width", 0)
                height = extracted.get("height", 0)
                ext = extracted.get("ext", "png")
                image_bytes = extracted.get("image", b"")

                image_info_dict = {
                    "xref": xref,
                    "width": width,
                    "height": height,
                    "ext": ext,
                }

                # Ignore tiny decorative image objects.
                if not self._is_meaningful_image(image_info_dict):
                    image_records.append({
                        "status": "skipped",
                        "reason": "Image below minimum processing size",
                        "page": page_no,
                        "xref": xref,
                        "width": width,
                        "height": height,
                    })
                    continue

                if not image_bytes:
                    image_records.append({
                        "status": "unresolved",
                        "reason": "Extracted image contains no bytes",
                        "page": page_no,
                        "xref": xref,
                        "width": width,
                        "height": height,
                    })
                    continue

                # Temporary OCR image.
                tmp_image = (
                    PAGES_DIR
                    / f"__img_{pdf_path.stem}_page_{page_no}_"
                      f"img_{image_index}.{ext}"
                )

                tmp_image.write_bytes(image_bytes)

                ocr_result = None

                try:
                    ocr_result = self.ocr.image_to_text(tmp_image)
                except Exception as exc:
                    # OCR itself failed.
                    unresolved_path = (
                        self.unresolved_images_dir
                        / f"{pdf_path.stem}_page_{page_no}_"
                          f"img_{image_index}.{ext}"
                    )

                    tmp_image.replace(unresolved_path)

                    image_records.append({
                        "status": "unresolved",
                        "reason": f"PaddleOCR error: {exc}",
                        "page": page_no,
                        "xref": xref,
                        "width": width,
                        "height": height,
                        "path": str(unresolved_path),
                    })

                    continue

                finally:
                    # If the file was not moved to unresolved storage,
                    # remove the temporary copy.
                    if tmp_image.exists():
                        tmp_image.unlink(missing_ok=True)

                ocr_text = ""
                mean_confidence = 0.0
                lines = []

                if isinstance(ocr_result, dict):
                    ocr_text = clean_text(
                        ocr_result.get("text", "") or ""
                    )
                    mean_confidence = float(
                        ocr_result.get("mean_confidence", 0.0) or 0.0
                    )
                    lines = ocr_result.get("lines", []) or []

                # OCR produced useful text.
                if ocr_text:
                    image_texts.append(ocr_text)

                    image_records.append({
                        "status": "processed",
                        "page": page_no,
                        "xref": xref,
                        "width": width,
                        "height": height,
                        "ocr_method": "paddleocr_image",
                        "ocr_mean_confidence": mean_confidence,
                        "ocr_lines": lines,
                    })

                else:
                    # Preserve image for later/manual/vision processing.
                    unresolved_path = (
                        self.unresolved_images_dir
                        / f"{pdf_path.stem}_page_{page_no}_"
                          f"img_{image_index}.{ext}"
                    )

                    unresolved_path.write_bytes(image_bytes)

                    image_records.append({
                        "status": "unresolved",
                        "reason": "PaddleOCR returned no usable text",
                        "page": page_no,
                        "xref": xref,
                        "width": width,
                        "height": height,
                        "path": str(unresolved_path),
                        "ocr_mean_confidence": mean_confidence,
                        "ocr_lines": lines,
                    })

            except Exception as exc:
                image_records.append({
                    "status": "unresolved",
                    "reason": f"Image processing error: {exc}",
                    "page": page_no,
                    "image_index": image_index,
                })

        return image_texts, image_records

    def _run_full_page_ocr(
        self,
        page,
        pdf_path: Path,
        page_no: int,
    ) -> dict:
        """
        Existing page-level OCR fallback.

        Used when native text is insufficient and the image-level OCR
        results are still insufficient.
        """
        pix = page.get_pixmap(dpi=OCR_DPI, alpha=False)

        tmp_image = (
            PAGES_DIR
            / f"__ocr_{pdf_path.stem}_page_{page_no}.png"
        )

        pix.save(str(tmp_image))

        try:
            return self.ocr.image_to_text(tmp_image)
        finally:
            tmp_image.unlink(missing_ok=True)

    def process(self, pdf_path: Path) -> list[dict]:
        """
        Process one PDF page-by-page.

        New behavior:
        1. Extract native text.
        2. Extract embedded images.
        3. Run PaddleOCR on meaningful embedded images.
        4. Add successful image OCR text to the page text.
        5. If native text is poor AND image OCR is insufficient,
           run full-page OCR.
        6. Preserve unresolved images and their metadata.
        """
        doc = fitz.open(pdf_path)
        page_records: list[dict] = []

        try:
            for page_index in range(len(doc)):
                page_no = page_index + 1
                page = doc[page_index]

                # --------------------------------------------------
                # 1. Native PDF text
                # --------------------------------------------------
                native = self._native_page(page)

                native_metrics = quality_metrics(native)

                native_good = is_good_native_text(
                    native,
                    NATIVE_MIN_CHARS,
                    NATIVE_MIN_ALPHA_RATIO,
                    NATIVE_MAX_SUSPICIOUS_RATIO,
                )

                # --------------------------------------------------
                # 2. Process embedded images on EVERY page
                # --------------------------------------------------
                image_texts, image_records = (
                    self._extract_and_ocr_images(
                        doc=doc,
                        page=page,
                        pdf_path=pdf_path,
                        page_no=page_no,
                    )
                )

                # --------------------------------------------------
                # 3. Combine native text + successful image OCR
                # --------------------------------------------------
                combined_parts: list[str] = []

                if native:
                    combined_parts.append(native)

                if image_texts:
                    combined_parts.append(
                        "\n\n".join(image_texts)
                    )

                combined_text = clean_text(
                    "\n\n".join(combined_parts)
                )

                # --------------------------------------------------
                # 4. Full-page OCR fallback
                # --------------------------------------------------
                full_page_ocr_used = False
                full_page_ocr_result: dict = {}

                combined_good = is_good_native_text(
                    combined_text,
                    NATIVE_MIN_CHARS,
                    NATIVE_MIN_ALPHA_RATIO,
                    NATIVE_MAX_SUSPICIOUS_RATIO,
                )

                if not combined_good:
                    full_page_ocr_result = self._run_full_page_ocr(
                        page=page,
                        pdf_path=pdf_path,
                        page_no=page_no,
                    )

                    full_page_text = clean_text(
                        full_page_ocr_result.get("text", "")
                    )

                    if full_page_text:
                        combined_text = clean_text(
                            "\n\n".join(
                                part
                                for part in [
                                    native,
                                    "\n\n".join(image_texts),
                                    full_page_text,
                                ]
                                if part
                            )
                        )

                    full_page_ocr_used = bool(full_page_text)

                # --------------------------------------------------
                # 5. Final quality
                # --------------------------------------------------
                final_metrics = quality_metrics(combined_text)

                processed_image_count = sum(
                    1
                    for r in image_records
                    if r.get("status") == "processed"
                )

                unresolved_image_count = sum(
                    1
                    for r in image_records
                    if r.get("status") == "unresolved"
                )

                skipped_image_count = sum(
                    1
                    for r in image_records
                    if r.get("status") == "skipped"
                )

                # --------------------------------------------------
                # 6. Decide the final source type
                # --------------------------------------------------
                if full_page_ocr_used:
                    source_type = "pdf_ocr"

                elif image_texts and native:
                    source_type = "pdf_mixed"

                elif image_texts:
                    source_type = "pdf_image_ocr"

                else:
                    source_type = "pdf_native"

                # --------------------------------------------------
                # 7. Save page record
                # --------------------------------------------------
                page_records.append({
                    "page": page_no,
                    "source": pdf_path.name,
                    "source_type": source_type,
                    "text": combined_text,
                    "extraction_method": (
                        "pymupdf_native+image_paddleocr"
                        if image_texts and native
                        else (
                            "paddleocr_image"
                            if image_texts
                            else (
                                "paddleocr_page"
                                if full_page_ocr_used
                                else "pymupdf_native"
                            )
                        )
                    ),

                    # Preserve both native and final quality.
                    "native_quality": native_metrics,
                    "quality": final_metrics,

                    # OCR information.
                    "ocr_used": bool(
                        image_texts or full_page_ocr_used
                    ),
                    "full_page_ocr_used": full_page_ocr_used,
                    "ocr_mean_confidence": (
                        full_page_ocr_result.get(
                            "mean_confidence",
                            0.0,
                        )
                        if full_page_ocr_used
                        else 0.0
                    ),
                    "ocr_lines": (
                        full_page_ocr_result.get(
                            "lines",
                            [],
                        )
                        if full_page_ocr_used
                        else []
                    ),

                    # Image information.
                    "image_count": len(image_records),
                    "image_ocr_success_count": processed_image_count,
                    "image_ocr_unresolved_count": unresolved_image_count,
                    "image_skipped_count": skipped_image_count,
                    "images": image_records,
                })

        finally:
            doc.close()

        return page_records