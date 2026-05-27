#!/usr/bin/env python3
"""Engine A group coverage contract validator.

Read-only. Enforces that every value returned by get_pair_score_group()
has the explicit entries the 2026 calibration review and audit require
in the critical ENGINE_A_* maps.

Usage (as module or script):
    python -m tools.engine_a_group_contracts
    from tools.engine_a_group_contracts import validate_engine_a_group_coverage
"""

from __future__ import annotations

from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS


# Maps that must contain an explicit key for *every* known score_group
# (these were the ones expanded in the calibration review so no group
# silently inherits a broad asset_type or default).
STRICT_PER_GROUP_MAPS = (
    "ENGINE_A_SCORE_GROUP_THRESHOLDS",
    "ENGINE_A_EMA_PERIODS_BY_CLASS",
    "ENGINE_A_RSI_PERIOD_BY_CLASS",
    "ENGINE_A_MACD_PARAMS_BY_CLASS",
)

# Maps that accept score_group or a containing asset_type (or default).
# We only warn if a group would fall all the way to "default" with no
# more specific entry.
CLASS_KEYED_MAPS = (
    "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS",
    "ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS",
    "ENGINE_A_ADX_SOURCE_BY_CLASS",
    "ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS",
    "ENGINE_A_CONVICTION_FLOOR_BY_CLASS",
    "ENGINE_A_ADDON_UNSUPPORTED_SPLIT_BY_CLASS",
)


def _get_map(name: str) -> dict:
    from config import CONFIG

    val = CONFIG.get(name) or {}
    return val if isinstance(val, dict) else {}


def _group_would_resolve(map_name: str, score_group: str) -> str:
    """Return 'explicit', 'via_asset_type', or 'default_only'."""
    m = _get_map(map_name)
    if score_group in m:
        return "explicit"
    # asset_type fallbacks used by _resolve_class_keyed
    asset_type = _guess_asset_type(score_group)
    if asset_type and asset_type in m:
        return "via_asset_type"
    if "default" in m:
        return "default_only"
    return "missing"


def _guess_asset_type(score_group: str) -> str | None:
    if score_group.startswith("forex"):
        return "forex"
    if score_group.startswith("crypto"):
        return "crypto"
    if score_group in ("precious_trackers", "energy_oil", "nat_gas", "copper",
                       "pgm_metals", "base_metals", "softs", "commodity_other"):
        return "commodity"
    if score_group in ("us_indices_trackers", "eu_indices", "asian_indices", "index_other"):
        return "index"
    if score_group in ("us_stock_single", "bond_tlt", "smallcap_em_etf", "stock_other"):
        return "stock"
    return None


def validate_engine_a_group_coverage() -> dict:
    """Return a structured report of coverage gaps.

    Keys:
      - ok: bool
      - strict_missing: list of (group, map)
      - class_keyed_only_default: list of (group, map)
      - total_groups: int
    """
    report = {
        "ok": True,
        "strict_missing": [],
        "class_keyed_only_default": [],
        "total_groups": len(ENGINE_A_KNOWN_SCORE_GROUPS),
    }

    for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
        for map_name in STRICT_PER_GROUP_MAPS:
            m = _get_map(map_name)
            if group not in m:
                report["strict_missing"].append((group, map_name))
                report["ok"] = False

        for map_name in CLASS_KEYED_MAPS:
            resolution = _group_would_resolve(map_name, group)
            if resolution == "default_only":
                report["class_keyed_only_default"].append((group, map_name))

    return report


def print_report(report: dict) -> None:
    if report["ok"] and not report["class_keyed_only_default"]:
        print("[OK] Engine A group coverage contract satisfied for all known groups.")
        return

    print("Engine A Group Coverage Contract Report")
    print(f"Groups checked: {report['total_groups']}")
    print()

    if report["strict_missing"]:
        print("CRITICAL - Missing explicit entries (must be added):")
        for g, m in report["strict_missing"]:
            print(f"  {g}  missing from {m}")
        print()

    if report["class_keyed_only_default"]:
        print("WARNING - Groups falling back to 'default' only (consider explicit entry):")
        for g, m in report["class_keyed_only_default"]:
            print(f"  {g}  -> {m}")
        print()

    if not report["ok"]:
        print("Contract violation detected. See strict_missing above.")


if __name__ == "__main__":
    import json
    import sys

    report = validate_engine_a_group_coverage()
    print_report(report)

    # Machine readable for CI / probe scripts
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))

    sys.exit(0 if report["ok"] else 2)
