import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("NVIDIA_API_KEY", "test-key")

import main  # noqa: E402


class MainOCRIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.temp_dir.name)

        self.upload_patch = patch.object(main, "UPLOAD_DIR", self.upload_dir)
        self.upload_patch.start()
        self.upload_dir.mkdir(exist_ok=True)

        self.warm_patch = patch.object(main, "hospital_search_by_name", return_value=[])
        self.warm_patch.start()

    def tearDown(self) -> None:
        self.warm_patch.stop()
        self.upload_patch.stop()
        self.temp_dir.cleanup()

    def test_upload_then_chat_includes_ocr_context(self) -> None:
        captured: Dict[str, List[dict]] = {}

        def fake_agent_chat(messages: List[dict]) -> str:
            captured["messages"] = messages
            return "ok"

        with patch.object(
            main.ocr,
            "extract_bill_text",
            return_value={"text": "Line item: TB test 86481", "pages": 1, "warnings": []},
        ):
            with patch.object(main.llm, "agent_chat", side_effect=fake_agent_chat):
                with TestClient(main.app) as client:
                    upload = client.post(
                        "/upload-image",
                        files={"file": ("bill.jpg", b"image-bytes", "image/jpeg")},
                    )
                    self.assertEqual(upload.status_code, 200)
                    upload_payload = upload.json()
                    self.assertEqual(upload_payload["ocr_status"], "ready")
                    image_id = upload_payload["image_id"]

                    response = client.post(
                        "/chat",
                        json={
                            "messages": [{"role": "user", "content": "summarize this bill"}],
                            "image_id": image_id,
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["message"]["content"], "ok")

        system_messages = [m for m in captured["messages"] if m.get("role") == "system"]
        self.assertTrue(any("MEDICAL_BILL_OCR_START" in m["content"] for m in system_messages))
        self.assertTrue(any("TB test 86481" in m["content"] for m in system_messages))

    def test_upload_failure_is_marked_and_chat_gets_unavailable_instruction(self) -> None:
        captured: Dict[str, List[dict]] = {}

        def fake_agent_chat(messages: List[dict]) -> str:
            captured["messages"] = messages
            return "fallback-ok"

        with patch.object(main.ocr, "extract_bill_text", side_effect=RuntimeError("ocr boom")):
            with patch.object(main.llm, "agent_chat", side_effect=fake_agent_chat):
                with TestClient(main.app) as client:
                    upload = client.post(
                        "/upload-image",
                        files={"file": ("bill.png", b"image-bytes", "image/png")},
                    )
                    self.assertEqual(upload.status_code, 200)
                    upload_payload = upload.json()
                    self.assertEqual(upload_payload["ocr_status"], "failed")
                    self.assertIn("ocr_error", upload_payload)
                    image_id = upload_payload["image_id"]

                    response = client.post(
                        "/chat",
                        json={
                            "messages": [{"role": "user", "content": "what line items are on my bill?"}],
                            "image_id": image_id,
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["message"]["content"], "fallback-ok")

        system_messages = [m for m in captured["messages"] if m.get("role") == "system"]
        self.assertTrue(any("MEDICAL_BILL_OCR_UNAVAILABLE_START" in m["content"] for m in system_messages))
        self.assertTrue(any("ocr boom" in m["content"] for m in system_messages))

    def test_ws_chat_includes_ocr_context(self) -> None:
        image_id = "ws-test-image"
        (self.upload_dir / f"{image_id}.ocr.txt").write_text("Facility fee: $220", encoding="utf-8")
        (self.upload_dir / f"{image_id}.ocr.meta.json").write_text(
            json.dumps({"image_id": image_id, "filename": "bill.pdf", "page_count": 2}),
            encoding="utf-8",
        )

        captured: Dict[str, List[dict]] = {}

        async def fake_stream_events(messages: List[dict], request_id: str):
            captured["messages"] = messages
            yield {"type": "token", "delta": "stream-ok", "request_id": request_id}
            yield {"type": "done", "request_id": request_id}

        with patch.object(main.llm, "stream_events", new=fake_stream_events):
            with TestClient(main.app) as client:
                with client.websocket_connect("/ws/chat") as websocket:
                    websocket.send_json(
                        {
                            "request_id": "req-1",
                            "messages": [{"role": "user", "content": "review this bill"}],
                            "image_id": image_id,
                        }
                    )
                    first = websocket.receive_json()
                    second = websocket.receive_json()

        self.assertEqual(first["type"], "token")
        self.assertEqual(second["type"], "done")
        system_messages = [m for m in captured["messages"] if m.get("role") == "system"]
        self.assertTrue(any("MEDICAL_BILL_OCR_START" in m["content"] for m in system_messages))
        self.assertTrue(any("Facility fee: $220" in m["content"] for m in system_messages))


if __name__ == "__main__":
    unittest.main()

