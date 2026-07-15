import pytest

from sizer_engine import binary_kelly, continuous_kelly, generalized_kelly, size_trade
from sizer_engine.engine import classify, shrink_edge
from sizer_engine.models import (
    DrawdownTolerance,
    EdgeEstimate,
    EdgeSource,
    Liquidity,
    OpenPosition,
    PayoffStructure,
    SizingRequest,
    TradeType,
)


def approx(value):
    return pytest.approx(value, rel=1e-6, abs=1e-6)


def test_binary_kelly_closed_form():
    assert binary_kelly(0.55, 1) == approx(0.10)


def test_generalized_matches_binary():
    assert generalized_kelly([(0.55, 1), (0.45, -1)]) == approx(0.10)


def test_continuous_kelly():
    assert continuous_kelly(0.04, 0.20) == approx(0.2)


def test_new_strategy_exploration_binds():
    req = SizingRequest(
        bankroll=10000,
        trade_type=TradeType.trading,
        edge_estimate=EdgeEstimate(expectancy_r=0.4),
        edge_source=EdgeSource.guess,
        sample_size=0,
        entry_price=100,
        stop_price=99,
        volatility=1,
        liquidity=Liquidity(tier="deep"),
    )
    out = size_trade(req)
    assert out.explanation["binding_constraint"] == "exploration_1_30"
    assert 0.0025 <= out.recommendation["percent_risk"] <= 0.005


def test_dice_capacity_bound():
    req = SizingRequest(
        bankroll=1000,
        trade_type=TradeType.prediction,
        payoff_structure=PayoffStructure.binary,
        edge_estimate=EdgeEstimate(win_probability=0.55, net_odds=1),
        edge_source=EdgeSource.exact_math,
        structural_max_loss=1,
        liquidity=Liquidity(hard_limit=10),
    )
    out = size_trade(req)
    assert out.explanation["binding_constraint"] == "capacity"
    assert "capacity-limited" in " ".join(out.diagnostics["warnings"])


def test_retail_futures_risk_binds():
    req = SizingRequest(
        bankroll=50000,
        trade_type=TradeType.trading,
        edge_estimate=EdgeEstimate(expectancy_r=0.4, variance=1),
        edge_source=EdgeSource.live_track_record,
        sample_size=400,
        entry_price=100,
        stop_price=95,
        volatility=0.1,
        liquidity=Liquidity(tier="deep", adv=1e9),
    )
    out = size_trade(req)
    assert out.explanation["binding_constraint"] == "per_trade_risk"


def test_sports_portfolio_heat_bound():
    positions = [
        OpenPosition(instrument=f"bet{i}", open_risk=3900, correlation_bucket="slate")
        for i in range(10)
    ]
    req = SizingRequest(
        bankroll=200000,
        trade_type=TradeType.prediction,
        payoff_structure=PayoffStructure.binary,
        edge_estimate=EdgeEstimate(win_probability=0.54, net_odds=0.909),
        edge_source=EdgeSource.exact_math,
        structural_max_loss=1,
        open_positions=positions,
        correlation_bucket="other",
        liquidity=Liquidity(hard_limit=15000),
    )
    out = size_trade(req)
    assert out.explanation["binding_constraint"] == "portfolio_heat"


def test_prediction_shrinkage_and_lockup_spec_constant():
    req = SizingRequest(
        bankroll=10000,
        trade_type=TradeType.prediction,
        payoff_structure=PayoffStructure.binary,
        edge_estimate=EdgeEstimate(win_probability=0.50),
        edge_source=EdgeSource.guess,
        sample_size=120,
        entry_price=0.30,
        structural_max_loss=0.30,
        expected_time_in_trade_days=180,
        exploration_override=True,
        per_trade_risk_cap=1,
    )
    out = size_trade(req)
    # Spec arithmetic: probability shrinks halfway from 50% to 30% => 40%.
    # Binary Kelly at price .30 has b=.70/.30=2.333, f=(bp-q)/b=.142857.
    # Quarter Kelly risks 3.571% before lockup; 180-day discount caps size at 82% of that = 2.9286%.
    assert 0.028 <= out.recommendation["percent_risk"] <= 0.030
    text = " ".join(out.diagnostics["warnings"]) + str(out.explanation["working_edge_used"])
    assert "shrunk" in text and "lockup" in text


def test_premium_tail_warning():
    req = SizingRequest(
        bankroll=100000,
        trade_type=TradeType.premium,
        payoff_structure=PayoffStructure.capped,
        edge_estimate=EdgeEstimate(outcomes=[(0.9, 0.1), (0.1, -1)]),
        edge_source=EdgeSource.live_track_record,
        sample_size=200,
        structural_max_loss=10,
        liquidity=Liquidity(tier="normal"),
    )
    out = size_trade(req)
    assert out.recommendation["percent_risk"] < 0.02
    assert "premium" in " ".join(out.diagnostics["warnings"])


def test_refusal_on_guessed_critical_field():
    req = SizingRequest(
        bankroll=10000,
        edge_estimate=EdgeEstimate(expectancy_r=0.2),
        field_confidence={"bankroll": "guessed"},
    )
    out = size_trade(req)
    assert out.refused


def test_classification_prefers_payoff_shape_over_position_horizon():
    req = SizingRequest(
        bankroll=10000,
        trade_type=TradeType.auto,
        expected_time_in_trade_days=120,
        payoff_structure=PayoffStructure.capped,
        edge_estimate=EdgeEstimate(outcomes=[(0.9, 0.1), (0.1, -1)]),
    )
    used, _, notes = classify(req)
    assert used == TradeType.premium
    assert any("payoff-shape" in note for note in notes)


def test_kelly_fraction_drawdown_path_does_not_report_default():
    req = SizingRequest(
        bankroll=10000,
        edge_estimate=EdgeEstimate(expectancy_r=0.4),
        drawdown_tolerance=DrawdownTolerance(pct=0.2),
        edge_source=EdgeSource.exact_math,
    )
    out = size_trade(req)
    assert "kelly_fraction=0.25" not in out.explanation["defaults_applied"]


def test_monte_carlo_is_seeded_and_uses_outcomes():
    req = SizingRequest(
        bankroll=10000,
        trade_type=TradeType.lottery,
        edge_estimate=EdgeEstimate(outcomes=[(0.1, 8), (0.9, -1)]),
        edge_source=EdgeSource.exact_math,
        structural_max_loss=1,
        monte_carlo_seed=7,
    )
    first = size_trade(req).diagnostics["drawdown_paths"]
    second = size_trade(req).diagnostics["drawdown_paths"]
    assert first == second
    assert first["worst_1_pct"] >= first["worst_5_pct"]


def test_losing_streak_not_hardcoded_to_five():
    req = SizingRequest(
        bankroll=10000,
        trade_type=TradeType.shortterm,
        edge_estimate=EdgeEstimate(win_probability=0.6, net_odds=1),
        edge_source=EdgeSource.exact_math,
        structural_max_loss=1,
    )
    streak = size_trade(req).diagnostics["losing_streak_illustration"]
    assert streak["streak_length"] != 5
    assert streak["drawdown_at_streak"] >= 0


@pytest.mark.parametrize(
    ("sample_size", "expected_layer"),
    [
        (0, "exploration_1_30"),
        (30, "exploration_30_100"),
        (100, "exploration_100_300"),
        (300, "fractional_kelly"),
    ],
)
def test_exploration_gate_tiers(sample_size, expected_layer):
    req = SizingRequest(
        bankroll=10000,
        trade_type=TradeType.trading,
        edge_estimate=EdgeEstimate(expectancy_r=2.0),
        edge_source=EdgeSource.guess,
        sample_size=sample_size,
        structural_max_loss=1,
        per_trade_risk_cap=1,
        portfolio_heat_cap=1,
        correlation_bucket_cap=1,
    )
    assert size_trade(req).explanation["binding_constraint"] == expected_layer


def test_realized_blend_weights():
    for n, expected_weight in [(0, 0), (30, 0.1), (150, 0.5), (300, 1), (1000, 1)]:
        req = SizingRequest(
            bankroll=10000,
            edge_estimate=EdgeEstimate(expectancy_r=0.2),
            edge_source=EdgeSource.live_track_record,
            sample_size=n,
            realized_results={"mean_r": 0.8},
        )
        edge, _ = shrink_edge(req, TradeType.trading)
        claimed_after_source = 0.2 * min(1.0, max(0.25, n / 300))
        expected = claimed_after_source * (1 - expected_weight) + 0.8 * expected_weight
        assert edge["expectancy_r"] == approx(expected)


def test_final_size_never_exceeds_layer_caps_sweep():
    for risk_cap in [0.005, 0.01, 0.02, 0.05]:
        req = SizingRequest(
            bankroll=20000,
            trade_type=TradeType.trading,
            edge_estimate=EdgeEstimate(expectancy_r=0.5),
            edge_source=EdgeSource.exact_math,
            structural_max_loss=1,
            per_trade_risk_cap=risk_cap,
            liquidity=Liquidity(hard_limit=100000),
        )
        out = size_trade(req)
        final = out.recommendation["size_native_units"]
        for cap in out.explanation["per_layer_cap_table"]:
            assert final <= cap["cap_size"] + 1e-9


def test_tightening_cap_never_increases_size():
    loose = SizingRequest(
        bankroll=20000,
        edge_estimate=EdgeEstimate(expectancy_r=0.5),
        edge_source=EdgeSource.exact_math,
        structural_max_loss=1,
        per_trade_risk_cap=0.05,
    )
    tight = SizingRequest(
        bankroll=20000,
        edge_estimate=EdgeEstimate(expectancy_r=0.5),
        edge_source=EdgeSource.exact_math,
        structural_max_loss=1,
        per_trade_risk_cap=0.01,
    )
    assert (
        size_trade(tight).recommendation["size_native_units"]
        <= size_trade(loose).recommendation["size_native_units"]
    )
