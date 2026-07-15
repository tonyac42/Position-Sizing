# Milestone 2 Acceptance Map

## Priority 1 — Functional UI

- Working no-build UI: `cd tools/ui && npm run dev`, then open `http://localhost:5173`.
- API round trip: `tests/e2e_ui_roundtrip.sh` starts the API, submits the same JSON path used by the UI, and asserts a binding constraint plus shrinkage warning.
- Golden scenario 4 is prefilled in the UI; submit it to see shrinkage, lockup, and binding constraint.

## Priority 2 — API auth/browser/idempotency

- Contract tests: `pytest tests/test_api.py -q` verifies valid key, missing scope, missing/invalid/forged key, malformed body, refusal, idempotency, and track-record account mode.
- CORS and dev mode are configured in `apps/api/sizer_api/main.py`.

## Priority 3 — Track-record learning loop

- Direct blend tests: `pytest tests/test_engine.py -q -k realized_blend_weights`.
- Account-mode loop: `pytest tests/test_api.py -q -k track_record_drives_account_mode`.

## Priority 4 — LLM artifacts

- Schema/golden validation: `pytest tests/test_llm_schema.py -q`.
- MCP examples live in `tools/mcp_tool_docs.md`.

## Priority 5 — Engine quality

- Formatting/lint: `ruff check .` and `ruff format --check .`.
- Regression/property tests: `pytest tests/test_engine.py -q`.

## Priority 6 — Ops floor

- CI workflow: `.github/workflows/ci.yml` runs ruff, pytest, schema validation, and the e2e script.
- Docker compose: `docker compose up --build`, then run the curl command documented in `README.md`.
- Constraint docs: `docs/constraints.md` documents layers 0–5.
