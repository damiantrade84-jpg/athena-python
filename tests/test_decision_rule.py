"""Decision rule boundary tests."""

from __future__ import annotations

from athena_ase.inference.decision import apply_decision_rule


def test_trade_requires_manifest_threshold_not_config():
    assert apply_decision_rule(p_cal=0.60, expected_net_r=0.12, thr_family=0.10) == "TRADE"
    assert apply_decision_rule(p_cal=0.60, expected_net_r=0.08, thr_family=0.10) == "WATCH"


def test_flat_when_core_not_ok():
    assert (
        apply_decision_rule(
            p_cal=0.9,
            expected_net_r=0.5,
            thr_family=0.1,
            data_quality={"coreOk": False},
        )
        == "FLAT"
    )
