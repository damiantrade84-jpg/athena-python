"""Authoritative symbol-, engine-, and style-aware timeframe policy.

This module is deliberately side-effect free: importing it does not load the
Athena runtime, start feeds, or connect to a broker.  Live callers may supply a
``SpeedState`` built only from confirmed candles; historical callers omit it or
build it from information available at the historical decision timestamp.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from hashlib import sha256
import json
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence


POLICY_VERSION = "timeframe_policy.v1"


class Timeframe(str, Enum):
    D1 = "D1"
    H4 = "H4"
    H1 = "H1"
    M30 = "M30"
    M15 = "M15"
    M5 = "M5"


class TimeframeRole(str, Enum):
    REGIME = "regime"
    BIAS = "bias"
    STRUCTURE = "structure"
    SETUP = "setup"
    TRIGGER = "trigger"
    EXECUTION = "execution"


class SpeedClass(str, Enum):
    SLOW = "SLOW"
    NORMAL = "NORMAL"
    FAST = "FAST"
    EXTREME = "EXTREME"


class M5Role(str, Enum):
    DISABLED = "DISABLED"
    ADVISORY = "ADVISORY"
    REFINEMENT = "REFINEMENT"
    EXECUTION = "EXECUTION"


class PolicySource(str, Enum):
    SYMBOL_OVERRIDE = "SYMBOL_OVERRIDE"
    SCORE_GROUP_OVERRIDE = "SCORE_GROUP_OVERRIDE"
    ASSET_STYLE_DEFAULT = "ASSET_STYLE_DEFAULT"
    SAFE_FALLBACK = "SAFE_FALLBACK"


@dataclass(frozen=True)
class SpeedThresholds:
    slow_max: float = 40.0
    normal_max: float = 70.0
    fast_max: float = 90.0
    hysteresis_closes: int = 2
    max_quote_age_sec: float = 15.0
    max_spread_m15_atr: float = 0.20


@dataclass(frozen=True)
class SpeedState:
    """Point-in-time speed inputs derived from confirmed H1/M15 bars only."""

    live_speed_class: SpeedClass | None = None
    candidate_speed_class: SpeedClass | None = None
    speed_percentile: float | None = None
    h1_atr14_pct: float | None = None
    h1_atr_pct_percentile: float | None = None
    m15_median_true_range_pct: float | None = None
    m15_range_percentile: float | None = None
    spread_m15_atr: float | None = None
    quote_age_sec: float | None = None
    relative_volume: float | None = None
    current_session: str | None = None
    price_velocity: float | None = None
    price_velocity_percentile: float | None = None
    gap_status: str | None = None
    scheduled_event: bool | None = None
    thin_liquidity: bool = False
    m5_quality_acceptable: bool = False
    last_closed_h1_open_time: int | None = None
    candidate_streak: int = 0
    updated_on_new_h1_close: bool = False
    missing_inputs: tuple[str, ...] = ()
    thresholds: SpeedThresholds = field(default_factory=SpeedThresholds)


@dataclass(frozen=True)
class PolicyDiagnostics:
    canonical_symbol: str
    requested_symbol: str
    asset_type: str
    score_group: str | None
    style: str
    messages: tuple[str, ...] = ()
    missing_speed_inputs: tuple[str, ...] = ()
    speed_thresholds: Mapping[str, Any] = field(default_factory=dict)
    m15_confirmation_required_for_m5: bool = False
    safe_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonicalSymbol": self.canonical_symbol,
            "requestedSymbol": self.requested_symbol,
            "assetType": self.asset_type,
            "scoreGroup": self.score_group,
            "style": self.style,
            "messages": list(self.messages),
            "missingSpeedInputs": list(self.missing_speed_inputs),
            "speedThresholds": dict(self.speed_thresholds),
            "m15ConfirmationRequiredForM5": self.m15_confirmation_required_for_m5,
            "safeFallback": self.safe_fallback,
        }


@dataclass(frozen=True)
class TimeframePolicy:
    policy_version: str
    profile: str
    regime_tf: Timeframe
    bias_tf: Timeframe
    structure_tf: Timeframe
    setup_tf: Timeframe
    trigger_tf: Timeframe
    execution_tf: Timeframe
    m5_role: M5Role
    required_closed_tfs: tuple[Timeframe, ...]
    forming_tf: Timeframe | None
    baseline_speed_class: SpeedClass
    live_speed_class: SpeedClass
    policy_source: PolicySource
    diagnostics: PolicyDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "profile": self.profile,
            "regime_tf": self.regime_tf.value,
            "bias_tf": self.bias_tf.value,
            "structure_tf": self.structure_tf.value,
            "setup_tf": self.setup_tf.value,
            "trigger_tf": self.trigger_tf.value,
            "execution_tf": self.execution_tf.value,
            "m5_role": self.m5_role.value,
            "required_closed_tfs": [tf.value for tf in self.required_closed_tfs],
            "forming_tf": self.forming_tf.value if self.forming_tf else None,
            "baseline_speed_class": self.baseline_speed_class.value,
            "live_speed_class": self.live_speed_class.value,
            "policy_source": self.policy_source.value,
            "diagnostics": self.diagnostics.to_dict(),
        }

    def payload(self) -> dict[str, Any]:
        data = self.to_dict()
        stable = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return {
            "timeframePolicyVersion": self.policy_version,
            "timeframePolicyHash": sha256(stable.encode("utf-8")).hexdigest(),
            "timeframeProfile": self.profile,
            "regimeTf": self.regime_tf.value,
            "biasTf": self.bias_tf.value,
            "structureTf": self.structure_tf.value,
            "setupTf": self.setup_tf.value,
            "triggerTf": self.trigger_tf.value,
            "executionTf": self.execution_tf.value,
            "m5Role": self.m5_role.value,
            "baselineSpeedClass": self.baseline_speed_class.value,
            "liveSpeedClass": self.live_speed_class.value,
            "speedPercentile": None,
            "policySource": self.policy_source.value,
            "timeframePolicyDiagnostics": self.diagnostics.to_dict(),
        }


@dataclass(frozen=True)
class _Template:
    profile: str
    regime: Timeframe = Timeframe.D1
    bias: Timeframe = Timeframe.H4
    structure: Timeframe = Timeframe.H1
    setup: Timeframe = Timeframe.M30
    trigger: Timeframe = Timeframe.M15
    execution: Timeframe = Timeframe.M15
    m5_role: M5Role = M5Role.REFINEMENT
    baseline_speed: SpeedClass = SpeedClass.NORMAL
    m15_confirmation_required_for_m5: bool = False


def _template(
    profile: str,
    *,
    bias: Timeframe = Timeframe.H4,
    structure: Timeframe = Timeframe.H1,
    setup: Timeframe = Timeframe.M30,
    trigger: Timeframe = Timeframe.M15,
    execution: Timeframe = Timeframe.M15,
    m5_role: M5Role = M5Role.REFINEMENT,
    speed: SpeedClass = SpeedClass.NORMAL,
    confirm_m15: bool = False,
) -> _Template:
    return _Template(
        profile=profile,
        bias=bias,
        structure=structure,
        setup=setup,
        trigger=trigger,
        execution=execution,
        m5_role=m5_role,
        baseline_speed=speed,
        m15_confirmation_required_for_m5=confirm_m15,
    )


_LIQUID_FAST = _template(
    "LIQUID_FAST",
    execution=Timeframe.M5,
    m5_role=M5Role.EXECUTION,
    speed=SpeedClass.FAST,
)
_STANDARD = _template("STANDARD", m5_role=M5Role.REFINEMENT)
_NO_M5 = _template("NO_M5", m5_role=M5Role.DISABLED)
_SLOW_RANGE = _template(
    "SLOW_RANGE",
    bias=Timeframe.H4,
    structure=Timeframe.H4,
    setup=Timeframe.H1,
    trigger=Timeframe.M30,
    execution=Timeframe.M15,
    m5_role=M5Role.DISABLED,
    speed=SpeedClass.SLOW,
)
_EXOTIC = _template("EXOTIC", m5_role=M5Role.ADVISORY, speed=SpeedClass.SLOW)
_EQUITY = _template(
    "CASH_EQUITY",
    bias=Timeframe.D1,
    structure=Timeframe.H1,
    execution=Timeframe.M5,
    m5_role=M5Role.EXECUTION,
    speed=SpeedClass.NORMAL,
)
_OIL = _template(
    "ENERGY_FAST_CONFIRMED",
    execution=Timeframe.M5,
    m5_role=M5Role.EXECUTION,
    speed=SpeedClass.FAST,
    confirm_m15=True,
)


def _key(value: Any) -> str:
    text = str(value or "").strip().upper()
    for prefix in ("BINANCE:", "BYBIT:", "MT5:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("=X", "").replace(".US", "")
    return "".join(ch for ch in text if ch.isalnum())


# Aliases are identities, not policies.  Every alias set points to one canonical
# instrument; unknown keys are never matched by prefix or nearest-neighbour logic.
_ALIASES: dict[str, str] = {}


def _aliases(canonical: str, *values: str) -> None:
    for value in (canonical, *values):
        _ALIASES[_key(value)] = canonical


for _canonical, _values in {
    "EURUSD": ("EUR/USD", "EURUSD=X"),
    "GBPUSD": ("GBP/USD", "GBPUSD=X"),
    "USDJPY": ("USD/JPY", "USDJPY=X"),
    "USDCHF": ("USD/CHF", "USDCHF=X"),
    "AUDUSD": ("AUD/USD", "AUDUSD=X"),
    "NZDUSD": ("NZD/USD", "NZDUSD=X"),
    "USDCAD": ("USD/CAD", "USDCAD=X"),
    "EURGBP": ("EUR/GBP", "EURGBP=X"),
    "AUDNZD": ("AUD/NZD", "AUDNZD=X"),
    "GBPJPY": ("GBP/JPY", "GBPJPY=X"),
    "EURJPY": ("EUR/JPY", "EURJPY=X"),
    "AUDJPY": ("AUD/JPY", "AUDJPY=X"),
    "EURCHF": ("EUR/CHF", "EURCHF=X"),
    "XAUUSD": ("XAU/USD", "GC=F"),
    "XAGUSD": ("XAG/USD", "SI=F"),
    "XPTUSD": ("XPT/USD", "PL=F"),
    "XPDUSD": ("XPD/USD", "PA=F"),
    "WTI": ("WTI Oil", "SpotCrude", "USOIL", "CL=F"),
    "BRENT": ("Brent Oil", "SpotBrent", "BZ=F"),
    "NATGAS": ("Nat Gas", "Natural Gas"),
    "NAS100": ("NASDAQ-100", "Nasdaq"),
    "US500": ("S&P 500", "SP500", "^GSPC"),
    "US30": ("Dow Jones", "^DJI"),
    "GER40": ("DAX", "DAX 40", "^GDAXI"),
    "UK100": ("FTSE 100", "^FTSE"),
    "JPN225": ("Nikkei 225", "JP225", "^N225"),
    "BTCUSDT": ("BTC/USDT",),
    "ETHUSDT": ("ETH/USDT",),
    "BNBUSDT": ("BNB/USDT",),
    "SOLUSDT": ("SOL/USDT",),
    "XRPUSDT": ("XRP/USDT",),
    "DOGEUSDT": ("DOGE/USDT",),
    "ADAUSDT": ("ADA/USDT",),
    "LINKUSDT": ("LINK/USDT",),
    "POLUSDT": ("POL/USDT", "MATICUSDT"),
    "AAPL": ("AAPL.US",),
    "SPY": ("SPY.US",),
}.items():
    _aliases(_canonical, *_values)


_SYMBOL_OVERRIDES: dict[str, _Template] = {
    "EURUSD": _STANDARD,
    "GBPUSD": _LIQUID_FAST,
    "USDJPY": _LIQUID_FAST,
    "USDCHF": _STANDARD,
    "AUDUSD": _STANDARD,
    "USDCAD": _STANDARD,
    "EURGBP": _SLOW_RANGE,
    "AUDNZD": _SLOW_RANGE,
    "GBPJPY": _LIQUID_FAST,
    "EURJPY": _LIQUID_FAST,
    "AUDJPY": _LIQUID_FAST,
    "EURCHF": _SLOW_RANGE,
    "XAUUSD": _LIQUID_FAST,
    "XAGUSD": _STANDARD,
    "XPTUSD": _NO_M5,
    "XPDUSD": _NO_M5,
    "WTI": _OIL,
    "BRENT": _OIL,
    "NATGAS": replace(_NO_M5, profile="NATGAS_NO_M5", baseline_speed=SpeedClass.FAST),
    "NAS100": _LIQUID_FAST,
    "US30": _LIQUID_FAST,
    "GER40": _LIQUID_FAST,
    "JPN225": _LIQUID_FAST,
    "US500": _STANDARD,
    "UK100": _STANDARD,
    "AAPL": _EQUITY,
    "SPY": _EQUITY,
    "BTCUSDT": _LIQUID_FAST,
    "ETHUSDT": _LIQUID_FAST,
    "BNBUSDT": _STANDARD,
    "SOLUSDT": _STANDARD,
    "XRPUSDT": _STANDARD,
    "DOGEUSDT": replace(_STANDARD, profile="CRYPTO_REFINEMENT", baseline_speed=SpeedClass.FAST),
    "ADAUSDT": _STANDARD,
    "LINKUSDT": _STANDARD,
}


_GROUP_OVERRIDES: dict[str, _Template] = {
    "forex_crosses": _SLOW_RANGE,
    "forex_exotics": _EXOTIC,
    "energy_oil": _OIL,
    "nat_gas": replace(_NO_M5, profile="NATGAS_NO_M5", baseline_speed=SpeedClass.FAST),
    "pgm_metals": _NO_M5,
    "base_metals": _NO_M5,
    "softs": _NO_M5,
    "us_stock_single": _EQUITY,
    "bond_tlt": _EQUITY,
    "smallcap_em_etf": _EQUITY,
    "crypto_other": replace(_STANDARD, profile="CRYPTO_REFINEMENT"),
    "crypto_alt_majors": replace(_STANDARD, profile="CRYPTO_REFINEMENT"),
}


_ASSET_DEFAULTS: dict[str, _Template] = {
    "forex": _STANDARD,
    "commodity": _NO_M5,
    "index": _STANDARD,
    "stock": _EQUITY,
    "etf": _EQUITY,
    "etf_bond": _EQUITY,
    "crypto": replace(_STANDARD, profile="CRYPTO_REFINEMENT"),
}


_TF_ORDER = (
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M30,
    Timeframe.M15,
    Timeframe.M5,
)


def canonical_symbol(symbol: str) -> str | None:
    """Return a known canonical identity, or ``None`` for an unknown alias."""
    return _ALIASES.get(_key(symbol))


def _adjacent(tf: Timeframe, faster: bool) -> Timeframe:
    index = _TF_ORDER.index(tf)
    index = min(len(_TF_ORDER) - 1, index + 1) if faster else max(0, index - 1)
    return _TF_ORDER[index]


def _engine_template(base: _Template, style: str) -> _Template:
    normalized = style.strip().lower().replace("-", "_")
    if normalized in {"engine_d", "d", "scalp_engine", "engine_d_scalp"}:
        return _Template(
            profile="ENGINE_D_NATIVE",
            regime=Timeframe.H1,
            bias=Timeframe.H1,
            structure=Timeframe.M15,
            setup=Timeframe.M15,
            trigger=Timeframe.M5,
            execution=Timeframe.M5,
            m5_role=M5Role.EXECUTION,
            baseline_speed=base.baseline_speed,
        )
    if normalized in {"engine_b_swing", "b_swing"}:
        return _Template(
            profile=f"ENGINE_B_SWING_{base.profile}",
            regime=Timeframe.D1,
            bias=Timeframe.D1,
            structure=Timeframe.H4,
            setup=Timeframe.H4,
            trigger=Timeframe.H1,
            execution=Timeframe.H1,
            m5_role=M5Role.DISABLED,
            baseline_speed=base.baseline_speed,
        )
    if normalized in {"engine_b_intraday", "b_intraday"}:
        return replace(
            base,
            profile=f"ENGINE_B_INTRADAY_{base.profile}",
            bias=Timeframe.H4,
            structure=Timeframe.H1,
            setup=Timeframe.M30,
            trigger=Timeframe.M15,
            execution=Timeframe.M15,
            m5_role=M5Role.REFINEMENT if base.m5_role != M5Role.DISABLED else M5Role.DISABLED,
        )
    if normalized in {"engine_b_scalp", "b_scalp"}:
        return _Template(
            profile=f"ENGINE_B_SCALP_{base.profile}",
            regime=Timeframe.H4,
            bias=Timeframe.H1,
            structure=Timeframe.H1,
            setup=Timeframe.M30,
            trigger=Timeframe.M15,
            execution=Timeframe.M5,
            m5_role=M5Role.EXECUTION,
            baseline_speed=base.baseline_speed,
        )
    return base


def resolve_timeframe_policy(
    symbol: str,
    asset_type: str,
    score_group: str | None,
    style: str,
    speed_state: SpeedState | None = None,
) -> TimeframePolicy:
    """Resolve the deterministic policy using the documented precedence."""
    requested = str(symbol or "").strip()
    canonical = canonical_symbol(requested)
    asset = str(asset_type or "").strip().lower()
    group = str(score_group or "").strip().lower() or None
    messages: list[str] = []

    if canonical and canonical in _SYMBOL_OVERRIDES:
        base = _SYMBOL_OVERRIDES[canonical]
        source = PolicySource.SYMBOL_OVERRIDE
    elif group and group in _GROUP_OVERRIDES:
        base = _GROUP_OVERRIDES[group]
        source = PolicySource.SCORE_GROUP_OVERRIDE
    elif asset in _ASSET_DEFAULTS:
        base = _ASSET_DEFAULTS[asset]
        source = PolicySource.ASSET_STYLE_DEFAULT
        if canonical is None:
            messages.append("symbol_not_in_alias_registry; using explicit asset/style default")
    else:
        base = replace(_NO_M5, profile="SAFE_FALLBACK", baseline_speed=SpeedClass.SLOW)
        source = PolicySource.SAFE_FALLBACK
        messages.append("unknown symbol/asset; fail-closed higher-timeframe fallback")

    selected = _engine_template(base, style)
    live_speed = (
        speed_state.live_speed_class
        if speed_state and speed_state.live_speed_class is not None
        else selected.baseline_speed
    )
    setup = selected.setup
    trigger = selected.trigger
    execution = selected.execution
    m5_role = selected.m5_role

    if speed_state is None or speed_state.live_speed_class is None:
        messages.append("live speed unavailable; baseline policy retained")
    elif live_speed == SpeedClass.SLOW:
        setup = _adjacent(setup, faster=False)
        trigger = _adjacent(trigger, faster=False)
        execution = _adjacent(execution, faster=False)
        if execution != Timeframe.M5 and m5_role == M5Role.EXECUTION:
            m5_role = M5Role.REFINEMENT
    elif live_speed in {SpeedClass.FAST, SpeedClass.EXTREME}:
        if selected.m5_role in {M5Role.EXECUTION, M5Role.REFINEMENT}:
            if speed_state.m5_quality_acceptable and not speed_state.thin_liquidity:
                execution = _adjacent(execution, faster=True)
                if execution == Timeframe.M5:
                    m5_role = M5Role.EXECUTION
            else:
                messages.append("M5 authority withheld: quote/spread/liquidity quality unacceptable")

    if live_speed == SpeedClass.EXTREME and speed_state and speed_state.thin_liquidity:
        if execution == Timeframe.M5:
            execution = Timeframe.M15
        m5_role = M5Role.DISABLED
        messages.append("EXTREME+thin liquidity forced execution away from M5")

    required = tuple(dict.fromkeys((
        selected.regime,
        selected.bias,
        selected.structure,
        setup,
        trigger,
    )))
    forming_tf = execution if execution == Timeframe.M5 and m5_role in {
        M5Role.EXECUTION,
        M5Role.REFINEMENT,
    } else None
    thresholds = speed_state.thresholds if speed_state else SpeedThresholds()
    diagnostics = PolicyDiagnostics(
        canonical_symbol=canonical or "UNKNOWN",
        requested_symbol=requested,
        asset_type=asset or "unknown",
        score_group=group,
        style=str(style or "intraday"),
        messages=tuple(messages),
        missing_speed_inputs=speed_state.missing_inputs if speed_state else ("speed_state",),
        speed_thresholds=asdict(thresholds),
        m15_confirmation_required_for_m5=selected.m15_confirmation_required_for_m5,
        safe_fallback=source == PolicySource.SAFE_FALLBACK,
    )
    return TimeframePolicy(
        policy_version=POLICY_VERSION,
        profile=selected.profile,
        regime_tf=selected.regime,
        bias_tf=selected.bias,
        structure_tf=selected.structure,
        setup_tf=setup,
        trigger_tf=trigger,
        execution_tf=execution,
        m5_role=m5_role,
        required_closed_tfs=required,
        forming_tf=forming_tf,
        baseline_speed_class=selected.baseline_speed,
        live_speed_class=live_speed,
        policy_source=source,
        diagnostics=diagnostics,
    )


def speed_class_for_percentile(
    percentile: float,
    thresholds: SpeedThresholds | None = None,
) -> SpeedClass:
    cfg = thresholds or SpeedThresholds()
    value = max(0.0, min(100.0, float(percentile)))
    if value < cfg.slow_max:
        return SpeedClass.SLOW
    if value < cfg.normal_max:
        return SpeedClass.NORMAL
    if value < cfg.fast_max:
        return SpeedClass.FAST
    return SpeedClass.EXTREME


def apply_speed_hysteresis(
    previous: SpeedClass | None,
    candidate: SpeedClass,
    candidate_streak: int,
    thresholds: SpeedThresholds | None = None,
) -> tuple[SpeedClass, int]:
    """Persist a class only after consecutive newly closed H1 observations."""
    cfg = thresholds or SpeedThresholds()
    if previous is None or candidate == previous:
        return candidate, 0
    streak = max(0, int(candidate_streak)) + 1
    if streak >= max(1, int(cfg.hysteresis_closes)):
        return candidate, 0
    return previous, streak


def _number(candle: Mapping[str, Any], key: str) -> float | None:
    try:
        value = float(candle.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _true_ranges(candles: Sequence[Mapping[str, Any]]) -> list[float]:
    out: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high = _number(candle, "high")
        low = _number(candle, "low")
        close = _number(candle, "close")
        if high is None or low is None or close is None or high < low:
            previous_close = close if close is not None else previous_close
            continue
        tr = high - low
        if previous_close is not None:
            tr = max(tr, abs(high - previous_close), abs(low - previous_close))
        out.append(tr)
        previous_close = close
    return out


def _rolling_atr_pct(candles: Sequence[Mapping[str, Any]], period: int = 14) -> list[float]:
    trs = _true_ranges(candles)
    closes = [_number(candle, "close") for candle in candles]
    if len(trs) != len(candles):
        return []
    out: list[float] = []
    for index in range(period - 1, len(trs)):
        close = closes[index]
        if close and close > 0:
            out.append((sum(trs[index - period + 1:index + 1]) / period) / close * 100.0)
    return out


def _percentile_rank(values: Sequence[float], current: float) -> float | None:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return None
    below = sum(1 for value in clean if value < current)
    equal = sum(1 for value in clean if value == current)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def calculate_speed_state(
    h1_confirmed: Sequence[Mapping[str, Any]],
    m15_confirmed: Sequence[Mapping[str, Any]],
    *,
    spread: float | None = None,
    quote_age_sec: float | None = None,
    relative_volume: float | None = None,
    current_session: str | None = None,
    gap_status: str | None = None,
    scheduled_event: bool | None = None,
    previous: SpeedState | None = None,
    last_closed_h1_open_time: int | None = None,
    thresholds: SpeedThresholds | None = None,
) -> SpeedState:
    """Calculate point-in-time speed from confirmed bars; never reads wall time."""
    cfg = thresholds or (previous.thresholds if previous else SpeedThresholds())
    missing: list[str] = []
    h1_atrs = _rolling_atr_pct(list(h1_confirmed), 14)
    h1_atr_pct = h1_atrs[-1] if h1_atrs else None
    h1_percentile = _percentile_rank(h1_atrs[:-1], h1_atr_pct) if h1_atr_pct is not None else None
    if h1_atr_pct is None:
        missing.append("h1_atr14_pct")

    m15_trs = _true_ranges(list(m15_confirmed))
    m15_closes = [_number(candle, "close") for candle in m15_confirmed]
    m15_range_pcts = [
        tr / close * 100.0
        for tr, close in zip(m15_trs, m15_closes)
        if close is not None and close > 0
    ] if len(m15_trs) == len(m15_closes) else []
    recent_ranges = m15_range_pcts[-14:]
    m15_median = statistics.median(recent_ranges) if recent_ranges else None
    m15_percentile = _percentile_rank(m15_range_pcts[:-1], m15_range_pcts[-1]) if m15_range_pcts else None
    if m15_median is None:
        missing.append("m15_median_true_range_pct")

    m15_atr_price = None
    if m15_trs and len(m15_trs) >= 14:
        m15_atr_price = sum(m15_trs[-14:]) / 14.0
    try:
        spread_ratio = float(spread) / m15_atr_price if spread is not None and m15_atr_price else None
    except (TypeError, ValueError, ZeroDivisionError):
        spread_ratio = None
    if spread_ratio is None:
        missing.append("spread_m15_atr")
    if quote_age_sec is None:
        missing.append("quote_age_sec")

    velocity = None
    velocity_percentile = None
    valid_closes = [value for value in m15_closes[-5:] if value is not None and value > 0]
    if len(valid_closes) >= 2:
        velocity = (valid_closes[-1] - valid_closes[0]) / valid_closes[0] * 100.0

    velocity_history: list[float] = []
    clean_m15_closes = [value for value in m15_closes if value is not None and value > 0]
    for index in range(4, len(clean_m15_closes)):
        start = clean_m15_closes[index - 4]
        velocity_history.append(abs(clean_m15_closes[index] - start) / start * 100.0)
    if velocity_history:
        velocity_percentile = _percentile_rank(
            velocity_history[:-1], velocity_history[-1]
        )
    if velocity_percentile is None:
        missing.append("price_velocity_percentile")

    volumes = [
        value
        for candle in m15_confirmed
        if (value := _number(candle, "vol")) is not None and value >= 0
    ]
    volume_percentile = None
    resolved_relative_volume = relative_volume
    if volumes:
        volume_percentile = _percentile_rank(volumes[:-1], volumes[-1])
        baseline_volumes = volumes[-21:-1]
        baseline_volume = statistics.median(baseline_volumes) if baseline_volumes else None
        if resolved_relative_volume is None and baseline_volume and baseline_volume > 0:
            resolved_relative_volume = volumes[-1] / baseline_volume
    if resolved_relative_volume is None:
        missing.append("relative_volume")
    if current_session is None:
        missing.append("current_session")
    if gap_status is None:
        missing.append("gap_status")
    if scheduled_event is None:
        missing.append("scheduled_event_context")

    # Median aggregation is robust to a single outlier component. Spread,
    # quote age, gaps, session state, and event context remain independent
    # quality gates; they can withhold M5 authority but never inflate speed.
    speed_components = [
        value
        for value in (
            h1_percentile,
            m15_percentile,
            velocity_percentile,
            volume_percentile,
        )
        if value is not None
    ]
    speed_percentile = statistics.median(speed_components) if speed_components else None
    candidate = speed_class_for_percentile(speed_percentile, cfg) if speed_percentile is not None else None

    new_h1_close = bool(
        candidate is not None
        and last_closed_h1_open_time is not None
        and (previous is None or previous.last_closed_h1_open_time != last_closed_h1_open_time)
    )
    required_speed_missing = any(
        name in missing
        for name in (
            "h1_atr14_pct",
            "m15_median_true_range_pct",
            "spread_m15_atr",
            "quote_age_sec",
        )
    )
    if candidate is None or required_speed_missing:
        persistent = previous.live_speed_class if previous else None
        streak = previous.candidate_streak if previous else 0
    elif not new_h1_close and previous and previous.live_speed_class is not None:
        persistent = previous.live_speed_class
        streak = previous.candidate_streak
    else:
        persistent, streak = apply_speed_hysteresis(
            previous.live_speed_class if previous else None,
            candidate,
            previous.candidate_streak if previous else 0,
            cfg,
        )

    gap = str(gap_status or "").strip().lower()
    session = str(current_session or "").strip().lower()
    thin = bool(
        quote_age_sec is None
        or float(quote_age_sec) > cfg.max_quote_age_sec
        or spread_ratio is None
        or spread_ratio > cfg.max_spread_m15_atr
        or gap not in {"", "none", "normal", "open"}
        or session in {"closed", "avoid", "off_hours"}
        or scheduled_event is True
    )
    m5_ok = not thin and spread_ratio is not None and quote_age_sec is not None
    return SpeedState(
        live_speed_class=persistent,
        candidate_speed_class=candidate,
        speed_percentile=speed_percentile,
        h1_atr14_pct=h1_atr_pct,
        h1_atr_pct_percentile=h1_percentile,
        m15_median_true_range_pct=m15_median,
        m15_range_percentile=m15_percentile,
        spread_m15_atr=spread_ratio,
        quote_age_sec=float(quote_age_sec) if quote_age_sec is not None else None,
        relative_volume=resolved_relative_volume,
        current_session=current_session,
        price_velocity=velocity,
        price_velocity_percentile=velocity_percentile,
        gap_status=gap_status,
        scheduled_event=scheduled_event,
        thin_liquidity=thin,
        m5_quality_acceptable=m5_ok,
        last_closed_h1_open_time=last_closed_h1_open_time,
        candidate_streak=streak,
        updated_on_new_h1_close=new_h1_close,
        missing_inputs=tuple(dict.fromkeys(missing)),
        thresholds=cfg,
    )


def attach_timeframe_policy_payload(
    signal: dict[str, Any],
    pair: Mapping[str, Any],
    style: str,
    *,
    engine: str = "engine_a",
    market_states: Mapping[str, Mapping[str, Any]] | None = None,
    speed_state: SpeedState | None = None,
) -> TimeframePolicy:
    """Attach server-authoritative policy/readiness fields without altering scores."""
    symbol = str(pair.get("display") or pair.get("symbol") or signal.get("display") or "")
    asset_type = str(pair.get("type") or pair.get("asset_type") or signal.get("type") or "")
    score_group = pair.get("score_group") or signal.get("scoreGroup") or signal.get("score_group")
    style_key = str(style or "intraday")
    engine_key = str(engine or "engine_a").strip().lower()
    if engine_key in {"engine_b", "b"}:
        style_key = f"engine_b_{style_key.lower()}"
    elif engine_key in {"engine_d", "d", "scalp"}:
        style_key = "engine_d"
    policy = resolve_timeframe_policy(symbol, asset_type, str(score_group) if score_group else None, style_key, speed_state)
    payload = policy.payload()
    payload["speedPercentile"] = speed_state.speed_percentile if speed_state else None
    signal.update(payload)

    states = market_states or {}
    confirmed_times: dict[str, Any] = {}
    forming_times: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []
    for tf in policy.required_closed_tfs:
        state = states.get(tf.value) or {}
        confirmed = list(state.get("confirmed") or [])
        confirmed_time = state.get("last_confirmed_open_time_utc")
        if confirmed_time is None and confirmed:
            confirmed_time = confirmed[-1].get("time") or confirmed[-1].get("datetime")
        confirmed_times[tf.value] = confirmed_time
        if not confirmed:
            missing.append(tf.value)
        elif state.get("stale") is True:
            stale.append(tf.value)
    for tf, state in states.items():
        forming_time = state.get("forming_open_time_utc")
        forming = state.get("forming")
        if forming_time is None and isinstance(forming, Mapping):
            forming_time = forming.get("time") or forming.get("datetime")
        if forming_time is not None:
            forming_times[str(tf)] = forming_time

    signal["confirmedBarTimes"] = confirmed_times
    signal["formingBarTime"] = forming_times or None
    trigger_state = states.get(policy.trigger_tf.value) or {}
    trigger_closed = bool(trigger_state.get("confirmed"))
    explicit_trigger = signal.get("triggerConfirmed")
    if explicit_trigger is None:
        explicit_trigger = next(
            (
                signal.get(key)
                for key in ("trigger_confirmed", "trigger_ok", "trigger_passed")
                if signal.get(key) is not None
            ),
            None,
        )
    trigger_confirmed = bool(explicit_trigger) if explicit_trigger is not None else False
    if missing:
        readiness = "UNAVAILABLE"
        reason = "missing_required_closed_timeframes:" + ",".join(missing)
    elif stale:
        readiness = "UNAVAILABLE"
        reason = "stale_required_closed_timeframes:" + ",".join(stale)
    elif not trigger_confirmed:
        readiness = "PENDING"
        reason = "confirmed trigger condition not supplied"
    else:
        readiness = "READY"
        reason = "required closed timeframes and trigger are confirmed"
    signal["entryReadiness"] = readiness
    signal["entryReadinessReason"] = reason
    signal["triggerConfirmed"] = trigger_confirmed
    signal["triggerCandleClosed"] = trigger_closed

    atr_diag = signal.get("atrDiagnostics") if isinstance(signal.get("atrDiagnostics"), Mapping) else {}
    atr_value = atr_diag.get("atr_value", signal.get("atr"))
    atr_tf = atr_diag.get("atr_tf") or policy.structure_tf.value
    signal["atrValue"] = atr_value
    signal["atrTimeframe"] = atr_tf
    signal.setdefault("structureAgeBars", None)
    signal.setdefault("quoteAgeSec", speed_state.quote_age_sec if speed_state else None)
    try:
        entry = float(signal.get("entry") or signal.get("price"))
        live = float(signal.get("livePrice") or signal.get("price"))
        atr = float(atr_value)
        signal["livePriceDriftAtr"] = abs(live - entry) / atr if atr > 0 else None
    except (TypeError, ValueError):
        signal["livePriceDriftAtr"] = None
    return policy


def derive_warmup_bars(
    *,
    ema_periods: Iterable[int] = (21, 50, 200),
    adx_period: int = 14,
    atr_period: int = 14,
    structure_lookback: int = 20,
    safety_margin: int = 20,
) -> int:
    """Derive warmup from actual configured lookbacks instead of a fixed 100."""
    lookbacks = [int(value) for value in ema_periods]
    lookbacks.extend((int(adx_period) * 2, int(atr_period), int(structure_lookback)))
    return max(lookbacks) + max(0, int(safety_margin))


def reconcile_symbol_universe(
    pairs: Sequence[Mapping[str, Any]],
    *,
    style: str = "intraday",
) -> dict[str, Any]:
    """Return a deterministic reconciliation suitable for tests and reports."""
    rows: list[dict[str, Any]] = []
    canonical_groups: dict[str, set[str]] = {}
    for pair in pairs:
        if not pair.get("enabled", True):
            continue
        display = str(pair.get("display") or pair.get("symbol") or "")
        asset_type = str(pair.get("type") or pair.get("asset_type") or "")
        score_group = pair.get("score_group")
        if not score_group:
            try:
                from engine_a_groups import resolve_score_group_by_type
                score_group = resolve_score_group_by_type(dict(pair))
            except Exception:
                score_group = None
        policy = resolve_timeframe_policy(display, asset_type, str(score_group) if score_group else None, style)
        canonical = policy.diagnostics.canonical_symbol
        if canonical == "UNKNOWN":
            canonical = _key(pair.get("symbol") or display) or "UNKNOWN"
        canonical_groups.setdefault(canonical, set()).add(str(score_group or "unknown"))
        rows.append({
            "display_symbol": display,
            "canonical_symbol": canonical,
            "asset_type": asset_type,
            "score_group": score_group,
            "provider": pair.get("source"),
            "provider_symbol": pair.get("bybit_symbol") or pair.get("mt5_symbol") or pair.get("symbol"),
            "supported_styles": ("intraday", "swing", "engine_b_intraday", "engine_b_swing", "engine_d"),
            "baseline_profile": policy.profile,
            "policy_source": policy.policy_source.value,
            "safe_fallback": policy.diagnostics.safe_fallback,
        })
    duplicates = sorted(
        canonical for canonical, count in {
            row["canonical_symbol"]: sum(1 for candidate in rows if candidate["canonical_symbol"] == row["canonical_symbol"])
            for row in rows
        }.items() if count > 1
    )
    multi_group = {
        canonical: sorted(groups)
        for canonical, groups in canonical_groups.items()
        if len(groups) > 1
    }
    return {
        "rows": rows,
        "duplicate_canonical_symbols": duplicates,
        "aliases_mapping_to_multiple_groups": multi_group,
        "unsafe_symbols": [row["display_symbol"] for row in rows if row["safe_fallback"]],
    }
