#!/usr/bin/env python3
"""Engine B group coverage contract validator.

Read-only. Ensures every known score_group (except ``unknown``) has explicit
``NAKED_ENGINE.score_group_overrides`` rows for intraday and swing styles after
``engine_b_config.yaml`` is merged at boot.

Usage:
    python -m tools.engine_b_group_contracts
    from tools.engine_b_group_contracts import validate_engine_b_group_coverage
"""

from __future__ import annotations

from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS

_REQUIRED_STYLES = ("intraday", "swing")


def _score_group_overrides() -> dict:
    from config import CONFIG

    naked = CONFIG.get("NAKED_ENGINE") or {}
    if not isinstance(naked, dict):
        return {}
    overrides = naked.get("score_group_overrides") or {}
    return overrides if isinstance(overrides, dict) else {}


def validate_engine_b_group_coverage() -> dict:
    """Return structured coverage report for Engine B per-group overrides."""
    overrides = _score_group_overrides()
    report = {
        "ok": True,
        "missing_styles": [],
        "missing_groups": [],
        "total_groups": sum(1 for g in ENGINE_A_KNOWN_SCORE_GROUPS if g != "unknown"),
    }

    expected_groups = sorted(g for g in ENGINE_A_KNOWN_SCORE_GROUPS if g != "unknown")

    for group in expected_groups:
        group_map = overrides.get(group)
        if not isinstance(group_map, dict):
            report["missing_groups"].append(group)
            report["ok"] = False
            continue
        for style in _REQUIRED_STYLES:
            style_row = group_map.get(style)
            if not isinstance(style_row, dict) or not style_row:
                report["missing_styles"].append((group, style))

    if report["missing_styles"]:
        report["ok"] = False

    return report


def print_report(report: dict) -> None:
    if report["ok"]:
        print(
            "[OK] Engine B group coverage contract satisfied "
            f"for all {report['total_groups']} known groups (excl. unknown)."
        )
        return

    print("Engine B Group Coverage Contract Report")
    print(f"Groups checked: {report['total_groups']}")
    print()

    if report["missing_groups"]:
        print("CRITICAL - Groups with no score_group_overrides entry:")
        for group in report["missing_groups"]:
            print(f"  {group}")
        print()

    if report["missing_styles"]:
        print("CRITICAL - Missing intraday/swing override rows:")
        for group, style in report["missing_styles"]:
            print(f"  {group}  missing {style}")
        print()

    print("Contract violation detected. See entries above.")


if __name__ == "__main__":
    import json
    import sys

    report = validate_engine_b_group_coverage()
    print_report(report)

    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))

    sys.exit(0 if report["ok"] else 2)
