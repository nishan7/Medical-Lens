Medical Lens
============

Primary project documentation now lives in README.md.

This plain-text companion summarizes the same project at a high level.

Overview
--------
Medical Lens is an AI agent for reviewing medical bills.

It supports two product modes:
- Chat Mode: ask pricing questions and upload a bill for streaming assistant help
- Analyze Mode: run a one-click bill analysis pipeline on demo text, pasted text,
  or an uploaded image/PDF

Autonomous Analysis Pipeline
----------------------------
1. OCR / Parse
   - Reads an attached image/PDF or parses pasted bill text into line items
2. Price Check
   - Benchmarks line items against local hospital price transparency data
3. Issue Detection
   - Flags likely overcharges and duplicate billing patterns
4. Action
   - Generates a dispute letter and a phone script

Main Features
-------------
- WebSocket chat UI
- OCR bill ingestion
- Autonomous `POST /analyze-bill` endpoint
- Demo examples in Analyze Mode
- Dispute letter download
- Fair-price lookup using local hospital pricing datasets

Run Locally
-----------
Backend:
`cd backend`
`pip install -r requirements.txt`
`python -m uvicorn main:app --host 127.0.0.1 --port 8000`

Frontend:
`cd frontend`
`npm install`
`npm run dev -- --host 127.0.0.1 --port 3000`

Open:
`http://127.0.0.1:3000`

Build
-----
Frontend build:
`cd frontend`
`npm run build`

Validation
----------
- Backend tests passed: 11/11
- Frontend lint passed
- Frontend build passed
- Chat Playwright tests passed: 3/3
- Analyze Mode Playwright tests passed: 2/2

Main Endpoints
--------------
- `POST /chat`
- `POST /upload-image`
- `POST /analyze-bill`
- `GET /docs`
- `WS /ws/chat`

See README.md for the full Markdown version.
