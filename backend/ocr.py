from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests

try:
    import fitz
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    fitz = None


NVAI_OCR_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/ocdrnet"
NVCF_ASSETS_URL = "https://api.nvcf.nvidia.com/v2/nvcf/assets"
TEXTISH_EXTENSIONS = {".txt", ".md", ".csv"}
JSON_EXTENSIONS = {".json"}
DEFAULT_IMAGE_CONTENT_TYPE = "image/jpeg"
MIN_DIRECT_PDF_TEXT_CHARS = 80
JSON_TEXT_KEYS = {
    "text",
    "value",
    "label",
    "content",
    "recognized_text",
    "ocr_text",
    "transcription",
    "line",
    "word",
    "description",
}


class OCRProcessingError(RuntimeError):
    """Raised when OCR processing cannot be completed."""


def _resolve_api_key(api_key: str | None) -> str:
    key = (api_key or os.getenv("NVIDIA_API_KEY", "")).strip()
    if not key:
        raise OCRProcessingError("NVIDIA_API_KEY is not configured for OCR.")
    return key


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _guess_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or DEFAULT_IMAGE_CONTENT_TYPE


def _upload_asset(
    *,
    data: bytes,
    description: str,
    api_key: str,
    content_type: str,
    timeout_seconds: float,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    payload = {"contentType": content_type, "description": description}
    create_timeout = max(1.0, min(timeout_seconds, 30.0))
    response = requests.post(NVCF_ASSETS_URL, headers=headers, json=payload, timeout=create_timeout)
    response.raise_for_status()
    body = response.json()

    upload_url = body["uploadUrl"]
    asset_id = str(body["assetId"])

    s3_headers = {
        "x-amz-meta-nvcf-asset-description": description,
        "content-type": content_type,
    }
    upload_timeout = max(1.0, timeout_seconds)
    upload_response = requests.put(upload_url, data=data, headers=s3_headers, timeout=upload_timeout)
    upload_response.raise_for_status()

    try:
        return str(uuid.UUID(asset_id))
    except ValueError:
        return asset_id


def _invoke_ocdrnet(*, asset_id: str, api_key: str, timeout_seconds: float) -> bytes:
    inputs = {"image": asset_id, "render_label": False}
    headers = {
        "Content-Type": "application/json",
        "NVCF-INPUT-ASSET-REFERENCES": asset_id,
        "NVCF-FUNCTION-ASSET-IDS": asset_id,
        "Authorization": f"Bearer {api_key}",
    }
    response = requests.post(NVAI_OCR_URL, headers=headers, json=inputs, timeout=max(1.0, timeout_seconds))
    response.raise_for_status()
    return response.content


def _iter_json_text_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if isinstance(child, str):
                if key_lower in JSON_TEXT_KEYS and child.strip():
                    yield child
                continue
            yield from _iter_json_text_values(child)
        return

    if isinstance(value, list):
        for item in value:
            yield from _iter_json_text_values(item)
        return

    if isinstance(value, str) and value.strip():
        yield value


def _extract_text_from_zip_bytes(zip_bytes: bytes) -> Tuple[str, List[str]]:
    texts: List[str] = []
    warnings: List[str] = []

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
            for member in sorted(archive.namelist()):
                if member.endswith("/"):
                    continue

                suffix = Path(member).suffix.lower()
                data = archive.read(member)
                if not data:
                    continue

                if suffix in TEXTISH_EXTENSIONS:
                    decoded = data.decode("utf-8", errors="ignore").strip()
                    if decoded:
                        texts.append(decoded)
                    continue

                if suffix in JSON_EXTENSIONS:
                    try:
                        parsed = json.loads(data.decode("utf-8", errors="ignore"))
                    except json.JSONDecodeError:
                        warnings.append(f"Skipped invalid OCR JSON file: {member}")
                        continue
                    json_texts = [item.strip() for item in _iter_json_text_values(parsed) if item.strip()]
                    if json_texts:
                        texts.append("\n".join(json_texts))
                    continue
    except zipfile.BadZipFile as exc:
        raise OCRProcessingError("OCR response is not a valid zip archive.") from exc

    merged = _normalize_text("\n\n".join(texts))
    return merged, warnings


def _ocr_image_bytes(
    *,
    image_bytes: bytes,
    api_key: str,
    timeout_seconds: float,
    description: str,
    content_type: str = DEFAULT_IMAGE_CONTENT_TYPE,
) -> Tuple[str, List[str]]:
    asset_id = _upload_asset(
        data=image_bytes,
        description=description,
        api_key=api_key,
        content_type=content_type,
        timeout_seconds=timeout_seconds,
    )
    zip_bytes = _invoke_ocdrnet(asset_id=asset_id, api_key=api_key, timeout_seconds=timeout_seconds)
    text, warnings = _extract_text_from_zip_bytes(zip_bytes)
    if not text:
        warnings.append("OCR returned empty text.")
    return text, warnings


def _extract_pdf_text_direct(file_path: Path, max_pages: int) -> Dict[str, Any]:
    if fitz is None:
        return {"text": "", "pages": 0, "warnings": []}

    page_texts: List[str] = []
    with fitz.open(file_path) as document:
        page_count = min(max_pages, document.page_count)
        for page_index in range(page_count):
            text = document.load_page(page_index).get_text("text")
            normalized = _normalize_text(text)
            if normalized:
                page_texts.append(f"[Page {page_index + 1}]\n{normalized}")

    merged_text = _normalize_text("\n\n".join(page_texts))
    warnings: List[str] = []
    if merged_text:
        warnings.append("Used embedded PDF text extraction.")

    return {
        "text": merged_text,
        "pages": page_count if "page_count" in locals() else 0,
        "warnings": warnings,
    }


def _rasterize_pdf_pages(file_path: Path, max_pages: int) -> List[bytes]:
    if max_pages < 1:
        raise OCRProcessingError("OCR_MAX_PDF_PAGES must be at least 1.")
    if fitz is None:
        raise OCRProcessingError("PyMuPDF (fitz) is required for PDF OCR support.")

    images: List[bytes] = []
    with fitz.open(file_path) as document:
        page_count = min(max_pages, document.page_count)
        for page_index in range(page_count):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            images.append(pixmap.tobytes("jpeg"))
    return images


def extract_bill_text(
    file_path: str | Path,
    *,
    api_key: str | None = None,
    max_pdf_pages: int = 3,
    timeout_seconds: float = 60.0,
) -> Dict[str, Any]:
    """Extract OCR text from an image or PDF bill file."""
    path = Path(file_path)
    if not path.exists():
        raise OCRProcessingError(f"File not found: {path}")

    suffix = path.suffix.lower()
    warnings: List[str] = []

    if suffix == ".pdf":
        direct_pdf_result = _extract_pdf_text_direct(path, max_pages=max_pdf_pages)
        direct_text = str(direct_pdf_result.get("text") or "").strip()
        if len(direct_text) >= MIN_DIRECT_PDF_TEXT_CHARS:
            return direct_pdf_result

        resolved_api_key = _resolve_api_key(api_key)
        page_images = _rasterize_pdf_pages(path, max_pages=max_pdf_pages)
        if not page_images:
            raise OCRProcessingError("No pages available in PDF for OCR.")

        page_texts: List[str] = []
        warnings.extend(str(item) for item in (direct_pdf_result.get("warnings") or []) if str(item).strip())
        for idx, page_bytes in enumerate(page_images, start=1):
            page_text, page_warnings = _ocr_image_bytes(
                image_bytes=page_bytes,
                api_key=resolved_api_key,
                timeout_seconds=timeout_seconds,
                description=f"OCR PDF page {idx}: {path.name}",
                content_type=DEFAULT_IMAGE_CONTENT_TYPE,
            )
            warnings.extend(f"Page {idx}: {warning}" for warning in page_warnings)
            if page_text:
                page_texts.append(f"[Page {idx}]\n{page_text}")

        return {
            "text": _normalize_text("\n\n".join(page_texts)),
            "pages": len(page_images),
            "warnings": warnings,
        }

    resolved_api_key = _resolve_api_key(api_key)
    image_bytes = path.read_bytes()
    text, image_warnings = _ocr_image_bytes(
        image_bytes=image_bytes,
        api_key=resolved_api_key,
        timeout_seconds=timeout_seconds,
        description=f"OCR image: {path.name}",
        content_type=_guess_content_type(path),
    )
    warnings.extend(image_warnings)
    return {
        "text": text,
        "pages": 1,
        "warnings": warnings,
    }
