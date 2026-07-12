"""Strict, fail-closed configuration for Ghost Trade."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from types import MappingProxyType
from typing import Any, Mapping

from .models import ExitStrategy, GhostMode


class GhostConfigError(ValueError):
    """Raised when Ghost Trade configuration is unsafe or malformed."""


_ENV_KEYS = {
    "GHOST_TRADE_ENABLED": "enabled",
    "GHOST_TRADE_MODE": "mode",
    "GHOST_TRADE_DEMO_AUTO_ENABLED": "demo_auto_enabled",
    "GHOST_TRADE_MAX_RISK_PER_TRADE": "max_risk_per_trade",
    "GHOST_TRADE_MAX_TOTAL_RISK": "max_total_risk",
    "GHOST_TRADE_SCAN_INTERVAL_SECONDS": "scan_interval_seconds",
}

_DEFAULT_GROUP_PROFILES: dict[str, dict[str, Any]] = {
    "forex": {"enabled": True, "default_style": "intraday", "volume_quality_mode": "tick_volume", "execution_source": "MT5", "maximum_spread_pct": 0.0005},
    "crypto": {"enabled": True, "default_style": "intraday", "volume_quality_mode": "exchange_volume", "execution_source": "BYBIT", "maximum_spread_pct": 0.002},
    "metals": {"enabled": True, "default_style": "intraday", "volume_quality_mode": "broker_or_exchange", "execution_source": "MT5", "maximum_spread_pct": 0.0015},
    "energy": {"enabled": True, "default_style": "intraday", "execution_source": "MT5", "maximum_spread_pct": 0.0015},
    "commodities_other": {"enabled": True, "default_style": "intraday", "execution_source": "MT5", "maximum_spread_pct": 0.0015},
    "indices": {"enabled": True, "default_style": "intraday", "execution_source": "MT5", "maximum_spread_pct": 0.001},
    "equities": {"enabled": True, "default_style": "swing", "execution_source": "MT5", "maximum_spread_pct": 0.002},
    "other": {"enabled": True, "default_style": "intraday", "maximum_spread_pct": 0.001},
}


def _strict_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise GhostConfigError(f"{label} must be a boolean")


def _strict_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise GhostConfigError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GhostConfigError(f"{label} must be numeric") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise GhostConfigError(f"{label} must be finite")
    return result


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise GhostConfigError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GhostConfigError(f"{label} must be an integer") from exc
    if str(value).strip() not in {str(result), f"{result}.0"} and not isinstance(value, int):
        raise GhostConfigError(f"{label} must be an integer")
    return result


def _nested_immutable(value: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    return MappingProxyType(
        {str(key): MappingProxyType(dict(profile)) for key, profile in value.items()}
    )


@dataclass(frozen=True, slots=True)
class GhostConfig:
    enabled: bool = True
    mode: GhostMode = GhostMode.SHADOW
    live_trading_allowed: bool = False
    demo_auto_enabled: bool = False
    effective_demo_auto_enabled: bool = False
    minimum_confirmed_score: float = 0.35
    minimum_entry_quality: float = 0.60
    minimum_raw_rr: float = 1.00
    risk_per_trade: float = 0.0025
    max_risk_per_trade: float = 0.005
    max_total_risk: float = 0.02
    max_open_positions: int = 8
    max_scan_workers: int = 8
    scan_interval_seconds: int = 3600
    signal_cooldown_seconds: int = 14400
    exit_strategy: ExitStrategy = ExitStrategy.STRUCTURAL
    symbol_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    group_profiles: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: _nested_immutable(_DEFAULT_GROUP_PROFILES)
    )

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Any] | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "GhostConfig":
        return _build_config(
            mapping or {}, os.environ if environ is None else environ
        )


def _build_config(mapping: Mapping[str, Any], environ: Mapping[str, str]) -> GhostConfig:
    source = mapping.get("ghost_trade", mapping)
    if not isinstance(source, Mapping):
        raise GhostConfigError("ghost_trade must be a mapping")
    values: dict[str, Any] = dict(source)
    for env_name, field_name in _ENV_KEYS.items():
        if env_name in environ and str(environ[env_name]).strip() != "":
            values[field_name] = environ[env_name]

    enabled = _strict_bool(values.get("enabled", True), "enabled")
    demo_auto = _strict_bool(
        values.get("demo_auto_enabled", False), "demo_auto_enabled"
    )
    try:
        mode = GhostMode(str(values.get("mode", GhostMode.SHADOW.value)).strip().upper())
    except ValueError as exc:
        raise GhostConfigError("mode must be SHADOW, DEMO_MANUAL, or DEMO_AUTO") from exc

    floats = {
        "minimum_confirmed_score": _strict_float(values.get("minimum_confirmed_score", 0.35), "minimum_confirmed_score"),
        "minimum_entry_quality": _strict_float(values.get("minimum_entry_quality", 0.60), "minimum_entry_quality"),
        "minimum_raw_rr": _strict_float(values.get("minimum_raw_rr", 1.00), "minimum_raw_rr"),
        "risk_per_trade": _strict_float(values.get("risk_per_trade", 0.0025), "risk_per_trade"),
        "max_risk_per_trade": _strict_float(values.get("max_risk_per_trade", 0.005), "max_risk_per_trade"),
        "max_total_risk": _strict_float(values.get("max_total_risk", 0.02), "max_total_risk"),
    }
    if not 0.0 <= floats["minimum_confirmed_score"] <= 1.0:
        raise GhostConfigError("minimum_confirmed_score must be in [0, 1]")
    if not 0.0 <= floats["minimum_entry_quality"] <= 1.0:
        raise GhostConfigError("minimum_entry_quality must be in [0, 1]")
    if floats["minimum_raw_rr"] < 0.0:
        raise GhostConfigError("minimum_raw_rr must be non-negative")
    if not 0.0 < floats["risk_per_trade"] <= 0.005:
        raise GhostConfigError("risk_per_trade must be in (0, 0.005]")
    if not 0.0 < floats["max_risk_per_trade"] <= 0.005:
        raise GhostConfigError("max_risk_per_trade must be in (0, 0.005]")
    if floats["risk_per_trade"] > floats["max_risk_per_trade"]:
        raise GhostConfigError("risk_per_trade cannot exceed max_risk_per_trade")
    if not floats["max_risk_per_trade"] <= floats["max_total_risk"] <= 0.02:
        raise GhostConfigError("max_total_risk must be between per-trade risk and 0.02")

    scan_interval = _strict_int(
        values.get("scan_interval_seconds", 3600), "scan_interval_seconds"
    )
    if not 60 <= scan_interval <= 86400:
        raise GhostConfigError("scan_interval_seconds must be in [60, 86400]")
    max_open_positions = _strict_int(
        values.get("max_open_positions", 8), "max_open_positions"
    )
    if not 1 <= max_open_positions <= 100:
        raise GhostConfigError("max_open_positions must be in [1, 100]")
    max_scan_workers = _strict_int(
        values.get("max_scan_workers", 8), "max_scan_workers"
    )
    if not 1 <= max_scan_workers <= 32:
        raise GhostConfigError("max_scan_workers must be in [1, 32]")
    cooldown = _strict_int(
        values.get("signal_cooldown_seconds", 14400), "signal_cooldown_seconds"
    )
    if cooldown < 0:
        raise GhostConfigError("signal_cooldown_seconds must be non-negative")

    raw_profiles = values.get("group_profiles", _DEFAULT_GROUP_PROFILES)
    if not isinstance(raw_profiles, Mapping):
        raise GhostConfigError("group_profiles must be a mapping")
    if any(not isinstance(profile, Mapping) for profile in raw_profiles.values()):
        raise GhostConfigError("every group profile must be a mapping")
    merged_profiles = {
        key: {**profile, **dict(raw_profiles.get(key, {}))}
        for key, profile in _DEFAULT_GROUP_PROFILES.items()
    }
    raw_overrides = values.get("symbol_overrides", {})
    if not isinstance(raw_overrides, Mapping):
        raise GhostConfigError("symbol_overrides must be a mapping")
    if any(not isinstance(override, Mapping) for override in raw_overrides.values()):
        raise GhostConfigError("every symbol override must be a mapping")

    return GhostConfig(
        enabled=enabled,
        mode=mode,
        live_trading_allowed=False,
        demo_auto_enabled=demo_auto,
        effective_demo_auto_enabled=False,
        minimum_confirmed_score=floats["minimum_confirmed_score"],
        minimum_entry_quality=floats["minimum_entry_quality"],
        minimum_raw_rr=floats["minimum_raw_rr"],
        risk_per_trade=floats["risk_per_trade"],
        max_risk_per_trade=floats["max_risk_per_trade"],
        max_total_risk=floats["max_total_risk"],
        max_open_positions=max_open_positions,
        max_scan_workers=max_scan_workers,
        scan_interval_seconds=scan_interval,
        signal_cooldown_seconds=cooldown,
        exit_strategy=ExitStrategy.STRUCTURAL,
        symbol_overrides=_nested_immutable(
            {str(key): dict(value) for key, value in raw_overrides.items()}
        ),
        group_profiles=_nested_immutable(merged_profiles),
    )


def load_ghost_config(
    root_config: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> GhostConfig:
    """Load Ghost config without mutating Athena's global configuration."""

    return GhostConfig.from_mapping(root_config or {}, environ=environ)
