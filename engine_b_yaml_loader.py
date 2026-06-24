"""Validate and translate standalone Engine B YAML configuration."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


_STYLES = ("scalp", "intraday", "swing")
_REGIMES = ("TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY")
_AGGTRADE_MODES = {"required", "preferred", "degraded"}
_GROUP_OVERRIDE_FIELDS = {
    "min_score",
    "min_rr",
    "min_room_atr",
    "max_sl_atr",
    "min_sl_atr",
    "space_rr_substitute",
    "profile_trusted",
    "macro_required",
}


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _as_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _float(value: Any, name: str, *, min_value: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if min_value is not None and out < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    return out


def _positive_float(value: Any, name: str) -> float:
    out = _float(value, name, min_value=0.0)
    if out <= 0.0:
        raise ValueError(f"{name} must be > 0")
    return out


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _strings(value: Any, name: str) -> list[str]:
    items = _as_list(value, name)
    out: list[str] = []
    for idx, item in enumerate(items):
        text = str(item or "").strip()
        if not text:
            raise ValueError(f"{name}[{idx}] must be a non-empty string")
        out.append(text)
    return out


def translate_engine_b_config(doc: dict[str, Any]) -> dict[str, Any]:
    """Translate standalone Engine B YAML into existing runtime CONFIG keys."""
    root = _as_mapping(doc, "root")
    base = _as_mapping(root.get("base_thresholds"), "base_thresholds")
    profiles: dict[str, dict[str, Any]] = {}
    style_min_scores: dict[str, float] = {}
    for style in _STYLES:
        item = _as_mapping(base.get(style), f"base_thresholds.{style}")
        profile = {
            "min_score": _float(item.get("min_score"), f"base_thresholds.{style}.min_score", min_value=0.0),
            "min_rr": _float(item.get("min_rr"), f"base_thresholds.{style}.min_rr", min_value=0.0),
            "fallback_rr": _float(item.get("fallback_rr"), f"base_thresholds.{style}.fallback_rr", min_value=0.0),
        }
        profiles[style] = profile
        style_min_scores[style] = profile["min_score"]

    regimes_raw = _as_mapping(root.get("regime_multipliers"), "regime_multipliers")
    regimes = {
        regime: _positive_float(regimes_raw.get(regime), f"regime_multipliers.{regime}")
        for regime in _REGIMES
    }

    agg = _as_mapping(root.get("crypto_aggtrade"), "crypto_aggtrade")
    mode = str(agg.get("mode") or "").strip().lower()
    if mode not in _AGGTRADE_MODES:
        raise ValueError("crypto_aggtrade.mode must be required, preferred, or degraded")

    space = _as_mapping(root.get("space_gate"), "space_gate")
    execution = _as_mapping(root.get("execution_levels"), "execution_levels")
    vp = _as_mapping(root.get("volume_profile"), "volume_profile")
    session = _as_mapping(root.get("forex_session"), "forex_session")

    overrides: dict[str, dict[str, dict[str, Any]]] = {}
    for idx, row in enumerate(_as_list(root.get("group_overrides", []), "group_overrides")):
        item = _as_mapping(row, f"group_overrides[{idx}]")
        style = str(item.get("style") or "").strip().lower()
        group = str(item.get("group") or "").strip().lower()
        if style not in _STYLES:
            raise ValueError(f"group_overrides[{idx}].style must be one of {', '.join(_STYLES)}")
        if not group:
            raise ValueError(f"group_overrides[{idx}].group must be a non-empty string")
        entry = overrides.setdefault(group, {}).setdefault(style, {})
        for key in _GROUP_OVERRIDE_FIELDS:
            if key not in item:
                continue
            if key in {"space_rr_substitute", "profile_trusted", "macro_required"}:
                entry[key] = _bool(item[key], f"group_overrides[{idx}].{key}")
            else:
                entry[key] = _float(item[key], f"group_overrides[{idx}].{key}", min_value=0.0)

    return {
        "NAKED_ENGINE": {
            "aggtrade_cvd_bonus": _float(
                agg.get("alignment_bonus"), "crypto_aggtrade.alignment_bonus", min_value=0.0
            ),
            "style_profiles": profiles,
            "score_group_overrides": overrides,
        },
        "ENGINE_B_REGIME_MULTIPLIERS": regimes,
        "ENGINE_B_REGIME_MULTIPLIERS_ENABLED": _bool(
            root.get("regime_scaling_enabled"), "regime_scaling_enabled"
        ),
        "ENGINE_B_STYLE_MIN_SCORE_DIFFERENTIATION_ENABLED": True,
        "ENGINE_B_STYLE_MIN_SCORE_BY_STYLE": style_min_scores,
        "ENGINE_B_CRYPTO_AGGTRADE_MODE": mode,
        "ENGINE_B_CRYPTO_AGGTRADE_CVD_SCORE_ENABLED": True,
        "ENGINE_B_CRYPTO_AGGTRADE_MISSING_PENALTY": _float(
            agg.get("missing_penalty"), "crypto_aggtrade.missing_penalty", min_value=0.0
        ),
        "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR_ENABLED": True,
        "ENGINE_B_SPACE_RR_SUBSTITUTE_MIN_ATR_FLOOR": _float(
            space.get("rr_substitute_min_atr_floor"),
            "space_gate.rr_substitute_min_atr_floor",
            min_value=0.0,
        ),
        "ENGINE_B_ENFORCE_MAX_SL_PCT": True,
        "MAX_SL_PCT": {"engine_b": _float(execution.get("max_sl_pct"), "execution_levels.max_sl_pct", min_value=0.0)},
        "ENGINE_B_MAX_SL_ATR_DEFAULT": _float(
            execution.get("max_sl_atr_default"), "execution_levels.max_sl_atr_default", min_value=0.0
        ),
        "ENGINE_B_MIN_SL_ATR_DEFAULT": _float(
            execution.get("min_sl_atr_default"), "execution_levels.min_sl_atr_default", min_value=0.0
        ),
        "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": _bool(
            execution.get("synthetic_fallback_rr_tp_enabled"),
            "execution_levels.synthetic_fallback_rr_tp_enabled",
        ),
        "ENGINE_B_PROFILE_TRUST_MODE": "score_group",
        "ENGINE_B_PROFILE_TRUSTED_SCORE_GROUPS": _strings(
            vp.get("trusted_groups", []), "volume_profile.trusted_groups"
        ),
        "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED": _bool(
            session.get("asian_skip_enabled"), "forex_session.asian_skip_enabled"
        ),
        "ENGINE_B_ASIAN_ACTIVE_CURRENCIES": _strings(
            session.get("asian_active_currencies", []), "forex_session.asian_active_currencies"
        ),
    }


def load_engine_b_yaml(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    raw = cfg_path.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    if doc is None:
        raise ValueError(f"{cfg_path} is empty")
    return translate_engine_b_config(doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Engine B YAML config")
    parser.add_argument("--validate", metavar="PATH", help="validate an Engine B YAML config")
    args = parser.parse_args(argv)
    if not args.validate:
        parser.error("--validate is required")
    load_engine_b_yaml(args.validate)
    print(f"OK: {args.validate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
