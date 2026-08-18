"""Configuration loading and validation for SOL."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SolConfigError(ValueError):
    pass


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
        raise SolConfigError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SolConfigError(f"{label} must be numeric") from exc
    if minimum is not None and result < minimum:
        raise SolConfigError(f"{label} must be >= {minimum}")
    return result


def _integer(value: Any, label: str, *, minimum: int = 1) -> int:
    result = _number(value, label, minimum=float(minimum))
    if not result.is_integer():
        raise SolConfigError(f"{label} must be an integer")
    return int(result)


@dataclass(frozen=True, slots=True)
class SolConfig:
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

    def public_dict(self) -> dict[str, Any]:
        return deepcopy(self.raw)


def _validate(raw: dict[str, Any]) -> None:
    required_sections = ("scan", "indicators", "scoring", "levels", "execution", "replay")
    for section in required_sections:
        if not isinstance(raw.get(section), dict):
            raise SolConfigError(f"{section} must be a mapping")

    frames = raw["scan"].get("timeframes") or {}
    expected_roles = {"context", "value", "setup", "trigger"}
    if set(frames) != expected_roles:
        raise SolConfigError("scan.timeframes must define context, value, setup, and trigger")
    allowed = {"H4", "H1", "M30", "M15", "M5"}
    if any(str(tf).upper() not in allowed for tf in frames.values()):
        raise SolConfigError("scan.timeframes contains an unsupported timeframe")
    if len({str(tf).upper() for tf in frames.values()}) != 4:
        raise SolConfigError("SOL role timeframes must be distinct")

    scan = raw["scan"]
    required_frames = {"H4", "H1", "M15", "M5"}
    bars = scan.get("bars") or {}
    minimum_bars = scan.get("minimum_bars") or {}
    if set(bars) != required_frames or set(minimum_bars) != required_frames:
        raise SolConfigError("scan bars and minimum_bars must define H4, H1, M15, and M5")
    for timeframe in sorted(required_frames):
        requested = _integer(bars[timeframe], f"scan.bars.{timeframe}")
        minimum = _integer(minimum_bars[timeframe], f"scan.minimum_bars.{timeframe}")
        if minimum > requested:
            raise SolConfigError(f"scan.minimum_bars.{timeframe} cannot exceed scan.bars.{timeframe}")
    _integer(scan.get("max_workers"), "scan.max_workers")
    _integer(scan.get("max_errors_returned"), "scan.max_errors_returned")
    _number(scan.get("maximum_clock_skew_sec"), "scan.maximum_clock_skew_sec", minimum=0.0)
    freshness = scan.get("maximum_closed_bar_age_buckets") or {}
    if set(freshness) != required_frames:
        raise SolConfigError("maximum_closed_bar_age_buckets must define every SOL timeframe")
    for timeframe, value in freshness.items():
        _number(value, f"maximum_closed_bar_age_buckets.{timeframe}", minimum=0.0)

    indicators = raw["indicators"]
    for key in (
        "atr_period",
        "orbit_lookback",
        "orbit_slope_window",
        "liquidity_lookback",
        "liquidity_recent_bars",
        "compression_short_window",
        "compression_baseline_window",
        "trigger_window",
        "trigger_break_lookback",
        "participation_recent_window",
        "participation_baseline_window",
    ):
        _integer(indicators.get(key), f"indicators.{key}")
    _number(indicators.get("participation_scale_floor"), "indicators.participation_scale_floor", minimum=1e-9)

    weights = raw["scoring"].get("weights") or {}
    expected_weights = {
        "regime_orbit",
        "value_location",
        "liquidity_event",
        "displacement",
        "participation",
        "execution_geometry",
    }
    if set(weights) != expected_weights:
        raise SolConfigError("scoring.weights does not match the SOL component contract")
    total_weight = sum(_number(value, f"scoring.weights.{key}", minimum=0.0) for key, value in weights.items())
    if abs(total_weight - 100.0) > 1e-9:
        raise SolConfigError("scoring.weights must sum to 100")

    ready = _number(raw["scoring"].get("ready_threshold"), "ready_threshold", minimum=0.0)
    watch = _number(raw["scoring"].get("watch_threshold"), "watch_threshold", minimum=0.0)
    if ready > 100 or watch > ready:
        raise SolConfigError("score thresholds must satisfy 0 <= watch <= ready <= 100")
    trigger_strength = _number(
        raw["scoring"].get("minimum_trigger_strength"),
        "scoring.minimum_trigger_strength",
        minimum=0.0,
    )
    if trigger_strength > 1.0:
        raise SolConfigError("scoring.minimum_trigger_strength cannot exceed 1")
    _number(raw["scoring"].get("maximum_value_extension_z"), "scoring.maximum_value_extension_z", minimum=0.0)

    minimum_rr = _number(raw["levels"].get("minimum_rr"), "levels.minimum_rr", minimum=1.0)
    target_rr = _number(raw["levels"].get("target_rr"), "levels.target_rr", minimum=minimum_rr)
    if target_rr < minimum_rr:
        raise SolConfigError("levels.target_rr must be >= levels.minimum_rr")
    minimum_stop = _number(raw["levels"].get("minimum_stop_atr"), "levels.minimum_stop_atr", minimum=0.0)
    maximum_stop = _number(raw["levels"].get("maximum_stop_atr"), "levels.maximum_stop_atr", minimum=minimum_stop)
    if maximum_stop < minimum_stop:
        raise SolConfigError("levels.maximum_stop_atr must be >= levels.minimum_stop_atr")

    execution = raw["execution"]
    default_mode = str(execution.get("default_mode") or "").strip().lower()
    if default_mode not in {"paper", "demo", "live"}:
        raise SolConfigError("execution.default_mode must be paper, demo, or live")
    if not bool(execution.get(f"{default_mode}_enabled")):
        raise SolConfigError("execution.default_mode must be enabled")
    _number(execution.get("risk_fraction"), "execution.risk_fraction", minimum=0.0001)
    if float(execution["risk_fraction"]) > 0.01:
        raise SolConfigError("execution.risk_fraction cannot exceed 1%")
    if bool(execution.get("live_enabled")) and str(execution.get("research_status")).upper() != "VALIDATED":
        if bool(execution.get("require_validated_research_for_live", True)):
            raise SolConfigError("live execution requires research_status=VALIDATED")
    _number(execution.get("paper_equity"), "execution.paper_equity", minimum=1.0)
    _number(execution.get("max_signal_age_sec"), "execution.max_signal_age_sec", minimum=0.0)
    _number(execution.get("maximum_clock_skew_sec"), "execution.maximum_clock_skew_sec", minimum=0.0)
    _number(execution.get("max_quote_drift_atr"), "execution.max_quote_drift_atr", minimum=0.0)
    for mapping_name in ("maximum_quote_age_sec", "maximum_spread_bps"):
        mapping = execution.get(mapping_name)
        if not isinstance(mapping, dict) or "default" not in mapping:
            raise SolConfigError(f"execution.{mapping_name} must be a mapping with a default")
        for key, value in mapping.items():
            _number(value, f"execution.{mapping_name}.{key}", minimum=1e-9)

    replay = raw["replay"]
    default_bars = _integer(replay.get("default_bars"), "replay.default_bars")
    maximum_bars = _integer(replay.get("maximum_bars"), "replay.maximum_bars")
    if default_bars > maximum_bars:
        raise SolConfigError("replay.default_bars cannot exceed replay.maximum_bars")
    _integer(replay.get("outcome_horizon_m5_bars"), "replay.outcome_horizon_m5_bars")
    _integer(replay.get("minimum_trades_for_evidence"), "replay.minimum_trades_for_evidence")


def load_sol_config(root_config: dict[str, Any] | None = None) -> SolConfig:
    path = Path(__file__).with_name("defaults.yaml")
    defaults = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = (root_config or {}).get("SOL_ENGINE")
    if overrides is not None and not isinstance(overrides, dict):
        raise SolConfigError("SOL_ENGINE override must be a mapping")
    raw = _merge(defaults, overrides or {})
    _validate(raw)
    return SolConfig(raw=raw)
