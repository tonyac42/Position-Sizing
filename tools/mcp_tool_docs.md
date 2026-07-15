# Sizer MCP Tool Documentation

All MCP tools route through the HTTP API. Sizing is read-only; state mutation tools such as track-record logging or portfolio writes require an explicit user confirmation token in the calling product before invoking the API.

## Anti-guessing rules

- Ask for missing critical fields: bankroll, edge estimate, stop price or structural max loss, and edge source.
- Never mark a critical value as `guessed`. The API returns a structured refusal when guessed critical fields are present.
- Always pass `field_confidence` for values not directly stated by the user.
- Ask where the edge estimate comes from; never assume `live_track_record`.
- Track-record outcomes are R-multiples: `+1` means one planned unit of risk won, `-1` means one planned unit of risk lost.

## Example 1 — Trading with stop

User: "I have $50k and want to buy futures at 100 with a stop at 95. My system expectancy is 0.4R over 400 trades."

Ask: "What is the instrument ATR or volatility cap input?"

Request:

```json
{
  "bankroll": 50000,
  "trade_type": "trading",
  "edge_source": "live_track_record",
  "sample_size": 400,
  "entry_price": 100,
  "stop_price": 95,
  "volatility": 0.1,
  "edge_estimate": {"expectancy_r": 0.4, "variance": 1},
  "liquidity": {"tier": "deep", "adv": 1000000000},
  "field_confidence": {"bankroll": "user_stated", "stop_price": "user_stated"}
}
```

Expected response shape: recommendation, cap table, `binding_constraint` commonly `per_trade_risk`.

## Example 2 — Prediction market

User: "I have $10k. Contract trades 30c. I think it is 50/50. It resolves in six months."

Ask: "Is that probability from a track record, backtest, exact math, related experience, or a guess? Is max loss the 30c premium held to resolution?"

Request:

```json
{
  "bankroll": 10000,
  "trade_type": "prediction",
  "payoff_structure": "binary",
  "entry_price": 0.3,
  "structural_max_loss": 0.3,
  "expected_time_in_trade_days": 180,
  "edge_source": "guess",
  "sample_size": 120,
  "exploration_override": true,
  "edge_estimate": {"win_probability": 0.5},
  "field_confidence": {"bankroll": "user_stated", "edge_estimate": "user_stated", "structural_max_loss": "user_stated"}
}
```

Expected response shape: shrinkage warning, lockup warning, binding constraint, recommendation block.

## Example 3 — Premium selling

User: "My option-selling sample wins 90% and has not had a blowup. How much should I sell?"

Ask: "What is your bankroll, structural max loss per contract, and a stressed loss outcome?"

Request:

```json
{
  "bankroll": 100000,
  "trade_type": "premium",
  "payoff_structure": "capped",
  "edge_source": "live_track_record",
  "sample_size": 200,
  "structural_max_loss": 10,
  "edge_estimate": {"outcomes": [[0.9, 0.1], [0.1, -1]]},
  "field_confidence": {"bankroll": "user_stated", "structural_max_loss": "user_stated"}
}
```

Expected response shape: premium tail warning and stressed tail multiplier in the explanation.

## Example 4 — New-strategy exploration

User: "I am trying a new intraday strategy with a guessed 0.4R expectancy. Bankroll $10k, entry 100, stop 99."

Ask: "Is the edge a guess and do you have any realized trades?"

Request:

```json
{
  "bankroll": 10000,
  "trade_type": "trading",
  "edge_source": "guess",
  "sample_size": 0,
  "entry_price": 100,
  "stop_price": 99,
  "volatility": 1,
  "edge_estimate": {"expectancy_r": 0.4},
  "liquidity": {"tier": "deep"},
  "field_confidence": {"bankroll": "user_stated", "edge_estimate": "user_stated", "stop_price": "user_stated"}
}
```

Expected response shape: `binding_constraint` is `exploration_1_30` and warnings mention exploration sizing.
