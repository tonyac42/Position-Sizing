# Sizer Design Decisions

- The Milestone 2 UI is a single-file no-build HTML+JS application. This keeps the UI dependency-light and prevents a half-Vite/half-static setup. `npm run dev` now uses a small Node static server, not Python's `http.server` script.
- The UI contains zero sizing logic. It builds canonical JSON requests, calls `/v1/size` or `/v1/scenarios/compare`, and renders the returned recommendation, warnings, refusal state, and cap table.
- API authentication now uses server-side SQLite API keys. Client-supplied scope headers are ignored. `SIZER_DEV_MODE=1` is allowed only for local development and logs a warning at startup.
- Idempotency is stored by `(api_key, idempotency_key, input_hash)` and returns the stored byte-identical response on replay.
- Track-record outcomes are stored as R-multiples and populate `sample_size` plus `realized_results` in account mode. Layer 0 blends claimed and realized edge with realized weight `min(1, n/300)`.
- Prediction-market probability shrinkage was restored to the spec's halfway-to-market default for uncalibrated users. Golden scenario 4 now expects about 2.93% risk after 50% probability shrinkage, quarter Kelly, and 180-day lockup discount.
- Classification precedence is explicit: payoff-shape signals (`lottery`, `premium`, `prediction`) beat holding-period `position` inference, with a warning note when both fire.
- Monte Carlo diagnostics now sample the actual discrete outcome distribution or a win/loss distribution from the working probability and odds. Losing-streak diagnostics compute a streak length with about 5% probability over 100 trades rather than hardcoding five losses.
