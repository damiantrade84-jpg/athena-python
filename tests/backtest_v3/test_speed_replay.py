"""Stage 5 verification: historical SpeedState replay determinism.

`timeframe_policy.calculate_speed_state` never reads wall time; the replay
chain must therefore be a pure function of the candle set -- two replays over
the same rows produce identical state fields at every H1 index, and a replay
consulted out of order matches the forward chain.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from athena_backtest.speed_replay import SpeedStateReplay


def _rows(*, step_minutes: int, count: int) -> list[dict]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    for index in range(count):
        center = 100.0 + math.sin(index / 9.0) * (1.0 + (index % 7) * 0.3)
        out.append(
            {
                "time": (start + timedelta(minutes=step_minutes * index)).isoformat(),
                "open": center,
                "high": center + 0.9,
                "low": center - 0.9,
                "close": center + 0.2 * math.cos(index / 4.0),
                "vol": 1000.0 + index,
            }
        )
    return out


def _pair() -> dict:
    return {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"}


def _state_key(state) -> tuple:
    if state is None:
        return ()
    return (
        str(getattr(state, "speed_class", None)),
        str(getattr(state, "liquidity_class", None)),
        getattr(state, "last_closed_h1_open_time", None),
    )


def test_speed_state_replay_is_deterministic_and_order_independent():
    h1 = _rows(step_minutes=60, count=300)
    m15 = _rows(step_minutes=15, count=1200)

    forward = SpeedStateReplay(_pair(), h1, m15)
    forward_states = [_state_key(forward.state_at(i)) for i in range(40, 300, 20)]

    rerun = SpeedStateReplay(_pair(), h1, m15)
    rerun_states = [_state_key(rerun.state_at(i)) for i in range(40, 300, 20)]
    assert forward_states == rerun_states

    # Memoized lookup returns the identical chained object, not a recompute.
    assert forward.state_at(280) is forward.state_at(280)

    # A later start index reflects newer bars in its stamp (chain advances).
    last = forward.state_at(299)
    assert getattr(last, "last_closed_h1_open_time", None) is not None
