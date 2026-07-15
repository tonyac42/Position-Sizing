# Position-Sizing / Sizer

Sizer is a monorepo prototype for a universal position sizing product. It contains a pure deterministic Python engine, an API, a no-build browser UI, LLM interface artifacts, docs, and tests.

## Repo layout

- `packages/sizer_engine`: deterministic engine with schemas, Kelly solvers, shrinkage, layered caps, tail/capacity/lockup overlays, diagnostics, and structured refusals.
- `apps/api`: FastAPI service exposing `/v1` endpoints, server-side API keys, SQLite audit/idempotency/track-record state, CORS, and structured logging.
- `tools/ui`: no-build HTML+JS app. It contains no sizing logic; it only submits canonical requests to the API and renders responses.
- `tools`: LLM function schema and MCP tool documentation.
- `docs`: methodology docs and constraint-layer explanations.

## Run locally

Terminal 1 — start the API in local dev mode:

```bash
export PYTHONPATH="$PWD/packages/sizer_engine:$PWD/apps/api"
export SIZER_DEV_MODE=1
python -m uvicorn sizer_api.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — start the UI:

```bash
cd tools/ui
npm run dev
```

Open `http://localhost:5173`, keep the API base URL as `http://localhost:8000`, and submit the prefilled prediction scenario. The UI should show a shrinkage warning, a capital-lockup warning, and a highlighted binding constraint.

## Run authenticated API mode

```bash
export PYTHONPATH="$PWD/packages/sizer_engine:$PWD/apps/api"
export SIZER_DB="$PWD/sizer.sqlite3"
python -m sizer_api.main create-dev-key --name local --scopes size:read,portfolio:write,track:write
python -m uvicorn sizer_api.main:app --host 127.0.0.1 --port 8000
```

Use the printed key as `Authorization: Bearer <key>`.

## Verify a sizing response with curl

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/size \
  -H 'content-type: application/json' \
  --data '{"bankroll":10000,"trade_type":"prediction","payoff_structure":"binary","edge_estimate":{"win_probability":0.5},"edge_source":"guess","sample_size":120,"entry_price":0.3,"structural_max_loss":0.3,"expected_time_in_trade_days":180,"exploration_override":true}'
```

## Docker Compose

```bash
docker compose up --build
curl -fsS -X POST http://127.0.0.1:8000/v1/size \
  -H 'content-type: application/json' \
  --data '{"bankroll":10000,"trade_type":"prediction","payoff_structure":"binary","edge_estimate":{"win_probability":0.5},"edge_source":"guess","sample_size":120,"entry_price":0.3,"structural_max_loss":0.3,"expected_time_in_trade_days":180,"exploration_override":true}'
```

## Test and lint

```bash
ruff check .
ruff format --check .
pytest -q
tests/e2e_ui_roundtrip.sh
```
