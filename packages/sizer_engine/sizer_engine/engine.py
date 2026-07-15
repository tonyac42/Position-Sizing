from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timezone
from typing import Any

from .kelly import binary_kelly, continuous_kelly, generalized_kelly
from .models import (
    ENGINE_VERSION,
    EdgeSource,
    FieldConfidence,
    PayoffStructure,
    SizingOutput,
    SizingRequest,
    TradeType,
)

EXPLORATION_GATES = [
    (30, 0.005, "exploration_1_30"),
    (100, 0.01, "exploration_30_100"),
    (300, 0.02, "exploration_100_300"),
]
DEFAULT_KELLY_FRACTION = 0.25
MONTE_CARLO_TRADES = 100
MONTE_CARLO_PATHS = 300


def input_hash(req: SizingRequest) -> str:
    return hashlib.sha256(req.model_dump_json().encode()).hexdigest()[:16]


def classify(req: SizingRequest) -> tuple[TradeType, bool, list[str]]:
    """Infer trade type with explicit payoff-shape precedence from spec section 1.1."""
    notes: list[str] = []
    payoff_inferred: TradeType | None = None
    horizon_inferred: TradeType | None = None

    if req.payoff_structure == PayoffStructure.binary:
        payoff_inferred = TradeType.prediction

    if req.edge_estimate.outcomes:
        multiples = [multiple for _, multiple in req.edge_estimate.outcomes]
        max_gain = max(multiples)
        max_loss = abs(min(multiples, default=0.0))
        if max_gain > max_loss * 2:
            payoff_inferred = TradeType.lottery
        elif max_loss > max_gain * 2:
            payoff_inferred = TradeType.premium

    if req.expected_time_in_trade_days > 60 and req.payoff_structure != PayoffStructure.binary:
        horizon_inferred = TradeType.position

    if payoff_inferred and horizon_inferred and payoff_inferred != horizon_inferred:
        inferred = payoff_inferred
        notes.append(
            "payoff-shape classification takes precedence over holding-period position signal"
        )
    else:
        inferred = payoff_inferred or horizon_inferred or TradeType.trading

    used = inferred if req.trade_type == TradeType.auto else req.trade_type
    mismatch = req.trade_type not in (TradeType.auto, inferred)
    if mismatch:
        notes.append(
            f"declared trade type {req.trade_type.value} differs from inferred {inferred.value}"
        )
    return used, mismatch, notes


def derive_kelly_fraction(req: SizingRequest, defaults_applied: list[str]) -> float:
    """Apply Layer 1 fractional Kelly preference from explicit input or drawdown tolerance."""
    if req.kelly_fraction is not None:
        return min(max(req.kelly_fraction, 0.1), 0.5)

    if req.drawdown_tolerance and req.drawdown_tolerance.pct is not None:
        return min(0.5, max(0.1, req.drawdown_tolerance.pct))

    defaults_applied.append("kelly_fraction=0.25")
    return DEFAULT_KELLY_FRACTION


def realized_mean_r(req: SizingRequest) -> float | None:
    if not req.realized_results:
        return None
    if "mean_r" in req.realized_results:
        return float(req.realized_results["mean_r"])
    outcomes = req.realized_results.get("outcomes")
    if outcomes:
        return sum(float(value) for value in outcomes) / len(outcomes)
    return None


def shrink_edge(req: SizingRequest, trade_type: TradeType) -> tuple[dict[str, float], list[str]]:
    """Implement Layer 0 edge shrinkage and confidence interval generation."""
    edge = req.edge_estimate.model_dump()
    warnings: list[str] = []
    source_factor = {
        EdgeSource.exact_math: 1.0,
        EdgeSource.live_track_record: min(1.0, max(0.25, req.sample_size / 300)),
        EdgeSource.backtest: 0.5,
        EdgeSource.related_experience: 0.6,
        EdgeSource.guess: 0.5,
    }[req.edge_source]

    if (
        trade_type == TradeType.prediction
        and edge.get("win_probability") is not None
        and req.entry_price is not None
    ):
        original = float(edge["win_probability"])
        shrink_to_market = 0.5 if not req.user_calibration_data else 0.25
        edge["win_probability"] = (
            original * (1 - shrink_to_market) + req.entry_price * shrink_to_market
        )
        warnings.append(
            "prediction probability shrunk toward market price "
            f"from {original:.2%} to {edge['win_probability']:.2%}"
        )
    elif edge.get("win_probability") is not None:
        edge["win_probability"] = 0.5 + (float(edge["win_probability"]) - 0.5) * source_factor

    for key in ("ev_per_unit_risk", "expectancy_r", "mean_return"):
        if edge.get(key) is not None:
            edge[key] = float(edge[key]) * source_factor

    mean_r = realized_mean_r(req)
    if mean_r is not None:
        realized_weight = min(1.0, req.sample_size / 300)
        claimed = float(edge.get("expectancy_r") or edge.get("ev_per_unit_risk") or 0.0)
        blended = claimed * (1 - realized_weight) + mean_r * realized_weight
        edge["expectancy_r"] = blended
        edge["realized_mean_r"] = mean_r
        edge["realized_weight"] = realized_weight
        warnings.append(
            f"live track record blended with claimed edge at {realized_weight:.0%} realized weight"
        )

    if req.edge_source == EdgeSource.guess:
        warnings.append("edge estimate is a guess; exploration sizing applies")

    center = float(
        edge.get("win_probability")
        or (0.5 + (edge.get("expectancy_r") or edge.get("ev_per_unit_risk") or 0.0) / 2)
    )
    width = max(0.02, 0.30 / math.sqrt(max(1, req.sample_size)))
    numeric_edge = {key: value for key, value in edge.items() if isinstance(value, (int, float))}
    return {
        "win_probability": center,
        "ci_low": max(0.0, center - width),
        "ci_high": min(1.0, center + width),
        **numeric_edge,
    }, warnings


def apply_tail_overlay(req: SizingRequest, trade_type: TradeType, warnings: list[str]) -> float:
    """Implement Layer 4 by returning effective risk per native unit after tail multipliers."""
    if req.structural_max_loss:
        base_risk = req.structural_max_loss
    elif req.entry_price is not None and req.stop_price is not None:
        base_risk = abs(req.entry_price - req.stop_price)
    elif trade_type == TradeType.prediction and req.entry_price is not None:
        base_risk = req.entry_price
    else:
        base_risk = 1.0

    tail_multiplier = 1.0
    if trade_type == TradeType.premium:
        tail_multiplier = max(
            3.0,
            {"normal": 3.0, "moderate": 4.0, "heavy": 5.0, "extreme": 5.0}[req.tail_profile.value],
        )
        warnings.append(
            "premium sizing uses mandatory stressed tail multiplier; observed Sharpe is ignored"
        )
    elif req.gap_risk:
        tail_multiplier = 2.0
    elif req.tail_profile.value in {"moderate", "heavy", "extreme"}:
        tail_multiplier = {"moderate": 1.5, "heavy": 2.5, "extreme": 5.0}[req.tail_profile.value]

    if req.liquidity.tier in {"thin", "very_thin"} and req.stop_price is not None:
        tail_multiplier *= 2.0 if req.liquidity.tier == "thin" else 3.0
        warnings.append("stop reliability haircut applied for thin liquidity")

    return base_risk * tail_multiplier


def full_kelly(
    req: SizingRequest, edge: dict[str, float], trade_type: TradeType
) -> tuple[float, str]:
    """Implement Layer 1 full-Kelly solver routing."""
    estimate = req.edge_estimate
    if estimate.outcomes or trade_type in {TradeType.lottery, TradeType.premium}:
        outcomes = estimate.outcomes or [
            (edge["win_probability"], estimate.net_odds or 1.0),
            (1 - edge["win_probability"], -1.0),
        ]
        return generalized_kelly(outcomes), "generalized_kelly"

    if estimate.win_probability is not None or trade_type == TradeType.prediction:
        odds = estimate.net_odds
        if odds is None and trade_type == TradeType.prediction:
            entry = req.entry_price or 0.5
            odds = (1 - entry) / entry
        return binary_kelly(edge["win_probability"], odds or 1.0), "binary_kelly"

    mean = (
        edge.get("mean_return") or edge.get("expectancy_r") or edge.get("ev_per_unit_risk") or 0.0
    )
    return continuous_kelly(mean, estimate.variance or 1.0), "continuous_kelly"


def apply_exploration_gate(req: SizingRequest, risk_per_unit: float) -> dict[str, Any] | None:
    """Implement Layer 0 sample-size gates as independent caps."""
    if (
        req.edge_source == EdgeSource.exact_math
        or req.exploration_override
        or req.sample_size >= 300
    ):
        return None
    threshold, risk_pct, layer = next(
        gate for gate in EXPLORATION_GATES if req.sample_size < gate[0]
    )
    return {
        "layer": layer,
        "cap_size": req.bankroll * risk_pct / risk_per_unit,
        "reason": f"sample-size exploration gate below {threshold} trades",
    }


def apply_risk_caps(req: SizingRequest, risk_per_unit: float) -> list[dict[str, Any]]:
    """Implement Layer 2 risk, volatility, portfolio heat, and bucket caps."""
    caps = [
        {
            "layer": "per_trade_risk",
            "cap_size": req.bankroll * req.per_trade_risk_cap / risk_per_unit,
            "reason": "maximum loss if stop/structural loss hits",
        }
    ]
    if req.volatility:
        caps.append(
            {
                "layer": "volatility",
                "cap_size": req.bankroll * req.volatility_cap / req.volatility,
                "reason": "one-ATR exposure cap",
            }
        )

    heat_used = sum(position.open_risk for position in req.open_positions)
    heat_room = max(0.0, req.bankroll * req.portfolio_heat_cap - heat_used)
    caps.append(
        {
            "layer": "portfolio_heat",
            "cap_size": heat_room / risk_per_unit,
            "reason": "remaining portfolio heat",
        }
    )

    bucket_used = sum(
        position.open_risk
        for position in req.open_positions
        if position.correlation_bucket == req.correlation_bucket
    )
    bucket_room = max(0.0, req.bankroll * req.correlation_bucket_cap - bucket_used)
    caps.append(
        {
            "layer": "correlation_bucket",
            "cap_size": bucket_room / risk_per_unit,
            "reason": "remaining bucket risk",
        }
    )
    return caps


def apply_capacity_cap(req: SizingRequest) -> dict[str, Any] | None:
    """Implement Layer 3 capacity cap from hard limits or ADV impact-zero point."""
    capacity = req.liquidity.hard_limit
    if capacity is None and req.liquidity.adv:
        capacity = req.liquidity.adv * req.liquidity.impact_zero_fraction
    if capacity is None:
        return None
    return {"layer": "capacity", "cap_size": capacity, "reason": "liquidity/book/impact capacity"}


def apply_bankroll_dynamics(
    req: SizingRequest,
    trade_type: TradeType,
    fractional_size: float,
    warnings: list[str],
) -> dict[str, Any] | None:
    """Implement Layer 5 capital lockup discount for long-dated concentrated trades."""
    if (
        trade_type not in {TradeType.prediction, TradeType.position}
        or req.expected_time_in_trade_days <= 90
    ):
        return None
    discount = max(0.5, 1 - 0.001 * req.expected_time_in_trade_days)
    warnings.append("capital lockup discount applied")
    return {
        "layer": "capital_lockup",
        "cap_size": fractional_size * discount,
        "reason": "long-dated opportunity-cost discount",
    }


def outcome_distribution(
    req: SizingRequest, edge: dict[str, float], trade_type: TradeType
) -> list[tuple[float, float]]:
    if req.edge_estimate.outcomes:
        total = sum(probability for probability, _ in req.edge_estimate.outcomes)
        return [
            (probability / total, multiple) for probability, multiple in req.edge_estimate.outcomes
        ]
    odds = req.edge_estimate.net_odds
    if odds is None and trade_type == TradeType.prediction and req.entry_price:
        odds = (1 - req.entry_price) / req.entry_price
    return [(edge["win_probability"], odds or 1.0), (1 - edge["win_probability"], -1.0)]


def simulate_drawdowns(
    req: SizingRequest,
    edge: dict[str, float],
    trade_type: TradeType,
    final_size: float,
    risk_per_unit: float,
) -> dict[str, float]:
    """Build deterministic Monte Carlo diagnostics from actual discrete/win-loss outcomes."""
    rng = random.Random(req.monte_carlo_seed or int(input_hash(req), 16))
    distribution = outcome_distribution(req, edge, trade_type)
    fraction_at_risk = final_size * risk_per_unit / req.bankroll
    terminal_drawdowns: list[float] = []

    for _ in range(MONTE_CARLO_PATHS):
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for _ in range(MONTE_CARLO_TRADES):
            roll = rng.random()
            cumulative = 0.0
            multiple = -1.0
            for probability, candidate in distribution:
                cumulative += probability
                if roll <= cumulative:
                    multiple = candidate
                    break
            equity *= max(0.0, 1 + fraction_at_risk * multiple)
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        terminal_drawdowns.append(max_drawdown)

    terminal_drawdowns.sort(reverse=True)
    return {
        "worst_5_pct": terminal_drawdowns[max(0, int(MONTE_CARLO_PATHS * 0.05) - 1)],
        "worst_1_pct": terminal_drawdowns[max(0, int(MONTE_CARLO_PATHS * 0.01) - 1)],
    }


def losing_streak(
    edge: dict[str, float], final_risk_fraction: float, trades: int = 100
) -> dict[str, float]:
    """Compute streak length with about 5% chance of at least one streak within N trades."""
    loss_probability = max(0.0, min(1.0, 1 - edge["win_probability"]))
    if loss_probability <= 0:
        return {"streak_length": 0, "probability": 0.0, "drawdown_at_streak": 0.0}

    target = 0.05
    length = 1
    while length < trades:
        probability = 1 - (1 - loss_probability**length) ** max(1, trades - length + 1)
        if probability <= target:
            break
        length += 1
    probability = 1 - (1 - loss_probability**length) ** max(1, trades - length + 1)
    drawdown = 1 - (1 - final_risk_fraction) ** length
    return {"streak_length": length, "probability": probability, "drawdown_at_streak": drawdown}


def size_trade(req: SizingRequest) -> SizingOutput:
    defaults_applied: list[str] = []
    warnings: list[str] = []
    suggestions: list[str] = []
    ignored_fields: list[str] = []

    guessed_critical = [
        key
        for key, value in req.field_confidence.items()
        if value == FieldConfidence.guessed
        and key in {"bankroll", "edge_estimate", "stop_price", "structural_max_loss"}
    ]
    if guessed_critical:
        return SizingOutput(
            refused=True,
            recommendation={},
            explanation={"binding_constraint": "refusal"},
            diagnostics={
                "warnings": ["critical guessed fields present"],
                "refusal_reasons": ["critical guessed fields present"],
            },
            metadata={"engine_version": ENGINE_VERSION, "needed_to_proceed": guessed_critical},
        )

    trade_type, mismatch, classification_notes = classify(req)
    warnings.extend(classification_notes)
    edge, shrinkage_warnings = shrink_edge(req, trade_type)
    warnings.extend(shrinkage_warnings)

    kelly_fraction = derive_kelly_fraction(req, defaults_applied)
    full_kelly_fraction, model = full_kelly(req, edge, trade_type)
    risk_per_unit = apply_tail_overlay(req, trade_type, warnings)
    fractional_size = (
        req.bankroll * full_kelly_fraction * kelly_fraction / max(risk_per_unit, 1e-12)
    )

    caps: list[dict[str, Any]] = [
        {
            "layer": "fractional_kelly",
            "cap_size": fractional_size,
            "reason": f"{kelly_fraction:.0%} of full Kelly {full_kelly_fraction:.2%}",
        }
    ]
    exploration_cap = apply_exploration_gate(req, risk_per_unit)
    if exploration_cap:
        caps.append(exploration_cap)
    caps.extend(apply_risk_caps(req, risk_per_unit))
    capacity_cap = apply_capacity_cap(req)
    if capacity_cap:
        caps.append(capacity_cap)
    dynamics_cap = apply_bankroll_dynamics(req, trade_type, fractional_size, warnings)
    if dynamics_cap:
        caps.append(dynamics_cap)

    binding = min(caps, key=lambda cap: cap["cap_size"])
    final_size = max(0.0, binding["cap_size"])
    for cap in caps:
        cap["binding"] = cap is binding

    if binding["layer"] == "capacity":
        warnings.append("you are capacity-limited, not risk-limited")
    if req.daily_loss_limit is not None and -req.intraday_pnl >= req.daily_loss_limit:
        warnings.append("daily loss limit has been hit; halt for the day")

    dollar_risk = final_size * risk_per_unit
    risk_fraction = dollar_risk / req.bankroll
    drawdowns = simulate_drawdowns(req, edge, trade_type, final_size, risk_per_unit)
    streak = losing_streak(edge, risk_fraction)
    summary = (
        f"Sizer recommends {final_size:.2f} native units ({risk_fraction:.2%} bankroll at risk). "
        f"The binding constraint is {binding['layer']}; working win probability/edge center is "
        f"{edge['win_probability']:.2%}."
    )

    return SizingOutput(
        recommendation={
            "size_native_units": final_size,
            "size_pct_bankroll": risk_fraction,
            "size_pct_full_kelly": (
                final_size / (req.bankroll * full_kelly_fraction / max(risk_per_unit, 1e-12))
                if full_kelly_fraction > 0
                else 0.0
            ),
            "dollar_risk": dollar_risk,
            "percent_risk": risk_fraction,
        },
        explanation={
            "binding_constraint": binding["layer"],
            "full_kelly_reference": full_kelly_fraction,
            "per_layer_cap_table": caps,
            "working_edge_used": edge,
            "defaults_applied": defaults_applied,
            "ignored_fields": ignored_fields,
        },
        diagnostics={
            "warnings": warnings,
            "suggestions": suggestions,
            "drawdown_paths": drawdowns,
            "losing_streak_illustration": streak,
        },
        metadata={
            "trade_type_used": trade_type.value,
            "trade_type_mismatch": mismatch,
            "sizing_model_applied": model,
            "engine_version": ENGINE_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input_hash": input_hash(req),
            "human_readable_summary": summary,
        },
    )
