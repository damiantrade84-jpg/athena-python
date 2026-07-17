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


def _load_pair_universe() -> list:
    """Import the root athena module lazily to reuse its canonical ALL_PAIRS."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_athena_root_for_contracts", root / "athena.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return list(getattr(module, "ALL_PAIRS", []) or [])


def validate_pair_profile_group_overrides() -> dict:
    """PAIR_PROFILES score_group overrides must agree with the canonical dispatch.

    Dual registration is intentional for some symbols (e.g. USDX/EURX/JPYX are
    in both ``_CURRENCY_INDICES`` and ``PAIR_PROFILES``); this check fails
    loudly if the two registrations drift apart. Keys not present in the pair
    universe are reported as warnings only.
    """
    from config import CONFIG
    from engine_a_groups import resolve_score_group_by_type

    profiles = CONFIG.get("PAIR_PROFILES") or {}
    overrides = {
        str(k): str(v.get("score_group"))
        for k, v in profiles.items()
        if isinstance(v, dict) and v.get("score_group")
    }
    report = {"ok": True, "mismatches": [], "unmatched_keys": [], "checked": 0}
    if not overrides:
        return report
    by_name: dict[str, dict] = {}
    for pair in _load_pair_universe():
        if not isinstance(pair, dict):
            continue
        for key in (pair.get("display"), pair.get("symbol")):
            if key and str(key) not in by_name:
                by_name[str(key)] = pair
    for key, group in sorted(overrides.items()):
        pair = by_name.get(key)
        if pair is None:
            report["unmatched_keys"].append(key)
            continue
        report["checked"] += 1
        canonical = resolve_score_group_by_type(pair)
        if canonical != group:
            report["mismatches"].append((key, group, canonical))
            report["ok"] = False
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

    overrides_report = validate_pair_profile_group_overrides()
    if not overrides_report["ok"]:
        print("CRITICAL - PAIR_PROFILES score_group overrides disagree with the type table:")
        for key, override, canonical in overrides_report["mismatches"]:
            print(f"  {key}: PAIR_PROFILES={override} vs resolve_score_group_by_type={canonical}")
        print()
    if overrides_report["unmatched_keys"]:
        print("WARNING - PAIR_PROFILES score_group keys not in the pair universe:")
        for key in overrides_report["unmatched_keys"]:
            print(f"  {key}")
        print()
    if overrides_report["ok"] and not overrides_report["unmatched_keys"]:
        print(f"[OK] PAIR_PROFILES score_group overrides consistent ({overrides_report['checked']} checked).")

    # Machine readable for CI / probe scripts
    if "--json" in sys.argv:
        print(json.dumps({"coverage": report, "overrides": overrides_report}, indent=2))

    sys.exit(0 if report["ok"] and overrides_report["ok"] else 2)
