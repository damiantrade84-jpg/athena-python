import pytest

from athena_backtesting_v2.replay import (
    _engine_a_required_timeframes,
    _policy_roles,
    _warm,
)


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
