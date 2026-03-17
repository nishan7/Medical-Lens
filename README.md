# Medical Lens

AI agent for reviewing unfair medical bills with OCR, hospital pricing lookups, and one-click dispute generation.

## What It Does

Medical Lens now supports two modes:

- `Chat Mode`: ask pricing questions, upload a bill, and get a streaming assistant response.
- `Analyze Mode`: run an autonomous 4-step pipeline on pasted bill text, a demo example, or an uploaded image/PDF.

### Autonomous Analysis Pipeline

| Step | What Happens | Implementation |
| --- | --- | --- |
| 1. OCR / Parse | Reads a bill file if attached, or parses pasted text into structured line items | FastAPI + OCR pipeline + Nemotron prompt/fallback parser |
| 2. Price Check | Looks up fair-price references from local hospital price transparency data | Existing pricing tools in `backend/tools.py` |
| 3. Issue Detection | Flags likely overcharges and duplicate billing patterns | Deterministic rules + Nemotron analysis pass |
| 4. Action | Generates a dispute letter and a phone script | Nemotron document generation + client-side download |

This is not just a question-answering chatbot. The app can now take a bill, run the full pipeline autonomously, and return something actionable.

## Demo Path

For a fast demo:

1. Start the backend and frontend.
2. Open the app and switch to `Analyze Mode`.
3. Choose `ER Visit ($12,000)` from the example selector, or attach a bill image/PDF.
4. Click `Analyze My Bill`.
5. Walk through:
   - `Summary`
   - `Issues`
   - `Dispute Letter`
   - `Phone Script`
6. Download the dispute letter.

## Key Features

- Streaming healthcare pricing chat over WebSocket
- OCR-based bill ingestion for JPEG, PNG, WEBP, and PDF uploads
- Autonomous bill analysis endpoint at `POST /analyze-bill`
- Demo-ready examples in Analyze Mode
- Fair-price benchmarking against local hospital price transparency data
- Dispute letter and phone script generation
- Graceful fallback behavior for malformed model JSON and OCR failures

## Built With

- NVIDIA Nemotron via `langchain-nvidia-ai-endpoints`
- FastAPI + Uvicorn
- React + TypeScript + Vite
- Playwright for frontend validation
- Local hospital standard charge datasets under `data/`

## Project Layout

- `backend/`
  - FastAPI app, OCR pipeline, autonomous analysis pipeline, tests, and environment config
- `frontend/`
  - React app, chat mode, analyze mode, Playwright tests, and build/lint config
- `data/`
  - Local hospital price transparency datasets used for pricing lookups

## Configuration

Backend configuration is loaded from `backend/.env`.

Important backend settings:

- `NVIDIA_API_KEY`
- `NVIDIA_MODEL`
- `OCR_ENABLED`
- `OCR_MAX_PDF_PAGES`
- `OCR_ALLOWED_TYPES`
- `TOOL_CACHE_ENABLED`

Optional frontend environment variables:

- `VITE_API_BASE_URL`
- `VITE_WS_URL`

If frontend variables are not set, the app defaults to:

- API: `http://localhost:8000`
- WebSocket: `ws://localhost:8000/ws/chat`

## Run Locally

### Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 3000
```

Open:

- `http://127.0.0.1:3000`

## Build

Frontend production build:

```bash
cd frontend
npm run build
```

Build output:

- `frontend/dist/`

The backend does not have a separate bundle/build artifact in this repo. It is deployed by installing dependencies and running the FastAPI app.

## API Surface

- `POST /chat`
- `POST /upload-image`
- `POST /analyze-bill`
- `GET /docs`
- `WS /ws/chat`

## Validation

Validated in this repository:

- Backend unit tests:
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -v`
  - Result: `11/11` tests passed
- Frontend lint:
  - `cd frontend && npm run lint`
  - Result: passed
- Frontend build:
  - `cd frontend && npm run build`
  - Result: passed
- Frontend Playwright chat regression:
  - `cd frontend && npx playwright test tests/chat.spec.ts --reporter=line`
  - Result: `3/3` passed
- Frontend Playwright Analyze Mode smoke test:
  - `cd frontend && npx playwright test tests/analyzer.spec.ts --reporter=line`
  - Result: `2/2` passed

## Notes

- Analyze Mode supports either pasted bill text or an uploaded image/PDF. If both are provided, the attached file is analyzed first.
- OCR and LLM-backed analysis depend on NVIDIA services. The backend includes fallback behavior so malformed JSON or OCR failures do not crash the pipeline.
- A plain-text companion document remains available at `readme.txt`.
