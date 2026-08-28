"""GROK-native score-group / family / pair profile resolution.

Pair identity uses the shared instrument taxonomy so EUR/USD, BTC/USDT, and
AAPL land in different groups. Overlay numbers stay GROK-owned and never
inherit Engine A or OX Alpha weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine_a_groups import resolve_score_group_by_type

from .config import GrokConfig, _merge, _validate_resolved_profile


SESSION_MODES = {"ict_killzone", "cash_rth"}


def _display_key(pair: dict[str, Any]) -> str:
    return str(pair.get("display") or pair.get("symbol") or "").strip()


def _normalized_forex_display(pair: dict[str, Any]) -> str:
    display = _display_key(pair).upper()
    asset_type = str(pair.get("type") or pair.get("asset_type") or "").strip().lower()
    if asset_type == "forex" and "/" not in display and len(display) == 6 and display.isalpha():
        return f"{display[:3]}/{display[3:]}"
    return display


def resolve_grok_score_group(pair: dict[str, Any]) -> str:
    explicit = pair.get("score_group") or pair.get("scoreGroup")
    if explicit:
        return str(explicit).strip()
    adapted = dict(pair)
    normalized = _normalized_forex_display(adapted)
    if normalized:
        adapted["display"] = normalized
    return resolve_score_group_by_type(adapted)


def _overlay_lookup(mapping: Any, key: str) -> dict[str, Any]:
    if not isinstance(mapping, dict) or not key:
        return {}
    row = mapping.get(key)
    return dict(row) if isinstance(row, dict) else {}


@dataclass(frozen=True, slots=True)
class GrokResolvedProfile:
    group: str
    family: str
    session_mode: str
    scoring: dict[str, Any]
    levels: dict[str, Any]
    indicators: dict[str, Any]
    source: str
    weight_scope: str
    calibration_status: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "family": self.family,
            "sessionMode": self.session_mode,
            "source": self.source,
            "weights": {key: float(value) for key, value in self.scoring["weights"].items()},
            "weightScope": self.weight_scope,
            "calibrationStatus": self.calibration_status,
            "readyThreshold": float(self.scoring["ready_threshold"]),
            "watchThreshold": float(self.scoring["watch_threshold"]),
            "minimumKillzoneQuality": float(self.scoring["minimum_killzone_quality"]),
            "minimumRr": float(self.levels["minimum_rr"]),
            "targetRr": float(self.levels["target_rr"]),
            "minimumStopAtr": float(self.levels["minimum_stop_atr"]),
            "maximumStopAtr": float(self.levels["maximum_stop_atr"]),
            "raidMinExcursionAtr": float(self.indicators["raid_min_excursion_atr"]),
            "raidRecentBars": int(self.indicators["raid_recent_bars"]),
        }


def resolve_grok_profile(pair: dict[str, Any], config: GrokConfig) -> GrokResolvedProfile:
    family = str(pair.get("type") or pair.get("asset_type") or "unknown").strip().lower() or "unknown"
    group = resolve_grok_score_group(pair)
    profiles = config.profiles
    merged: dict[str, Any] = {}
    sources: list[str] = ["base"]
    weight_scope = "base"
    family_overlay = _overlay_lookup(profiles.get("families"), family)
    if family_overlay:
        merged = _merge(merged, family_overlay)
        sources.append(f"family:{family}")
        if isinstance((family_overlay.get("scoring") or {}).get("weights"), dict):
            weight_scope = f"family:{family}"
    group_overlay = _overlay_lookup(profiles.get("groups"), group)
    if group_overlay:
        merged = _merge(merged, group_overlay)
        sources.append(f"group:{group}")
        if isinstance((group_overlay.get("scoring") or {}).get("weights"), dict):
            weight_scope = f"group:{group}"
    pairs_overlay = profiles.get("pairs") if isinstance(profiles.get("pairs"), dict) else {}
    display = _display_key(pair)
    for key in (display, display.upper(), _normalized_forex_display(pair)):
        pair_overlay = _overlay_lookup(pairs_overlay, key)
        if pair_overlay:
            merged = _merge(merged, pair_overlay)
            sources.append(f"pair:{key}")
            if isinstance((pair_overlay.get("scoring") or {}).get("weights"), dict):
                weight_scope = f"pair:{key}"
            break
    session_mode = str(merged.get("session_mode") or "ict_killzone").strip().lower()
    if session_mode not in SESSION_MODES:
        session_mode = "ict_killzone"
    scoring = _merge(config.scoring, merged.get("scoring") if isinstance(merged.get("scoring"), dict) else {})
    levels = _merge(config.levels, merged.get("levels") if isinstance(merged.get("levels"), dict) else {})
    indicators = _merge(
        config.indicators,
        merged.get("indicators") if isinstance(merged.get("indicators"), dict) else {},
    )
    _validate_resolved_profile(scoring, levels, indicators, f"resolved profile {group}")
    return GrokResolvedProfile(
        group=group,
        family=family,
        session_mode=session_mode,
        scoring=scoring,
        levels=levels,
        indicators=indicators,
        source=">".join(sources),
        weight_scope=weight_scope,
        calibration_status=str(config.execution.get("research_status") or "UNKNOWN").upper(),
    )


def profile_from_signal(signal: dict[str, Any], config: GrokConfig) -> GrokResolvedProfile:
    stamped = signal.get("grokProfile")
    pair = {
        "display": signal.get("pair") or signal.get("symbol"),
        "symbol": signal.get("symbol") or signal.get("pair"),
        "type": signal.get("assetType"),
        "score_group": signal.get("scoreGroup") or (stamped.get("group") if isinstance(stamped, dict) else None),
    }
    return resolve_grok_profile(pair, config)
