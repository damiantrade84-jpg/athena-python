"""Authoritative symbol-, engine-, and style-aware timeframe policy.

This module is deliberately side-effect free: importing it does not load the
Athena runtime, start feeds, or connect to a broker.  Live callers may supply a
``SpeedState`` built only from confirmed candles; historical callers omit it or
build it from information available at the historical decision timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from hashlib import sha256
import json
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from m5_eligibility import M5EligibilityContext, evaluate_m5_eligibility
from trigger_lifecycle import TriggerRecord, TriggerState, TriggerTracker

POLICY_VERSION = "timeframe_policy.v4"


class PolicyConfigurationError(ValueError):
    """Raised for deterministic symbol/group policy configuration conflicts."""


class Timeframe(str, Enum):
    D1 = "D1"
    H4 = "H4"
    H1 = "H1"
    M30 = "M30"
    M15 = "M15"
    M5 = "M5"
    M1 = "M1"


# M1 is the terminal rung and may appear only in scalp/engine-d-native
# templates; ``resolve_timeframe_policy`` enforces that restriction.
TIMEFRAME_LADDER = (
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M30,
    Timeframe.M15,
    Timeframe.M5,
    Timeframe.M1,
)


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
    UNAVAILABLE = "UNAVAILABLE"


class LiquidityClass(str, Enum):
    DEEP = "DEEP"
    NORMAL = "NORMAL"
    THIN = "THIN"
    UNAVAILABLE = "UNAVAILABLE"


class M5Role(str, Enum):
    EXECUTION = "execution"
    REFINEMENT = "refinement"
    ADVISORY = "advisory"
    DISABLED = "disabled"


class ExecutionMode(str, Enum):
    """How the execution role is filled.

    ``LIVE_QUOTE`` — production fills are quote-based (bid/ask); the emitted
    ``executionTf`` is advisory execution context only and is not required to
    sit on the slower-to-faster ladder relative to the trigger.
    ``NEXT_AVAILABLE_QUOTE`` — historical/backtest fills use the next
    deterministic quote after the decision timestamp.
    """

    LIVE_QUOTE = "live_quote"
    NEXT_AVAILABLE_QUOTE = "next_available_quote"


class M5Policy(str, Enum):
    """Declarative M5 authority for a resolved profile.

    ``CONDITIONAL`` pairs with ``m5Role=refinement`` and
    ``m15_confirmation_required_for_m5=True``: M5 may act only after M15
    confirmation and a passing M5-eligibility evaluation. ``DISABLED`` pairs
    with ``m5Role=disabled``. There is no unconditional M5 authority in v4.
    """

    DISABLED = "disabled"
    CONDITIONAL = "conditional"


# Single mapping point so m5Policy can never drift from the legacy m5Role /
# M15-confirmation fields that are still emitted for backward compatibility.
_M5_POLICY_DERIVED: dict[M5Policy, tuple[M5Role, bool]] = {
    M5Policy.DISABLED: (M5Role.DISABLED, False),
    M5Policy.CONDITIONAL: (M5Role.REFINEMENT, True),
}


def parse_m5_matrix_language(value: str) -> tuple[M5Role, Timeframe | None]:
    """Map the approved policy-matrix language without heuristic authority."""
    normalized = " ".join(str(value or "").strip().lower().split())
    exact = {
        "m5": (M5Role.EXECUTION, None),
        "m5 after m15 confirmation": (M5Role.EXECUTION, Timeframe.M15),
        "m5 refinement optional": (M5Role.REFINEMENT, None),
        "m5 refinement only": (M5Role.REFINEMENT, None),
        "m5 advisory": (M5Role.ADVISORY, None),
        "m5 disabled": (M5Role.DISABLED, None),
        "no m5 authority": (M5Role.DISABLED, None),
    }
    if normalized not in exact:
        raise ValueError(f"unsupported M5 matrix language: {value!r}")
    return exact[normalized]


class PolicyMode(str, Enum):
    OFF = "off"
    ENFORCED_DEMO = "enforced_demo"
    ENFORCED_LIVE = "enforced_live"
    # Source-compatible aliases for callers that still import the previous enum.
    SHADOW = "off"
    ENFORCED = "enforced_demo"


# Compatibility aliases are deliberately parsed at the boundary only.  They are
# not emitted in payloads, so live callers can distinguish the two approved
# promotion states from historical shadow/enforced configuration.
_LEGACY_POLICY_MODE_ALIASES = {
    "shadow": PolicyMode.OFF,
    "enforced": PolicyMode.ENFORCED_DEMO,
}


class SessionCalendarSource(str, Enum):
    PROVIDER_METADATA = "PROVIDER_METADATA"
    PROVIDER_CONFIG = "PROVIDER_CONFIG"
    UNDERLYING_EXCHANGE_FALLBACK = "UNDERLYING_EXCHANGE_FALLBACK"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"


@dataclass(frozen=True)
class SessionCalendarResolution:
    calendar_id: str | None
    source: SessionCalendarSource
    provider_timezone: str | None
    session_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionCalendarId": self.calendar_id,
            "sessionCalendarSource": self.source.value,
            "providerSessionTimezone": self.provider_timezone,
            "sessionState": self.session_state,
        }


def _validated_timezone(value: Any) -> str | None:
    name = str(value or "").strip()
    if not name:
        return None
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None
    return name


def resolve_session_calendar(
    *,
    provider_metadata: Mapping[str, Any] | None = None,
    provider_calendar: Mapping[str, Any] | None = None,
    underlying_exchange_calendar: Mapping[str, Any] | None = None,
) -> SessionCalendarResolution:
    """Resolve timezone-aware calendars in provider-first precedence order."""
    candidates = (
        (SessionCalendarSource.PROVIDER_METADATA, provider_metadata),
        (SessionCalendarSource.PROVIDER_CONFIG, provider_calendar),
        (
            SessionCalendarSource.UNDERLYING_EXCHANGE_FALLBACK,
            underlying_exchange_calendar,
        ),
    )
    for source, candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        timezone_name = _validated_timezone(
            candidate.get("providerSessionTimezone")
            or candidate.get("timezone")
        )
        calendar_id = str(
            candidate.get("sessionCalendarId")
            or candidate.get("calendar_id")
            or ""
        ).strip()
        if calendar_id and timezone_name:
            return SessionCalendarResolution(
                calendar_id=calendar_id,
                source=source,
                provider_timezone=timezone_name,
                session_state=str(
                    candidate.get("sessionState")
                    or candidate.get("state")
                    or "UNKNOWN"
                ).upper(),
            )
    return SessionCalendarResolution(
        calendar_id=None,
        source=SessionCalendarSource.SESSION_UNAVAILABLE,
        provider_timezone=None,
        session_state="SESSION_UNAVAILABLE",
    )


class PolicySource(str, Enum):
    SYMBOL_OVERRIDE = "SYMBOL_OVERRIDE"
    SCORE_GROUP_OVERRIDE = "SCORE_GROUP_OVERRIDE"
    ASSET_STYLE_DEFAULT = "ASSET_STYLE_DEFAULT"
    SAFE_FALLBACK = "SAFE_FALLBACK"
    CONFIG_CONFLICT = "CONFIG_CONFLICT"


@dataclass(frozen=True)
class SpeedThresholds:
    slow_to_normal_min: float = 45.0
    normal_to_slow_max: float = 35.0
    normal_to_fast_min: float = 75.0
    fast_to_normal_max: float = 65.0
    fast_to_extreme_min: float = 92.0
    extreme_to_fast_max: float = 88.0
    hysteresis_closes: int = 2
    required_h1_bars: int = 214
    required_m15_bars: int = 214


@dataclass(frozen=True)
class LiquidityThresholds:
    max_quote_age_sec: float = 15.0
    deep_max_spread_trigger_atr: float = 0.05
    normal_max_spread_trigger_atr: float = 0.20
    deep_min_relative_volume: float = 1.25
    thin_max_relative_volume: float = 0.50
    required_volume_bars: int = 21


def thresholds_for_group(
    config: Mapping[str, Any],
    score_group: str | None,
) -> tuple[SpeedThresholds, LiquidityThresholds]:
    """Load configurable default plus score-group threshold overrides."""
    group = str(score_group or "").strip().lower()

    def _values(key: str) -> dict[str, Any]:
        section = config.get(key)
        if not isinstance(section, Mapping):
            return {}
        values = dict(section.get("default") or {})
        override = section.get(group)
        if isinstance(override, Mapping):
            values.update(override)
        return values

    speed_fields = SpeedThresholds.__dataclass_fields__
    liquidity_fields = LiquidityThresholds.__dataclass_fields__
    speed = SpeedThresholds(
        **{
            key: value
            for key, value in _values("TF_POLICY_SPEED_THRESHOLDS").items()
            if key in speed_fields
        }
    )
    liquidity = LiquidityThresholds(
        **{
            key: value
            for key, value in _values("TF_POLICY_LIQUIDITY_THRESHOLDS").items()
            if key in liquidity_fields
        }
    )
    return speed, liquidity


def baseline_liquidity_for_group(
    config: Mapping[str, Any],
    score_group: str | None,
) -> LiquidityClass:
    section = config.get("TF_POLICY_BASELINE_LIQUIDITY")
    if not isinstance(section, Mapping):
        return LiquidityClass.NORMAL
    value = section.get(str(score_group or "").strip().lower(), section.get("default"))
    try:
        return LiquidityClass(str(value or LiquidityClass.NORMAL.value).upper())
    except ValueError:
        return LiquidityClass.NORMAL


@dataclass(frozen=True)
class SpeedState:
    """Point-in-time speed inputs derived from confirmed H1/M15 bars only."""

    live_speed_class: SpeedClass | None = SpeedClass.UNAVAILABLE
    candidate_speed_class: SpeedClass | None = None
    candidate_age_h1_bars: int = 0
    transition_pending: bool = False
    last_speed_transition_utc: str | None = None
    liquidity_class: LiquidityClass = LiquidityClass.UNAVAILABLE
    baseline_liquidity_class: LiquidityClass = LiquidityClass.NORMAL
    history_ready: bool = False
    adaptation_applied: bool = False
    adaptation_reason: str = "INSUFFICIENT_HISTORY"
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
    liquidity_thresholds: LiquidityThresholds = field(default_factory=LiquidityThresholds)


def speed_state_from_policy_payload(
    signal: Mapping[str, Any] | None,
) -> SpeedState | None:
    """Rebuild only the policy-selection state emitted on a policy-stamped signal.

    This is used by execute-time server recomputation so the policy is
    re-resolved with the same speed/liquidity inputs instead of silently
    reverting to the baseline timeframe.  It does not restore indicator inputs
    or bypass the resolver; malformed values fail closed to baseline
    resolution, and callers verify that the recomputed policy hash still
    matches the stamped hash.  Both v3 (``executionTf``/``m5Role``) and v4
    (``m5Policy``) payloads are tolerated.
    """
    payload = signal if isinstance(signal, Mapping) else {}
    raw_speed = payload.get("currentSpeedClass", payload.get("liveSpeedClass"))
    try:
        live_speed = SpeedClass(str(raw_speed or SpeedClass.UNAVAILABLE.value).upper())
    except ValueError:
        return None
    raw_liquidity = payload.get("liquidityClass", LiquidityClass.UNAVAILABLE.value)
    try:
        liquidity = LiquidityClass(str(raw_liquidity or "").upper())
    except ValueError:
        return None
    try:
        speed_percentile = (
            float(payload.get("speedPercentile"))
            if payload.get("speedPercentile") is not None
            else None
        )
    except (TypeError, ValueError):
        speed_percentile = None
    execution_tf = str(payload.get("executionTf") or "").upper()
    m5_role = str(payload.get("m5Role") or "").lower()
    m5_policy = str(payload.get("m5Policy") or "").lower()
    return SpeedState(
        live_speed_class=live_speed,
        liquidity_class=liquidity,
        history_ready=live_speed != SpeedClass.UNAVAILABLE,
        speed_percentile=speed_percentile,
        thin_liquidity=liquidity in {
            LiquidityClass.THIN,
            LiquidityClass.UNAVAILABLE,
        },
        m5_quality_acceptable=(
            # v3 payloads: speed-promoted M5 execution was stamped as
            # executionTf=M5 + m5Role=execution. v4 payloads: declarative
            # m5Policy=conditional. Both are tolerated; neither re-arms the
            # removed speed-driven M5 promotion.
            (
                execution_tf == Timeframe.M5.value
                and m5_role == M5Role.EXECUTION.value
            )
            or m5_policy == M5Policy.CONDITIONAL.value
        ),
    )


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
    config_conflict: bool = False
    adaptation_applied: bool = False
    adaptation_reason: str = "INSUFFICIENT_HISTORY"
    liquidity_class: LiquidityClass = LiquidityClass.UNAVAILABLE
    symbol_override_applied: bool = False
    symbol_override_patched_roles: tuple[str, ...] = ()
    m5_liquidity_blocked: bool = False

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
            "configConflict": self.config_conflict,
            "adaptationApplied": self.adaptation_applied,
            "adaptationReason": self.adaptation_reason,
            "liquidityClass": self.liquidity_class.value,
            "symbolOverrideApplied": self.symbol_override_applied,
            "symbolOverridePatchedRoles": list(self.symbol_override_patched_roles),
            "m5LiquidityBlocked": self.m5_liquidity_blocked,
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
    canonical_symbol: str
    engine_id: str
    style: str
    policy_key: str
    execution_prerequisite_tf: Timeframe | None = None
    # v4 fields (defaulted so older constructions remain source-compatible):
    # execution is live-quote based for resolved production policies; the
    # emitted executionTf is advisory execution context only.
    execution_mode: ExecutionMode = ExecutionMode.LIVE_QUOTE
    m5_policy: M5Policy = M5Policy.DISABLED

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
            "execution_mode": self.execution_mode.value,
            "m5_policy": self.m5_policy.value,
            "required_closed_tfs": [tf.value for tf in self.required_closed_tfs],
            "forming_tf": self.forming_tf.value if self.forming_tf else None,
            "baseline_speed_class": self.baseline_speed_class.value,
            "live_speed_class": self.live_speed_class.value,
            "policy_source": self.policy_source.value,
            "canonical_symbol": self.canonical_symbol,
            "engine_id": self.engine_id,
            "style": self.style,
            "policy_key": self.policy_key,
            "execution_prerequisite_tf": (
                self.execution_prerequisite_tf.value
                if self.execution_prerequisite_tf
                else None
            ),
            "diagnostics": self.diagnostics.to_dict(),
        }

    def payload(self) -> dict[str, Any]:
        data = self.to_dict()
        stable_policy = {
            key: value
            for key, value in data.items()
            if key != "diagnostics"
        }
        stable = json.dumps(stable_policy, sort_keys=True, separators=(",", ":"))
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
            "executionMode": self.execution_mode.value,
            "m5Policy": self.m5_policy.value,
            "baselineSpeedClass": self.baseline_speed_class.value,
            "liveSpeedClass": self.live_speed_class.value,
            "speedPercentile": None,
            "policySource": self.policy_source.value,
            "canonicalSymbol": self.canonical_symbol,
            "engineId": self.engine_id,
            "style": self.style,
            "policyKey": self.policy_key,
            "executionPrerequisiteTf": (
                self.execution_prerequisite_tf.value
                if self.execution_prerequisite_tf
                else None
            ),
            "liquidityClass": self.diagnostics.liquidity_class.value,
            "adaptationApplied": self.diagnostics.adaptation_applied,
            "adaptationReason": self.diagnostics.adaptation_reason,
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
    # Advisory execution-context TF; production execution is live-quote based
    # (execution_mode) and never resolved from this field.
    execution: Timeframe = Timeframe.M15
    m5_role: M5Role = M5Role.DISABLED
    m5_policy: M5Policy = M5Policy.DISABLED
    execution_mode: ExecutionMode = ExecutionMode.LIVE_QUOTE
    baseline_speed: SpeedClass = SpeedClass.NORMAL
    m15_confirmation_required_for_m5: bool = False
    # DEPRECATED (policy v4): accepted so older serialized state and callers
    # still parse, but ignored. Speed never promotes M5 execution; conditional
    # M5 authority is governed by m5_policy plus the M5-eligibility layer.
    allow_dynamic_m5_execution: bool = False


def _group_template(
    profile: str,
    *,
    regime: Timeframe = Timeframe.D1,
    bias: Timeframe = Timeframe.H4,
    structure: Timeframe = Timeframe.H1,
    setup: Timeframe = Timeframe.M30,
    trigger: Timeframe = Timeframe.M15,
    m5_policy: M5Policy = M5Policy.DISABLED,
    speed: SpeedClass = SpeedClass.NORMAL,
) -> _Template:
    m5_role, confirm_m15 = _M5_POLICY_DERIVED[m5_policy]
    return _Template(
        profile=profile,
        regime=regime,
        bias=bias,
        structure=structure,
        setup=setup,
        trigger=trigger,
        # Advisory execution context follows the trigger by default.
        execution=trigger,
        m5_role=m5_role,
        m5_policy=m5_policy,
        execution_mode=ExecutionMode.LIVE_QUOTE,
        baseline_speed=speed,
        m15_confirmation_required_for_m5=confirm_m15,
    )


# Policy-v4 group matrix (regime/bias/structure/setup/trigger/M5 policy).
_FOREX_MAJORS_STANDARD = _group_template(
    "FOREX_MAJORS_STANDARD", setup=Timeframe.M30, trigger=Timeframe.M15
)
_FOREX_MAJORS_FAST = _group_template(
    "FOREX_MAJORS_FAST",
    setup=Timeframe.M15,
    trigger=Timeframe.M5,
    m5_policy=M5Policy.CONDITIONAL,
    speed=SpeedClass.FAST,
)
_FOREX_CROSSES_BROAD = _group_template(
    "FOREX_CROSSES_BROAD",
    structure=Timeframe.H4,
    setup=Timeframe.H1,
    trigger=Timeframe.M30,
)
_FOREX_CROSSES_LIQUID = _group_template(
    "FOREX_CROSSES_LIQUID", setup=Timeframe.M30, trigger=Timeframe.M15
)
_FOREX_EXOTICS_LIQUID = _group_template(
    "FOREX_EXOTICS_LIQUID",
    structure=Timeframe.H4,
    setup=Timeframe.H1,
    trigger=Timeframe.M30,
    speed=SpeedClass.SLOW,
)
_FOREX_EXOTICS_RESTRICTED = _group_template(
    "FOREX_EXOTICS_RESTRICTED",
    structure=Timeframe.H4,
    setup=Timeframe.H1,
    trigger=Timeframe.H1,
    speed=SpeedClass.SLOW,
)
_ENERGY_OIL = _group_template(
    "ENERGY_OIL_CONDITIONAL",
    setup=Timeframe.M15,
    trigger=Timeframe.M5,
    m5_policy=M5Policy.CONDITIONAL,
    speed=SpeedClass.FAST,
)
_NAT_GAS = _group_template(
    "NAT_GAS_NO_M5",
    setup=Timeframe.M30,
    trigger=Timeframe.M15,
    speed=SpeedClass.FAST,
)
_LIQUID_METALS = _group_template(
    "LIQUID_METALS", setup=Timeframe.M30, trigger=Timeframe.M15
)
_THIN_METALS_BASE_SOFTS = _group_template(
    "THIN_METALS_BASE_SOFTS",
    structure=Timeframe.H4,
    setup=Timeframe.H1,
    trigger=Timeframe.M30,
)
_EQUITY_INDEX_FAST = _group_template(
    "EQUITY_INDEX_FAST",
    setup=Timeframe.M15,
    trigger=Timeframe.M5,
    m5_policy=M5Policy.CONDITIONAL,
    speed=SpeedClass.FAST,
)
_EQUITY_INDEX_STANDARD = _group_template(
    "EQUITY_INDEX_STANDARD", setup=Timeframe.M30, trigger=Timeframe.M15
)
_US_STOCK_SINGLE = _group_template(
    "US_STOCK_SINGLE",
    bias=Timeframe.D1,
    structure=Timeframe.H1,
    setup=Timeframe.M15,
    trigger=Timeframe.M5,
    m5_policy=M5Policy.CONDITIONAL,
)
_BOND_TLT_SMALLCAP_EM_ETF = _group_template(
    "BOND_TLT_SMALLCAP_EM_ETF",
    bias=Timeframe.D1,
    structure=Timeframe.H1,
    setup=Timeframe.M30,
    trigger=Timeframe.M15,
)
_CRYPTO_MAJORS_FAST = _group_template(
    "CRYPTO_MAJORS_FAST",
    setup=Timeframe.M15,
    trigger=Timeframe.M5,
    m5_policy=M5Policy.CONDITIONAL,
    speed=SpeedClass.FAST,
)
_CRYPTO_ALT_MAJORS = _group_template(
    "CRYPTO_ALT_MAJORS", setup=Timeframe.M30, trigger=Timeframe.M15
)
_CRYPTO_OTHER_THIN = _group_template(
    "CRYPTO_OTHER_THIN",
    structure=Timeframe.H4,
    setup=Timeframe.H1,
    trigger=Timeframe.M30,
)
# Fail-closed base for CONFIG_CONFLICT / SAFE_FALLBACK resolutions.
_FAIL_CLOSED = _group_template(
    "EXTREME_EVENT_SENSITIVE",
    setup=Timeframe.M30,
    trigger=Timeframe.M15,
    speed=SpeedClass.SLOW,
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
_CANONICAL_DISPLAY: dict[str, str] = {}


def _aliases(canonical: str, *values: str) -> None:
    _CANONICAL_DISPLAY[canonical] = values[0] if values else canonical
    for value in (canonical, *values):
        alias_key = _key(value)
        existing = _ALIASES.get(alias_key)
        if existing and existing != canonical:
            raise PolicyConfigurationError(
                f"alias {value!r} maps to conflicting canonical symbols: "
                f"{existing}, {canonical}"
            )
        _ALIASES[alias_key] = canonical


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
    "AUDCHF": ("AUD/CHF", "AUDCHF=X"),
    "EURAUD": ("EUR/AUD", "EURAUD=X"),
    "GBPAUD": ("GBP/AUD", "GBPAUD=X"),
    "USDSGD": ("USD/SGD", "USDSGD=X"),
    "USDZAR": ("USD/ZAR", "USDZAR=X"),
    "USDMXN": ("USD/MXN", "USDMXN=X"),
    "USDBRL": ("USD/BRL", "USDBRL=X"),
    "USDINR": ("USD/INR", "USDINR=X"),
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
    "TLT": ("TLT.US",),
    "IWM": ("IWM.US",),
    "EEM": ("EEM.US",),
}.items():
    _aliases(_canonical, *_values)


# Symbol overrides are per-role patches over the resolved group/asset
# template (unset roles inherit).  ``m5_policy`` patches also re-derive
# m5_role/m15 confirmation via _M5_POLICY_DERIVED, and a patched trigger moves
# the advisory execution context with it unless ``execution`` is patched
# explicitly.  Patches pin every spec'd role so a symbol resolves
# deterministically regardless of the group fallback underneath it.
_SYMBOL_OVERRIDES: dict[str, dict[str, Any]] = {
    # Standard majors — D1/H4/H1/M30/M15, M5 disabled.
    "EURUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_MAJORS_STANDARD"},
    "USDCHF": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_MAJORS_STANDARD"},
    "AUDUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_MAJORS_STANDARD"},
    "NZDUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_MAJORS_STANDARD"},
    "USDCAD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_MAJORS_STANDARD"},
    # Fast majors — D1/H4/H1/M15/M5, M5 conditional.
    "GBPUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "FOREX_MAJORS_FAST"},
    "USDJPY": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "FOREX_MAJORS_FAST"},
    # Broad crosses — D1/H4/H4/H1/M30, M5 disabled.
    "EURGBP": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    "AUDNZD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    "EURCHF": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    "AUDCHF": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    "EURAUD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    "GBPAUD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    "USDSGD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_BROAD"},
    # Liquid crosses — D1/H4/H1/M30/M15, M5 disabled.
    "EURJPY": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_LIQUID"},
    "AUDJPY": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "FOREX_CROSSES_LIQUID"},
    # GBP/JPY fast-cross symbol override: partial patch over the liquid-cross
    # group (regime/bias inherit D1/H4); H1 structure, M15 setup, conditional
    # M5 trigger.
    "GBPJPY": {"structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "GBPJPY_FAST_CROSS_CONDITIONAL"},
    # Exotics — liquid: D1/H4/H4/H1/M30; restricted: H1 confirmed trigger, no
    # M30/M15/M5 promotion.
    "USDZAR": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "baseline_speed": SpeedClass.SLOW, "profile": "FOREX_EXOTICS_LIQUID"},
    "USDMXN": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "baseline_speed": SpeedClass.SLOW, "profile": "FOREX_EXOTICS_LIQUID"},
    "USDBRL": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.H1, "m5_policy": M5Policy.DISABLED, "baseline_speed": SpeedClass.SLOW, "profile": "FOREX_EXOTICS_RESTRICTED"},
    "USDINR": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.H1, "m5_policy": M5Policy.DISABLED, "baseline_speed": SpeedClass.SLOW, "profile": "FOREX_EXOTICS_RESTRICTED"},
    # Metals — XAU conditional M5 (M15 setup); XAG stays M15; XPT/XPD H4 structure.
    "XAUUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "XAU_CONDITIONAL_M5"},
    "XAGUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "LIQUID_METALS"},
    "XPTUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "THIN_METALS_BASE_SOFTS"},
    "XPDUSD": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "profile": "THIN_METALS_BASE_SOFTS"},
    # Energy — WTI/Brent conditional M5; NATGAS M5 never.
    "WTI": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "ENERGY_OIL_CONDITIONAL"},
    "BRENT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "ENERGY_OIL_CONDITIONAL"},
    "NATGAS": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "baseline_speed": SpeedClass.FAST, "profile": "NAT_GAS_NO_M5"},
    # Indices — fast conditional; standard disabled.
    "NAS100": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "EQUITY_INDEX_FAST"},
    "US30": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "EQUITY_INDEX_FAST"},
    "GER40": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "EQUITY_INDEX_FAST"},
    "US500": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "EQUITY_INDEX_STANDARD"},
    "UK100": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "EQUITY_INDEX_STANDARD"},
    "JPN225": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "EQUITY_INDEX_STANDARD"},
    # Single stocks / bond & small-cap & EM ETFs — D1 bias.
    "AAPL": {"regime": Timeframe.D1, "bias": Timeframe.D1, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "profile": "US_STOCK_SINGLE"},
    "SPY": {"regime": Timeframe.D1, "bias": Timeframe.D1, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "profile": "US_STOCK_SINGLE"},
    "TLT": {"regime": Timeframe.D1, "bias": Timeframe.D1, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "BOND_TLT_SMALLCAP_EM_ETF"},
    "IWM": {"regime": Timeframe.D1, "bias": Timeframe.D1, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "BOND_TLT_SMALLCAP_EM_ETF"},
    "EEM": {"regime": Timeframe.D1, "bias": Timeframe.D1, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "BOND_TLT_SMALLCAP_EM_ETF"},
    # Crypto — fast majors conditional; alt majors disabled; thin H4 structure.
    "BTCUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "CRYPTO_MAJORS_FAST"},
    "ETHUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "CRYPTO_MAJORS_FAST"},
    "SOLUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M15, "trigger": Timeframe.M5, "m5_policy": M5Policy.CONDITIONAL, "baseline_speed": SpeedClass.FAST, "profile": "CRYPTO_MAJORS_FAST"},
    "BNBUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "CRYPTO_ALT_MAJORS"},
    "XRPUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "CRYPTO_ALT_MAJORS"},
    "ADAUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "CRYPTO_ALT_MAJORS"},
    "LINKUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H1, "setup": Timeframe.M30, "trigger": Timeframe.M15, "m5_policy": M5Policy.DISABLED, "profile": "CRYPTO_ALT_MAJORS"},
    "DOGEUSDT": {"regime": Timeframe.D1, "bias": Timeframe.H4, "structure": Timeframe.H4, "setup": Timeframe.H1, "trigger": Timeframe.M30, "m5_policy": M5Policy.DISABLED, "baseline_speed": SpeedClass.FAST, "profile": "CRYPTO_OTHER_THIN"},
}


def _apply_symbol_patch(
    base: _Template, patch: Mapping[str, Any]
) -> tuple[_Template, tuple[str, ...]]:
    """Merge a per-role symbol patch over the resolved group/asset template."""
    updates: dict[str, Any] = {}
    patched_roles: list[str] = []
    for role in ("regime", "bias", "structure", "setup", "trigger", "execution"):
        if role in patch:
            updates[role] = patch[role]
            patched_roles.append(role)
    if "m5_policy" in patch:
        policy = patch["m5_policy"]
        m5_role, confirm_m15 = _M5_POLICY_DERIVED[policy]
        updates["m5_policy"] = policy
        updates["m5_role"] = m5_role
        updates["m15_confirmation_required_for_m5"] = confirm_m15
        patched_roles.append("m5_policy")
    if "execution" not in patch and "trigger" in patch:
        # Advisory execution context follows a patched trigger.
        updates["execution"] = patch["trigger"]
    if "profile" in patch:
        updates["profile"] = patch["profile"]
    if "baseline_speed" in patch:
        updates["baseline_speed"] = patch["baseline_speed"]
    return replace(base, **updates), tuple(sorted(patched_roles))


_GROUP_OVERRIDES: dict[str, _Template] = {
    "forex_majors_standard": _FOREX_MAJORS_STANDARD,
    "forex_majors_fast": _FOREX_MAJORS_FAST,
    "forex_crosses_broad": _FOREX_CROSSES_BROAD,
    "forex_crosses_liquid": _FOREX_CROSSES_LIQUID,
    "forex_exotics_liquid": _FOREX_EXOTICS_LIQUID,
    "forex_exotics_restricted": _FOREX_EXOTICS_RESTRICTED,
    "energy_oil": _ENERGY_OIL,
    "nat_gas": _NAT_GAS,
    "liquid_metals": _LIQUID_METALS,
    "thin_metals_base_softs": _THIN_METALS_BASE_SOFTS,
    "equity_index_fast": _EQUITY_INDEX_FAST,
    "equity_index_standard": _EQUITY_INDEX_STANDARD,
    "us_stock_single": _US_STOCK_SINGLE,
    "bond_tlt_smallcap_em_etf": _BOND_TLT_SMALLCAP_EM_ETF,
    "crypto_majors_fast": _CRYPTO_MAJORS_FAST,
    "crypto_alt_majors": _CRYPTO_ALT_MAJORS,
    "crypto_other_thin": _CRYPTO_OTHER_THIN,
}


_ASSET_DEFAULTS: dict[str, _Template] = {
    "forex": _FOREX_MAJORS_STANDARD,
    "commodity": _THIN_METALS_BASE_SOFTS,
    "index": _EQUITY_INDEX_STANDARD,
    "stock": _US_STOCK_SINGLE,
    "etf": _BOND_TLT_SMALLCAP_EM_ETF,
    "etf_bond": _BOND_TLT_SMALLCAP_EM_ETF,
    "crypto": _CRYPTO_OTHER_THIN,
}


def canonical_symbol(symbol: str) -> str | None:
    """Return a known canonical identity, or ``None`` for an unknown alias."""
    return _ALIASES.get(_key(symbol))


def _adjacent(tf: Timeframe, faster: bool) -> Timeframe:
    index = TIMEFRAME_LADDER.index(tf)
    index = min(len(TIMEFRAME_LADDER) - 1, index + 1) if faster else max(0, index - 1)
    return TIMEFRAME_LADDER[index]


def _normalize_group_name(name: Any) -> str | None:
    """Map deprecated policy-group aliases to the v4 taxonomy.

    Old group names keep working as deprecated aliases (one logged warning per
    name per process); scoring-group names from ``engine_a_groups`` are mapped
    to their policy-group equivalent.  Unknown names pass through unchanged so
    they fail closed to asset defaults as before.
    """
    text = str(name or "").strip().lower()
    if not text:
        return None
    try:
        from engine_a_groups import normalize_timeframe_policy_group

        return normalize_timeframe_policy_group(text)
    except Exception:
        return text


def _normalize_policy_identity(engine_id: str, style: str) -> tuple[str, str]:
    engine = str(engine_id or "engine_a").strip().lower().replace("-", "_")
    normalized_style = str(style or "intraday").strip().lower().replace("-", "_")
    if normalized_style.startswith("engine_b_"):
        engine = "engine_b"
        normalized_style = normalized_style.removeprefix("engine_b_")
    elif normalized_style in {"engine_d", "d", "scalp_engine", "engine_d_scalp"}:
        engine = "engine_d"
        normalized_style = "scalp"
    if engine in {"a", "enginea"}:
        engine = "engine_a"
    elif engine in {"b", "engineb"}:
        engine = "engine_b"
    elif engine in {"d", "scalp", "engined"}:
        engine = "engine_d"
    if normalized_style == "auto":
        normalized_style = "intraday"
    return engine, normalized_style


def policy_key_for(canonical: str, engine_id: str, style: str) -> str:
    """Return the only authoritative cache/serialization identity."""
    return f"{canonical}:{engine_id}:{style}"


def validate_timeframe_role_order(
    regime: Timeframe,
    bias: Timeframe,
    structure: Timeframe,
    setup: Timeframe,
    trigger: Timeframe,
    execution: Timeframe,
    *,
    execution_mode: ExecutionMode | None = None,
) -> None:
    """Reject a policy whose roles reverse the slower-to-faster ladder.

    Regime through trigger are always strictly ordered.  The execution TF is
    only required to sit on the ladder when execution is timeframe-based; with
    ``ExecutionMode.LIVE_QUOTE`` the execution TF is advisory context and is
    exempt from ordering relative to the trigger.
    """
    roles: tuple[Timeframe, ...] = (regime, bias, structure, setup, trigger)
    if execution_mode != ExecutionMode.LIVE_QUOTE:
        roles = (*roles, execution)
    indexes = [TIMEFRAME_LADDER.index(tf) for tf in roles]
    if indexes != sorted(indexes):
        rendered = " -> ".join(tf.value for tf in roles)
        raise ValueError(f"timeframe role order reverses slower-to-faster ladder: {rendered}")


def _clamp_adaptive_roles(
    structure: Timeframe,
    setup: Timeframe,
    trigger: Timeframe,
) -> tuple[Timeframe, Timeframe]:
    """Clamp the adaptive roles (setup/trigger) to the permitted ordering.

    Regime/bias/structure are never adapted, and execution is no longer an
    adapted role (execution is live-quote based in v4).
    """
    previous = TIMEFRAME_LADDER.index(structure)
    clamped: list[Timeframe] = []
    for tf in (setup, trigger):
        index = max(previous, TIMEFRAME_LADDER.index(tf))
        clamped.append(TIMEFRAME_LADDER[index])
        previous = index
    return clamped[0], clamped[1]


def _is_thin_swing_base(base: _Template) -> bool:
    """Return whether the resolved instrument template is a thin/slow one.

    The intraday matrix already encodes thin-ness: broad crosses, exotics, thin
    metals/base/softs and thin crypto carry an H4 structure rung, and the
    fail-closed templates carry a SLOW baseline.  Engine B swing reuses that
    classification instead of duplicating a second group list that could drift
    away from ``_GROUP_OVERRIDES``/``_SYMBOL_OVERRIDES``.
    """
    return base.structure is Timeframe.H4 or base.baseline_speed is SpeedClass.SLOW


def _engine_template(base: _Template, engine_id: str, style: str) -> _Template:
    if engine_id == "engine_d":
        # Engine D native scalp contract: H1 context/regime, M15 confirmed
        # structure/bias, M5 setup, M1 trigger. Execution is live-quote based;
        # the emitted M1 executionTf is advisory context only.
        return _Template(
            profile="ENGINE_D_NATIVE",
            regime=Timeframe.H1,
            bias=Timeframe.M15,
            structure=Timeframe.M15,
            setup=Timeframe.M5,
            trigger=Timeframe.M1,
            execution=Timeframe.M1,
            m5_role=M5Role.REFINEMENT,
            m5_policy=M5Policy.CONDITIONAL,
            execution_mode=ExecutionMode.LIVE_QUOTE,
            baseline_speed=base.baseline_speed,
            m15_confirmation_required_for_m5=True,
        )
    if engine_id == "engine_b" and style == "swing":
        # Swing is a slow overlay, but it is no longer uniform across groups:
        # liquid instruments keep the H4 structural horizon with an H1 trigger,
        # thin/slow instruments step the whole chain one rung slower (D1
        # structure, H4 trigger) so the group matrix is not inert on the style
        # that Auto selects for commodities, indices, stocks and ETFs.
        thin = _is_thin_swing_base(base)
        return _Template(
            profile=f"ENGINE_B_SWING_{base.profile}",
            regime=Timeframe.D1,
            bias=Timeframe.D1,
            structure=Timeframe.D1 if thin else Timeframe.H4,
            setup=Timeframe.H4,
            trigger=Timeframe.H4 if thin else Timeframe.H1,
            execution=Timeframe.H4 if thin else Timeframe.H1,
            m5_role=M5Role.DISABLED,
            m5_policy=M5Policy.DISABLED,
            execution_mode=ExecutionMode.LIVE_QUOTE,
            baseline_speed=base.baseline_speed,
        )
    if engine_id == "engine_b" and style == "intraday":
        return replace(
            base,
            profile=f"ENGINE_B_INTRADAY_{base.profile}",
        )
    if engine_id == "engine_b" and style == "scalp":
        # Scalp chain H1/M15/M15/M5/M1: H1 is context only (never a mandatory
        # H4 gate), M15 confirms structure/bias, M5 setup, M1 trigger with
        # conditional M5 policy.
        return _Template(
            profile=f"ENGINE_B_SCALP_{base.profile}",
            regime=Timeframe.H1,
            bias=Timeframe.M15,
            structure=Timeframe.M15,
            setup=Timeframe.M5,
            trigger=Timeframe.M1,
            execution=Timeframe.M1,
            m5_role=M5Role.REFINEMENT,
            m5_policy=M5Policy.CONDITIONAL,
            execution_mode=ExecutionMode.LIVE_QUOTE,
            baseline_speed=base.baseline_speed,
            m15_confirmation_required_for_m5=True,
        )
    # Engine A intraday keeps the instrument profile unchanged.  Swing is a
    # full slow overlay: D1 regime/bias, H4 structure, H1 setup, H1 confirmed
    # trigger, M5 disabled.
    if engine_id == "engine_a" and style == "swing":
        return replace(
            base,
            profile=f"ENGINE_A_SWING_{base.profile}",
            regime=Timeframe.D1,
            bias=Timeframe.D1,
            structure=Timeframe.H4,
            setup=Timeframe.H1,
            trigger=Timeframe.H1,
            execution=Timeframe.H1,
            m5_role=M5Role.DISABLED,
            m5_policy=M5Policy.DISABLED,
            m15_confirmation_required_for_m5=False,
        )
    return base


def resolve_timeframe_policy(
    symbol: str,
    asset_type: str,
    score_group: str | None,
    style: str,
    speed_state: SpeedState | None = None,
    *,
    engine_id: str = "engine_a",
    authoritative_group: str | None = None,
) -> TimeframePolicy:
    """Resolve the deterministic policy using the documented precedence.

    Precedence: CONFIG_CONFLICT guard → score-group/asset-default template →
    per-role symbol-override patch → engine/style overlay → restricted speed
    adaptation (setup/trigger only).  Execution is live-quote based; the
    emitted executionTf is advisory execution context.
    """
    requested = str(symbol or "").strip()
    canonical = canonical_symbol(requested)
    asset = str(asset_type or "").strip().lower()
    group = _normalize_group_name(score_group)
    authoritative = _normalize_group_name(authoritative_group)
    resolved_engine, resolved_style = _normalize_policy_identity(engine_id, style)
    messages: list[str] = []
    symbol_override_applied = False
    symbol_override_patched_roles: tuple[str, ...] = ()

    config_conflict = bool(authoritative and group != authoritative)
    if config_conflict:
        base = replace(
            _FAIL_CLOSED, profile="CONFIG_CONFLICT", baseline_speed=SpeedClass.SLOW
        )
        source = PolicySource.CONFIG_CONFLICT
        messages.append(
            f"canonical group conflict: requested={group or 'none'} authoritative={authoritative}"
        )
    else:
        if group and group in _GROUP_OVERRIDES:
            base = _GROUP_OVERRIDES[group]
            source = PolicySource.SCORE_GROUP_OVERRIDE
        elif asset in _ASSET_DEFAULTS:
            base = _ASSET_DEFAULTS[asset]
            source = PolicySource.ASSET_STYLE_DEFAULT
            if canonical is None:
                messages.append(
                    "symbol_not_in_alias_registry; using explicit asset/style default"
                )
        else:
            base = replace(
                _FAIL_CLOSED, profile="SAFE_FALLBACK", baseline_speed=SpeedClass.SLOW
            )
            source = PolicySource.SAFE_FALLBACK
            messages.append("unknown symbol/asset; fail-closed higher-timeframe fallback")
        if canonical and canonical in _SYMBOL_OVERRIDES:
            base, symbol_override_patched_roles = _apply_symbol_patch(
                base, _SYMBOL_OVERRIDES[canonical]
            )
            symbol_override_applied = True
            source = PolicySource.SYMBOL_OVERRIDE

    selected = _engine_template(base, resolved_engine, resolved_style)
    reported_speed = (
        speed_state.live_speed_class
        if speed_state and speed_state.live_speed_class is not None
        else SpeedClass.UNAVAILABLE
    )
    adaptation_ready = bool(
        speed_state
        and speed_state.history_ready
        and reported_speed != SpeedClass.UNAVAILABLE
    )
    effective_speed = reported_speed if adaptation_ready else selected.baseline_speed
    setup = selected.setup
    trigger = selected.trigger
    # Execution is never adapted: no speed/liquidity input may rewrite the
    # advisory execution TF, and speed never promotes M5 execution (the v3
    # allow_dynamic_m5_execution promotion is removed in v4).
    execution = selected.execution
    m5_role = selected.m5_role
    m5_policy = selected.m5_policy

    if not adaptation_ready:
        messages.append("INSUFFICIENT_HISTORY: baseline policy retained")
    elif effective_speed == SpeedClass.SLOW:
        # Speed adaptation may modify setup and trigger ONLY — never
        # regime/bias/structure, never execution.
        setup = _adjacent(setup, faster=False)
        trigger = _adjacent(trigger, faster=False)

    # THIN/UNAVAILABLE liquidity no longer rewrites any role TF.  It is
    # recorded as an M5-eligibility input consumed by the eligibility layer.
    m5_liquidity_blocked = bool(
        speed_state
        and speed_state.liquidity_class
        in {LiquidityClass.THIN, LiquidityClass.UNAVAILABLE}
    )
    if m5_liquidity_blocked and (
        m5_policy == M5Policy.CONDITIONAL
        or trigger == Timeframe.M5
        or setup == Timeframe.M5
    ):
        messages.append(
            "THIN or UNAVAILABLE liquidity recorded as M5-eligibility block "
            "(m5_liquidity_blocked)"
        )

    setup, trigger = _clamp_adaptive_roles(
        selected.structure,
        setup,
        trigger,
    )
    if (
        Timeframe.M1
        in (selected.regime, selected.bias, selected.structure, setup, trigger, execution)
        and resolved_engine != "engine_d"
        and resolved_style != "scalp"
    ):
        raise PolicyConfigurationError(
            "M1 roles are only permitted in scalp/engine-d-native policy templates"
        )
    validate_timeframe_role_order(
        selected.regime,
        selected.bias,
        selected.structure,
        setup,
        trigger,
        execution,
        execution_mode=selected.execution_mode,
    )

    required = tuple(dict.fromkeys((
        selected.regime,
        selected.bias,
        selected.structure,
        setup,
        trigger,
    )))
    forming_tf = (
        execution
        if (
            (execution == Timeframe.M5 and m5_policy == M5Policy.CONDITIONAL)
            or execution == Timeframe.M1
        )
        else None
    )
    thresholds = speed_state.thresholds if speed_state else SpeedThresholds()
    diagnostics = PolicyDiagnostics(
        canonical_symbol=canonical or "UNKNOWN",
        requested_symbol=requested,
        asset_type=asset or "unknown",
        score_group=group,
        style=resolved_style,
        messages=tuple(messages),
        missing_speed_inputs=speed_state.missing_inputs if speed_state else ("speed_state",),
        speed_thresholds=asdict(thresholds),
        m15_confirmation_required_for_m5=selected.m15_confirmation_required_for_m5,
        safe_fallback=source == PolicySource.SAFE_FALLBACK,
        config_conflict=config_conflict,
        adaptation_applied=adaptation_ready and (
            setup != selected.setup
            or trigger != selected.trigger
        ),
        adaptation_reason=(
            "SPEED_AND_LIQUIDITY"
            if adaptation_ready
            else "INSUFFICIENT_HISTORY"
        ),
        liquidity_class=(
            speed_state.liquidity_class
            if speed_state
            else LiquidityClass.UNAVAILABLE
        ),
        symbol_override_applied=symbol_override_applied,
        symbol_override_patched_roles=symbol_override_patched_roles,
        m5_liquidity_blocked=m5_liquidity_blocked,
    )
    canonical_identity = canonical or _key(requested) or "UNKNOWN"
    policy_key = policy_key_for(canonical_identity, resolved_engine, resolved_style)
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
        live_speed_class=reported_speed,
        policy_source=source,
        diagnostics=diagnostics,
        canonical_symbol=canonical_identity,
        engine_id=resolved_engine,
        style=resolved_style,
        policy_key=policy_key,
        execution_prerequisite_tf=(
            Timeframe.M15
            if selected.m15_confirmation_required_for_m5
            else None
        ),
        execution_mode=selected.execution_mode,
        m5_policy=m5_policy,
    )


def speed_class_for_percentile(
    percentile: float,
    thresholds: SpeedThresholds | None = None,
) -> SpeedClass:
    cfg = thresholds or SpeedThresholds()
    value = max(0.0, min(100.0, float(percentile)))
    if value <= cfg.normal_to_slow_max:
        return SpeedClass.SLOW
    if value < cfg.normal_to_fast_min:
        return SpeedClass.NORMAL
    if value < cfg.fast_to_extreme_min:
        return SpeedClass.FAST
    return SpeedClass.EXTREME


def speed_transition_candidate(
    previous: SpeedClass | None,
    percentile: float,
    thresholds: SpeedThresholds,
) -> SpeedClass:
    value = max(0.0, min(100.0, float(percentile)))
    if previous in {None, SpeedClass.UNAVAILABLE}:
        return speed_class_for_percentile(value, thresholds)
    if previous == SpeedClass.SLOW:
        return SpeedClass.NORMAL if value >= thresholds.slow_to_normal_min else previous
    if previous == SpeedClass.NORMAL:
        if value <= thresholds.normal_to_slow_max:
            return SpeedClass.SLOW
        if value >= thresholds.normal_to_fast_min:
            return SpeedClass.FAST
        return previous
    if previous == SpeedClass.FAST:
        if value <= thresholds.fast_to_normal_max:
            return SpeedClass.NORMAL
        if value >= thresholds.fast_to_extreme_min:
            return SpeedClass.EXTREME
        return previous
    if previous == SpeedClass.EXTREME:
        return SpeedClass.FAST if value <= thresholds.extreme_to_fast_max else previous
    return SpeedClass.UNAVAILABLE


def apply_speed_hysteresis(
    previous: SpeedClass | None,
    candidate: SpeedClass,
    candidate_streak: int,
    thresholds: SpeedThresholds | None = None,
) -> tuple[SpeedClass, int]:
    """Persist a class only after consecutive newly closed H1 observations."""
    cfg = thresholds or SpeedThresholds()
    if previous is None or previous == SpeedClass.UNAVAILABLE:
        return candidate, 0
    speed_ladder = (
        SpeedClass.SLOW,
        SpeedClass.NORMAL,
        SpeedClass.FAST,
        SpeedClass.EXTREME,
    )
    previous_index = speed_ladder.index(previous)
    candidate_index = speed_ladder.index(candidate)
    if abs(candidate_index - previous_index) > 1:
        candidate = speed_ladder[
            previous_index + (1 if candidate_index > previous_index else -1)
        ]
    if candidate == previous:
        return previous, 0
    streak = max(0, int(candidate_streak)) + 1
    if streak >= max(1, int(cfg.hysteresis_closes)):
        return candidate, 0
    return previous, streak


def classify_liquidity(
    *,
    quote_age_sec: float | None,
    spread_trigger_atr: float | None,
    relative_volume: float | None,
    provider_market_state: str | None,
    baseline: LiquidityClass = LiquidityClass.NORMAL,
    relative_volume_reliable: bool = True,
    thresholds: LiquidityThresholds | None = None,
) -> LiquidityClass:
    cfg = thresholds or LiquidityThresholds()
    state = str(provider_market_state or "").strip().lower()
    if not state:
        return LiquidityClass.UNAVAILABLE
    if state in {"closed", "halted", "suspended", "unavailable"}:
        return LiquidityClass.THIN
    if quote_age_sec is None or spread_trigger_atr is None:
        return LiquidityClass.UNAVAILABLE
    if quote_age_sec > cfg.max_quote_age_sec:
        return LiquidityClass.THIN
    if relative_volume_reliable and relative_volume is None:
        return LiquidityClass.UNAVAILABLE
    if (
        spread_trigger_atr > cfg.normal_max_spread_trigger_atr
        or (
            relative_volume_reliable
            and relative_volume is not None
            and relative_volume <= cfg.thin_max_relative_volume
        )
    ):
        observed = LiquidityClass.THIN
    elif (
        spread_trigger_atr <= cfg.deep_max_spread_trigger_atr
        and (
            not relative_volume_reliable
            or (
                relative_volume is not None
                and relative_volume >= cfg.deep_min_relative_volume
            )
        )
    ):
        observed = LiquidityClass.DEEP
    else:
        observed = LiquidityClass.NORMAL
    restriction_order = {
        LiquidityClass.DEEP: 0,
        LiquidityClass.NORMAL: 1,
        LiquidityClass.THIN: 2,
        LiquidityClass.UNAVAILABLE: 3,
    }
    return max((observed, baseline), key=restriction_order.__getitem__)


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
    provider_market_state: str | None = None,
    baseline_speed_class: SpeedClass = SpeedClass.NORMAL,
    baseline_liquidity_class: LiquidityClass = LiquidityClass.NORMAL,
    relative_volume_reliable: bool = True,
    previous: SpeedState | None = None,
    last_closed_h1_open_time: int | None = None,
    decision_utc: str | None = None,
    thresholds: SpeedThresholds | None = None,
    liquidity_thresholds: LiquidityThresholds | None = None,
) -> SpeedState:
    """Calculate point-in-time speed from confirmed bars; never reads wall time."""
    cfg = thresholds or (previous.thresholds if previous else SpeedThresholds())
    liquidity_cfg = liquidity_thresholds or (
        previous.liquidity_thresholds if previous else LiquidityThresholds()
    )
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
    history_ready = bool(
        len(h1_confirmed) >= cfg.required_h1_bars
        and len(m15_confirmed) >= cfg.required_m15_bars
    )
    previous_speed = (
        previous.live_speed_class
        if previous and previous.live_speed_class not in {None, SpeedClass.UNAVAILABLE}
        else baseline_speed_class
    )
    candidate = (
        speed_transition_candidate(previous_speed, speed_percentile, cfg)
        if history_ready and speed_percentile is not None
        else None
    )

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
            "price_velocity_percentile",
        )
    )
    if not history_ready or candidate is None or required_speed_missing:
        persistent = SpeedClass.UNAVAILABLE
        streak = 0
    elif not new_h1_close and previous and previous.live_speed_class is not None:
        persistent = previous.live_speed_class
        streak = previous.candidate_streak
    else:
        prior_age = (
            previous.candidate_age_h1_bars
            if previous and previous.candidate_speed_class == candidate
            else 0
        )
        persistent, streak = apply_speed_hysteresis(
            previous_speed,
            candidate,
            prior_age,
            cfg,
        )

    gap = str(gap_status or "").strip().lower()
    session = str(current_session or "").strip().lower()
    hard_liquidity_failure = bool(
        quote_age_sec is None
        or float(quote_age_sec) > liquidity_cfg.max_quote_age_sec
        or spread_ratio is None
        or spread_ratio > liquidity_cfg.normal_max_spread_trigger_atr
        or gap not in {"", "none", "normal", "open"}
        or session in {"closed", "avoid", "off_hours"}
        or not str(provider_market_state or "").strip()
        or str(provider_market_state or "").strip().lower()
        in {"closed", "halted", "suspended", "unavailable"}
        or scheduled_event is True
    )
    if hard_liquidity_failure:
        liquidity_class = (
            LiquidityClass.UNAVAILABLE
            if quote_age_sec is None or spread_ratio is None
            else LiquidityClass.THIN
        )
    elif not history_ready:
        liquidity_class = baseline_liquidity_class
    else:
        liquidity_class = classify_liquidity(
            quote_age_sec=float(quote_age_sec) if quote_age_sec is not None else None,
            spread_trigger_atr=spread_ratio,
            relative_volume=resolved_relative_volume,
            provider_market_state=provider_market_state,
            baseline=baseline_liquidity_class,
            relative_volume_reliable=relative_volume_reliable,
            thresholds=liquidity_cfg,
        )
    thin = liquidity_class in {LiquidityClass.THIN, LiquidityClass.UNAVAILABLE}
    m5_ok = bool(
        history_ready
        and not thin
        and spread_ratio is not None
        and quote_age_sec is not None
    )
    transitioned = bool(
        previous
        and previous.live_speed_class not in {None, SpeedClass.UNAVAILABLE}
        and persistent != previous.live_speed_class
    )
    transition_time = previous.last_speed_transition_utc if previous else None
    if transitioned:
        transition_time = decision_utc
        if transition_time is None and last_closed_h1_open_time is not None:
            transition_time = datetime.fromtimestamp(
                last_closed_h1_open_time, tz=timezone.utc
            ).isoformat()
    transition_pending = bool(candidate is not None and persistent != candidate)
    return SpeedState(
        live_speed_class=persistent,
        candidate_speed_class=candidate,
        candidate_age_h1_bars=streak,
        transition_pending=transition_pending,
        last_speed_transition_utc=transition_time,
        liquidity_class=liquidity_class,
        baseline_liquidity_class=baseline_liquidity_class,
        history_ready=history_ready,
        adaptation_applied=bool(
            history_ready and persistent != baseline_speed_class
        ),
        adaptation_reason=(
            "SPEED_CLASS_CHANGED"
            if history_ready and persistent != baseline_speed_class
            else "BASELINE_MATCH"
            if history_ready
            else "INSUFFICIENT_HISTORY"
        ),
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
        liquidity_thresholds=liquidity_cfg,
    )


def _policy_runtime_settings(
    policy_mode: str | PolicyMode | None,
    config: Mapping[str, Any] | None,
) -> tuple[PolicyMode, bool, bool]:
    cfg = config
    supplied_config = cfg is not None
    if cfg is None:
        try:
            from config import CONFIG

            cfg = CONFIG
        except Exception:
            cfg = {}
    raw_mode = policy_mode.value if isinstance(policy_mode, PolicyMode) else policy_mode
    if raw_mode is None:
        raw_mode = cfg.get(
            "TF_POLICY_MODE",
            PolicyMode.OFF.value if supplied_config else PolicyMode.ENFORCED_DEMO.value,
        )
    try:
        mode = PolicyMode(str(raw_mode).strip().lower())
    except ValueError:
        mode = _LEGACY_POLICY_MODE_ALIASES.get(
            str(raw_mode).strip().lower(), PolicyMode.OFF
        )
    demo_autotrade_enabled = bool(
        cfg.get(
            "TF_POLICY_DEMO_AUTOTRADE_ENABLED",
            # Compatibility only; new configuration must use the explicit demo
            # key so it cannot also authorize real accounts.
            cfg.get("TF_POLICY_AUTOTRADE_ENABLED", False),
        )
    )
    real_autotrade_enabled = bool(
        cfg.get("TF_POLICY_REAL_AUTOTRADE_ENABLED", False)
    )
    return mode, demo_autotrade_enabled, real_autotrade_enabled


def policy_mode_is_authoritative(mode: str | PolicyMode | None, config: Mapping[str, Any] | None = None) -> bool:
    """Return whether policy score/direction must control the live result."""
    resolved, _, _ = _policy_runtime_settings(mode, config)
    return resolved in {PolicyMode.ENFORCED_DEMO, PolicyMode.ENFORCED_LIVE}


def apply_authoritative_policy_result(
    signal: dict[str, Any],
    *,
    policy_score: Any | None = None,
    policy_direction: Any | None = None,
    policy_mode: str | PolicyMode | None = None,
    config: Mapping[str, Any] | None = None,
) -> None:
    """Expose both calculations and promote policy only in an enforced mode.

    This is intentionally the single mutation point for score authority.  It
    keeps rollback diagnostics stable while preventing serializers, rankers and
    auto-traders that consume ``confluenceScore``/``direction`` from silently
    retaining the legacy result after promotion.
    """
    mode, _, _ = _policy_runtime_settings(policy_mode, config)
    legacy_score = signal.get("legacyScore", signal.get("confluenceScore", signal.get("score")))
    legacy_direction = signal.get("legacyDirection", signal.get("direction"))
    resolved_policy_score = signal.get("policyScore", policy_score)
    resolved_policy_direction = signal.get("policyDirection", policy_direction)
    if resolved_policy_score is None:
        resolved_policy_score = legacy_score
    if resolved_policy_direction is None:
        resolved_policy_direction = legacy_direction

    signal["legacyScore"] = legacy_score
    signal["legacyDirection"] = legacy_direction
    signal["policyScore"] = resolved_policy_score
    signal["policyDirection"] = resolved_policy_direction
    try:
        signal["scoreDelta"] = round(float(resolved_policy_score) - float(legacy_score), 6)
    except (TypeError, ValueError):
        signal["scoreDelta"] = None
    signal["directionChanged"] = resolved_policy_direction != legacy_direction

    if mode in {PolicyMode.ENFORCED_DEMO, PolicyMode.ENFORCED_LIVE}:
        signal["authoritativeScore"] = resolved_policy_score
        signal["authoritativeDirection"] = resolved_policy_direction
        signal["authoritativeScoreSource"] = "POLICY"
        signal["confluenceScore"] = resolved_policy_score
        signal["score"] = resolved_policy_score
        signal["direction"] = resolved_policy_direction
        max_score = signal.get("maxScore")
        try:
            if float(max_score) > 0:
                signal["scoreNorm"] = float(resolved_policy_score) / float(max_score)
        except (TypeError, ValueError):
            pass
    else:
        signal["authoritativeScore"] = legacy_score
        signal["authoritativeDirection"] = legacy_direction
        signal["authoritativeScoreSource"] = "LEGACY"


def classify_broker_account(account: Mapping[str, Any] | None) -> tuple[str, str | None]:
    """Classify account environment from broker metadata, failing closed.

    Explicit broker environment metadata always wins.  Name/server text is used
    only as a final compatibility hint when no stronger signal exists.
    """
    details = account if isinstance(account, Mapping) else {}
    raw_environment = str(
        details.get("accountEnvironment") or details.get("environment") or ""
    ).strip().lower()
    if raw_environment in {"demo", "paper", "testnet", "sandbox"}:
        return "demo", None
    if raw_environment in {"live", "real", "production"}:
        return "real", None
    if details.get("demo") is True or details.get("testnet") is True:
        return "demo", None
    if details.get("demo") is False or details.get("testnet") is False:
        # A broker-provided false value is stronger than account-name heuristics.
        return "real", None
    hint = " ".join(str(details.get(key) or "") for key in ("server", "name", "accountName"))
    if "demo" in hint.lower() or "test" in hint.lower():
        return "demo", "ACCOUNT_NAME_FALLBACK"
    return "unknown", "BROKER_ENVIRONMENT_UNAVAILABLE"


def evaluate_execution_timeframe(
    policy: TimeframePolicy,
    signal: Mapping[str, Any],
    market_states: Mapping[str, Mapping[str, Any]] | None,
    *,
    trigger_confirmed: bool,
    use_mt5_bid_candle_close: bool = False,
) -> dict[str, Any]:
    """Evaluate entry timing on the policy execution TF without changing the thesis.

    The trigger remains the final analytical confirmation.  This helper compares
    the current forming/live move on the policy execution timeframe with the
    signal direction and only decides whether the entry may be taken now.  It
    never changes score, direction, structure, or SL/TP, and it never substitutes
    a slower timeframe when execution data is unavailable.
    """
    states = market_states or {}
    execution_tf = policy.execution_tf.value
    trigger_tf = policy.trigger_tf.value
    raw_state = states.get(execution_tf) or {}
    state = raw_state if isinstance(raw_state, Mapping) else {}
    confirmed = list(state.get("confirmed") or [])
    forming = state.get("forming") if isinstance(state.get("forming"), Mapping) else None

    if not (confirmed or forming):
        return {
            "executionTfActual": None,
            "executionTfConsumed": False,
            "executionTimingStatus": "UNAVAILABLE",
            "executionTimingAligned": None,
            "executionTimingDirection": None,
            "executionTimingSource": None,
            "executionCandleTime": None,
        }
    if state.get("stale") is True:
        return {
            "executionTfActual": execution_tf,
            "executionTfConsumed": False,
            "executionTimingStatus": "STALE",
            "executionTimingAligned": None,
            "executionTimingDirection": None,
            "executionTimingSource": None,
            "executionCandleTime": None,
        }

    shared_trigger = execution_tf == trigger_tf
    if shared_trigger and not trigger_confirmed:
        candle = forming or (confirmed[-1] if confirmed else None)
        candle_time = (
            candle.get("time")
            or candle.get("timestamp")
            or candle.get("datetime")
            or candle.get("date")
        ) if isinstance(candle, Mapping) else None
        return {
            "executionTfActual": execution_tf,
            "executionTfConsumed": True,
            "executionTimingStatus": "AWAITING_TRIGGER",
            "executionTimingAligned": None,
            "executionTimingDirection": None,
            "executionTimingSource": "trigger_timeframe",
            "executionCandleTime": candle_time,
        }

    live_price_raw = signal.get("current_price") or signal.get("price")
    try:
        live_price = float(live_price_raw) if live_price_raw is not None else None
        if live_price is not None and (not math.isfinite(live_price) or live_price <= 0):
            live_price = None
    except (TypeError, ValueError):
        live_price = None
    if use_mt5_bid_candle_close:
        # MT5 OHLC is bid-based, so a bid/ask midpoint is not a compatible
        # substitute when the provider did not supply a forming bid candle.
        live_price = None

    reference_candle = forming or (confirmed[-1] if confirmed else None)
    if not isinstance(reference_candle, Mapping):
        return {
            "executionTfActual": execution_tf,
            "executionTfConsumed": False,
            "executionTimingStatus": "UNAVAILABLE",
            "executionTimingAligned": None,
            "executionTimingDirection": None,
            "executionTimingSource": None,
            "executionCandleTime": None,
        }

    if forming is not None:
        reference_value = reference_candle.get("open")
        if use_mt5_bid_candle_close:
            # MT5 OHLC is bid-based. Substituting a bid/ask midpoint here adds
            # half the spread to the forming candle and can flip lower-TF
            # direction on wide-spread brokers.
            source = "forming_candle_mt5_bid_close"
            current_value = reference_candle.get("close")
        else:
            source = "forming_candle_live_price" if live_price is not None else "forming_candle"
            current_value = live_price if live_price is not None else reference_candle.get("close")
    elif live_price is not None:
        source = "live_price_vs_confirmed_close"
        reference_value = reference_candle.get("close")
        current_value = live_price
    elif shared_trigger:
        candle_time = (
            reference_candle.get("time")
            or reference_candle.get("timestamp")
            or reference_candle.get("datetime")
            or reference_candle.get("date")
        )
        return {
            "executionTfActual": execution_tf,
            "executionTfConsumed": True,
            "executionTimingStatus": "SHARED_TRIGGER_CONFIRMED",
            "executionTimingAligned": True,
            "executionTimingDirection": str(signal.get("direction") or "").upper() or None,
            "executionTimingSource": "confirmed_trigger",
            "executionCandleTime": candle_time,
        }
    else:
        return {
            "executionTfActual": execution_tf,
            "executionTfConsumed": False,
            "executionTimingStatus": "UNAVAILABLE",
            "executionTimingAligned": None,
            "executionTimingDirection": None,
            "executionTimingSource": None,
            "executionCandleTime": None,
        }

    candle_time = (
        reference_candle.get("time")
        or reference_candle.get("timestamp")
        or reference_candle.get("datetime")
        or reference_candle.get("date")
    )
    try:
        candle_open = float(reference_value)
        candle_close = float(current_value)
    except (TypeError, ValueError):
        candle_open = candle_close = float("nan")

    direction = str(signal.get("direction") or "").upper()
    if (
        direction not in {"LONG", "SHORT"}
        or not math.isfinite(candle_open)
        or not math.isfinite(candle_close)
        or candle_open <= 0
        or candle_close <= 0
    ):
        return {
            "executionTfActual": execution_tf,
            "executionTfConsumed": False,
            "executionTimingStatus": "UNAVAILABLE",
            "executionTimingAligned": None,
            "executionTimingDirection": None,
            "executionTimingSource": source,
            "executionCandleTime": candle_time,
        }

    if candle_close > candle_open:
        execution_direction = "LONG"
    elif candle_close < candle_open:
        execution_direction = "SHORT"
    else:
        execution_direction = "NEUTRAL"
    aligned = execution_direction == direction if execution_direction != "NEUTRAL" else None
    return {
        "executionTfActual": execution_tf,
        "executionTfConsumed": True,
        "executionTimingStatus": (
            "ALIGNED" if aligned is True else "OPPOSED" if aligned is False else "NEUTRAL"
        ),
        "executionTimingAligned": aligned,
        "executionTimingDirection": execution_direction,
        "executionTimingSource": source,
        "executionCandleTime": candle_time,
    }


def timeframe_policy_execution_block_reason(
    signal: Mapping[str, Any],
    config: Mapping[str, Any],
    account: Mapping[str, Any] | None = None,
) -> str | None:
    """Fail closed only for policy config errors or explicitly enforced rollout."""
    if (
        signal.get("policySource") == PolicySource.CONFIG_CONFLICT.value
        or signal.get("entryReadiness") == "CONFIG_ERROR"
    ):
        return "TF_POLICY_CONFIG_CONFLICT"
    mode, demo_autotrade_enabled, real_autotrade_enabled = _policy_runtime_settings(None, config)
    if mode == PolicyMode.ENFORCED_DEMO:
        if not demo_autotrade_enabled:
            return "TF_POLICY_DEMO_AUTOTRADE_DISABLED"
        if "entryReadiness" in signal:
            readiness = str(signal.get("entryReadiness") or "UNAVAILABLE").upper()
            if readiness != "READY":
                return f"TF_POLICY_ENTRY_READINESS_{readiness}"
        if account is None:
            # _can_execute runs before broker retrieval; account scope is checked
            # again immediately before execution with broker metadata.
            return None
        environment, detail = classify_broker_account(account)
        allowed = environment == "demo"
        reason = (
            None if allowed else
            ("TF_POLICY_REAL_ACCOUNT_LOCKED" if environment == "real" else
             f"TF_POLICY_ACCOUNT_ENVIRONMENT_UNVERIFIED:{detail or 'UNKNOWN'}")
        )
        if isinstance(signal, dict):
            signal["timeframePolicyExecution"] = {
                "accountId": account.get("login") or account.get("accountId") or account.get("id"),
                "broker": account.get("exchange") or account.get("symbol") or account.get("broker"),
                "accountEnvironment": environment,
                "policyMode": mode.value,
                "executionAllowed": allowed,
                "executionRestrictionReason": reason,
            }
        return reason
    if mode == PolicyMode.ENFORCED_LIVE:
        if "entryReadiness" in signal:
            readiness = str(signal.get("entryReadiness") or "UNAVAILABLE").upper()
            if readiness != "READY":
                return f"TF_POLICY_ENTRY_READINESS_{readiness}"
        if account is None:
            return None
        environment, detail = classify_broker_account(account)
        allowed = environment == "demo" or (environment == "real" and real_autotrade_enabled)
        reason = (
            None if allowed else
            ("TF_POLICY_REAL_AUTOTRADE_DISABLED" if environment == "real" else
             f"TF_POLICY_ACCOUNT_ENVIRONMENT_UNVERIFIED:{detail or 'UNKNOWN'}")
        )
        if isinstance(signal, dict):
            signal["timeframePolicyExecution"] = {
                "accountId": account.get("login") or account.get("accountId") or account.get("id"),
                "broker": account.get("exchange") or account.get("symbol") or account.get("broker"),
                "accountEnvironment": environment,
                "policyMode": mode.value,
                "executionAllowed": allowed,
                "executionRestrictionReason": reason,
            }
        return reason
    return None


def _signal_id(
    signal: Mapping[str, Any],
    policy_key: str,
) -> str:
    existing = signal.get("signalId") or signal.get("signal_id") or signal.get("id")
    if existing not in {None, ""}:
        return str(existing)
    material = {
        "policyKey": policy_key,
        "timestamp": signal.get("timestamp") or signal.get("ts") or signal.get("time"),
        "direction": signal.get("direction"),
        "entry": signal.get("entry") or signal.get("price"),
    }
    stable = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(stable.encode("utf-8")).hexdigest()[:24]


def _configured_session_candidate(
    config: Mapping[str, Any] | None,
    section_key: str,
    pair: Mapping[str, Any],
    canonical: str,
) -> Mapping[str, Any] | None:
    if not isinstance(config, Mapping):
        return None
    section = config.get(section_key)
    if not isinstance(section, Mapping):
        return None
    provider = str(pair.get("source") or pair.get("provider") or "").strip().lower()
    provider_section = section.get(provider)
    if not isinstance(provider_section, Mapping):
        return None
    candidate = provider_section.get(canonical) or provider_section.get("default")
    return candidate if isinstance(candidate, Mapping) else None


# ---------------------------------------------------------------------------
# Conditional M5 eligibility + trigger lifecycle wiring (diagnostics only).
#
# Both layers are additive and fail-safe: they never mutate score, direction,
# or conviction, never raise into payload stamping, and stamp nothing when
# their required inputs are absent (backward compatible).
# ---------------------------------------------------------------------------

#: Module-level trigger registry keyed by signal id. The execution layer
#: reads state through :func:`get_trigger_record`.
_TRIGGER_TRACKER = TriggerTracker()

#: TTL defaults, overridable via CONFIG["TRIGGER_LIFECYCLE"]: whichever of
#: 8 trigger-timeframe bars or 4 hours elapses first.
_TRIGGER_LIFECYCLE_DEFAULT_MAX_BARS = 8
_TRIGGER_LIFECYCLE_DEFAULT_TTL_SECONDS = 4 * 3600.0

_TF_SECONDS: dict[str, float] = {
    "D1": 86400.0,
    "H4": 14400.0,
    "H1": 3600.0,
    "M30": 1800.0,
    "M15": 900.0,
    "M5": 300.0,
    "M1": 60.0,
}


def get_trigger_tracker() -> TriggerTracker:
    """Return the module-level trigger registry."""
    return _TRIGGER_TRACKER


def get_trigger_record(signal_id: str) -> TriggerRecord | None:
    """Return the tracked trigger record for ``signal_id`` (or None).

    Read API for the execution layer; never mutates tracker state.
    """
    if signal_id in {None, ""}:
        return None
    return _TRIGGER_TRACKER.get(str(signal_id))


def _first_signal_value(signal: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = signal.get(key)
        if value is not None:
            return value
    return None


def _parse_signal_timestamp(raw: Any) -> datetime | None:
    """Parse an epoch (s/ms) or ISO-8601 timestamp to an aware UTC datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        ts = raw
    elif isinstance(raw, (int, float)) and math.isfinite(raw):
        seconds = float(raw)
        if seconds > 1e12:  # epoch milliseconds
            seconds = seconds / 1000.0
        try:
            ts = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(raw, str):
        try:
            ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _stamp_trigger_lifecycle(
    signal: dict[str, Any],
    policy: TimeframePolicy,
    config: Mapping[str, Any] | None,
) -> None:
    """Arm/update a :class:`TriggerRecord` and stamp its state on the signal.

    Required inputs: a signal id, a setup zone, a trigger level, and a usable
    decision timestamp (all timestamps are passed in from the payload context
    — the lifecycle module has no wall-clock dependence). When any is absent
    nothing is stamped. TTL comes from CONFIG["TRIGGER_LIFECYCLE"] with
    defaults of max 8 trigger bars / 4 hours, whichever elapses first.
    """
    try:
        signal_id = signal.get("signalId")
        zone = _first_signal_value(signal, "setupZone", "setup_zone")
        trigger_level = _first_signal_value(signal, "triggerLevel", "trigger_level")
        now = _parse_signal_timestamp(
            _first_signal_value(signal, "timestamp", "ts", "time", "decisionTime", "decision_time")
        )
        if signal_id in {None, ""} or now is None:
            return
        if not (isinstance(zone, (list, tuple)) and len(zone) == 2):
            return
        try:
            setup_zone = (float(zone[0]), float(zone[1]))
            trigger_price = float(trigger_level)
        except (TypeError, ValueError):
            return
        lifecycle = config.get("TRIGGER_LIFECYCLE") if isinstance(config, Mapping) else None
        if not isinstance(lifecycle, Mapping):
            lifecycle = {}
        try:
            ttl_seconds = float(
                lifecycle.get("TTL_SECONDS", _TRIGGER_LIFECYCLE_DEFAULT_TTL_SECONDS)
            )
            max_bars = int(
                lifecycle.get("MAX_TRIGGER_BARS", _TRIGGER_LIFECYCLE_DEFAULT_MAX_BARS)
            )
        except (TypeError, ValueError):
            ttl_seconds = _TRIGGER_LIFECYCLE_DEFAULT_TTL_SECONDS
            max_bars = _TRIGGER_LIFECYCLE_DEFAULT_MAX_BARS
        bar_seconds = _TF_SECONDS.get(policy.trigger_tf.value)

        _TRIGGER_TRACKER.sweep_expired(now)
        record = _TRIGGER_TRACKER.get(str(signal_id))
        if record is None:
            record = _TRIGGER_TRACKER.arm(
                signal_id=str(signal_id),
                symbol=policy.canonical_symbol,
                engine=policy.engine_id,
                style=policy.style,
                direction=str(signal.get("direction") or ""),
                profile=policy.profile,
                regime_tf=policy.regime_tf.value,
                bias_tf=policy.bias_tf.value,
                structure_tf=policy.structure_tf.value,
                setup_tf=policy.setup_tf.value,
                trigger_tf=policy.trigger_tf.value,
                armed_at=now,
                setup_zone=setup_zone,
                trigger_level=trigger_price,
                ttl_seconds=ttl_seconds,
                max_trigger_bars=max_bars if bar_seconds else None,
                bar_seconds=bar_seconds,
            )
        signal["triggerState"] = record.state.value
        signal["triggerAge"] = (
            max(0.0, (now - record.armed_at).total_seconds())
            if record.armed_at is not None
            else None
        )
        signal["triggerExpiry"] = record.to_dict().get("expires_at")
    except Exception:
        # Diagnostics must never break payload stamping.
        return


def _stamp_m5_eligibility(
    signal: dict[str, Any],
    policy: TimeframePolicy,
    config: Mapping[str, Any] | None,
) -> None:
    """Stamp ``m5Eligible``/``m5EligibilityReasons`` from available inputs.

    Builds an :class:`M5EligibilityContext` from data already present on the
    signal (thresholds fall back to CONFIG["M5_ELIGIBILITY"]). Missing inputs
    fail closed as ``*_missing`` reason codes; this layer never raises and
    never mutates score/direction/conviction.
    """
    try:
        m5_cfg = config.get("M5_ELIGIBILITY") if isinstance(config, Mapping) else None
        if not isinstance(m5_cfg, Mapping):
            m5_cfg = {}

        def _threshold(*keys: str, cfg_key: str) -> Any:
            value = _first_signal_value(signal, *keys)
            if value is None:
                value = m5_cfg.get(cfg_key)
            return value

        record = _TRIGGER_TRACKER.get(str(signal.get("signalId") or ""))
        if record is not None:
            trigger_expired = record.state == TriggerState.EXPIRED
        else:
            trigger_expired = bool(
                _first_signal_value(signal, "triggerExpired", "trigger_expired") or False
            )
        context = M5EligibilityContext(
            m5_policy=policy.m5_policy,
            htf_structure_valid=_first_signal_value(
                signal, "htfStructureValid", "htf_structure_valid", "structureOk", "structure_ok"
            ),
            setup_armed=_first_signal_value(signal, "setupArmed", "setup_armed"),
            session_or_participation_eligible=_first_signal_value(
                signal,
                "sessionEligible",
                "session_eligible",
                "participationEligible",
                "participation_eligible",
            ),
            current_spread=_first_signal_value(signal, "currentSpread", "current_spread", "spread"),
            spread_threshold=_threshold("m5SpreadThreshold", "m5_spread_threshold", cfg_key="SPREAD_THRESHOLD"),
            distance_from_setup_atr=_first_signal_value(
                signal, "distanceFromSetupAtr", "distance_from_setup_atr"
            ),
            max_displacement_atr=_threshold(
                "m5MaxDisplacementAtr", "m5_max_displacement_atr", cfg_key="MAX_DISPLACEMENT_ATR"
            ),
            entry_event=_first_signal_value(signal, "m5EntryEvent", "m5_entry_event"),
            trigger_expired=trigger_expired,
            structural_rr_at_current_price=_first_signal_value(
                signal, "rrAtCurrentPrice", "structuralRrAtCurrentPrice", "structural_rr_at_current_price"
            ),
            min_rr=_threshold("m5MinRr", "m5_min_rr", cfg_key="MIN_RR"),
            quote_inside_opposing_zone=bool(
                _first_signal_value(signal, "quoteInsideOpposingZone", "quote_inside_opposing_zone")
                or False
            ),
            ltf_direction_agrees_with_thesis=_first_signal_value(
                signal, "ltfDirectionAgrees", "ltf_direction_agrees", "m5DirectionAgrees", "m5_direction_agrees"
            ),
            fallback_trigger_tf=policy.trigger_tf.value,
        )
        result = evaluate_m5_eligibility(context)
        signal["m5Eligible"] = bool(result.eligible)
        signal["m5EligibilityReasons"] = list(result.reasons)
    except Exception:
        signal["m5Eligible"] = False
        signal["m5EligibilityReasons"] = ["m5_eligibility_error"]


def attach_timeframe_policy_payload(
    signal: dict[str, Any],
    pair: Mapping[str, Any],
    style: str,
    *,
    engine: str = "engine_a",
    market_states: Mapping[str, Mapping[str, Any]] | None = None,
    speed_state: SpeedState | None = None,
    policy_mode: str | PolicyMode | None = None,
    config: Mapping[str, Any] | None = None,
    session_calendar: SessionCalendarResolution | None = None,
) -> TimeframePolicy:
    """Attach policy/readiness fields and promote the current policy result."""
    symbol = str(pair.get("display") or pair.get("symbol") or signal.get("display") or "")
    asset_type = str(pair.get("type") or pair.get("asset_type") or signal.get("type") or "")
    provider = str(
        pair.get("source") or pair.get("provider") or signal.get("source") or ""
    ).strip().lower()
    score_group = pair.get("score_group") or signal.get("scoreGroup") or signal.get("score_group")
    engine_key, style_key = _normalize_policy_identity(engine, str(style or "intraday"))
    mode, demo_autotrade_enabled, real_autotrade_enabled = _policy_runtime_settings(
        policy_mode, config
    )
    authoritative_group = pair.get("authoritative_score_group")
    if not authoritative_group:
        try:
            from engine_a_groups import resolve_score_group_by_type

            canonical = canonical_symbol(symbol)
            canonical_pair = dict(pair)
            if canonical:
                canonical_pair["display"] = _CANONICAL_DISPLAY.get(canonical, canonical)
            authoritative_group = resolve_score_group_by_type(canonical_pair)
        except Exception:
            authoritative_group = None
    if not score_group and authoritative_group:
        score_group = authoritative_group
    policy = resolve_timeframe_policy(
        symbol,
        asset_type,
        str(score_group) if score_group else None,
        style_key,
        speed_state,
        engine_id=engine_key,
        authoritative_group=(
            str(authoritative_group) if authoritative_group else None
        ),
    )
    legacy = {
        "structureTf": signal.get("structure_tf") or signal.get("structureTf"),
        "setupTf": (
            signal.get("entry_tf")
            or signal.get("setupTf")
            or signal.get("entryTimeframe")
        ),
        "triggerTf": (
            signal.get("trigger_tf")
            or signal.get("triggerTf")
            or signal.get("timeframe")
        ),
        "executionTf": (
            signal.get("execution_tf")
            or signal.get("executionTf")
            or signal.get("executionTimeframe")
            or signal.get("entryTimeframe")
        ),
    }
    payload = policy.payload()
    payload["speedPercentile"] = speed_state.speed_percentile if speed_state else None
    payload["currentSpeedClass"] = (
        speed_state.live_speed_class.value
        if speed_state and speed_state.live_speed_class
        else SpeedClass.UNAVAILABLE.value
    )
    payload["candidateSpeedClass"] = (
        speed_state.candidate_speed_class.value
        if speed_state and speed_state.candidate_speed_class
        else None
    )
    payload["candidateAgeH1Bars"] = speed_state.candidate_age_h1_bars if speed_state else 0
    payload["transitionPending"] = speed_state.transition_pending if speed_state else False
    payload["lastSpeedTransitionUtc"] = (
        speed_state.last_speed_transition_utc if speed_state else None
    )
    payload["timeframePolicyMode"] = mode.value
    payload["timeframePolicyDemoAutotradeEnabled"] = bool(
        mode == PolicyMode.ENFORCED_DEMO
        and demo_autotrade_enabled
        and policy.policy_source != PolicySource.CONFIG_CONFLICT
    )
    payload["timeframePolicyRealAutotradeEnabled"] = bool(
        mode == PolicyMode.ENFORCED_LIVE
        and real_autotrade_enabled
        and policy.policy_source != PolicySource.CONFIG_CONFLICT
    )
    # Retained for consumers that only display a single policy auto-trade flag.
    payload["timeframePolicyAutotradeEnabled"] = bool(
        payload["timeframePolicyDemoAutotradeEnabled"]
        or payload["timeframePolicyRealAutotradeEnabled"]
    )
    payload["proposedVersusLegacy"] = {
        key: {"legacy": legacy.get(key), "proposed": payload.get(key)}
        for key in ("structureTf", "setupTf", "triggerTf", "executionTf")
        if legacy.get(key) != payload.get(key)
    }
    signal.update(payload)
    signal["symbolOverrideApplied"] = policy.diagnostics.symbol_override_applied
    apply_authoritative_policy_result(
        signal,
        policy_mode=mode,
        config=config,
    )
    signal["signalId"] = _signal_id(signal, policy.policy_key)
    signal["engineId"] = policy.engine_id
    signal["style"] = policy.style
    signal["policyKey"] = policy.policy_key

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
    # Engine B entry confirmation is entry_ok (candle trigger OR structural
    # catalysts: breakout+volume, sweep@zone, CHoCH@zone). Checking only
    # trigger_ok rejects valid catalyst passes and blocks execute refresh with
    # ENGINE_B_ENTRY_NOT_READY: PENDING: confirmed trigger condition not supplied.
    _trigger_aliases = (
        "triggerConfirmed",
        "trigger_confirmed",
        "entry_ok",
        "canonical_trigger_ok",
        "trigger_ok",
        "trigger_passed",
    )
    _present_triggers = [
        signal.get(key) for key in _trigger_aliases if signal.get(key) is not None
    ]
    trigger_supplied = bool(_present_triggers)
    trigger_confirmed = any(bool(value) for value in _present_triggers)
    execution_timing = evaluate_execution_timeframe(
        policy,
        signal,
        states,
        trigger_confirmed=trigger_confirmed,
        use_mt5_bid_candle_close=provider == "mt5",
    )
    signal.update(execution_timing)
    execution_status = str(execution_timing.get("executionTimingStatus") or "UNAVAILABLE")
    if policy.policy_source == PolicySource.CONFIG_CONFLICT:
        readiness = "CONFIG_ERROR"
        reason = "CONFIG_CONFLICT"
    elif missing:
        readiness = "UNAVAILABLE"
        reason = "missing_required_closed_timeframes:" + ",".join(missing)
    elif stale:
        readiness = "UNAVAILABLE"
        reason = "stale_required_closed_timeframes:" + ",".join(stale)
    elif not trigger_confirmed:
        readiness = "PENDING"
        reason = (
            "confirmed trigger condition not met"
            if trigger_supplied
            else "confirmed trigger condition not supplied"
        )
    elif execution_status in {"UNAVAILABLE", "STALE"}:
        readiness = "UNAVAILABLE"
        reason = (
            f"execution timeframe {policy.execution_tf.value} "
            f"{execution_status.lower()}"
        )
    elif execution_status in {"OPPOSED", "NEUTRAL"}:
        readiness = "PENDING"
        reason = (
            f"execution timeframe {policy.execution_tf.value} "
            f"{execution_status.lower()} signal direction"
        )
    else:
        readiness = "READY"
        reason = "required closed timeframes, trigger, and execution timing are aligned"
    signal["entryReadiness"] = readiness
    signal["entryReadinessReason"] = reason
    signal["triggerConfirmed"] = trigger_confirmed
    signal["triggerCandleClosed"] = trigger_closed
    if readiness == "CONFIG_ERROR":
        signal["timeframePolicyAutotradeEnabled"] = False
        signal["automatedExecutionDisabledReason"] = "CONFIG_CONFLICT"

    atr_diag = signal.get("atrDiagnostics") if isinstance(signal.get("atrDiagnostics"), Mapping) else {}
    atr_value = atr_diag.get("atr_value", signal.get("atr"))
    atr_tf = str(atr_diag.get("atr_tf") or policy.structure_tf.value).upper()
    entry_drift_atr = signal.get("entryDriftAtr") or atr_diag.get("entry_drift_atr")
    entry_drift_tf = (
        signal.get("entryDriftAtrTimeframe")
        or atr_diag.get("entry_drift_atr_tf")
    )
    risk_atr = signal.get("riskAtr") or atr_diag.get("risk_atr")
    risk_atr_tf = signal.get("riskAtrTimeframe") or atr_diag.get("risk_atr_tf")
    execution_move_atr = signal.get("executionMoveAtr") or atr_diag.get("execution_move_atr")
    execution_move_tf = (
        signal.get("executionMoveAtrTimeframe")
        or atr_diag.get("execution_move_atr_tf")
    )
    if entry_drift_atr is None and atr_tf == policy.trigger_tf.value:
        entry_drift_atr, entry_drift_tf = atr_value, policy.trigger_tf.value
    if risk_atr is None and atr_tf == policy.structure_tf.value:
        risk_atr, risk_atr_tf = atr_value, policy.structure_tf.value
    if execution_move_atr is None and atr_tf == policy.execution_tf.value:
        execution_move_atr, execution_move_tf = atr_value, policy.execution_tf.value
    signal["entryDriftAtr"] = entry_drift_atr
    signal["entryDriftAtrTimeframe"] = entry_drift_tf or policy.trigger_tf.value
    signal["riskAtr"] = risk_atr
    signal["riskAtrTimeframe"] = risk_atr_tf or policy.structure_tf.value
    signal["executionMoveAtr"] = execution_move_atr
    signal["executionMoveAtrTimeframe"] = execution_move_tf or policy.execution_tf.value
    signal["atrValue"] = risk_atr
    signal["atrTimeframe"] = signal["riskAtrTimeframe"]
    signal.setdefault("structureAgeBars", None)
    signal.setdefault("quoteAgeSec", speed_state.quote_age_sec if speed_state else None)
    try:
        entry = float(signal.get("entry") or signal.get("price"))
        live = float(signal.get("livePrice") or signal.get("price"))
        atr = float(entry_drift_atr)
        signal["livePriceDriftAtr"] = abs(live - entry) / atr if atr > 0 else None
    except (TypeError, ValueError):
        signal["livePriceDriftAtr"] = None
    runtime_config = config
    if runtime_config is None:
        try:
            from config import CONFIG

            runtime_config = CONFIG
        except Exception:
            runtime_config = {}
    resolved_session = session_calendar or resolve_session_calendar(
        provider_metadata=(
            pair.get("provider_session_metadata")
            if isinstance(pair.get("provider_session_metadata"), Mapping)
            else None
        ),
        provider_calendar=(
            pair.get("provider_session_calendar")
            if isinstance(pair.get("provider_session_calendar"), Mapping)
            else _configured_session_candidate(
                runtime_config,
                "TF_POLICY_SESSION_CALENDARS",
                pair,
                policy.canonical_symbol,
            )
        ),
        underlying_exchange_calendar=(
            pair.get("underlying_exchange_calendar")
            if isinstance(pair.get("underlying_exchange_calendar"), Mapping)
            else _configured_session_candidate(
                runtime_config,
                "TF_POLICY_UNDERLYING_EXCHANGE_CALENDARS",
                pair,
                policy.canonical_symbol,
            )
        ),
    )
    signal.update(resolved_session.to_dict())
    _stamp_trigger_lifecycle(signal, policy, runtime_config)
    _stamp_m5_eligibility(signal, policy, runtime_config)
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
    engine_id: str = "engine_a",
    strict: bool = True,
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
                canonical_pair = dict(pair)
                resolved_canonical = canonical_symbol(display)
                if resolved_canonical:
                    canonical_pair["display"] = _CANONICAL_DISPLAY.get(
                        resolved_canonical, resolved_canonical
                    )
                score_group = resolve_score_group_by_type(canonical_pair)
            except Exception:
                score_group = None
        policy = resolve_timeframe_policy(
            display,
            asset_type,
            str(score_group) if score_group else None,
            style,
            engine_id=engine_id,
        )
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
            "engine_id": policy.engine_id,
            "style": policy.style,
            "policy_key": policy.policy_key,
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
    result = {
        "rows": rows,
        "duplicate_canonical_symbols": duplicates,
        "aliases_mapping_to_multiple_groups": multi_group,
        "unsafe_symbols": [row["display_symbol"] for row in rows if row["safe_fallback"]],
    }
    if strict and (multi_group or result["unsafe_symbols"]):
        raise PolicyConfigurationError(
            "timeframe policy configuration invalid: "
            f"group_conflicts={multi_group}; fallback_symbols={result['unsafe_symbols']}"
        )
    return result
