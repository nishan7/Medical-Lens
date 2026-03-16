Medical Lens
========================

Overview
--------
This project is a two-part application for reviewing hospital pricing data and
medical bill content.

- Backend: FastAPI service that handles chat, bill uploads, OCR extraction, and
  tool-assisted price lookups.
- Frontend: React + TypeScript + Vite chat UI with streaming responses and file
  attachment support.


Core Features
-------------
1. Streaming healthcare pricing chat
   - The frontend opens a WebSocket connection to the backend and renders
     assistant output incrementally.

2. Medical bill upload support
   - Users can attach JPEG, PNG, WEBP, or PDF files.
   - The backend stores the upload and runs OCR extraction.
   - OCR text is injected into the chat context so the assistant can reason
     about bill line items and charges.

3. OCR fallback behavior
   - If OCR succeeds, the extracted text is attached to the conversation.
   - If OCR fails, the app records the failure and instructs the assistant not
     to invent bill details.
   - The UI surfaces upload/OCR errors instead of hanging silently.

4. Hospital pricing data search
   - The backend exposes tool-assisted search over local pricing datasets.
   - Supported lookups include:
     - Search by description or procedure name
     - Search by billing/procedure code
     - List insurers found in negotiated-rate data
     - Find lowest self-pay or negotiated prices for a query

5. Tool result caching
   - The backend can cache tool responses in SQLite to reduce repeated lookup
     cost.


Project Layout
--------------
- backend/
  - FastAPI app, OCR pipeline, chat agent, tests, and environment config
- frontend/
  - React application, Playwright tests, lint/build config
- data/
  - Local hospital pricing datasets used by the backend tools


Prerequisites
-------------
- Python 3.x
- Node.js + npm
- A valid NVIDIA API key for chat and OCR features


Configuration
-------------
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

If frontend env vars are not set, the UI defaults to:
- HTTP API: `http://localhost:8000`
- WebSocket API: `ws://localhost:8000/ws/chat`


How To Run Locally
------------------
Backend
1. Go to the backend folder:
   `cd backend`
2. Create and activate a virtual environment if needed.
3. Install dependencies:
   `pip install -r requirements.txt`
4. Start the API:
   `python -m uvicorn main:app --host 127.0.0.1 --port 8000`

Frontend
1. Open a second terminal.
2. Go to the frontend folder:
   `cd frontend`
3. Install dependencies:
   `npm install`
4. Start the dev server on port 3000:
   `npm run dev -- --host 127.0.0.1 --port 3000`

Open the app in a browser:
- `http://127.0.0.1:3000`

Notes
- The checked-in Playwright config uses `http://localhost:3000` as its base
  URL, so running the frontend on port 3000 is the simplest path for local E2E
  validation.
- The backend listens on port 8000 by default.


How To Build
------------
Frontend production build:
1. `cd frontend`
2. `npm run build`

The built frontend output is generated in:
- `frontend/dist/`

Backend
- The backend is a Python service and does not have a separate bundled build
  step in this repository.
- Deployment is typically done by installing dependencies and running the
  FastAPI app with Uvicorn.


Validation Performed
--------------------
The following validations were run successfully in this repository:

1. Backend unit tests
   - Command:
     `cd backend && ./.venv/bin/python -m unittest discover -s tests -v`
   - Result:
     8 tests passed

2. Frontend production build
   - Command:
     `cd frontend && npm run build`
   - Result:
     Passed

3. Frontend Playwright chat tests
   - Command:
     `cd frontend && npx playwright test tests/chat.spec.ts --reporter=line`
   - Result:
     3 tests passed

4. Manual browser smoke validation
   - Verified app load
   - Verified chat send/receive flow over WebSocket
   - Verified attachment upload behavior
   - Verified OCR failure fallback messaging in the UI


Current Validation Notes
------------------------
- Frontend lint currently reports one issue in `frontend/playwright.config.ts`
  for an unused `devices` import.
- OCR depends on NVIDIA services. During manual validation, the OCR endpoint
  returned a server-side 500 for one uploaded test image. The application
  handled that case correctly by marking OCR as failed and guiding the user to
  re-upload a clearer file or paste bill text.


API Surface
-----------
Main backend endpoints:
- `POST /chat`
- `POST /upload-image`
- `GET /docs`
- `WS /ws/chat`


Summary
-------
This project provides a chat-based interface for exploring hospital pricing
data and reviewing uploaded medical bills with OCR-assisted context. The
frontend build, backend tests, and end-to-end chat validation are all working,
with one minor frontend lint issue currently outstanding.
