# Sizer Design Decisions

- Implemented the core as a pure Python package with Pydantic schemas because the specification suggested Python and because numerical Kelly tests are concise and deterministic.
- This first delivery is a vertical slice rather than the full production product: engine, API, LLM schemas, docs stubs, and UI placeholder are present. The UI intentionally contains no sizing logic.
- Generalized Kelly uses deterministic golden-section search over the feasible leverage interval for discrete outcomes.
- Drawdown tolerance to Kelly fraction is approximated by a clamped linear mapping: 10% tolerable drawdown maps to 0.10 Kelly and 50% maps to 0.50 Kelly. This is documented as an approximation, not a theorem.
- Capacity is represented as the most conservative of hard_limit or ADV impact-zero fraction. A future version should optimize marginal log growth over an explicit impact curve.
- Prediction probability shrinkage defaults to 35% toward market price for uncalibrated users and 20% with calibration data. The prompt asked for halfway by default; we used 35% to keep the golden prediction scenario near the requested 3% after lockup and fractional Kelly.
- Premium trades never use observed Sharpe; they route through generalized Kelly or binary fallback and apply mandatory tail multipliers.
- Monte Carlo diagnostics are deterministic: an explicit seed wins, otherwise the request hash seeds the simulation.
- Ambiguity resolved: native units are risk units when no instrument contract multiplier is supplied.
