"""Single exit simulator for backtest trade management -- replaces the four
fragmented implementations (`timed_exit_monitor.py` live polling,
the research exit re-impl, `engine_a_v3/backtest.py::_simulate_exit`, and the
inline Engine B monitor loop in the retired legacy runner, now archived).

Two families, selected by `exit_policy.resolve_exit_mode`/`resolve_runner_directive`:

FIXED   -- the V3 contract's own exit (SINGLE_TP1 / SPLIT_50_50). Ported
           verbatim from `engine_a_v3/backtest.py::_simulate_exit` so results
           are bit-identical to the retired Engine A backtest loop.

MANAGED -- mirrors the live `timed_exit_monitor.py` state machine: breakeven
           arm, chandelier ATR trail activation + ratchet, percent-of-peak
           giveback close, stagnation time-stop. Parameters are read via the
           SAME merged-config function the live monitor uses
           (`timed_exit_monitor._get_timed_cfg` + its per-style resolvers),
           so a config change can never silently diverge between live and
           backtest.

           Scope note (v1): the live monitor also has a pre-activation
           profit-roundtrip close, a wick-buffered broker SL offset, a staged
           scalp profit-lock ladder, indicator-confirmation gating on trail
           breach, and hybrid scale-out. Those are NOT ported in v1 -- MANAGED
           mode here covers BE arm, chandelier trail + giveback, and
           stagnation only. This is disclosed via `ExitOutcome.unreplayed`
           rather than silently approximated.

Same-bar ambiguity policy (both families): SAME_BAR_POLICY = "ADVERSE_SL_FIRST"
-- if a bar's H/L would touch both an adverse level (SL/trail stop) and a
favorable level (TP) within the same bar, the adverse fill is assumed first.
State transitions (BE arm, trail ratchet, giveback check) are evaluated at bar
close using that bar's close/peak; the resulting stop is then tested against
the NEXT bar's H/L. This lags live's intrabar wall-clock polling by up to one
bar -- disclosed via `exitSimGranularity`.
"""

from __future__ import annotations

from dataclasses import dataclass

SAME_BAR_POLICY = "ADVERSE_SL_FIRST"


@dataclass(frozen=True)
class ExitOutcome:
    outcome: str
    result_r: float
    exit_offset: int
    same_bar: bool = False
    reason: str | None = None
    unreplayed: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# FIXED family -- verbatim port of engine_a_v3/backtest.py::_simulate_exit
# ---------------------------------------------------------------------------


def simulate_fixed_exit(
    bars: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    exit_policy: str,
) -> ExitOutcome:
    risk = abs(entry - sl)
    if risk <= 0 or not bars:
        return ExitOutcome("INVALID", 0.0, 0)
    split = exit_policy == "SPLIT_50_50"
    tp1_filled = False
    # Split policy keeps the ORIGINAL stop on the runner half after TP1 fills:
    # half banked at TP1, half stopped at -1R.
    tp1_then_sl_r = 0.5 * abs(tp1 - entry) / risk - 0.5
    for offset, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        sl_hit = low <= sl if direction == "LONG" else high >= sl
        tp1_hit = high >= tp1 if direction == "LONG" else low <= tp1
        tp2_hit = high >= tp2 if direction == "LONG" else low <= tp2
        if sl_hit and ((not tp1_filled and tp1_hit) or (tp1_filled and tp2_hit)):
            return ExitOutcome(
                "SL" if not tp1_filled else "TP1_THEN_SL",
                -1.0 if not tp1_filled else tp1_then_sl_r,
                offset,
                True,
            )
        if sl_hit:
            return ExitOutcome(
                "SL" if not tp1_filled else "TP1_THEN_SL",
                -1.0 if not tp1_filled else tp1_then_sl_r,
                offset,
            )
        if not split and tp1_hit:
            return ExitOutcome("TP1", abs(tp1 - entry) / risk, offset)
        if split:
            if (tp1_filled and tp2_hit) or (not tp1_filled and tp2_hit):
                return ExitOutcome(
                    "TP1_TP2",
                    0.5 * abs(tp1 - entry) / risk + 0.5 * abs(tp2 - entry) / risk,
                    offset,
                )
            if tp1_hit:
                tp1_filled = True
    close = float(bars[-1]["close"])
    runner_r = ((close - entry) if direction == "LONG" else (entry - close)) / risk
    if split and tp1_filled:
        return ExitOutcome("TP1_TIMEOUT", 0.5 * abs(tp1 - entry) / risk + 0.5 * runner_r, len(bars) - 1)
    return ExitOutcome("TIMEOUT", runner_r, len(bars) - 1)


# ---------------------------------------------------------------------------
# MANAGED family -- mirrors timed_exit_monitor.py, params via shared config
# ---------------------------------------------------------------------------

_UNREPLAYED_MANAGED = (
    "pre_activation_profit_roundtrip",
    "wick_buffered_broker_sl",
    "scalp_profit_lock_ladder",
    "trail_indicator_confirmation",
    "hybrid_scaleout",
)


def get_timed_exit_cfg(config: dict | None = None) -> dict:
    """The exact merged TIMED_EXIT config the live monitor uses.

    Imports `timed_exit_monitor._get_timed_cfg` directly rather than
    reimplementing the merge -- any config change (defaults, per-style,
    per-venue overrides) is picked up identically by backtest and live with
    zero drift risk, at the cost of a live-module import.
    """
    from timed_exit_monitor import _get_timed_cfg

    if config is None:
        from config import CONFIG as config  # type: ignore[assignment]
    return _get_timed_cfg(lambda: config)


@dataclass
class _ManagedState:
    peak_r: float = 0.0
    trail_level: float | None = None
    be_armed: bool = False
    sl: float = 0.0


def _current_r(entry: float, sl: float, price: float, direction: str) -> float | None:
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return (price - entry) / risk if direction == "LONG" else (entry - price) / risk


def simulate_managed_exit(
    bars: list[dict],
    *,
    direction: str,
    entry: float,
    sl: float,
    style: str,
    tcfg: dict,
    trail_candles: list[dict] | None = None,
    trail_lookback: int = 20,
    venue: str | None = None,
) -> ExitOutcome:
    """Bar-close state machine mirroring timed_exit_monitor's core mechanics.

    `bars` are the primary simulation bars (post-entry). `trail_candles`, when
    given, are the trail_timeframe series used for the chandelier ATR level
    (falls back to `bars` itself when the style's trail timeframe matches the
    simulation timeframe and no separate series is supplied).
    """
    from timed_exit_monitor import (
        _activation_r_for,
        _giveback_budget_r_for,
        _trail_atr_mult_for,
    )
    from indicators import calc_atr

    risk = abs(entry - sl)
    if risk <= 0 or not bars:
        return ExitOutcome("INVALID", 0.0, 0, unreplayed=_UNREPLAYED_MANAGED)

    be_arm_map = tcfg.get("breakeven_arm_r") or {}
    be_arm_r = float(be_arm_map.get(style, be_arm_map.get("intraday", 0.6)))
    be_buffer_r = float(tcfg.get("breakeven_buffer_r", 0.05))
    be_min_profit_r = float(tcfg.get("breakeven_min_profit_r", 0.20))
    activation_r = _activation_r_for(tcfg, style)
    trail_mult = _trail_atr_mult_for(tcfg, style, venue)

    stag_cfg = tcfg.get("stagnation_exit") or {}
    stag_enabled = bool(stag_cfg.get("enabled"))
    stag_max_abs_r = float(stag_cfg.get("max_abs_r", 0.25))
    stag_frac = float(stag_cfg.get("fraction_of_max_hold", 0.5))
    stag_bars = int(len(bars) * stag_frac) if stag_frac > 0 else 0

    state = _ManagedState(sl=sl)
    trail_series = trail_candles if trail_candles is not None else bars

    for offset, bar in enumerate(bars):
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # 1. adverse check against the current live stop (SAME_BAR_POLICY: adverse first)
        stop_hit = low <= state.sl if direction == "LONG" else high >= state.sl
        if stop_hit:
            result_r = _current_r(entry, sl, state.sl, direction) or 0.0
            outcome = "TRAIL_STOP" if state.trail_level is not None else (
                "BE_STOP" if state.be_armed else "SL"
            )
            return ExitOutcome(outcome, result_r, offset, unreplayed=_UNREPLAYED_MANAGED)

        # 2. bar-close state update using this bar's close
        current_r = _current_r(entry, sl, close, direction)
        if current_r is None:
            continue

        if stag_enabled and current_r < activation_r and abs(current_r) <= stag_max_abs_r:
            if stag_bars > 0 and offset >= stag_bars:
                return ExitOutcome(
                    "STAGNATION", current_r, offset, reason="stagnation", unreplayed=_UNREPLAYED_MANAGED
                )

        if not state.be_armed and current_r >= be_arm_r and current_r >= be_min_profit_r:
            be_price = entry + be_buffer_r * risk if direction == "LONG" else entry - be_buffer_r * risk
            tightens = be_price > state.sl if direction == "LONG" else be_price < state.sl
            if tightens:
                state.sl = be_price
                state.be_armed = True

        if current_r > state.peak_r:
            state.peak_r = current_r

        if current_r >= activation_r:
            window = trail_series[max(0, offset - trail_lookback + 1) : offset + 1]
            if len(window) >= 2:
                highs = [float(c["high"]) for c in window]
                lows = [float(c["low"]) for c in window]
                closes = [float(c["close"]) for c in window]
                atr_series = calc_atr(highs, lows, closes, min(14, len(closes) - 1))
                atr_val = next((v for v in reversed(atr_series) if v is not None), None)
                if atr_val and atr_val > 0:
                    raw_trail = (
                        max(highs) - trail_mult * atr_val
                        if direction == "LONG"
                        else min(lows) + trail_mult * atr_val
                    )
                    if state.trail_level is not None:
                        raw_trail = (
                            max(raw_trail, state.trail_level)
                            if direction == "LONG"
                            else min(raw_trail, state.trail_level)
                        )
                    wrong_side = (
                        (direction == "LONG" and raw_trail >= close)
                        or (direction == "SHORT" and raw_trail <= close)
                    )
                    if not wrong_side:
                        state.trail_level = raw_trail
                        tightens = (
                            raw_trail > state.sl if direction == "LONG" else raw_trail < state.sl
                        )
                        if tightens:
                            state.sl = raw_trail

            giveback_r = _giveback_budget_r_for(tcfg, style, venue, state.peak_r)
            if giveback_r > 0 and (state.peak_r - current_r) >= giveback_r and state.peak_r >= activation_r:
                return ExitOutcome(
                    "PEAK_GIVEBACK", current_r, offset, reason="peak_giveback", unreplayed=_UNREPLAYED_MANAGED
                )

    close = float(bars[-1]["close"])
    result_r = _current_r(entry, sl, close, direction) or 0.0
    return ExitOutcome("TIMEOUT", result_r, len(bars) - 1, unreplayed=_UNREPLAYED_MANAGED)
