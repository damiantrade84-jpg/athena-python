"""Configuration loading and validation for MUSE."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class MuseConfigError(ValueError):
    pass


_MUSE_ROLES = {"atlas": "D1", "current": "H4", "vector": "M15", "spark": "M5"}
_MUSE_FRAMES = {"D1", "H4", "M15", "M5"}


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _number(value: Any, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise MuseConfigError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MuseConfigError(f"{label} must be numeric") from exc
    if minimum is not None and result < minimum:
        raise MuseConfigError(f"{label} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise MuseConfigError(f"{label} must be <= {maximum}")
    return result


def _integer(value: Any, label: str, *, minimum: int = 1) -> int:
    result = _number(value, label, minimum=float(minimum))
    if not result.is_integer():
        raise MuseConfigError(f"{label} must be an integer")
    return int(result)


@dataclass(frozen=True, slots=True)
class MuseConfig:
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
    def prisms(self) -> dict[str, Any]:
        return self.raw["prisms"]

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
    def sounding(self) -> dict[str, Any]:
        return self.raw["sounding"]

    def public_dict(self) -> dict[str, Any]:
        return deepcopy(self.raw)


def _validate(raw: dict[str, Any]) -> None:
    for section in ("scan", "sessions", "prisms", "scoring", "levels", "execution", "sounding"):
        if not isinstance(raw.get(section), dict):
            raise MuseConfigError(f"{section} must be a mapping")
    if str(raw.get("version") or "") != "muse.v1":
        raise MuseConfigError("version must be 'muse.v1'")

    scan = raw["scan"]
    roles = scan.get("timeframes") or {}
    if dict(roles) != _MUSE_ROLES:
        raise MuseConfigError("scan.timeframes must be exactly atlas:D1 current:H4 vector:M15 spark:M5")
    for key in ("bars", "minimum_bars", "maximum_closed_bar_age_buckets"):
        mapping = scan.get(key) or {}
        if set(mapping) != _MUSE_FRAMES:
            raise MuseConfigError(f"scan.{key} must define D1, H4, M15 and M5")
    for timeframe in sorted(_MUSE_FRAMES):
        requested = _integer(scan["bars"][timeframe], f"scan.bars.{timeframe}")
        minimum = _integer(scan["minimum_bars"][timeframe], f"scan.minimum_bars.{timeframe}")
        if minimum > requested:
            raise MuseConfigError(f"scan.minimum_bars.{timeframe} cannot exceed scan.bars.{timeframe}")
        _number(scan["maximum_closed_bar_age_buckets"][timeframe], f"scan.maximum_closed_bar_age_buckets.{timeframe}", minimum=0.0)
    _integer(scan.get("max_workers"), "scan.max_workers")
    _integer(scan.get("max_errors_returned"), "scan.max_errors_returned")
    _number(scan.get("maximum_clock_skew_sec"), "scan.maximum_clock_skew_sec", minimum=0.0)

    sessions = raw["sessions"]
    for key in ("timezone", "display_timezone"):
        if not str(sessions.get(key) or "").strip():
            raise MuseConfigError(f"sessions.{key} is required")
    for key in ("weekend_close_weekday", "weekend_open_weekday"):
        weekday = _integer(sessions.get(key), f"sessions.{key}", minimum=0)
        if weekday > 6:
            raise MuseConfigError(f"sessions.{key} must be 0..6")
    for key in ("weekend_close_hour", "weekend_open_hour"):
        hour = _integer(sessions.get(key), f"sessions.{key}", minimum=0)
        if hour > 23:
            raise MuseConfigError(f"sessions.{key} must be 0..23")
    if not isinstance(sessions.get("apply_weekend_gate_to"), list):
        raise MuseConfigError("sessions.apply_weekend_gate_to must be a list")
    _number(sessions.get("fallback_utc_offset_hours"), "sessions.fallback_utc_offset_hours")
    _number(sessions.get("display_fallback_utc_offset_hours"), "sessions.display_fallback_utc_offset_hours")
    _integer(sessions.get("fringe_minutes"), "sessions.fringe_minutes", minimum=0)
    _number(sessions.get("fringe_quality"), "sessions.fringe_quality", minimum=0.0, maximum=1.0)
    _number(sessions.get("off_tide_quality"), "sessions.off_tide_quality", minimum=0.0, maximum=1.0)
    windows = sessions.get("windows")
    if not isinstance(windows, dict) or not windows:
        raise MuseConfigError("sessions.windows must be a non-empty mapping")
    for name, window in windows.items():
        if not isinstance(window, dict):
            raise MuseConfigError(f"sessions.windows.{name} must be a mapping")
        start = _integer(window.get("start_minute"), f"sessions.windows.{name}.start_minute", minimum=0)
        end = _integer(window.get("end_minute"), f"sessions.windows.{name}.end_minute", minimum=1)
        if start > 1440 or end > 1440 or start == end:
            raise MuseConfigError(f"sessions.windows.{name} has an invalid minute range")
        if str(window.get("kind") or "") not in {"drift", "tide", "surge", "slack"}:
            raise MuseConfigError(f"sessions.windows.{name}.kind must be drift/tide/surge/slack")
        _number(window.get("quality"), f"sessions.windows.{name}.quality", minimum=0.0, maximum=1.0)

    prisms = raw["prisms"]
    for key in ("atr_period", "echo_lookback_bars", "echo_reclaim_bars", "echo_max_reclaim_bars",
                "surge_min_run", "haven_lookback", "haven_max_age_bars",
                "compass_channel", "compass_slope_span", "spark_recent_bars"):
        _integer(prisms.get(key), f"prisms.{key}")
    for key in ("echo_min_depth_atr", "surge_body_share", "surge_leg_atr", "surge_single_leg_atr",
                "haven_fresh_boost", "compass_min_slope_atr", "spark_min_body_atr"):
        _number(prisms.get(key), f"prisms.{key}", minimum=0.0)
    ote_inner = _number(prisms.get("ote_inner"), "prisms.ote_inner", minimum=0.0, maximum=1.0)
    ote_outer = _number(prisms.get("ote_outer"), "prisms.ote_outer", minimum=0.0, maximum=1.0)
    if ote_inner >= ote_outer:
        raise MuseConfigError("prisms.ote_inner must be below prisms.ote_outer")

    scoring = raw["scoring"]
    prime = _number(scoring.get("prime_threshold"), "scoring.prime_threshold", minimum=0.0, maximum=100.0)
    stage = _number(scoring.get("stage_threshold"), "scoring.stage_threshold", minimum=0.0, maximum=100.0)
    if stage > prime:
        raise MuseConfigError("scoring.stage_threshold cannot exceed scoring.prime_threshold")
    for key in ("minimum_tide_quality", "minimum_echo", "minimum_surge", "minimum_haven", "maximum_halo_dissent"):
        _number(scoring.get(key), f"scoring.{key}", minimum=0.0, maximum=1.0)
    _number(scoring.get("timing_base"), "scoring.timing_base", minimum=0.0, maximum=1.0)
    _number(scoring.get("timing_gain"), "scoring.timing_gain", minimum=0.0, maximum=1.0)
    halo_floor = _number(scoring.get("halo_floor"), "scoring.halo_floor", minimum=0.5, maximum=1.0)
    halo_ceiling = _number(scoring.get("halo_ceiling"), "scoring.halo_ceiling", minimum=1.0, maximum=1.25)
    if halo_floor > 1.0 or halo_ceiling < 1.0:
        raise MuseConfigError("halo band must straddle 1.0")
    _number(scoring.get("harmonic_floor"), "scoring.harmonic_floor", minimum=1e-6, maximum=0.2)

    levels = raw["levels"]
    inner = _number(levels.get("entry_ote_inner"), "levels.entry_ote_inner", minimum=0.0, maximum=1.0)
    outer = _number(levels.get("entry_ote_outer"), "levels.entry_ote_outer", minimum=0.0, maximum=1.0)
    if inner >= outer:
        raise MuseConfigError("levels.entry_ote_inner must be below levels.entry_ote_outer")
    minimum_stop = _number(levels.get("minimum_stop_atr"), "levels.minimum_stop_atr", minimum=0.0)
    _number(levels.get("maximum_stop_atr"), "levels.maximum_stop_atr", minimum=minimum_stop)
    minimum_rr = _number(levels.get("minimum_rr"), "levels.minimum_rr", minimum=1.0)
    _number(levels.get("target_rr"), "levels.target_rr", minimum=minimum_rr)
    _number(levels.get("stop_buffer_atr"), "levels.stop_buffer_atr", minimum=0.0)
    _number(levels.get("haven_buffer_atr"), "levels.haven_buffer_atr", minimum=0.0)

    execution = raw["execution"]
    default_mode = str(execution.get("default_mode") or "").strip().lower()
    if default_mode not in {"paper", "demo", "live"}:
        raise MuseConfigError("execution.default_mode must be paper, demo, or live")
    if not bool(execution.get(f"{default_mode}_enabled")):
        raise MuseConfigError("execution.default_mode must be enabled")
    risk = _number(execution.get("risk_fraction"), "execution.risk_fraction", minimum=0.0001)
    if risk > 0.01:
        raise MuseConfigError("execution.risk_fraction cannot exceed 1%")
    if bool(execution.get("live_enabled")) and str(execution.get("research_status")).upper() != "VALIDATED":
        if bool(execution.get("require_validated_research_for_live", True)):
            raise MuseConfigError("live execution requires research_status=VALIDATED")
    _number(execution.get("paper_equity"), "execution.paper_equity", minimum=1.0)
    _number(execution.get("max_signal_age_sec"), "execution.max_signal_age_sec", minimum=0.0)
    _number(execution.get("maximum_clock_skew_sec"), "execution.maximum_clock_skew_sec", minimum=0.0)
    _number(execution.get("max_quote_drift_atr"), "execution.max_quote_drift_atr", minimum=0.0)
    for mapping_name in ("maximum_quote_age_sec", "maximum_spread_bps"):
        mapping = execution.get(mapping_name)
        if not isinstance(mapping, dict) or "default" not in mapping:
            raise MuseConfigError(f"execution.{mapping_name} must be a mapping with a default")
        for key, value in mapping.items():
            _number(value, f"execution.{mapping_name}.{key}", minimum=1e-9)

    sounding = raw["sounding"]
    default_bars = _integer(sounding.get("default_bars"), "sounding.default_bars")
    maximum_bars = _integer(sounding.get("maximum_bars"), "sounding.maximum_bars")
    if default_bars > maximum_bars:
        raise MuseConfigError("sounding.default_bars cannot exceed sounding.maximum_bars")
    _integer(sounding.get("outcome_horizon_m5_bars"), "sounding.outcome_horizon_m5_bars")
    _integer(sounding.get("minimum_trades_for_evidence"), "sounding.minimum_trades_for_evidence")


def load_muse_config(root_config: dict[str, Any] | None = None) -> MuseConfig:
    path = Path(__file__).with_name("defaults.yaml")
    defaults = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = (root_config or {}).get("MUSE_ENGINE")
    if overrides is not None and not isinstance(overrides, dict):
        raise MuseConfigError("MUSE_ENGINE override must be a mapping")
    raw = _merge(defaults, overrides or {})
    _validate(raw)
    return MuseConfig(raw=raw)
