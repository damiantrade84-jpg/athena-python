from types import SimpleNamespace

import pytest

from athena_backtesting_v2.datasets import resolve_policy_requirements
from athena_backtesting_v2.replay import (
    _engine_a_required_timeframes,
    _policy_roles,
    _warm,
)


@pytest.mark.parametrize("engine", ("A", "B"))
def test_v2_dataset_requirements_match_v3_context_for_tuned_roles(monkeypatch, engine):
    import engine_a_groups
    import market_structure
    import timeframe_policy

    tuned_policy = {
        "regime_tf": "H4",
        "bias_tf": "H4",
        "structure_tf": "H4",
        "setup_tf": "H4",
        "trigger_tf": "M30",
        "execution_tf": "M15",
        "required_closed_tfs": ["H4", "M30"],
    }
    policy = SimpleNamespace(
        to_dict=lambda: dict(tuned_policy),
        payload=lambda: {"policyKey": "XAUUSD:engine_a:intraday"},
    )
    monkeypatch.setattr(engine_a_groups, "resolve_score_group_by_type", lambda _pair: "liquid_metals")
    monkeypatch.setattr(timeframe_policy, "resolve_timeframe_policy", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(
        market_structure,
        "resolve_engine_b_tfs",
        lambda *_args, **_kwargs: {
            "regime": "H4",
            "bias": "H4",
            "struct": "H4",
            "setup": "H4",
            "trigger": "M30",
            "execution": "M15",
            "atr": "H4",
            "policy_mode": "authoritative",
            "proposed": None,
        },
    )

    requirement = resolve_policy_requirements(
        {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"},
        engine,
        "intraday",
    )

    assert requirement["policy"]["setup_tf"] == "H4"
    assert requirement["policy"]["trigger_tf"] == "M30"
    assert set(requirement["timeframes"]) == {"D1", "H4", "H1", "M30", "M15"}


@pytest.mark.parametrize(
    ("m5_policy", "execution_prerequisite"),
    (("conditional", "M15"), ("disabled", "")),
)
def test_engine_a_warmup_requires_only_candle_timeframes(
    m5_policy,
    execution_prerequisite,
):
    roles = _policy_roles(
        {
            "regime_tf": "D1",
            "bias_tf": "H4",
            "structure_tf": "H1",
            "setup_tf": "M30",
            "trigger_tf": "M15",
            "execution_tf": "M15",
            "m5_policy": m5_policy,
            "execution_prerequisite_tf": execution_prerequisite,
        }
    )

    required = _engine_a_required_timeframes(roles)
    snapshot = {timeframe: [{}] * 50 for timeframe in required}

    assert roles["m5_policy"] not in required
    assert _warm(None, snapshot, required)
