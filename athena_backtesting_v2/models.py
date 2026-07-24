from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from .constants import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAX_CONCURRENT_POSITIONS,
    DEFAULT_MAX_HOLD_BARS,
    DEFAULT_OOS_FRACTION,
    DEFAULT_RANDOM_SEED,
    DEFAULT_RISK_PER_TRADE,
    DEFAULT_TRAIN_FRACTION,
    DEFAULT_WALK_FORWARD_FOLDS,
    ENGINES,
    ENGINE_STYLES,
)


class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str, *, fields: Mapping[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = dict(fields or {})

    def to_dict(self) -> dict[str, Any]:
        return {"success": False, "code": self.code, "error": self.message, "fields": self.fields}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise RequestValidationError("INVALID_DATE_RANGE", f"{field_name} is required", fields={field_name: "required"})
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise RequestValidationError(
            "INVALID_DATE_RANGE",
            f"{field_name} must be an ISO date (YYYY-MM-DD)",
            fields={field_name: "invalid_iso_date"},
        ) from exc


@dataclass(frozen=True)
class CostModel:
    version: str = "athena-cost-model-v2.1"
    spread_bps: float = 1.0
    commission_bps_per_side: float = 0.5
    slippage_bps: float = 0.5
    funding_bps_per_day: float = 0.0
    swap_bps_per_day: float = 0.0
    dividend_mode: str = "adjusted_series"
    roll_bps: float = 0.0
    source: str = "operator_declared_v2_default"

    @classmethod
    def from_payload(cls, raw: Any, *, require_nonzero: bool = True) -> "CostModel":
        payload = raw if isinstance(raw, dict) else {}
        kwargs: dict[str, Any] = {}
        for name in (
            "version",
            "spread_bps",
            "commission_bps_per_side",
            "slippage_bps",
            "funding_bps_per_day",
            "swap_bps_per_day",
            "dividend_mode",
            "roll_bps",
            "source",
        ):
            camel = {
                "spread_bps": "spreadBps",
                "commission_bps_per_side": "commissionBpsPerSide",
                "slippage_bps": "slippageBps",
                "funding_bps_per_day": "fundingBpsPerDay",
                "swap_bps_per_day": "swapBpsPerDay",
                "dividend_mode": "dividendMode",
                "roll_bps": "rollBps",
            }.get(name, name)
            if camel in payload:
                kwargs[name] = payload[camel]
            elif name in payload:
                kwargs[name] = payload[name]
        try:
            model = cls(**kwargs)
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("INVALID_COST_MODEL", str(exc)) from exc
        numeric = (
            model.spread_bps,
            model.commission_bps_per_side,
            model.slippage_bps,
            model.funding_bps_per_day,
            model.swap_bps_per_day,
            model.roll_bps,
        )
        if any(float(value) < 0 for value in numeric):
            raise RequestValidationError("INVALID_COST_MODEL", "cost inputs cannot be negative")
        if not str(model.version).strip() or not str(model.source).strip():
            raise RequestValidationError("INVALID_COST_MODEL", "cost model requires a version and source")
        if require_nonzero and not any(float(value) > 0 for value in numeric):
            raise RequestValidationError("ZERO_COST_MODEL", "certified runs require a non-zero cost model")
        return model


@dataclass(frozen=True)
class ValidationPlan:
    enabled: bool = True
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION
    folds: int = DEFAULT_WALK_FORWARD_FOLDS
    initial_train_fraction: float = DEFAULT_TRAIN_FRACTION
    oos_fraction: float = DEFAULT_OOS_FRACTION
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS
    bootstrap_samples: int = 1_000

    @classmethod
    def from_payload(cls, raw: Any) -> "ValidationPlan":
        payload = raw if isinstance(raw, dict) else {}
        try:
            plan = cls(
                enabled=bool(payload.get("enabled", True)),
                holdout_fraction=float(payload.get("holdoutFraction", payload.get("holdout_fraction", DEFAULT_HOLDOUT_FRACTION))),
                folds=int(payload.get("folds", DEFAULT_WALK_FORWARD_FOLDS)),
                initial_train_fraction=float(payload.get("initialTrainFraction", payload.get("initial_train_fraction", DEFAULT_TRAIN_FRACTION))),
                oos_fraction=float(payload.get("oosFraction", payload.get("oos_fraction", DEFAULT_OOS_FRACTION))),
                max_hold_bars=int(payload.get("maxHoldBars", payload.get("max_hold_bars", DEFAULT_MAX_HOLD_BARS))),
                bootstrap_samples=int(payload.get("bootstrapSamples", payload.get("bootstrap_samples", 1_000))),
            )
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("INVALID_VALIDATION_PLAN", str(exc)) from exc
        if not 0 < plan.holdout_fraction < 0.5:
            raise RequestValidationError("INVALID_VALIDATION_PLAN", "holdoutFraction must be between 0 and 0.5")
        if plan.folds != 5 or abs(plan.initial_train_fraction - 0.40) > 1e-9 or abs(plan.oos_fraction - 0.08) > 1e-9:
            raise RequestValidationError(
                "INVALID_CERTIFIED_WALK_FORWARD",
                "v2 certified validation is fixed at 40% initial train plus five anchored 8% OOS folds",
            )
        if plan.max_hold_bars <= 0 or plan.bootstrap_samples < 100:
            raise RequestValidationError("INVALID_VALIDATION_PLAN", "maxHoldBars must be positive and bootstrapSamples at least 100")
        return plan


@dataclass(frozen=True)
class PortfolioPlan:
    enabled: bool = False
    initial_capital: float = DEFAULT_INITIAL_CAPITAL
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE
    max_concurrent_positions: int = DEFAULT_MAX_CONCURRENT_POSITIONS

    @classmethod
    def from_payload(cls, raw: Any) -> "PortfolioPlan":
        payload = raw if isinstance(raw, dict) else {}
        try:
            plan = cls(
                enabled=bool(payload.get("enabled", False)),
                initial_capital=float(payload.get("initialCapital", payload.get("initial_capital", DEFAULT_INITIAL_CAPITAL))),
                risk_per_trade=float(payload.get("riskPerTrade", payload.get("risk_per_trade", DEFAULT_RISK_PER_TRADE))),
                max_concurrent_positions=int(payload.get("maxConcurrentPositions", payload.get("max_concurrent_positions", DEFAULT_MAX_CONCURRENT_POSITIONS))),
            )
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("INVALID_PORTFOLIO_PLAN", str(exc)) from exc
        if plan.initial_capital <= 0 or not 0 < plan.risk_per_trade <= 0.05 or plan.max_concurrent_positions <= 0:
            raise RequestValidationError("INVALID_PORTFOLIO_PLAN", "invalid capital, risk, or position limit")
        return plan


@dataclass(frozen=True)
class RunRequest:
    engines: tuple[str, ...]
    styles: dict[str, str]
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    mode: str
    dataset_id: str | None
    validation: ValidationPlan
    cost_model: CostModel
    portfolio: PortfolioPlan
    comparison_family: str | None
    variant_id: str | None
    random_seed: int
    force: bool
    market_on_close: bool = False

    @classmethod
    def from_payload(cls, raw: Any) -> "RunRequest":
        if not isinstance(raw, dict):
            raise RequestValidationError("INVALID_REQUEST", "JSON object required")
        engine_raw = raw.get("engines", raw.get("engine"))
        if isinstance(engine_raw, str):
            engine_values = [engine_raw]
        elif isinstance(engine_raw, (list, tuple)):
            engine_values = list(engine_raw)
        else:
            engine_values = []
        engines = tuple(dict.fromkeys(str(item).strip().upper() for item in engine_values if str(item).strip()))
        if not engines or any(engine not in ENGINES for engine in engines):
            raise RequestValidationError("INVALID_ENGINE", "engines must explicitly contain A, B, or both")

        symbol_raw = raw.get("symbols")
        if not isinstance(symbol_raw, (list, tuple)):
            raise RequestValidationError(
                "EXPLICIT_UNIVERSE_REQUIRED",
                "symbols must be an explicit non-empty list; omission never means the full universe",
                fields={"symbols": "required_non_empty_list"},
            )
        symbols = tuple(dict.fromkeys(str(item).strip().upper() for item in symbol_raw if str(item).strip()))
        if not symbols:
            raise RequestValidationError(
                "EXPLICIT_UNIVERSE_REQUIRED",
                "symbols must contain at least one instrument",
                fields={"symbols": "required_non_empty_list"},
            )

        default_end = datetime.now(timezone.utc).date()
        default_start = default_end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        start = _parse_date(raw.get("startDate", default_start.isoformat()), "startDate")
        end = _parse_date(raw.get("endDate", default_end.isoformat()), "endDate")
        if start >= end or end > default_end:
            raise RequestValidationError("INVALID_DATE_RANGE", "startDate must precede endDate and endDate cannot be in the future")

        mode = str(raw.get("mode") or "CERTIFIED").strip().upper()
        if mode not in ("CERTIFIED", "EXPLORATORY"):
            raise RequestValidationError("INVALID_MODE", "mode must be CERTIFIED or EXPLORATORY")
        style_raw = raw.get("styles") if isinstance(raw.get("styles"), dict) else {}
        styles: dict[str, str] = {}
        for engine in engines:
            style = str(style_raw.get(engine, raw.get("style", "auto"))).strip().lower()
            if style not in ENGINE_STYLES[engine]:
                raise RequestValidationError("INVALID_STYLE", f"Engine {engine} does not support style {style!r}")
            styles[engine] = style

        try:
            seed = int(raw.get("randomSeed", DEFAULT_RANDOM_SEED))
        except (TypeError, ValueError) as exc:
            raise RequestValidationError("INVALID_RANDOM_SEED", "randomSeed must be an integer") from exc

        validation = ValidationPlan.from_payload(raw.get("validation"))
        if mode == "CERTIFIED" and not validation.enabled:
            raise RequestValidationError(
                "CERTIFIED_VALIDATION_REQUIRED",
                "certified runs require the locked walk-forward and holdout validation plan",
            )

        return cls(
            engines=engines,
            styles=styles,
            symbols=symbols,
            start_date=start,
            end_date=end,
            mode=mode,
            dataset_id=str(raw.get("datasetId") or "").strip() or None,
            validation=validation,
            cost_model=CostModel.from_payload(raw.get("costModel"), require_nonzero=mode == "CERTIFIED"),
            portfolio=PortfolioPlan.from_payload(raw.get("portfolio")),
            comparison_family=str(raw.get("comparisonFamily") or "").strip() or None,
            variant_id=str(raw.get("variantId") or "").strip() or None,
            random_seed=seed,
            force=bool(raw.get("force", False)),
            # Explicit opt-in to market-on-close fills (decision-bar close).
            # Default False: same-candle fills are prohibited.
            market_on_close=bool(raw.get("marketOnClose", raw.get("market_on_close", False))),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start_date"] = self.start_date.isoformat()
        payload["end_date"] = self.end_date.isoformat()
        return payload


@dataclass(frozen=True)
class DatasetSeriesManifest:
    series_id: str
    symbol: str
    provider: str
    provider_symbol: str
    timeframe: str
    asset_type: str
    path: str
    file_hash: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    gap_count: int
    provider_no_quote_count: int
    duplicate_count: int
    calendar: str
    market_timezone: str
    bar_semantics: str
    adjustment_status: str
    corporate_actions_status: str
    quality_state: str
    limitations: tuple[str, ...] = ()
    observations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfigSnapshot:
    snapshot_id: str
    created_at: str
    git_commit: str
    engine_a_contract_version: str
    engine_b_contract_version: str
    timeframe_policy_version: str
    timeframe_policy_hash: str
    config_hash: str
    config: dict[str, Any]
    simulator_version: str
    cost_model: dict[str, Any]
    random_seed: int


@dataclass
class TradeRecord:
    run_id: str
    engine: str
    symbol: str
    direction: str
    style: str
    signal_time: str
    order_time: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    stop_price: float
    targets: list[float]
    gross_r: float
    cost_r: float
    net_r: float
    exit_reason: str
    ambiguous_same_bar: bool
    duration_bars: int
    split: str = "UNASSIGNED"
    fill_source: str = ""
    actual_rr_at_fill: float | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    costs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
