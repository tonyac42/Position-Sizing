import math
from sizer_engine import binary_kelly, continuous_kelly, generalized_kelly, size_trade
from sizer_engine.models import EdgeEstimate, EdgeSource, Liquidity, OpenPosition, PayoffStructure, SizingRequest, TradeType


def test_binary_kelly_closed_form():
    assert binary_kelly(0.55, 1) == pytest_approx(0.10)

def pytest_approx(x):
    import pytest
    return pytest.approx(x, rel=1e-6, abs=1e-6)

def test_generalized_matches_binary():
    assert generalized_kelly([(0.55, 1), (0.45, -1)]) == pytest_approx(0.10)

def test_continuous_kelly():
    assert continuous_kelly(0.04, 0.20) == pytest_approx(0.2)

def test_new_strategy_exploration_binds():
    req=SizingRequest(bankroll=10000, trade_type=TradeType.trading, edge_estimate=EdgeEstimate(expectancy_r=.4), edge_source=EdgeSource.guess, sample_size=0, entry_price=100, stop_price=99, volatility=1, liquidity=Liquidity(tier="deep"))
    out=size_trade(req)
    assert out.explanation["binding_constraint"] == "exploration_1_30"
    assert 0.0025 <= out.recommendation["percent_risk"] <= 0.005

def test_dice_capacity_bound():
    req=SizingRequest(bankroll=1000, trade_type=TradeType.prediction, payoff_structure=PayoffStructure.binary, edge_estimate=EdgeEstimate(win_probability=.55, net_odds=1), edge_source=EdgeSource.exact_math, structural_max_loss=1, liquidity=Liquidity(hard_limit=10))
    out=size_trade(req)
    assert out.explanation["binding_constraint"] == "capacity"
    assert "capacity-limited" in " ".join(out.diagnostics["warnings"])

def test_retail_futures_risk_binds():
    req=SizingRequest(bankroll=50000, trade_type=TradeType.trading, edge_estimate=EdgeEstimate(expectancy_r=.4, variance=1), edge_source=EdgeSource.live_track_record, sample_size=400, entry_price=100, stop_price=95, volatility=0.1, liquidity=Liquidity(tier="deep", adv=1e9))
    out=size_trade(req)
    assert out.explanation["binding_constraint"] == "per_trade_risk"

def test_sports_portfolio_heat_bound():
    positions=[OpenPosition(instrument=f"bet{i}", open_risk=3900, correlation_bucket="slate") for i in range(10)]
    req=SizingRequest(bankroll=200000, trade_type=TradeType.prediction, payoff_structure=PayoffStructure.binary, edge_estimate=EdgeEstimate(win_probability=.54, net_odds=.909), edge_source=EdgeSource.exact_math, structural_max_loss=1, open_positions=positions, correlation_bucket="other", liquidity=Liquidity(hard_limit=15000))
    out=size_trade(req)
    assert out.explanation["binding_constraint"] == "portfolio_heat"

def test_prediction_shrinkage_and_lockup():
    req=SizingRequest(bankroll=10000, trade_type=TradeType.prediction, payoff_structure=PayoffStructure.binary, edge_estimate=EdgeEstimate(win_probability=.50), edge_source=EdgeSource.guess, sample_size=120, entry_price=.30, structural_max_loss=.30, expected_time_in_trade_days=180, exploration_override=True)
    out=size_trade(req)
    assert .02 <= out.recommendation["percent_risk"] <= .04
    text=" ".join(out.diagnostics["warnings"])+str(out.explanation["working_edge_used"])
    assert "shrunk" in text and "lockup" in text

def test_premium_tail_warning():
    req=SizingRequest(bankroll=100000, trade_type=TradeType.premium, payoff_structure=PayoffStructure.capped, edge_estimate=EdgeEstimate(outcomes=[(.9,.1),(.1,-1)]), edge_source=EdgeSource.live_track_record, sample_size=200, structural_max_loss=10, liquidity=Liquidity(tier="normal"))
    out=size_trade(req)
    assert out.recommendation["percent_risk"] < .02
    assert "premium" in " ".join(out.diagnostics["warnings"])

def test_refusal_on_guessed_critical_field():
    req=SizingRequest(bankroll=10000, edge_estimate=EdgeEstimate(expectancy_r=.2), field_confidence={"bankroll":"guessed"})
    out=size_trade(req)
    assert out.refused
