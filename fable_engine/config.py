"""Configuration loading and validation for FABLE.

Defaults ship in ``defaults.yaml``. The root ``FABLE_ENGINE`` mapping from
``config.yaml`` / ``config.local.yaml`` is deep-merged on top, then the merged
document is validated so a bad override fails at load time, not mid-scan.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import ACT_NAMES, ROLE_TIMEFRAMES


class FableConfigError(ValueError):
    pass


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
        raise FableConfigError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FableConfigError(f"{label} must be numeric") from exc
    if minimum is not None and result < minimum:
        raise FableConfigError(f"{label} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise FableConfigError(f"{label} must be <= {maximum}")
    return result


def _integer(value: Any, label: str, *, minimum: int = 1) -> int:
    result = _number(value, label, minimum=float(minimum))
    if not result.is_integer():
        raise FableConfigError(f"{label} must be an integer")
    return int(result)


@dataclass(frozen=True, slots=True)
class FableConfig:
    raw: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw["version"])

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", False))

    @property
    def sessions(self) -> dict[str, Any]:
        return self.raw["sessions"]

    @property
    def scan(self) -> dict[str, Any]:
        return self.raw["scan"]

    @property
    def structure(self) -> dict[str, Any]:
        return self.raw["structure"]

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
    def chronicle(self) -> dict[str, Any]:
        return self.raw["chronicle"]

    def levels_for(self, asset_type: str) -> dict[str, Any]:
        merged = {key: deepcopy(value) for key, value in self.levels.items() if key != "by_asset_type"}
        overrides = self.levels.get("by_asset_type") or {}
        asset = overrides.get(str(asset_type or "").strip().lower()) if isinstance(overrides, dict) else None
        if isinstance(asset, dict):
            merged.update(deepcopy(asset))
        return merged

    def public_dict(self) -> dict[str, Any]:
        return deepcopy(self.raw)


def _validate(raw: dict[str, Any]) -> None:
    for section in ("sessions", "scan", "structure", "scoring", "levels", "execution", "chronicle"):
        if not isinstance(raw.get(section), dict):
            raise FableConfigError(f"{section} must be a mapping")

    frames = set(ROLE_TIMEFRAMES.values())
    scan = raw["scan"]
    for key in ("bars", "minimum_bars", "maximum_closed_bar_age_buckets"):
        mapping = scan.get(key)
        if not isinstance(mapping, dict) or set(mapping) != frames:
            raise FableConfigError(f"scan.{key} must define exactly {sorted(frames)}")
    for timeframe in sorted(frames):
        requested = _integer(scan["bars"][timeframe], f"scan.bars.{timeframe}")
        minimum = _integer(scan["minimum_bars"][timeframe], f"scan.minimum_bars.{timeframe}")
        if minimum > requested:
            raise FableConfigError(f"scan.minimum_bars.{timeframe} cannot exceed scan.bars.{timeframe}")
        _number(scan["maximum_closed_bar_age_buckets"][timeframe], f"scan.maximum_closed_bar_age_buckets.{timeframe}", minimum=0.0)
    _integer(scan.get("max_workers"), "scan.max_workers")
    _integer(scan.get("max_errors_returned"), "scan.max_errors_returned")
    _number(scan.get("maximum_clock_skew_sec"), "scan.maximum_clock_skew_sec", minimum=0.0)

    sessions = raw["sessions"]
    for key in ("timezone", "display_timezone"):
        if not str(sessions.get(key) or "").strip():
            raise FableConfigError(f"sessions.{key} is required")
    _number(sessions.get("fallback_utc_offset_hours"), "sessions.fallback_utc_offset_hours")
    _number(sessions.get("display_fallback_utc_offset_hours"), "sessions.display_fallback_utc_offset_hours")
    _number(sessions.get("minimum_window_quality"), "sessions.minimum_window_quality", minimum=0.0, maximum=1.0)
    _integer(sessions.get("fringe_minutes"), "sessions.fringe_minutes", minimum=0)
    _number(sessions.get("fringe_quality"), "sessions.fringe_quality", minimum=0.0, maximum=1.0)
    _number(sessions.get("off_window_quality"), "sessions.off_window_quality", minimum=0.0, maximum=1.0)
    windows = sessions.get("windows")
    if not isinstance(windows, dict) or not windows:
        raise FableConfigError("sessions.windows must be a non-empty mapping")
    for name, window in windows.items():
        if not isinstance(window, dict):
            raise FableConfigError(f"sessions.windows.{name} must be a mapping")
        start = _integer(window.get("start_minute"), f"sessions.windows.{name}.start_minute", minimum=0)
        end = _integer(window.get("end_minute"), f"sessions.windows.{name}.end_minute", minimum=1)
        if start > 1440 or end > 1440 or start == end:
            raise FableConfigError(f"sessions.windows.{name} has an invalid minute range")
        _number(window.get("quality"), f"sessions.windows.{name}.quality", minimum=0.0, maximum=1.0)
    apply_to = sessions.get("apply_window_gate_to")
    if not isinstance(apply_to, list):
        raise FableConfigError("sessions.apply_window_gate_to must be a list")

    structure = raw["structure"]
    _integer(structure.get("atr_period"), "structure.atr_period", minimum=2)
    strength = structure.get("swing_strength")
    if not isinstance(strength, dict) or set(strength) != frames:
        raise FableConfigError("structure.swing_strength must define every FABLE timeframe")
    for timeframe, value in strength.items():
        _integer(value, f"structure.swing_strength.{timeframe}")
    lookback = structure.get("pool_lookback")
    if not isinstance(lookback, dict) or set(lookback) != {"H4", "H1"}:
        raise FableConfigError("structure.pool_lookback must define H4 and H1")
    for timeframe, value in lookback.items():
        _integer(value, f"structure.pool_lookback.{timeframe}")
    for key in (
        "raid_lookback_bars",
        "raid_max_excursion_bars",
        "shift_max_bars_after_raid",
        "participation_baseline_window",
        "efficiency_window",
        "atr_percentile_window",
    ):
        _integer(structure.get(key), f"structure.{key}")
    for key in (
        "equal_level_tolerance_atr",
        "raid_min_depth_atr",
        "raid_max_depth_atr",
        "shift_min_displacement_atr",
        "shift_min_body_atr",
        "return_tolerance_atr",
    ):
        _number(structure.get(key), f"structure.{key}", minimum=0.0)
    if float(structure["raid_min_depth_atr"]) >= float(structure["raid_max_depth_atr"]):
        raise FableConfigError("structure.raid_min_depth_atr must be below raid_max_depth_atr")
    ote_low = _number(structure.get("ote_low"), "structure.ote_low", minimum=0.0, maximum=1.0)
    ote_high = _number(structure.get("ote_high"), "structure.ote_high", minimum=0.0, maximum=1.0)
    if ote_low >= ote_high:
        raise FableConfigError("structure.ote_low must be below structure.ote_high")

    scoring = raw["scoring"]
    weights = scoring.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(ACT_NAMES):
        raise FableConfigError("scoring.weights must define exactly the five FABLE acts")
    total = sum(_number(value, f"scoring.weights.{key}", minimum=0.0) for key, value in weights.items())
    if abs(total - 100.0) > 1e-9:
        raise FableConfigError("scoring.weights must sum to 100")
    execute = _number(scoring.get("execute_threshold"), "scoring.execute_threshold", minimum=0.0, maximum=100.0)
    stage = _number(scoring.get("stage_threshold"), "scoring.stage_threshold", minimum=0.0, maximum=100.0)
    if stage > execute:
        raise FableConfigError("scoring.stage_threshold cannot exceed scoring.execute_threshold")
    tiers = scoring.get("tiers")
    if not isinstance(tiers, dict) or set(tiers) != {"LEGEND", "SAGA", "TALE"}:
        raise FableConfigError("scoring.tiers must define LEGEND, SAGA and TALE")
    legend = _number(tiers["LEGEND"], "scoring.tiers.LEGEND", minimum=0.0, maximum=100.0)
    saga = _number(tiers["SAGA"], "scoring.tiers.SAGA", minimum=0.0, maximum=100.0)
    tale = _number(tiers["TALE"], "scoring.tiers.TALE", minimum=0.0, maximum=100.0)
    if not (legend > saga > tale):
        raise FableConfigError("scoring.tiers must satisfy LEGEND > SAGA > TALE")
    _number(scoring.get("quality_floor"), "scoring.quality_floor", minimum=1e-6, maximum=0.5)
    atr_min = _number(scoring.get("atr_pct_min"), "scoring.atr_pct_min", minimum=0.0)
    atr_max = _number(scoring.get("atr_pct_max"), "scoring.atr_pct_max", minimum=atr_min)
    if atr_max <= atr_min:
        raise FableConfigError("scoring.atr_pct_max must exceed scoring.atr_pct_min")
    chorus = scoring.get("chorus")
    if not isinstance(chorus, dict):
        raise FableConfigError("scoring.chorus must be a mapping")
    for key in (
        "carry_weight",
        "cot_weight",
        "vol_skew_weight",
        "funding_weight",
        "participation_weight",
        "volatility_weight",
        "session_weight",
    ):
        _number(chorus.get(key), f"scoring.chorus.{key}", minimum=0.0)

    levels = raw["levels"]
    minimum_stop = _number(levels.get("minimum_stop_atr"), "levels.minimum_stop_atr", minimum=0.0)
    maximum_stop = _number(levels.get("maximum_stop_atr"), "levels.maximum_stop_atr", minimum=minimum_stop)
    if maximum_stop < minimum_stop:
        raise FableConfigError("levels.maximum_stop_atr must be >= levels.minimum_stop_atr")
    _number(levels.get("stop_buffer_atr"), "levels.stop_buffer_atr", minimum=0.0)
    _number(levels.get("minimum_rr"), "levels.minimum_rr", minimum=1.0)
    _number(levels.get("target_liquidity_buffer_atr"), "levels.target_liquidity_buffer_atr", minimum=0.0)
    overlays = levels.get("by_asset_type") or {}
    if overlays and not isinstance(overlays, dict):
        raise FableConfigError("levels.by_asset_type must be a mapping")
    for asset, overlay in (overlays or {}).items():
        if not isinstance(overlay, dict):
            raise FableConfigError(f"levels.by_asset_type.{asset} must be a mapping")
        if "maximum_stop_atr" in overlay:
            _number(overlay["maximum_stop_atr"], f"levels.by_asset_type.{asset}.maximum_stop_atr", minimum=minimum_stop)
        if "minimum_rr" in overlay:
            _number(overlay["minimum_rr"], f"levels.by_asset_type.{asset}.minimum_rr", minimum=1.0)

    execution = raw["execution"]
    default_mode = str(execution.get("default_mode") or "").strip().lower()
    if default_mode not in {"paper", "demo", "live"}:
        raise FableConfigError("execution.default_mode must be paper, demo, or live")
    if not bool(execution.get(f"{default_mode}_enabled")):
        raise FableConfigError("execution.default_mode must be enabled")
    risk_fraction = _number(execution.get("risk_fraction"), "execution.risk_fraction", minimum=0.0001)
    if risk_fraction > 0.01:
        raise FableConfigError("execution.risk_fraction cannot exceed 1%")
    if bool(execution.get("live_enabled")) and str(execution.get("research_status")).upper() != "VALIDATED":
        if bool(execution.get("require_validated_research_for_live", True)):
            raise FableConfigError("live execution requires research_status=VALIDATED")
    _number(execution.get("paper_equity"), "execution.paper_equity", minimum=1.0)
    _number(execution.get("max_signal_age_sec"), "execution.max_signal_age_sec", minimum=0.0)
    _number(execution.get("maximum_clock_skew_sec"), "execution.maximum_clock_skew_sec", minimum=0.0)
    _number(execution.get("max_quote_drift_atr"), "execution.max_quote_drift_atr", minimum=0.0)
    _number(execution.get("maximum_narrative_bar_age_buckets"), "execution.maximum_narrative_bar_age_buckets", minimum=0.0)
    for mapping_name in ("maximum_quote_age_sec", "maximum_spread_bps"):
        mapping = execution.get(mapping_name)
        if not isinstance(mapping, dict) or "default" not in mapping:
            raise FableConfigError(f"execution.{mapping_name} must be a mapping with a default")
        for key, value in mapping.items():
            _number(value, f"execution.{mapping_name}.{key}", minimum=1e-9)

    chronicle = raw["chronicle"]
    default_bars = _integer(chronicle.get("default_bars"), "chronicle.default_bars")
    maximum_bars = _integer(chronicle.get("maximum_bars"), "chronicle.maximum_bars")
    if default_bars > maximum_bars:
        raise FableConfigError("chronicle.default_bars cannot exceed chronicle.maximum_bars")
    _integer(chronicle.get("outcome_horizon_bars"), "chronicle.outcome_horizon_bars")
    _integer(chronicle.get("minimum_trades_for_evidence"), "chronicle.minimum_trades_for_evidence")


def load_fable_config(root_config: dict[str, Any] | None = None) -> FableConfig:
    path = Path(__file__).with_name("defaults.yaml")
    defaults = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = (root_config or {}).get("FABLE_ENGINE")
    if overrides is not None and not isinstance(overrides, dict):
        raise FableConfigError("FABLE_ENGINE override must be a mapping")
    raw = _merge(defaults, overrides or {})
    _validate(raw)
    return FableConfig(raw=raw)
