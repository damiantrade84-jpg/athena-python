"""Engine B top-down (ICT/SMC) hierarchical timeframe mode.

Covers ``timeframe_policy._apply_engine_b_hierarchical``:

* disabled / inactive states preserve the exact pre-hierarchy roles AND the
  emitted policy hash (a changed hash is what trips ENGINE_B_TF_POLICY_CHANGED),
* enabled mode re-points only the HTF rungs (regime/bias) for Engine B,
* Engine A and Engine D are never affected,
* precedence against ``ENGINE_TF_ROLE_OVERRIDES`` in both directions,
* malformed configuration fails closed instead of silently keeping H4 bias.

Every test pins ``ENGINE_TF_ROLE_OVERRIDES`` explicitly so it cannot depend on
the shipped per-group matrix in config.yaml.
"""

from __future__ import annotations

import pytest

from timeframe_policy import (
    PolicyConfigurationError,
    Timeframe,
    resolve_timeframe_policy,
)

_UNIVERSAL = (Timeframe.D1, Timeframe.H4, Timeframe.H4, Timeframe.H1, Timeframe.M15)

_HIERARCHICAL_BLOCK = {
    "ENABLED": True,
    "FOLLOW_BIAS_MODE": True,
    "RESPECT_ROLE_OVERRIDES": False,
    "BY_STYLE": {
        "intraday": {"REGIME": "D1", "BIAS": "D1"},
        "swing": {"REGIME": "D1", "BIAS": "D1"},
    },
    "REQUIRE_SEQUENTIAL_CONFIRMATION": True,
}


def _roles(policy) -> tuple[Timeframe, ...]:
    return (
        policy.regime_tf,
        policy.bias_tf,
        policy.structure_tf,
        policy.setup_tf,
        policy.trigger_tf,
    )


@pytest.fixture()
def cfg(monkeypatch):
    """CONFIG with the per-group role matrix pinned off by default."""
    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_TF_ROLE_OVERRIDES", {"ENABLED": False, "BY_GROUP": {}})
    monkeypatch.setitem(CONFIG, "ENGINE_B_BIAS_MODE", "legacy")
    monkeypatch.delitem(CONFIG, "ENGINE_B_HIERARCHICAL_TF", raising=False)
    return CONFIG


def _resolve(style: str = "intraday", engine_id: str = "engine_b"):
    return resolve_timeframe_policy(
        "EUR/USD", "forex", "forex_majors", style, engine_id=engine_id
    )


@pytest.mark.parametrize(
    "block",
    [
        None,
        {"ENABLED": False},
        {"ENABLED": False, "BY_STYLE": {"intraday": {"BIAS": "D1"}}},
    ],
)
def test_disabled_preserves_roles_and_policy_hash(cfg, monkeypatch, block) -> None:
    baseline = _resolve()
    baseline_hash = baseline.payload()["timeframePolicyHash"]
    assert _roles(baseline) == _UNIVERSAL

    if block is not None:
        monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", block)
    policy = _resolve()

    assert _roles(policy) == _UNIVERSAL
    assert policy.payload()["timeframePolicyHash"] == baseline_hash
    assert policy.diagnostics.hierarchical_mode_active is False
    assert policy.diagnostics.hierarchical_patched_roles == ()
    assert policy.diagnostics.hierarchical_sequence_required is False
    assert policy.payload()["hierarchicalBias"]["applied"] is False


def test_follow_bias_mode_keeps_ladder_while_scorer_is_legacy(cfg, monkeypatch) -> None:
    baseline_hash = _resolve().payload()["timeframePolicyHash"]
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", _HIERARCHICAL_BLOCK)

    policy = _resolve()

    assert _roles(policy) == _UNIVERSAL
    assert policy.payload()["timeframePolicyHash"] == baseline_hash
    assert policy.diagnostics.hierarchical_mode_active is False
    assert any(
        "ENGINE_B_HIERARCHICAL_TF inactive" in message
        for message in policy.diagnostics.messages
    )


def test_follow_bias_mode_false_applies_without_the_scorer(cfg, monkeypatch) -> None:
    monkeypatch.setitem(
        cfg,
        "ENGINE_B_HIERARCHICAL_TF",
        {**_HIERARCHICAL_BLOCK, "FOLLOW_BIAS_MODE": False},
    )

    policy = _resolve()

    assert policy.bias_tf == Timeframe.D1
    assert policy.diagnostics.hierarchical_mode_active is True


@pytest.mark.parametrize("bias_mode", ["hierarchical", "strict"])
def test_daily_is_primary_bias_for_engine_b_intraday(cfg, monkeypatch, bias_mode) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_BIAS_MODE", bias_mode)
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", _HIERARCHICAL_BLOCK)

    policy = _resolve("intraday")

    # HTF rungs move; MTF (structure/setup) and LTF (trigger) are untouched.
    assert _roles(policy) == (
        Timeframe.D1,
        Timeframe.D1,
        Timeframe.H4,
        Timeframe.H1,
        Timeframe.M15,
    )
    assert policy.diagnostics.hierarchical_mode_active is True
    assert set(policy.diagnostics.hierarchical_patched_roles) == {"regime", "bias"}
    assert policy.diagnostics.hierarchical_sequence_required is True

    payload = policy.payload()["hierarchicalBias"]
    assert payload["applied"] is True
    assert payload["sequenceRequired"] is True
    assert payload["htfBiasTf"] == "D1"
    assert payload["mtfConfirmationTf"] == "H4"
    assert payload["ltfEntryTf"] == "M15"


def test_swing_keeps_daily_bias_and_no_w1_rung(cfg, monkeypatch) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_BIAS_MODE", "hierarchical")
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", _HIERARCHICAL_BLOCK)

    policy = _resolve("swing")

    assert policy.regime_tf == Timeframe.D1
    assert policy.bias_tf == Timeframe.D1
    # The swing Weekly read is engine_b_hierarchy's resampled W1, never a role.
    assert "W1" not in {tf.value for tf in _roles(policy)}


def test_style_without_a_row_is_untouched(cfg, monkeypatch) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_BIAS_MODE", "hierarchical")
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", _HIERARCHICAL_BLOCK)

    policy = _resolve("scalp")

    assert policy.bias_tf == Timeframe.H4
    assert policy.diagnostics.hierarchical_mode_active is False


def test_engine_a_and_engine_d_are_never_affected(cfg, monkeypatch) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_BIAS_MODE", "hierarchical")
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", _HIERARCHICAL_BLOCK)

    engine_a = _resolve("intraday", engine_id="engine_a")
    assert _roles(engine_a) == _UNIVERSAL
    assert engine_a.diagnostics.hierarchical_mode_active is False

    engine_d = _resolve("scalp", engine_id="engine_d")
    assert engine_d.regime_tf == Timeframe.H1
    assert engine_d.bias_tf == Timeframe.M15
    assert engine_d.trigger_tf == Timeframe.M1
    assert engine_d.diagnostics.hierarchical_mode_active is False


def test_hierarchy_outranks_a_role_override_row_pinning_h4_bias(cfg, monkeypatch) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_BIAS_MODE", "hierarchical")
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", _HIERARCHICAL_BLOCK)
    monkeypatch.setitem(
        cfg,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {
                "forex_majors_standard": {
                    "intraday": {"regime": "D1", "bias": "H4", "trigger": "M30"}
                }
            },
        },
    )

    policy = _resolve("intraday")

    # HTF rung follows the hierarchy; the override still owns the LTF trigger.
    assert policy.bias_tf == Timeframe.D1
    assert policy.trigger_tf == Timeframe.M30
    assert set(policy.diagnostics.hierarchical_patched_roles) == {"regime", "bias"}
    assert policy.diagnostics.role_override_applied is True


def test_respect_role_overrides_defers_to_the_group_matrix(cfg, monkeypatch) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_BIAS_MODE", "hierarchical")
    monkeypatch.setitem(
        cfg,
        "ENGINE_B_HIERARCHICAL_TF",
        {**_HIERARCHICAL_BLOCK, "RESPECT_ROLE_OVERRIDES": True},
    )
    monkeypatch.setitem(
        cfg,
        "ENGINE_TF_ROLE_OVERRIDES",
        {
            "ENABLED": True,
            "BY_GROUP": {"forex_majors_standard": {"intraday": {"bias": "H4"}}},
        },
    )

    policy = _resolve("intraday")

    assert policy.bias_tf == Timeframe.H4
    # regime was not patched by the override row, so the hierarchy still owns it.
    assert policy.regime_tf == Timeframe.D1
    assert policy.diagnostics.hierarchical_patched_roles == ("regime",)
    assert any("deferred" in message for message in policy.diagnostics.messages)


@pytest.mark.parametrize(
    "block",
    [
        [],
        {"ENABLED": "yes"},
        {"ENABLED": True, "FOLLOW_BIAS_MODE": "no"},
        {"ENABLED": True, "FOLLOW_BIAS_MODE": False, "BY_STYLE": []},
        {"ENABLED": True, "FOLLOW_BIAS_MODE": False, "BY_STYLE": {"intraday": []}},
        # Only the HTF rungs are hierarchy-owned.
        {
            "ENABLED": True,
            "FOLLOW_BIAS_MODE": False,
            "BY_STYLE": {"intraday": {"TRIGGER": "M5"}},
        },
        # W1 is not a rung on the v4 ladder.
        {
            "ENABLED": True,
            "FOLLOW_BIAS_MODE": False,
            "BY_STYLE": {"intraday": {"BIAS": "W1"}},
        },
        {
            "ENABLED": True,
            "FOLLOW_BIAS_MODE": False,
            "BY_STYLE": {"intraday": {"BIAS": ""}},
        },
        # bias faster than structure reverses the slower-to-faster ladder.
        {
            "ENABLED": True,
            "FOLLOW_BIAS_MODE": False,
            "BY_STYLE": {"intraday": {"BIAS": "M15"}},
        },
        {
            "ENABLED": True,
            "FOLLOW_BIAS_MODE": False,
            "BY_STYLE": {"intraday": {"BIAS": "D1"}},
            "REQUIRE_SEQUENTIAL_CONFIRMATION": "yes",
        },
        {
            "ENABLED": True,
            "FOLLOW_BIAS_MODE": False,
            "RESPECT_ROLE_OVERRIDES": "no",
            "BY_STYLE": {"intraday": {"BIAS": "D1"}},
        },
    ],
)
def test_malformed_hierarchical_config_fails_closed(cfg, monkeypatch, block) -> None:
    monkeypatch.setitem(cfg, "ENGINE_B_HIERARCHICAL_TF", block)

    with pytest.raises(PolicyConfigurationError):
        _resolve("intraday")

    # Engine A must not be dragged down by an Engine B configuration error.
    assert _roles(_resolve("intraday", engine_id="engine_a")) == _UNIVERSAL
