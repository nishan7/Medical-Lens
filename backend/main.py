import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from analyze import router as analyze_router
from config import settings
import llm
import ocr
from schemas import ChatMessage, ChatRequest, ChatResponse
from tools import hospital_search_by_name


app = FastAPI(title="RightCost Backend")
logger = logging.getLogger(__name__)
app.include_router(analyze_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

CONTENT_TYPE_SUFFIX_OVERRIDES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _allowed_ocr_types() -> set[str]:
    return {item.strip().lower() for item in settings.ocr_allowed_types.split(",") if item.strip()}


def _resolve_upload_suffix(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix:
        return suffix

    normalized_type = (content_type or "").lower()
    if normalized_type in CONTENT_TYPE_SUFFIX_OVERRIDES:
        return CONTENT_TYPE_SUFFIX_OVERRIDES[normalized_type]

    guessed = mimetypes.guess_extension(normalized_type)
    if guessed:
        return guessed

    return ".bin"


def _ocr_text_path(image_id: str) -> Path:
    return UPLOAD_DIR / f"{image_id}.ocr.txt"


def _ocr_meta_path(image_id: str) -> Path:
    return UPLOAD_DIR / f"{image_id}.ocr.meta.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _append_ocr_context(messages: List[dict], image_id: str) -> None:
    text_path = _ocr_text_path(image_id)
    meta_path = _ocr_meta_path(image_id)
    meta = _read_json(meta_path)

    if text_path.exists():
        raw_text = text_path.read_text(encoding="utf-8", errors="ignore").strip()
        if raw_text:
            capped_text = raw_text[: max(1, int(settings.ocr_max_text_chars))].strip()
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "MEDICAL_BILL_OCR_START\n"
                        f"image_id: {image_id}\n"
                        f"source_filename: {meta.get('filename') or 'unknown'}\n"
                        f"page_count: {meta.get('page_count') or 'unknown'}\n"
                        "Use this OCR text as bill evidence. "
                        "Use the bill to understand the details of operations performed and the charges for the same. Please care bill records and charges check if it makes sense"
                        "If data is missing or ambiguous, ask a short follow-up question.\n\n"
                        f"{capped_text}\n"
                        "MEDICAL_BILL_OCR_END"
                    ),
                }
            )
            return

    failure_reason = meta.get("ocr_error") or "No OCR text found for the uploaded bill."
    messages.append(
        {
            "role": "system",
            "content": (
                "MEDICAL_BILL_OCR_UNAVAILABLE_START\n"
                f"image_id: {image_id}\n"
                f"reason: {failure_reason}\n"
                "If the user asks about bill/document details, do not invent values. "
                "Ask the user to re-upload a clearer image/PDF or paste bill text.\n"
                "MEDICAL_BILL_OCR_UNAVAILABLE_END"
            ),
        }
    )


@app.on_event("startup")
async def warm_data_cache() -> None:
    # Warm pandas dataset cache once to reduce first tool-call latency.
    await asyncio.to_thread(hospital_search_by_name, "TB test", 1)


async def _run_bounded_agent_chat(messages: List[dict]) -> str:
    timeout_seconds = max(1.0, float(settings.chat_request_timeout_seconds))
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(llm.agent_chat, messages),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("agent_chat timed out after %.1fs", timeout_seconds)
        return (
            "I couldn't complete this request in time. "
            "Please narrow the query (for example, include insurer, city, or hospital)."
        )
    except Exception:
        logger.exception("agent_chat failed")
        return (
            "I hit an agent/tool execution error while processing this request. "
            "Please retry."
        )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    messages: List[dict] = [m.model_dump() for m in request.messages]
    if request.image_id:
        _append_ocr_context(messages, request.image_id)

    content = await _run_bounded_agent_chat(messages)
    return ChatResponse(message=ChatMessage(role="assistant", content=content))


@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...)) -> dict:
    content_type = (file.content_type or "").lower()
    allowed_types = _allowed_ocr_types()
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type or 'unknown'}")

    image_id = uuid4().hex
    suffix = _resolve_upload_suffix(file.filename, content_type)
    stored_filename = f"{image_id}{suffix}"
    target = UPLOAD_DIR / stored_filename
    text_path = _ocr_text_path(image_id)
    meta_path = _ocr_meta_path(image_id)

    data = await file.read()
    target.write_bytes(data)

    ocr_status = "failed"
    ocr_error: str | None = None
    page_count: int | None = None
    warnings: List[str] = []

    if settings.ocr_enabled:
        try:
            ocr_result = await asyncio.to_thread(
                ocr.extract_bill_text,
                target,
                api_key=settings.nvidia_api_key,
                max_pdf_pages=max(1, int(settings.ocr_max_pdf_pages)),
                timeout_seconds=max(1.0, float(settings.ocr_timeout_seconds)),
            )
            raw_text = str(ocr_result.get("text") or "").strip()
            page_count = int(ocr_result.get("pages") or 0)
            warnings = [str(item) for item in (ocr_result.get("warnings") or []) if str(item).strip()]

            if raw_text:
                text_path.write_text(raw_text, encoding="utf-8")
                ocr_status = "ready"
            else:
                ocr_error = "OCR completed but no readable text was extracted."
        except Exception as exc:
            logger.exception("OCR extraction failed for %s", stored_filename)
            ocr_error = str(exc)
    else:
        ocr_error = "OCR is disabled by server configuration."

    meta_payload = {
        "image_id": image_id,
        "filename": file.filename or stored_filename,
        "stored_filename": stored_filename,
        "content_type": content_type,
        "ocr_status": ocr_status,
        "ocr_error": ocr_error,
        "page_count": page_count,
        "warnings": warnings,
    }
    _write_json(meta_path, meta_payload)

    response = {
        "image_id": image_id,
        "filename": file.filename or stored_filename,
        "ocr_status": ocr_status,
    }
    if ocr_error:
        response["ocr_error"] = ocr_error
    if page_count:
        response["page_count"] = page_count
    return response


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            messages_data = data.get("messages", [])
            image_id = data.get("image_id")
            request_id = str(data.get("request_id") or uuid4())

            messages: List[dict] = list(messages_data)
            if image_id:
                _append_ocr_context(messages, str(image_id))

            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            async def produce() -> None:
                try:
                    async for event in llm.stream_events(messages=messages, request_id=request_id):
                        await queue.put(event)
                except Exception as exc:
                    await queue.put({"type": "error", "message": str(exc), "request_id": request_id})
                finally:
                    await queue.put(None)

            producer_task = asyncio.create_task(produce())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    await websocket.send_json(event)
            finally:
                if not producer_task.done():
                    producer_task.cancel()
                    await asyncio.gather(producer_task, return_exceptions=True)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": str(exc), "request_id": str(uuid4())})
