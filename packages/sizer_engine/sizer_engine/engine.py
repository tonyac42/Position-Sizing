from __future__ import annotations

import hashlib, json, math, random
from datetime import datetime, timezone
from typing import Any
from .kelly import binary_kelly, continuous_kelly, generalized_kelly
from .models import ENGINE_VERSION, EdgeSource, FieldConfidence, PayoffStructure, SizingOutput, SizingRequest, TradeType

EXPLORATION_GATES = [(30, 0.005, "exploration_1_30"), (100, 0.01, "exploration_30_100"), (300, 0.02, "exploration_100_300")]
DEFAULTS = {"kelly_fraction": 0.25, "per_trade_risk_cap": 0.02, "volatility_cap": 0.01, "portfolio_heat_cap": 0.20, "correlation_bucket_cap": 0.06}

def _hash(req: SizingRequest) -> str:
    return hashlib.sha256(req.model_dump_json(exclude_none=False).encode()).hexdigest()[:16]

def classify(req: SizingRequest) -> tuple[TradeType, bool, list[str]]:
    inferred = TradeType.trading
    if req.payoff_structure == PayoffStructure.binary:
        inferred = TradeType.prediction
    if req.edge_estimate.outcomes:
        xs = [x for _, x in req.edge_estimate.outcomes]
        if max(xs) > abs(min(xs, default=0)) * 2: inferred = TradeType.lottery
        if abs(min(xs, default=0)) > max(xs) * 2: inferred = TradeType.premium
    if req.expected_time_in_trade_days > 60 and req.payoff_structure != PayoffStructure.binary:
        inferred = TradeType.position
    used = inferred if req.trade_type == TradeType.auto else req.trade_type
    mismatch = req.trade_type not in (TradeType.auto, inferred)
    warnings = [f"declared trade type {req.trade_type} differs from inferred {inferred}"] if mismatch else []
    return used, mismatch, warnings

def kelly_fraction(req: SizingRequest, defaults: list[str]) -> float:
    if req.kelly_fraction is not None:
        return min(max(req.kelly_fraction, 0.1), 0.5)
    defaults.append("kelly_fraction=0.25")
    if req.drawdown_tolerance and req.drawdown_tolerance.pct:
        # Approximation: tolerable drawdown maps linearly from 10%->0.10 Kelly to 50%->0.50 Kelly.
        return min(0.5, max(0.1, req.drawdown_tolerance.pct))
    return DEFAULTS["kelly_fraction"]

def shrink(req: SizingRequest, trade_type: TradeType) -> tuple[dict[str, float], list[str], list[str]]:
    edge = req.edge_estimate.model_dump()
    warnings=[]; suggestions=[]
    factor = {EdgeSource.exact_math:1.0, EdgeSource.backtest:0.5, EdgeSource.related_experience:0.6, EdgeSource.guess:0.5, EdgeSource.live_track_record:min(1.0, max(0.25, req.sample_size/300))}[req.edge_source]
    if trade_type == TradeType.prediction and edge.get("win_probability") is not None and req.entry_price is not None:
        original=edge["win_probability"]
        cal = 0.35 if not req.user_calibration_data else 0.2
        edge["win_probability"] = original*(1-cal)+req.entry_price*cal
        warnings.append(f"prediction probability shrunk toward market price from {original:.2%} to {edge['win_probability']:.2%}")
    elif edge.get("win_probability") is not None:
        edge["win_probability"] = 0.5 + (edge["win_probability"]-0.5)*factor
    for key in ("ev_per_unit_risk", "expectancy_r", "mean_return"):
        if edge.get(key) is not None: edge[key] *= factor
    if req.edge_source == EdgeSource.guess:
        warnings.append("edge estimate is a guess; exploration sizing applies")
    width = max(0.02, 0.30 / math.sqrt(max(1, req.sample_size)))
    center = edge.get("win_probability") or (0.5 + (edge.get("expectancy_r") or edge.get("ev_per_unit_risk") or 0)/2)
    return {"win_probability": center, "ci_low": max(0, center-width), "ci_high": min(1, center+width), **{k:v for k,v in edge.items() if isinstance(v,(int,float))}}, warnings, suggestions

def _risk_per_unit(req: SizingRequest, trade_type: TradeType, warnings: list[str]) -> float:
    if req.structural_max_loss: base = req.structural_max_loss
    elif req.entry_price is not None and req.stop_price is not None: base = abs(req.entry_price-req.stop_price)
    elif trade_type == TradeType.prediction and req.entry_price is not None: base = req.entry_price
    else: base = 1.0
    tail = 1.0
    if trade_type == TradeType.premium: tail = max(3.0, {"normal":3,"moderate":4,"heavy":5,"extreme":5}[req.tail_profile.value]); warnings.append("premium sizing uses mandatory stressed tail multiplier; observed Sharpe is ignored")
    elif req.gap_risk: tail = 2.0
    elif req.tail_profile.value in ("moderate","heavy","extreme"): tail = {"moderate":1.5,"heavy":2.5,"extreme":5}[req.tail_profile.value]
    if req.liquidity.tier in ("thin","very_thin") and req.stop_price is not None:
        tail *= 2 if req.liquidity.tier=="thin" else 3; warnings.append("stop reliability haircut applied for thin liquidity")
    return base * tail

def _full_kelly(req: SizingRequest, edge: dict[str,float], trade_type: TradeType) -> tuple[float,str]:
    e=req.edge_estimate
    if e.outcomes or trade_type in (TradeType.lottery, TradeType.premium):
        outcomes=e.outcomes or [(edge["win_probability"], e.net_odds or 1.0),(1-edge["win_probability"], -1.0)]
        return generalized_kelly(outcomes), "generalized_kelly"
    if e.win_probability is not None or trade_type==TradeType.prediction:
        odds=e.net_odds or ((1-(req.entry_price or 0.5))/(req.entry_price or 0.5) if trade_type==TradeType.prediction else 1.0)
        return binary_kelly(edge["win_probability"], odds), "binary_kelly"
    return continuous_kelly(edge.get("mean_return") or edge.get("expectancy_r") or edge.get("ev_per_unit_risk") or 0.0, e.variance or 1.0), "continuous_kelly"

def size_trade(req: SizingRequest) -> SizingOutput:
    defaults=[]; warnings=[]; suggestions=[]; ignored=[]
    critical=[k for k,v in req.field_confidence.items() if v==FieldConfidence.guessed and k in {"bankroll","edge_estimate","stop_price","structural_max_loss"}]
    if critical:
        return SizingOutput(refused=True,recommendation={},explanation={"binding_constraint":"refusal"},diagnostics={"warnings":["critical guessed fields present"]},metadata={"engine_version":ENGINE_VERSION,"needed_to_proceed":critical})
    trade_type,mismatch,tw=classify(req); warnings+=tw
    edge, sw, sug = shrink(req, trade_type); warnings += sw; suggestions += sug
    kfrac=kelly_fraction(req, defaults)
    full, model=_full_kelly(req, edge, trade_type)
    risk_unit=_risk_per_unit(req, trade_type, warnings)
    fractional = req.bankroll * full * kfrac / max(risk_unit, 1e-9)
    caps=[{"layer":"fractional_kelly","cap_size":fractional,"reason":f"{kfrac:.0%} of full Kelly {full:.2%}"}]
    if req.edge_source != EdgeSource.exact_math and req.sample_size < 300 and not req.exploration_override:
        gate=next(g for g in EXPLORATION_GATES if req.sample_size < g[0])
        caps.append({"layer":gate[2],"cap_size":req.bankroll*gate[1]/risk_unit,"reason":"sample-size exploration gate"})
    caps.append({"layer":"per_trade_risk","cap_size":req.bankroll*req.per_trade_risk_cap/risk_unit,"reason":"maximum loss if stop/structural loss hits"})
    if req.volatility: caps.append({"layer":"volatility","cap_size":req.bankroll*req.volatility_cap/req.volatility,"reason":"one-ATR exposure cap"})
    heat_room=max(0, req.bankroll*req.portfolio_heat_cap-sum(p.open_risk for p in req.open_positions)); caps.append({"layer":"portfolio_heat","cap_size":heat_room/risk_unit,"reason":"remaining portfolio heat"})
    bucket_room=max(0, req.bankroll*req.correlation_bucket_cap-sum(p.open_risk for p in req.open_positions if p.correlation_bucket==req.correlation_bucket)); caps.append({"layer":"correlation_bucket","cap_size":bucket_room/risk_unit,"reason":"remaining bucket risk"})
    cap_liq=req.liquidity.hard_limit or (req.liquidity.adv*req.liquidity.impact_zero_fraction if req.liquidity.adv else float("inf"))
    if cap_liq < float("inf"): caps.append({"layer":"capacity","cap_size":cap_liq,"reason":"liquidity/book/impact capacity"})
    if trade_type in (TradeType.prediction,TradeType.position) and req.expected_time_in_trade_days>90:
        disc=max(0.5, 1-0.001*req.expected_time_in_trade_days); caps.append({"layer":"capital_lockup","cap_size":fractional*disc,"reason":"long-dated opportunity-cost discount"}); warnings.append("capital lockup discount applied")
    binding=min(caps, key=lambda c:c["cap_size"]); final=max(0.0,binding["cap_size"])
    for c in caps: c["binding"]=c is binding
    if binding["layer"]=="capacity": warnings.append("you are capacity-limited, not risk-limited")
    if req.daily_loss_limit is not None and -req.intraday_pnl >= req.daily_loss_limit: warnings.append("daily loss limit has been hit; halt for the day")
    seed=req.monte_carlo_seed or int(_hash(req),16); rng=random.Random(seed)
    paths=sorted([sum(rng.gauss(edge["win_probability"]-0.5,0.2)*final*risk_unit/req.bankroll for _ in range(50)) for _ in range(200)])
    risk=final*risk_unit
    summary=f"Sizer recommends {final:.2f} native units ({final*risk_unit/req.bankroll:.2%} bankroll at risk). The binding constraint is {binding['layer']}; working win probability/edge center is {edge['win_probability']:.2%}."
    return SizingOutput(recommendation={"size_native_units":final,"size_pct_bankroll":final*risk_unit/req.bankroll,"size_pct_full_kelly":(full and final/(req.bankroll*full/max(risk_unit,1e-9))) or 0,"dollar_risk":risk,"percent_risk":risk/req.bankroll},explanation={"binding_constraint":binding["layer"],"full_kelly_reference":full,"per_layer_cap_table":caps,"working_edge_used":edge,"defaults_applied":defaults,"ignored_fields":ignored},diagnostics={"warnings":warnings,"suggestions":suggestions,"drawdown_paths":{"worst_5_pct":paths[9],"worst_1_pct":paths[1]},"losing_streak_illustration":{"over_100_trades_probability":(1-edge["win_probability"])**5,"streak_length":5}},metadata={"trade_type_used":trade_type,"trade_type_mismatch":mismatch,"sizing_model_applied":model,"engine_version":ENGINE_VERSION,"timestamp":datetime.now(timezone.utc).isoformat(),"input_hash":_hash(req),"human_readable_summary":summary})
