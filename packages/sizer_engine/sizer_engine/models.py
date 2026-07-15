from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Literal

ENGINE_VERSION = "0.2.0"


class TradeType(str, Enum):
    auto = "auto"
    lottery = "lottery"
    premium = "premium"
    position = "position"
    shortterm = "shortterm"
    prediction = "prediction"
    trading = "trading"


class EdgeSource(str, Enum):
    exact_math = "exact_math"
    live_track_record = "live_track_record"
    backtest = "backtest"
    related_experience = "related_experience"
    guess = "guess"


class PayoffStructure(str, Enum):
    binary = "binary"
    continuous = "continuous"
    capped = "capped"
    unbounded = "unbounded"


class CapacityPolicy(str, Enum):
    reject = "reject"
    downsize = "downsize"
    queue = "queue"


class TailProfile(str, Enum):
    normal = "normal"
    moderate = "moderate"
    heavy = "heavy"
    extreme = "extreme"


class FieldConfidence(str, Enum):
    user_stated = "user_stated"
    inferred = "inferred"
    guessed = "guessed"


def clean_for_json(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        return {key: clean_for_json(value) for key, value in asdict(obj).items()}
    if isinstance(obj, dict):
        return {key: clean_for_json(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_for_json(value) for value in obj]
    return obj


class ModelMixin:
    def model_dump(self, *_: Any, **__: Any) -> dict[str, Any]:
        return clean_for_json(self)

    def model_dump_json(self, *_: Any, **__: Any) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))


@dataclass
class OpenPosition(ModelMixin):
    instrument: str
    open_risk: float
    direction: str = "long"
    correlation_bucket: str = "default"


@dataclass
class DrawdownTolerance(ModelMixin):
    pct: float | None = None
    dollars: float | None = None


@dataclass
class EdgeEstimate(ModelMixin):
    win_probability: float | None = None
    net_odds: float | None = None
    ev_per_unit_risk: float | None = None
    expectancy_r: float | None = None
    mean_return: float | None = None
    variance: float | None = None
    outcomes: list[tuple[float, float]] | None = None


@dataclass
class Liquidity(ModelMixin):
    adv: float | None = None
    book_depth: float | None = None
    tier: Literal["deep", "normal", "thin", "very_thin"] = "normal"
    hard_limit: float | None = None
    impact_start_fraction: float = 0.01
    impact_zero_fraction: float = 0.10


@dataclass
class EquityThrottleBand(ModelMixin):
    drawdown_from: float
    drawdown_to: float
    scale: float


@dataclass
class SizingRequest(ModelMixin):
    bankroll: float
    edge_estimate: EdgeEstimate | dict[str, Any]
    bankroll_segregated: bool = True
    drawdown_tolerance: DrawdownTolerance | dict[str, Any] | None = None
    kelly_fraction: float | None = None
    open_positions: list[OpenPosition | dict[str, Any]] = field(default_factory=list)
    trade_type: TradeType | str = TradeType.auto
    subtype_flags: dict[str, Any] = field(default_factory=dict)
    edge_source: EdgeSource | str = EdgeSource.guess
    sample_size: int = 0
    realized_results: dict[str, Any] | None = None
    user_calibration_data: dict[str, Any] | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    expected_time_in_trade_days: float = 1.0
    payoff_structure: PayoffStructure | str = PayoffStructure.continuous
    structural_max_loss: float | None = None
    instrument_id: str = "UNKNOWN"
    volatility: float | None = None
    liquidity: Liquidity | dict[str, Any] = field(default_factory=Liquidity)
    correlation_bucket: str = "default"
    tail_profile: TailProfile | str = TailProfile.normal
    gap_risk: bool = False
    per_trade_risk_cap: float = 0.02
    volatility_cap: float = 0.01
    portfolio_heat_cap: float = 0.20
    correlation_bucket_cap: float = 0.06
    capacity_policy: CapacityPolicy | str = CapacityPolicy.downsize
    equity_throttle_schedule: list[EquityThrottleBand | dict[str, Any]] | None = None
    daily_loss_limit: float | None = None
    intraday_pnl: float = 0.0
    exploration_override: bool = False
    strategy_id: str | None = None
    field_confidence: dict[str, FieldConfidence | str] = field(default_factory=dict)
    monte_carlo_seed: int | None = None

    def __post_init__(self) -> None:
        if self.bankroll <= 0:
            raise ValueError("bankroll must be positive")
        if isinstance(self.edge_estimate, dict):
            self.edge_estimate = EdgeEstimate(**self.edge_estimate)
        if isinstance(self.drawdown_tolerance, dict):
            self.drawdown_tolerance = DrawdownTolerance(**self.drawdown_tolerance)
        if isinstance(self.liquidity, dict):
            self.liquidity = Liquidity(**self.liquidity)
        self.open_positions = [
            OpenPosition(**position) if isinstance(position, dict) else position
            for position in self.open_positions
        ]
        if self.equity_throttle_schedule is not None:
            self.equity_throttle_schedule = [
                EquityThrottleBand(**band) if isinstance(band, dict) else band
                for band in self.equity_throttle_schedule
            ]
        self.trade_type = TradeType(self.trade_type)
        self.edge_source = EdgeSource(self.edge_source)
        self.payoff_structure = PayoffStructure(self.payoff_structure)
        self.tail_profile = TailProfile(self.tail_profile)
        self.capacity_policy = CapacityPolicy(self.capacity_policy)
        self.field_confidence = {
            key: FieldConfidence(value) for key, value in self.field_confidence.items()
        }


@dataclass
class SizingOutput(ModelMixin):
    recommendation: dict[str, float]
    explanation: dict[str, Any]
    diagnostics: dict[str, Any]
    metadata: dict[str, Any]
    refused: bool = False
