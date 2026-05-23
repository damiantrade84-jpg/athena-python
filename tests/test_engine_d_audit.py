"""Engine D audit contract tests (replaces placeholder stubs)."""

from __future__ import annotations

from ai_playbooks.engine_d_scalp_playbook import get_engine_d_scalp_playbook
from scalp_engine import _append_engine_d_gate_issue


def test_advisory_gate_issue_routes_to_soft_warnings():
    fail_reasons: list[str] = []
    soft_warnings: list[str] = []
    advisory_mechanical: list[str] = []
    _append_engine_d_gate_issue(
        "rr_below_min",
        fail_reasons=fail_reasons,
        soft_warnings=soft_warnings,
        advisory_mode=True,
        advisory_mechanical_issues=advisory_mechanical,
    )
    assert fail_reasons == []
    assert soft_warnings == ["rr_below_min"]
    assert advisory_mechanical == ["rr_below_min"]


def test_advisory_gate_issue_routes_to_fail_reasons_when_disabled():
    fail_reasons: list[str] = []
    soft_warnings: list[str] = []
    _append_engine_d_gate_issue(
        "fee_guard_high_cost",
        fail_reasons=fail_reasons,
        soft_warnings=soft_warnings,
        advisory_mode=False,
    )
    assert fail_reasons == ["fee_guard_high_cost"]
    assert soft_warnings == []


def test_engine_d_playbook_contains_adjudication_sections():
    playbook = get_engine_d_scalp_playbook()
    for key in (
        "sessionRegimeSwitch",
        "effortVsResult",
        "trappedTraderLogic",
        "pocMagnet",
        "casinoTimeDegradation",
        "structuralStopPrinciples",
    ):
        assert key in playbook
        assert isinstance(playbook[key], list)
        assert len(playbook[key]) >= 2
