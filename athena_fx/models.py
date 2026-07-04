"""Shared contracts for the athena_fx factor lane.

All athena_fx modules exchange data through these dataclasses so the
live/scanner path and the backtest path resolve factor inputs through one
adapter with identical semantics (Phase 1 parity requirement).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── Universe ──────────────────────────────────────────────────────────────────

# Retail-liquid G8 universe (not G10: SEK/NOK excluded intentionally).
G8_CURRENCIES: tuple[str, ...] = (
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
)

# All 28 G8 crosses, quoted in market-conventional order.
G8_PAIRS: tuple[str, ...] = (
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCHF", "USDCAD",
    "EURGBP", "EURJPY", "EURCHF", "EURCAD", "EURAUD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPCAD", "GBPAUD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF", "CHFJPY",
)


def pair_legs(symbol: str) -> tuple[str, str]:
    """Return (base, quote) for a 6-char FX symbol. Raises ValueError otherwise."""
    sym = symbol.replace("/", "").upper()
    if len(sym) != 6:
        raise ValueError(f"not a 6-char FX symbol: {symbol!r}")
    return sym[:3], sym[3:]


# ── Diagnostics ───────────────────────────────────────────────────────────────

# Canonical diagnostic codes (Phase 1 requirement — explicit, never silent).
DIAG_MISSING_CARRY = "missing_carry"
DIAG_MISSING_COT = "missing_cot"
DIAG_MISSING_FRED = "missing_fred"
DIAG_MISSING_REER = "missing_reer"
DIAG_MISSING_SWAP = "missing_swap"
DIAG_STALE_DERIVATIVES = "stale_derivatives"
DIAG_MISSING_OHLC = "missing_ohlc"
DIAG_MISSING_CONVERSION = "missing_conversion"
DIAG_LOOKAHEAD_BLOCKED = "lookahead_blocked"


@dataclass(frozen=True)
class Diagnostic:
    code: str                      # e.g. DIAG_MISSING_CARRY
    severity: str                  # "info" | "warning" | "error"
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "context": dict(self.context),
        }


class StrictFactorDataError(Exception):
    """Raised in strict mode when required factor inputs are absent."""

    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = list(diagnostics)
        codes = ", ".join(d.code for d in self.diagnostics) or "unknown"
        super().__init__(f"strict_factor_data violation: {codes}")


# ── Factor derivative bundle (Phase 1 shared adapter output) ─────────────────

@dataclass
class FactorDerivatives:
    """Point-in-time factor inputs for one pair; shared by live and backtest."""

    symbol: str
    as_of_date: str                          # YYYY-MM-DD decision date
    carry_z: Optional[float] = None
    carry_differential: Optional[float] = None   # theoretical, annualized decimal
    cot_z: Optional[float] = None
    base_cot_percentile: Optional[float] = None
    quote_cot_percentile: Optional[float] = None
    reer_base_z: Optional[float] = None
    reer_quote_z: Optional[float] = None
    swap_audit_valid: bool = False
    status: str = "ok"                       # "ok" | "degraded" | "invalid"
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of_date": self.as_of_date,
            "carry_z": self.carry_z,
            "carry_differential": self.carry_differential,
            "cot_z": self.cot_z,
            "base_cot_percentile": self.base_cot_percentile,
            "quote_cot_percentile": self.quote_cot_percentile,
            "reer_base_z": self.reer_base_z,
            "reer_quote_z": self.reer_quote_z,
            "swap_audit_valid": self.swap_audit_valid,
            "status": self.status,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


# ── Swap audit (Phase 2) ──────────────────────────────────────────────────────

@dataclass
class SwapAuditRow:
    symbol: str
    base_currency: str
    quote_currency: str
    account_currency: str
    point: float
    trade_tick_value: float
    trade_tick_size: float
    trade_contract_size: float
    swap_mode: int                            # MT5 SYMBOL_SWAP_MODE_* value
    swap_long: float                          # raw broker units (per swap_mode)
    swap_short: float
    swap_rollover3days: int                   # weekday of triple swap (MT5: 3=Wed)
    point_value_per_lot: Optional[float] = None      # account ccy per point per lot
    swap_cash_long_per_lot: Optional[float] = None   # normal daily, account ccy
    swap_cash_short_per_lot: Optional[float] = None
    weekly_swap_cash_long_per_lot: Optional[float] = None   # incl. triple day
    weekly_swap_cash_short_per_lot: Optional[float] = None
    notional_value_account_ccy: Optional[float] = None
    daily_swap_return_long: Optional[float] = None   # decimal daily return
    daily_swap_return_short: Optional[float] = None
    annualized_swap_return_long: Optional[float] = None
    annualized_swap_return_short: Optional[float] = None
    theoretical_carry: Optional[float] = None        # annualized decimal, base-quote
    broker_markup_long: Optional[float] = None       # theoretical - broker (long)
    broker_markup_short: Optional[float] = None
    carry_long_eligible: bool = False
    carry_short_eligible: bool = False
    net_carry_long_annualized: Optional[float] = None
    net_carry_short_annualized: Optional[float] = None
    markup_warning: bool = False
    conversion_warning: bool = False
    stale_swap_warning: bool = False
    broker_timestamp: Optional[str] = None    # ISO UTC when metadata was read
    generated_at: Optional[str] = None
    status: str = "ok"                        # "ok" | "degraded" | "invalid"
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "diagnostics"}
        out["diagnostics"] = [d.to_dict() for d in self.diagnostics]
        return out


# ── Total return (Phase 3) ────────────────────────────────────────────────────

@dataclass
class TotalReturnRow:
    date: str                                 # YYYY-MM-DD (bar close date)
    symbol: str
    spot_return: float
    long_swap_return: float
    short_swap_return: float
    long_total_return: float
    short_total_return: float
    long_total_return_index: float
    short_total_return_index: float
    data_quality_status: str                  # "ok" | "degraded" | "invalid"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ── Family scores (Phases 4-6) ────────────────────────────────────────────────

FAMILY_CARRY = "carry"
FAMILY_MOMENTUM = "momentum"
FAMILY_VALUE = "value"
PRIMARY_FAMILIES: tuple[str, ...] = (FAMILY_CARRY, FAMILY_MOMENTUM, FAMILY_VALUE)


@dataclass
class FamilyScore:
    family: str                               # one of PRIMARY_FAMILIES
    symbol: str
    as_of_date: str
    score: Optional[float]                    # continuous, family-native units
    direction: str                            # "bullish" | "bearish" | "neutral"
    subcomponents: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"                        # "ok" | "degraded" | "invalid"
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "symbol": self.symbol,
            "as_of_date": self.as_of_date,
            "score": self.score,
            "direction": self.direction,
            "subcomponents": dict(self.subcomponents),
            "status": self.status,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


# ── COT overlay (Phase 7) ─────────────────────────────────────────────────────

@dataclass
class CotOverlayResult:
    symbol: str
    as_of_date: str
    base_currency_cot_percentile: Optional[float]
    quote_currency_cot_percentile: Optional[float]
    pair_crowding_status: str                 # "none"|"one_leg"|"double_crowded"|"unknown"
    action: str                               # "none" | "downweight" | "veto"
    modifier: float                           # score modifier in [-10, +10]; 0 when veto/none
    cot_release_used: Optional[str]           # report_date of release consumed
    cot_lag_days: Optional[int]
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "diagnostics"}
        out["diagnostics"] = [d.to_dict() for d in self.diagnostics]
        return out


# ── Gates (Phase 8) ───────────────────────────────────────────────────────────

GATE_PASS = "pass"
GATE_REDUCED = "reduced"
GATE_FAIL = "fail"


@dataclass
class GateResult:
    gate_name: str                            # "volatility" | "cost" | "data_quality"
    result: str                               # GATE_PASS | GATE_REDUCED | GATE_FAIL
    reason_code: str
    numeric_inputs: dict[str, Any] = field(default_factory=dict)
    recommended_action: str = "allow"         # "allow"|"reduce"|"block"|"flatten"
    size_multiplier: float = 1.0              # 1.0 allow, 0.5 reduce, 0.0 block/flatten

    def to_dict(self) -> dict[str, Any]:
        return dict(
            gate_name=self.gate_name,
            result=self.result,
            reason_code=self.reason_code,
            numeric_inputs=dict(self.numeric_inputs),
            recommended_action=self.recommended_action,
            size_multiplier=self.size_multiplier,
        )


# ── Decision (Phase 9) ────────────────────────────────────────────────────────

ACTION_BUY = "BUY"
ACTION_SELL = "SELL"
ACTION_NO_TRADE = "NO_TRADE"
ACTION_REDUCE = "REDUCE"
ACTION_FLATTEN = "FLATTEN"

# Score model caps (Phase 9). Vol/cost are gates, never score inputs.
SCORE_CAP_CARRY = 35.0
SCORE_CAP_MOMENTUM = 35.0
SCORE_CAP_VALUE = 20.0
SCORE_CAP_COT_MODIFIER = 10.0


@dataclass
class FxDecision:
    symbol: str
    as_of_date: str
    action: str                               # ACTION_* constant
    direction: Optional[str]                  # "LONG" | "SHORT" | None
    aligned_families: list[str] = field(default_factory=list)
    blocked_families: list[str] = field(default_factory=list)
    factor_scores: dict[str, Any] = field(default_factory=dict)
    total_score: Optional[float] = None
    cot_overlay: Optional[dict[str, Any]] = None
    gate_results: list[GateResult] = field(default_factory=list)
    volatility_action: str = "allow"
    size_multiplier: float = 1.0
    cost_diagnostics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of_date": self.as_of_date,
            "action": self.action,
            "direction": self.direction,
            "aligned_families": list(self.aligned_families),
            "blocked_families": list(self.blocked_families),
            "factor_scores": dict(self.factor_scores),
            "total_score": self.total_score,
            "cot_overlay": dict(self.cot_overlay) if self.cot_overlay else None,
            "gate_results": [g.to_dict() for g in self.gate_results],
            "volatility_action": self.volatility_action,
            "size_multiplier": self.size_multiplier,
            "cost_diagnostics": dict(self.cost_diagnostics),
            "reason": self.reason,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }
