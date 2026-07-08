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


def test_counter_trend_blocked_when_with_trend_only_hard_block():
    from scalp_engine import _append_engine_d_gate_issue

    fail_reasons: list[str] = []
    soft_warnings: list[str] = []
    advisory_mechanical: list[str] = []
    _append_engine_d_gate_issue(
        "counter_trend:LONG_vs_H1_SHORT",
        fail_reasons=fail_reasons,
        soft_warnings=soft_warnings,
        advisory_mode=False,
        advisory_mechanical_issues=advisory_mechanical,
    )
    assert fail_reasons == ["counter_trend:LONG_vs_H1_SHORT"]
    assert soft_warnings == []


def test_fee_guard_allows_tight_crypto_scalp_with_higher_max_cost_r():
    from scalp_engine import _scalp_cfg_lookup

    cfg = {
        "ENGINE_D_MAX_COST_R": 0.20,
        "ENGINE_D_MAX_COST_R_CLASS": {"crypto": 0.40},
        "ESTIMATED_FEE_PCT_BY_ASSET": {"crypto": 0.0006},
        "ESTIMATED_SLIPPAGE_PCT_BY_ASSET": {"crypto": 0.0002},
    }
    max_cost_r = float(
        _scalp_cfg_lookup(cfg, "ENGINE_D_MAX_COST_R", 0.20, asset_type="crypto")
    )
    total_cost = 0.0008
    stop_pct = 0.0025  # 0.25% structural stop — would fail at 0.20 max (needs 0.4%)
    cost_as_r = total_cost / stop_pct
    assert max_cost_r == 0.40
    assert cost_as_r <= max_cost_r
