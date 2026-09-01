"""Configuration loading and validation for GROK."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class GrokConfigError(ValueError):
    pass


_GROK_WEIGHTS = {
    "killzone_clock",
    "liquidity_raid",
    "impulse_vector",
    "void_alignment",
    "dealing_range",
    "cisd_state",
    "geometry",
}


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise GrokConfigError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GrokConfigError(f"{label} must be numeric") from exc
    if minimum is not None and result < minimum:
        raise GrokConfigError(f"{label} must be >= {minimum}")
    return result


def _integer(value: Any, label: str, *, minimum: int = 1) -> int:
    result = _number(value, label, minimum=float(minimum))
    if not result.is_integer():
        raise GrokConfigError(f"{label} must be an integer")
    return int(result)


@dataclass(frozen=True, slots=True)
class GrokConfig:
    raw: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw["version"])

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", False))

    @property
    def scan(self) -> dict[str, Any]:
        return self.raw["scan"]

    @property
    def sessions(self) -> dict[str, Any]:
        return self.raw["sessions"]

    @property
    def indicators(self) -> dict[str, Any]:
        return self.raw["indicators"]

    @property
    def scoring(self) -> dict[str, Any]:
        return self.raw["scoring"]

    @property
    def levels(self) -> dict[str, Any]:
        return self.raw["levels"]

    @property
    def execution(self) -> dict[str, Any]:
        return self.raw["execution"]

    @property
    def replay(self) -> dict[str, Any]:
        return self.raw["replay"]

    @property
    def profiles(self) -> dict[str, Any]:
        payload = self.raw.get("profiles")
        return payload if isinstance(payload, dict) else {}

    def public_dict(self) -> dict[str, Any]:
        return deepcopy(self.raw)


def _validate(raw: dict[str, Any]) -> None:
    required_sections = (
        "scan",
        "sessions",
        "indicators",
        "scoring",
        "levels",
        "execution",
        "replay",
        "profiles",
    )
    for section in required_sections:
        if not isinstance(raw.get(section), dict):
            raise GrokConfigError(f"{section} must be a mapping")

    frames = raw["scan"].get("timeframes") or {}
    expected_roles = {"bias", "session", "setup", "trigger"}
    if set(frames) != expected_roles:
        raise GrokConfigError("scan.timeframes must define bias, session, setup, and trigger")
    allowed = {"D1", "H1", "M15", "M5"}
    if any(str(tf).upper() not in allowed for tf in frames.values()):
        raise GrokConfigError("scan.timeframes contains an unsupported timeframe")
    if len({str(tf).upper() for tf in frames.values()}) != 4:
        raise GrokConfigError("GROK role timeframes must be distinct")

    scan = raw["scan"]
    required_frames = {"D1", "H1", "M15", "M5"}
    bars = scan.get("bars") or {}
    minimum_bars = scan.get("minimum_bars") or {}
    if set(bars) != required_frames or set(minimum_bars) != required_frames:
        raise GrokConfigError("scan bars and minimum_bars must define D1, H1, M15, and M5")
    for timeframe in sorted(required_frames):
        requested = _integer(bars[timeframe], f"scan.bars.{timeframe}")
        minimum = _integer(minimum_bars[timeframe], f"scan.minimum_bars.{timeframe}")
        if minimum > requested:
            raise GrokConfigError(f"scan.minimum_bars.{timeframe} cannot exceed scan.bars.{timeframe}")
    _integer(scan.get("max_workers"), "scan.max_workers")
    _integer(scan.get("max_errors_returned"), "scan.max_errors_returned")
    _number(scan.get("maximum_clock_skew_sec"), "scan.maximum_clock_skew_sec", minimum=0.0)
    freshness = scan.get("maximum_closed_bar_age_buckets") or {}
    if set(freshness) != required_frames:
        raise GrokConfigError("maximum_closed_bar_age_buckets must define every GROK timeframe")
    for timeframe, value in freshness.items():
        _number(value, f"maximum_closed_bar_age_buckets.{timeframe}", minimum=0.0)

    sessions = raw["sessions"]
    if not str(sessions.get("timezone") or "").strip():
        raise GrokConfigError("sessions.timezone is required")
    if not str(sessions.get("display_timezone") or "").strip():
        raise GrokConfigError("sessions.display_timezone is required")
    _number(sessions.get("fallback_utc_offset_hours"), "sessions.fallback_utc_offset_hours")
    _number(sessions.get("display_fallback_utc_offset_hours"), "sessions.display_fallback_utc_offset_hours")
    for key in ("weekend_close_weekday", "weekend_open_weekday"):
        weekday = _integer(sessions.get(key), f"sessions.{key}", minimum=0)
        if weekday > 6:
            raise GrokConfigError(f"sessions.{key} must be 0-6")
    for key in ("weekend_close_hour", "weekend_open_hour"):
        hour = _integer(sessions.get(key), f"sessions.{key}", minimum=0)
        if hour > 23:
            raise GrokConfigError(f"sessions.{key} must be 0-23")
    _integer(sessions.get("adjacent_minutes"), "sessions.adjacent_minutes", minimum=0)
    _number(sessions.get("adjacent_quality"), "sessions.adjacent_quality", minimum=0.0)
    _number(sessions.get("off_session_quality"), "sessions.off_session_quality", minimum=0.0)
    cash_rth = sessions.get("cash_rth")
    if not isinstance(cash_rth, dict):
        raise GrokConfigError("sessions.cash_rth must be a mapping")
    cash_start = _integer(cash_rth.get("start_minute"), "sessions.cash_rth.start_minute", minimum=0)
    cash_end = _integer(cash_rth.get("end_minute"), "sessions.cash_rth.end_minute", minimum=1)
    if cash_start >= cash_end or cash_end > 1440:
        raise GrokConfigError("sessions.cash_rth has an invalid minute range")
    _number(cash_rth.get("quality"), "sessions.cash_rth.quality", minimum=0.0)
    windows = sessions.get("windows")
    if not isinstance(windows, dict) or not windows:
        raise GrokConfigError("sessions.windows must be a non-empty mapping")
    allowed_kinds = {"range", "killzone", "silver_bullet", "dead"}
    for name, window in windows.items():
        if not isinstance(window, dict):
            raise GrokConfigError(f"sessions.windows.{name} must be a mapping")
        start = _integer(window.get("start_minute"), f"sessions.windows.{name}.start_minute", minimum=0)
        end = _integer(window.get("end_minute"), f"sessions.windows.{name}.end_minute", minimum=1)
        if start > 1440 or end > 1440 or start == end:
            raise GrokConfigError(f"sessions.windows.{name} has an invalid minute range")
        kind = str(window.get("kind") or "")
        if kind not in allowed_kinds:
            raise GrokConfigError(f"sessions.windows.{name}.kind is unsupported")
        quality = _number(window.get("quality"), f"sessions.windows.{name}.quality", minimum=0.0)
        if quality > 1.0:
            raise GrokConfigError(f"sessions.windows.{name}.quality cannot exceed 1")

    indicators = raw["indicators"]
    for key in (
        "atr_period",
        "raid_lookback_bars",
        "raid_recent_bars",
        "impulse_min_run",
        "trigger_recent_bars",
        "void_lookback",
        "dealing_lookback",
        "cisd_lookback",
    ):
        _integer(indicators.get(key), f"indicators.{key}")
    for key in (
        "raid_min_excursion_atr",
        "impulse_body_fraction",
        "impulse_range_atr",
        "impulse_single_range_atr",
        "ote_inner",
        "ote_outer",
    ):
        _number(indicators.get(key), f"indicators.{key}", minimum=0.0)
    if float(indicators["ote_inner"]) >= float(indicators["ote_outer"]):
        raise GrokConfigError("indicators.ote_inner must be < indicators.ote_outer")
    if str(indicators.get("session_source_timeframe") or "").upper() not in {"H1", "M15"}:
        raise GrokConfigError("indicators.session_source_timeframe must be H1 or M15")
    for key in ("bias_enabled", "bias_require_price_side"):
        if not isinstance(indicators.get(key), bool):
            raise GrokConfigError(f"indicators.{key} must be a boolean")
    bias_fast = _integer(indicators.get("bias_fast_period"), "indicators.bias_fast_period", minimum=2)
    bias_slow = _integer(indicators.get("bias_slow_period"), "indicators.bias_slow_period", minimum=2)
    if bias_fast >= bias_slow:
        raise GrokConfigError("indicators.bias_fast_period must be < indicators.bias_slow_period")
    _number(indicators.get("bias_min_separation_atr"), "indicators.bias_min_separation_atr", minimum=0.0)

    weights = raw["scoring"].get("weights") or {}
    if set(weights) != _GROK_WEIGHTS:
        raise GrokConfigError("scoring.weights does not match the GROK component contract")
    total_weight = sum(_number(value, f"scoring.weights.{key}", minimum=0.0) for key, value in weights.items())
    if abs(total_weight - 100.0) > 1e-9:
        raise GrokConfigError("scoring.weights must sum to 100")

    ready = _number(raw["scoring"].get("ready_threshold"), "ready_threshold", minimum=0.0)
    watch = _number(raw["scoring"].get("watch_threshold"), "watch_threshold", minimum=0.0)
    if ready > 100 or watch > ready:
        raise GrokConfigError("score thresholds must satisfy 0 <= watch <= ready <= 100")
    for key in (
        "minimum_killzone_quality",
        "minimum_raid_strength",
        "minimum_impulse_strength",
        "maximum_premium_for_long",
        "minimum_discount_for_short",
    ):
        value = _number(raw["scoring"].get(key), f"scoring.{key}", minimum=0.0)
        if value > 1.0:
            raise GrokConfigError(f"scoring.{key} cannot exceed 1")

    minimum_rr = _number(raw["levels"].get("minimum_rr"), "levels.minimum_rr", minimum=1.0)
    target_rr = _number(raw["levels"].get("target_rr"), "levels.target_rr", minimum=minimum_rr)
    if target_rr < minimum_rr:
        raise GrokConfigError("levels.target_rr must be >= levels.minimum_rr")
    minimum_stop = _number(raw["levels"].get("minimum_stop_atr"), "levels.minimum_stop_atr", minimum=0.0)
    maximum_stop = _number(raw["levels"].get("maximum_stop_atr"), "levels.maximum_stop_atr", minimum=minimum_stop)
    if maximum_stop < minimum_stop:
        raise GrokConfigError("levels.maximum_stop_atr must be >= levels.minimum_stop_atr")
    _number(raw["levels"].get("stop_atr_buffer"), "levels.stop_atr_buffer", minimum=0.0)
    _number(raw["levels"].get("opposing_pool_buffer_atr"), "levels.opposing_pool_buffer_atr", minimum=0.0)

    execution = raw["execution"]
    default_mode = str(execution.get("default_mode") or "").strip().lower()
    if default_mode not in {"paper", "demo", "live"}:
        raise GrokConfigError("execution.default_mode must be paper, demo, or live")
    if not bool(execution.get(f"{default_mode}_enabled")):
        raise GrokConfigError("execution.default_mode must be enabled")
    _number(execution.get("risk_fraction"), "execution.risk_fraction", minimum=0.0001)
    if float(execution["risk_fraction"]) > 0.01:
        raise GrokConfigError("execution.risk_fraction cannot exceed 1%")
    if bool(execution.get("live_enabled")) and str(execution.get("research_status")).upper() != "VALIDATED":
        if bool(execution.get("require_validated_research_for_live", True)):
            raise GrokConfigError("live execution requires research_status=VALIDATED")
    _number(execution.get("paper_equity"), "execution.paper_equity", minimum=1.0)
    _number(execution.get("max_signal_age_sec"), "execution.max_signal_age_sec", minimum=0.0)
    _number(execution.get("maximum_clock_skew_sec"), "execution.maximum_clock_skew_sec", minimum=0.0)
    _number(execution.get("max_quote_drift_atr"), "execution.max_quote_drift_atr", minimum=0.0)
    for mapping_name in ("maximum_quote_age_sec", "maximum_spread_bps"):
        mapping = execution.get(mapping_name)
        if not isinstance(mapping, dict) or "default" not in mapping:
            raise GrokConfigError(f"execution.{mapping_name} must be a mapping with a default")
        for key, value in mapping.items():
            _number(value, f"execution.{mapping_name}.{key}", minimum=1e-9)

    replay = raw["replay"]
    default_bars = _integer(replay.get("default_bars"), "replay.default_bars")
    maximum_bars = _integer(replay.get("maximum_bars"), "replay.maximum_bars")
    if default_bars > maximum_bars:
        raise GrokConfigError("replay.default_bars cannot exceed replay.maximum_bars")
    _integer(replay.get("outcome_horizon_m5_bars"), "replay.outcome_horizon_m5_bars")
    _integer(replay.get("minimum_trades_for_evidence"), "replay.minimum_trades_for_evidence")

    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise GrokConfigError("profiles must be a mapping")
    for section in ("families", "groups", "pairs"):
        mapping = profiles.get(section)
        if mapping is None:
            continue
        if not isinstance(mapping, dict):
            raise GrokConfigError(f"profiles.{section} must be a mapping")
        for name, overlay in mapping.items():
            if not isinstance(overlay, dict):
                raise GrokConfigError(f"profiles.{section}.{name} must be a mapping")
            _validate_profile_overlay(overlay, f"profiles.{section}.{name}", raw)


def _validate_resolved_profile(
    scoring: dict[str, Any],
    levels: dict[str, Any],
    indicators: dict[str, Any],
    label: str,
) -> None:
    weights = scoring.get("weights") or {}
    if set(weights) != _GROK_WEIGHTS:
        raise GrokConfigError(f"{label}.scoring.weights does not match the GROK component contract")
    total = sum(_number(value, f"{label}.scoring.weights.{key}", minimum=0.0) for key, value in weights.items())
    if abs(total - 100.0) > 1e-9:
        raise GrokConfigError(f"{label}.scoring.weights must sum to 100")
    ready = _number(scoring.get("ready_threshold"), f"{label}.scoring.ready_threshold", minimum=0.0)
    watch = _number(scoring.get("watch_threshold"), f"{label}.scoring.watch_threshold", minimum=0.0)
    if ready > 100.0 or watch > ready:
        raise GrokConfigError(f"{label} score thresholds must satisfy 0 <= watch <= ready <= 100")
    for key in (
        "minimum_killzone_quality",
        "minimum_raid_strength",
        "minimum_impulse_strength",
        "maximum_premium_for_long",
        "minimum_discount_for_short",
    ):
        value = _number(scoring.get(key), f"{label}.scoring.{key}", minimum=0.0)
        if value > 1.0:
            raise GrokConfigError(f"{label}.scoring.{key} cannot exceed 1")
    minimum_rr = _number(levels.get("minimum_rr"), f"{label}.levels.minimum_rr", minimum=1.0)
    _number(levels.get("target_rr"), f"{label}.levels.target_rr", minimum=minimum_rr)
    minimum_stop = _number(levels.get("minimum_stop_atr"), f"{label}.levels.minimum_stop_atr", minimum=0.0)
    _number(levels.get("maximum_stop_atr"), f"{label}.levels.maximum_stop_atr", minimum=minimum_stop)
    ote_inner = _number(indicators.get("ote_inner"), f"{label}.indicators.ote_inner", minimum=0.0)
    ote_outer = _number(indicators.get("ote_outer"), f"{label}.indicators.ote_outer", minimum=0.0)
    if ote_inner >= ote_outer or ote_outer > 1.0:
        raise GrokConfigError(f"{label} OTE bounds must satisfy 0 <= inner < outer <= 1")
    bias_fast = _integer(indicators.get("bias_fast_period"), f"{label}.indicators.bias_fast_period", minimum=2)
    bias_slow = _integer(indicators.get("bias_slow_period"), f"{label}.indicators.bias_slow_period", minimum=2)
    if bias_fast >= bias_slow:
        raise GrokConfigError(f"{label}.indicators.bias_fast_period must be < bias_slow_period")
    _number(indicators.get("bias_min_separation_atr"), f"{label}.indicators.bias_min_separation_atr", minimum=0.0)
    for key in ("bias_enabled", "bias_require_price_side"):
        if not isinstance(indicators.get(key), bool):
            raise GrokConfigError(f"{label}.indicators.{key} must be a boolean")


def _validate_profile_overlay(overlay: dict[str, Any], label: str, base: dict[str, Any]) -> None:
    if "session_mode" in overlay:
        mode = str(overlay.get("session_mode") or "").strip().lower()
        if mode not in {"ict_killzone", "cash_rth"}:
            raise GrokConfigError(f"{label}.session_mode must be ict_killzone or cash_rth")
    scoring = overlay.get("scoring")
    if scoring is not None:
        if not isinstance(scoring, dict):
            raise GrokConfigError(f"{label}.scoring must be a mapping")
        weights = scoring.get("weights")
        if weights is not None:
            if not isinstance(weights, dict) or set(weights) != _GROK_WEIGHTS:
                raise GrokConfigError(f"{label}.scoring.weights does not match the GROK component contract")
            total = sum(_number(value, f"{label}.scoring.weights.{key}", minimum=0.0) for key, value in weights.items())
            if abs(total - 100.0) > 1e-9:
                raise GrokConfigError(f"{label}.scoring.weights must sum to 100")
        for key in (
            "ready_threshold",
            "watch_threshold",
            "minimum_killzone_quality",
            "minimum_raid_strength",
            "minimum_impulse_strength",
            "maximum_premium_for_long",
            "minimum_discount_for_short",
        ):
            if key in scoring:
                _number(scoring.get(key), f"{label}.scoring.{key}", minimum=0.0)
    levels = overlay.get("levels")
    if levels is not None:
        if not isinstance(levels, dict):
            raise GrokConfigError(f"{label}.levels must be a mapping")
        for key in (
            "stop_atr_buffer",
            "minimum_stop_atr",
            "maximum_stop_atr",
            "target_rr",
            "minimum_rr",
            "opposing_pool_buffer_atr",
        ):
            if key in levels:
                _number(levels.get(key), f"{label}.levels.{key}", minimum=0.0)
    indicators = overlay.get("indicators")
    if indicators is not None:
        if not isinstance(indicators, dict):
            raise GrokConfigError(f"{label}.indicators must be a mapping")
        for key in ("raid_lookback_bars", "raid_recent_bars", "impulse_min_run", "trigger_recent_bars", "void_lookback", "dealing_lookback", "cisd_lookback", "atr_period", "bias_fast_period", "bias_slow_period"):
            if key in indicators:
                _integer(indicators.get(key), f"{label}.indicators.{key}")
        for key in ("raid_min_excursion_atr", "impulse_body_fraction", "impulse_range_atr", "impulse_single_range_atr", "bias_min_separation_atr"):
            if key in indicators:
                _number(indicators.get(key), f"{label}.indicators.{key}", minimum=0.0)
        for key in ("bias_enabled", "bias_require_price_side"):
            if key in indicators and not isinstance(indicators.get(key), bool):
                raise GrokConfigError(f"{label}.indicators.{key} must be a boolean")

    _validate_resolved_profile(
        _merge(base["scoring"], scoring or {}),
        _merge(base["levels"], levels or {}),
        _merge(base["indicators"], indicators or {}),
        label,
    )


def load_grok_config(root_config: dict[str, Any] | None = None) -> GrokConfig:
    path = Path(__file__).with_name("defaults.yaml")
    defaults = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = (root_config or {}).get("GROK_ENGINE")
    if overrides is not None and not isinstance(overrides, dict):
        raise GrokConfigError("GROK_ENGINE override must be a mapping")
    raw = _merge(defaults, overrides or {})
    _validate(raw)
    return GrokConfig(raw=raw)
