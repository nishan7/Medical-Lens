# LLM Validation Test Cases

- "What is the cheapest TB test with insurance?" -> asks insurance follow-up.
- "What is the cheapest TB test for me?" -> asks insurance follow-up.
- "What is the cheapest TB test with Aetna?" -> answers directly with Aetna pricing.
- "What is the cheapest TB test?" -> answers directly.
- "What is best price for TB test in San Jose?" -> answers directly with San Jose-filtered TB pricing.

## Latest Validation Run (2026-03-13)

- FAIL: "What is the cheapest TB test with insurance?" returned direct pricing, no insurance follow-up.
- FAIL: "What is the cheapest TB test for me?" returned direct pricing, no insurance follow-up.
- PASS: "What is the cheapest TB test with Aetna?" answered direct with Aetna pricing.
- PASS: "What is the cheapest TB test?" answered direct.
- PASS: "What is best price for TB test in San Jose?" answered direct with San Jose pricing.

Summary: 3/5 passed.

### Consistency Check (3 repeated runs each)

- "What is the cheapest TB test with insurance?" follow-up count: 0/3 (always direct).
- "What is the cheapest TB test for me?" follow-up count: 0/3 (always direct).

## Backend Validation Run (2026-03-13, after restart)

- HTTP `/chat` summary: 3/6 passed.
- WebSocket `/ws/chat` smoke test for "what are other hospitals who do TB test": PASS (streamed tokens, no raw internal error text).

### HTTP Case Results

- FAIL: "What is the cheapest TB test with insurance?" returned direct pricing instead of insurance follow-up.
- FAIL: "What is the cheapest TB test for me?" returned direct pricing instead of insurance follow-up.
- FAIL: "What is the cheapest TB test with Aetna?" returned fallback error text from bounded `/chat`.
- PASS: "What is the cheapest TB test?" answered directly.
- PASS: "What is best price for TB test in San Jose?" answered directly with San Jose pricing.
- PASS: "what are other hospitals who do TB test" listed hospitals.

### Observed Root Cause for Aetna Failure

- Backend log showed LangChain/NVIDIA tool-call runtime failure:
  - `ValueError: Message {'role': 'tool', 'content': None, ...} has no content.`
- This aligns with startup warning that `nvidia/nemotron-3-super-120b-a12b` tool support is unknown/unsupported.

## Validation After Model Change (2026-03-13)

Configured model:
- `NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b`

HTTP `/chat` results:
- FAIL: "What is the cheapest TB test with insurance?" still answers direct pricing (no insurance follow-up).
- FAIL: "What is the cheapest TB test for me?" still answers direct pricing (no insurance follow-up).
- PASS: "What is the cheapest TB test with Aetna?" answers direct with Aetna pricing.
- PASS: "What is the cheapest TB test?" answers direct.
- PASS: "What is best price for TB test in San Jose?" answers direct with San Jose pricing.
- PASS (follow-up behavior): "what are other hospitals who do TB test" asks location clarification instead of failing.

Summary: 4/6 strict passes on original expectations; 5/6 if location follow-up is accepted for the hospitals query.

## Retry Validation Run (2026-03-13)

Configured model:
- `NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b`

HTTP `/chat` results:
- FAIL: "What is the cheapest TB test with insurance?" still answers direct pricing.
- FAIL: "What is the cheapest TB test for me?" still answers direct pricing.
- PASS: "What is the cheapest TB test with Aetna?" answers direct with Aetna pricing.
- PASS: "What is the cheapest TB test?" answers direct.
- PASS: "What is best price for TB test in San Jose?" answers direct with San Jose pricing.
- PASS (clarification): "what are other hospitals who do TB test" asks for location.

Summary: 4/6 strict passes; no raw internal agent error surfaced.

WebSocket `/ws/chat` check:
- PASS: streamed response completed with 65 token chunks for "what are other hospitals who do TB test".
- PASS: no raw `[ERROR: Agent failed ... process_single_item_agent ...]` text observed.

## Validation After Env Update + Restart (2026-03-13)

Configured model:
- `NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b`

HTTP `/chat` strict results:
- FAIL: "What is the cheapest TB test with insurance?" still returns direct pricing (no insurance-specific follow-up).
- FAIL: "What is the cheapest TB test for me?" asks a follow-up, but not insurance-specific.
- PASS: "What is the cheapest TB test with Aetna?" answers direct with Aetna pricing.
- PASS: "What is the cheapest TB test?" answers direct.
- PASS: "What is best price for TB test in San Jose?" answers direct with San Jose pricing.
- PASS: "what are other hospitals who do TB test" asks location clarification.

Strict summary: 4/6.

WebSocket `/ws/chat` smoke:
- PASS: streaming response completed (57 chunks) for "what are other hospitals who do TB test".
- PASS: no raw internal timeout/error text surfaced.

## Validation Run (2026-03-13, based on top test matrix)

HTTP `/chat` strict results (5 cases):
- FAIL: "What is the cheapest TB test with insurance?" returned direct pricing instead of insurance follow-up.
- FAIL: "What is the cheapest TB test for me?" returned direct pricing instead of insurance follow-up.
- PASS: "What is the cheapest TB test with Aetna?" answered direct with Aetna pricing.
- PASS: "What is the cheapest TB test?" answered directly.
- PASS: "What is best price for TB test in San Jose?" answered directly with San Jose pricing.

Strict summary: 3/5.

Consistency check (3 runs each):
- "What is the cheapest TB test with insurance?" insurance follow-up count: 1/3.
- "What is the cheapest TB test for me?" insurance follow-up count: 1/3.

WebSocket `/ws/chat` smoke ("what are other hospitals who do TB test"):
- PASS: streamed response completed (112 chunks).
- PASS: no raw internal timeout/error text surfaced.

## Additional Use Cases Added (2026-03-13)

- "List all insurers in your dataset." -> should return insurer list.
- "What is the cheapest TB test with United Healthcare?" -> should return United-specific pricing (or clear no-match/follow-up).
- "What is the cheapest TB test with Blue Shield in San Jose?" -> should apply insurer + city filter together.
- "What are other hospitals who do TB test in San Jose?" -> should return hospital list, not timeout.
- "Show cheapest TB test and include CPT/CDM/RC codes." -> should return cheapest row with codes.
- "What is the cheapest MRI test in San Jose?" -> should return coherent non-TB answer (or explicit no-match), not planner failure text.

## Expanded Validation Run (2026-03-13, HTTP `/chat`)

Environment at run time:
- `NVIDIA_MODEL=nvidia/nemotron-3-nano-30b-a3b`
- `SEARCH_MAX_ROWS=10`

Results:
- FAIL: "What is the cheapest TB test with insurance?" -> returned generic agent/tool error text.
- PASS (partial expectation): "What is the cheapest TB test for me?" -> asked follow-up for hospital and insurance.
- FAIL: "What is the cheapest TB test with Aetna?" -> returned generic agent/tool error text.
- PASS: "What is the cheapest TB test?" -> returned direct price answer.
- FAIL: "What is best price for TB test in San Jose?" -> returned generic agent/tool error text.
- PASS: "List all insurers in your dataset." -> returned insurer list.
- FAIL: "What is the cheapest TB test with United Healthcare?" -> returned response anchored to Aetna pricing (payer mismatch risk).
- FAIL: "What is the cheapest TB test with Blue Shield in San Jose?" -> returned likely wrong negotiated value ($86.13) and mislabeled source.
- FAIL: "What are other hospitals who do TB test in San Jose?" -> returned generic agent/tool error text.
- PASS: "Show cheapest TB test and include CPT/CDM/RC codes." -> returned codes correctly.
- FAIL: "What is the cheapest MRI test in San Jose?" -> returned "Sorry, need more steps to process this request."

Summary: 4/11 pass (counting the "for me" follow-up as a partial pass), with multiple tool-call/runtime regressions in bounded `/chat`.

## Expanded Validation Run (2026-03-13, WebSocket `/ws/chat`)

Single-run sweep (8 cases):
- All cases emitted `done` and no raw transport-level `error` frame.
- First token latency ranged ~0.27s to 0.44s.
- Intermittent empty assistant payload observed for some requests (only newline tokens before `done`).

Targeted variability check (3 runs each):
- "What is best price for TB test in San Jose?" -> 2/3 runs returned empty content (`"\\n\\n\\n\\n"`), 1/3 returned full answer.
- "What is the cheapest TB test with Blue Shield in San Jose?" -> 1/3 runs returned empty content, 2/3 returned full answers.
- "What are other hospitals who do TB test in San Jose?" -> highly variable:
  - one actionable hospital-list answer,
  - one timeout fallback message,
  - one `"Sorry, need more steps to process this request."`

Conclusion: WebSocket transport is stable, but model/tool planner output is nondeterministic and can resolve to empty-text completions.

## UI Validation (Playwright, 2026-03-13)

Validated in headed browser at `http://127.0.0.1:5173`:
- PASS: markdown rendering for standard answer ("cheapest TB with insurance") showed clean heading + bullets.
- PASS: timeout path is user-safe ("temporary model/tool timeout..." shown as assistant message, no raw stack error in UI).
- PASS: "best price for TB test in San Jose" rendered structured markdown when backend produced non-empty text.
- PASS (format check): codes query rendered CPT/CDM/RC with markdown formatting.
- NOTE: markdown output still depends on LLM style; nested lists can still appear in some responses.

Playwright snapshots captured under:
- `.playwright-cli/page-2026-03-13T22-28-56-778Z.yml`
- `.playwright-cli/page-2026-03-13T22-29-17-569Z.yml`
- `.playwright-cli/page-2026-03-13T22-29-38-725Z.yml`
- `.playwright-cli/page-2026-03-13T22-30-59-706Z.yml`
