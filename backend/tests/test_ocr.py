import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    import fitz
except ImportError:  # pragma: no cover - only when dependency missing.
    fitz = None

from ocr import _extract_text_from_zip_bytes, _rasterize_pdf_pages, _resolve_api_key, extract_bill_text


class OCRModuleTests(unittest.TestCase):
    def test_extract_text_from_zip_bytes(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("plain.txt", "Patient: Jane Doe\nAmount Due: $120")
            archive.writestr(
                "structured.json",
                json.dumps(
                    {
                        "pages": [
                            {"lines": [{"text": "Procedure: TB Test"}, {"text": "Rate: $86.13"}]},
                        ]
                    }
                ),
            )

        text, warnings = _extract_text_from_zip_bytes(payload.getvalue())
        self.assertIn("Amount Due: $120", text)
        self.assertIn("Procedure: TB Test", text)
        self.assertEqual(warnings, [])

    def test_pdf_rasterize_respects_max_pages(self) -> None:
        if fitz is None:
            self.skipTest("PyMuPDF is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "sample.pdf"
            with fitz.open() as doc:
                for _ in range(5):
                    page = doc.new_page()
                    page.insert_text((72, 72), "Medical bill page")
                doc.save(pdf_path)

            page_images = _rasterize_pdf_pages(pdf_path, max_pages=3)
            self.assertEqual(len(page_images), 3)
            self.assertTrue(all(isinstance(item, bytes) and len(item) > 0 for item in page_images))

    def test_extract_bill_text_prefers_embedded_pdf_text(self) -> None:
        if fitz is None:
            self.skipTest("PyMuPDF is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "embedded.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text(
                    (72, 72),
                    "Medical claim summary\nProvider: Santa Clara Valley Medical Center\nAmount billed: $467.00\nYour share: $0.00",
                )
                doc.save(pdf_path)

            with patch("ocr._rasterize_pdf_pages") as rasterize_mock:
                result = extract_bill_text(pdf_path, api_key="")

            self.assertIn("Santa Clara Valley Medical Center", result["text"])
            self.assertEqual(result["pages"], 1)
            self.assertIn("Used embedded PDF text extraction.", result["warnings"])
            rasterize_mock.assert_not_called()

    def test_resolve_api_key_uses_env_and_no_hardcoded_literal(self) -> None:
        os.environ["NVIDIA_API_KEY"] = "env-test-key"
        self.assertEqual(_resolve_api_key(None), "env-test-key")

        ocr_source = Path(__file__).resolve().parents[1] / "ocr.py"
        source = ocr_source.read_text(encoding="utf-8")
        self.assertNotIn("nvapi-", source)


if __name__ == "__main__":
    unittest.main()
