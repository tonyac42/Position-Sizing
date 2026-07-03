# Position-Sizing / Sizer

Sizer is a monorepo prototype for a universal position sizing engine. It contains:

- `packages/sizer_engine`: pure deterministic Python engine with schemas, Kelly solvers, shrinkage, constraint caps, tail/capacity/lockup overlays, diagnostics, and structured refusals.
- `apps/api`: FastAPI-oriented thin HTTP layer over the engine with `/v1` endpoints, API-key scope checks, SQLite audit logging, instrument metadata, portfolio state, track records, and scenario comparison.
- `tools`: LLM function schema and MCP documentation routed through the API contract.
- `tools/ui`: React placeholder proving the UI boundary; no client-side sizing logic is implemented.
- `docs`: constraint documentation stubs.

Run the engine tests with:

```bash
PYTHONPATH=packages/sizer_engine pytest -q
```
