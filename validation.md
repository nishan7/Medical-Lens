# RightCost Validation Guide (for Codex Agent)

Use this file as the single source of truth when validating this app.

## Goal
Validate chat quality for hospital pricing queries, with focus on:
- tool usage reliability,
- streaming behavior in UI,
- correct insurer/location handling,
- no raw internal errors shown to users,
- bill/document review quality for uploaded claims and medical bills.

## Environment
- Backend: `cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000`
- Frontend: `cd frontend && npm run dev -- --host 127.0.0.1 --port 5173`
- UI URL: `http://127.0.0.1:5173`
- API URL: `http://127.0.0.1:8000`
- WS URL: `ws://127.0.0.1:8000/ws/chat`

## Required Test Cases
Run these prompts and record pass/fail with short evidence.

### Pricing Query Validation

1. `What is the cheapest TB test with insurance?`
Expected: asks insurance follow-up or clearly explains insurer assumptions.

2. `What is the cheapest TB test for me?`
Expected: asks follow-up (insurance and/or location).

3. `What is the cheapest TB test with Aetna?`
Expected: direct Aetna-specific answer (no payer substitution).

4. `What is the cheapest TB test?`
Expected: direct cheapest result.

5. `What is best price for TB test in San Jose?`
Expected: city-aware answer with San Jose context.

6. `what are other hospitals who do TB test`
Expected: asks city clarification or lists hospitals, but never returns raw internal error text.

### Bill / Document Validation

Use an uploaded bill or claim document, such as `uploads/claims.pdf`, and validate the following prompts.

7. `Summarize this medical bill and list the charges you can identify.`
Expected: extracts the main line items and amounts from the uploaded document.

8. `Does this bill look good?`
Expected: gives a judgment-oriented answer, not just a repeated summary. Should mention obvious concerns, caveats, or missing information.

9. `Tell me problems in this bill or inconsistencies.`
Expected: identifies concrete issues or ambiguities in the bill, such as:
- denied items,
- missing monetary amounts,
- unclear patient responsibility,
- totals that are inferred instead of explicitly shown,
- places where the document looks like a claim summary rather than a final bill/EOB.


10. `What should I question or verify on this bill?`
Expected: returns actionable follow-up checks, not generic filler.

11. `What is my total responsibility on this bill?`
Expected: answers with a cautious total if supported by the document, and clearly flags any missing or uncertain items.



### Bill-Specific Failure Examples
Mark as FAIL if bill-review answers do any of these:
- only repeat extracted charges when the user asked for judgment or inconsistencies,
- claim the bill is fine without referencing missing/denied/unclear items,
- invent line items, totals, or insurer decisions not visible in the document,
- expose OCR/provider/internal error text directly to the user,
- ignore the uploaded bill context and answer generically.

## Streaming Validation
- Confirm WS returns incremental `token` events (not only `done`).
- In UI, assistant text should visibly grow over time (not appear only at end).
- Ignore stale WS events from old request IDs.

## Failure Conditions
Mark as FAIL if any of these appear:
- Empty assistant output.
- Generic failure text (`agent/tool execution error`, `need more steps`, `please retry`) for normal prompts.
- Insurer mismatch (asked for one payer, answered with another without explicit caveat).
- Location mismatch (city requested but response not city-aware).
- Raw internal stack/error text shown in UI.
- Bill-review mismatch (asked for problems/inconsistencies, answered only with a charge list).
- Uploaded-bill follow-up loses attachment context.

## Report Format
Return:
- strict pass count (`x/y`),
- list of failed prompts,
- one-line root cause guess per failure,
- backend vs UI discrepancy notes,
- next 3 highest-priority fixes.
