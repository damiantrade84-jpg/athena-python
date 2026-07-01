"""Contract tests for Engine B per-group score_group_overrides coverage."""

from __future__ import annotations

import os

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from config import CONFIG
from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from tools.engine_b_group_contracts import validate_engine_b_group_coverage


def test_engine_b_score_group_overrides_cover_all_known_groups_except_unknown():
    report = validate_engine_b_group_coverage()
    assert report["ok"], (
        f"Engine B override contract broken: "
        f"missing_groups={report['missing_groups']} "
        f"missing_styles={report['missing_styles']}"
    )


def test_engine_b_override_map_has_no_orphan_unknown_entry():
    overrides = (CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {}
    assert "unknown" not in overrides


def test_engine_b_expected_group_count_excludes_unknown_only():
    expected = {g for g in ENGINE_A_KNOWN_SCORE_GROUPS if g != "unknown"}
    assert len(expected) == len(ENGINE_A_KNOWN_SCORE_GROUPS) - 1
    assert len(expected) == 25
