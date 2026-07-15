# Constraint Layers

## Layer 0 — Edge estimation and exploration

Sizer shrinks claimed edge by source and uses stored track records when available. Defaults: guessed edges are discounted and unproven strategies pass through exploration gates of 0.5%, 1%, and 2% risk tiers at 30, 100, and 300 trades. To loosen this layer, add a verified track record or explicitly override exploration after understanding the risk.

## Layer 1 — Kelly engine

Sizer computes full Kelly with binary, continuous, or generalized solvers, then applies the user's Kelly fraction. Default Kelly fraction is 0.25 unless a drawdown tolerance or explicit fraction is supplied. To loosen it, increase the Kelly fraction within the configured 0.1–0.5 range.

## Layer 2 — Risk constraint caps

Sizer independently computes per-trade risk, volatility, portfolio heat, and correlation bucket caps. Defaults are 2% per-trade risk, 1% one-ATR exposure, 20% portfolio heat, and 6% bucket heat. To loosen this layer, raise the relevant cap or reduce existing open risk.

## Layer 3 — Capacity

Sizer caps size at hard book limits or the configured ADV impact-zero point. Default ADV capacity reaches zero edge at 10% of ADV. To loosen this layer, trade a deeper venue, split across independent markets, or deploy additional capital to different strategies.

## Layer 4 — Tail risk overlay

Sizer multiplies effective per-unit risk for gaps, heavy tails, premium selling, and unreliable stops. Defaults range from 1.0 for discrete settled outcomes to at least 3.0 for premium trades. To loosen this layer, use instruments with structural max loss, avoid event windows, or improve execution reliability.

## Layer 5 — Bankroll dynamics

Sizer recalculates against current bankroll and discounts long-dated concentrated prediction/position trades for capital lockup. The current default lockup discount scales down after 90 days with a floor at 50%. To loosen this layer, shorten the holding period or diversify locked capital across independent strategies.
