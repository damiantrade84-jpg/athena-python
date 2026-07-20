"""Stage 3 verification: cost resolver + UnifiedExitSimulator.

FIXED family is golden-diffed against the still-live engine_a_v3.backtest
`_simulate_exit` (the donor this was ported from verbatim) across a scenario
table. MANAGED family gets direct scenario checks against the documented
state-machine semantics (no equivalent pure function exists in
timed_exit_monitor to diff against -- it is a stateful live poller).
"""

from __future__ import annotations

import pytest

from athena_backtest.costs import resolve_costs, cost_r
from athena_backtest.exits import simulate_fixed_exit, simulate_managed_exit, get_timed_exit_cfg
from engine_a_v3.backtest import _simulate_exit as legacy_simulate_exit


def _bars(*ohlc: tuple[float, float, float, float]) -> list[dict]:
    return [{"open": o, "high": h, "low": low, "close": c} for o, h, low, c in ohlc]


FIXED_SCENARIOS = [
    dict(
        name="clean_tp1_single",
        bars=_bars((100, 106, 99, 105), (105, 108, 104, 107)),
        direction="LONG", entry=100, sl=95, tp1=105, tp2=110, exit_policy="SINGLE_TP1",
    ),
    dict(
        name="sl_first",
        bars=_bars((100, 101, 93, 94),),
        direction="LONG", entry=100, sl=95, tp1=105, tp2=110, exit_policy="SINGLE_TP1",
    ),
    dict(
        name="same_bar_sl_and_tp1_adverse_first",
        bars=_bars((100, 106, 93, 95),),
        direction="LONG", entry=100, sl=95, tp1=105, tp2=110, exit_policy="SINGLE_TP1",
    ),
    dict(
        name="split_tp1_then_sl",
        bars=_bars((100, 106, 99, 105), (104, 105, 93, 94)),
        direction="LONG", entry=100, sl=95, tp1=105, tp2=110, exit_policy="SPLIT_50_50",
    ),
    dict(
        name="split_tp1_then_tp2",
        bars=_bars((100, 106, 99, 105), (105, 111, 104, 110)),
        direction="LONG", entry=100, sl=95, tp1=105, tp2=110, exit_policy="SPLIT_50_50",
    ),
    dict(
        name="short_clean_tp1",
        bars=_bars((100, 101, 94, 95),),
        direction="SHORT", entry=100, sl=105, tp1=95, tp2=90, exit_policy="SINGLE_TP1",
    ),
    dict(
        name="timeout_no_touch",
        bars=_bars((100, 102, 99, 101), (101, 103, 100, 102)),
        direction="LONG", entry=100, sl=95, tp1=110, tp2=120, exit_policy="SINGLE_TP1",
    ),
    dict(
        name="split_timeout_after_tp1",
        bars=_bars((100, 106, 99, 105), (105, 107, 103, 106)),
        direction="LONG", entry=100, sl=95, tp1=105, tp2=115, exit_policy="SPLIT_50_50",
    ),
]


@pytest.mark.parametrize("scenario", FIXED_SCENARIOS, ids=lambda s: s["name"])
def test_fixed_exit_matches_legacy_simulate_exit(scenario):
    kwargs = {k: v for k, v in scenario.items() if k != "name"}
    new = simulate_fixed_exit(**kwargs)
    legacy = legacy_simulate_exit(**kwargs)
    assert new.outcome == legacy.outcome
    assert new.result_r == pytest.approx(legacy.result_r, abs=1e-9)
    assert new.exit_offset == legacy.exit_offset
    assert new.same_bar == legacy.same_bar


def test_cost_resolver_crypto_override_applies_to_both_engines():
    pair = {"symbol": "BTCUSDT", "type": "crypto"}
    spec_a = resolve_costs(pair, engine="A")
    spec_b = resolve_costs(pair, engine="B")
    assert spec_a.commission_bps == spec_b.commission_bps == pytest.approx(5.5)
    assert spec_a.slippage_bps == spec_b.slippage_bps == pytest.approx(2.0)
    assert any(k.startswith("BY_ASSET.crypto") for k in spec_a.source_keys)


def test_cost_resolver_forex_uses_base():
    pair = {"symbol": "EURUSD", "type": "forex"}
    spec = resolve_costs(pair, engine="A")
    assert spec.source_keys == ("ENGINE_A_V3_BACKTEST",)


def test_cost_r_matches_legacy_formula():
    from engine_a_v3.backtest import _cost_r as legacy_cost_r
    from datetime import datetime, timezone

    pair = {"symbol": "BTCUSDT", "type": "crypto"}
    spec = resolve_costs(pair, engine="A")
    entry_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    exit_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
    new = cost_r(100.0, 95.0, spec, entry_time=entry_time, exit_time=exit_time)
    legacy = legacy_cost_r(
        100.0, 95.0,
        spread_bps=spec.spread_bps, commission_bps=spec.commission_bps,
        slippage_bps=spec.slippage_bps, swap_bps_per_day=spec.swap_bps_per_day,
        entry_time=entry_time, exit_time=exit_time, round_trip=spec.round_trip,
    )
    assert new == pytest.approx(legacy)


def test_managed_exit_breakeven_then_trail_stop():
    tcfg = get_timed_exit_cfg()
    bars = _bars(
        (100, 101, 99, 100.5),
        (100.5, 103, 100, 102.5),   # ~0.5R -> BE arm territory for intraday(0.6) not yet
        (102.5, 106, 102, 105.5),   # ~1.1R -> trail activates (intraday activation 1.0)
        (105.5, 106, 96, 97),       # sharp reversal should hit trail/BE stop
    )
    out = simulate_managed_exit(
        bars, direction="LONG", entry=100, sl=95, style="intraday", tcfg=tcfg,
    )
    assert out.outcome in ("TRAIL_STOP", "BE_STOP", "PEAK_GIVEBACK")
    assert out.result_r > -1.0  # protective stop must have moved off the original -1R


def test_managed_exit_timeout_when_flat():
    tcfg = get_timed_exit_cfg()
    tcfg = {**tcfg, "stagnation_exit": {**(tcfg.get("stagnation_exit") or {}), "enabled": False}}
    bars = _bars((100, 101, 99, 100.2), (100.2, 100.8, 99.5, 100.1))
    out = simulate_managed_exit(
        bars, direction="LONG", entry=100, sl=95, style="intraday", tcfg=tcfg,
    )
    assert out.outcome == "TIMEOUT"


def test_managed_exit_unreplayed_disclosed():
    tcfg = get_timed_exit_cfg()
    out = simulate_managed_exit(
        _bars((100, 101, 99, 100.1),), direction="LONG", entry=100, sl=95,
        style="intraday", tcfg=tcfg,
    )
    assert "hybrid_scaleout" in out.unreplayed
