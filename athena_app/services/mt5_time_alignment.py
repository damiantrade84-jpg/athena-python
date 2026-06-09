"""MT5 OHLC timestamp alignment helpers for fetch_mt5().

Native MT5 `copy_rates*` bar times can be stamped in broker-/server-aligned grids.
When the newest bar is *severely stale* (illiquid exotics / closed sessions),
tick-based TZ fallback historically produced bogus multi-hour offsets and poisoned
downstream freshness; we clamp and refuse that path for dead feeds."""

from __future__ import annotations

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}

# Largest plausible timezone correction from tick clock skew (broker vs local), in seconds.
_MAX_ABS_TICK_TZ_SECONDS = 14 * 3600

# If the newest MT5 bar predates wall clock by this much, assume illiquid/stale —
# not solvable via tick TZ nudge — keep offset at 0 to avoid hallucinated series.
_DEFAULT_STALE_UNSHIFTED_AGE_SEC = int(36 * 3600)


def infer_mt5_rates_time_shift_seconds(
    timeframe: str,
    utc_now: float,
    newest_bar_epoch: float,
    broker_offset_hours: int,
    tick_epoch: float | None,
    *,
    stale_unshifted_age_sec: float | None = None,
) -> tuple[int, str]:
    """Return `(offset_seconds, reason_tag)` to subtract from each raw MT5 bar time.

    D1 normalization is intentionally **not** done here (`fetch_mt5` skips this for D1).

    Positive `offset_seconds` means: canonical UTC-aligned ``time = mt5_native - offset``.

    Reason tags:
    - ``utc_assumed_recent`` — newest bar is plausibly already on UTC-ish grid vs now
      (and the tick clock does not contradict it).
    - ``broker_offset_applied`` — shift by configured broker UTC offset made bar recent,
      or the tick clock confirmed broker-aligned stamps for a bar in the recent window.
    - ``stale_feed_skip_tick_tz`` — bar too old; refuse tick TZ (misleading shift).
    - ``tick_tz_out_of_band`` — inferred tick TZ exceeds clamp → treat as unreliable.
    - ``tick_inferred_tz`` — used rounded tick-vs-clock delta within clamp band.
    - ``no_tick_timezone_uncertain`` — ambiguous; keep 0 shift.
    """
    tf_upper = str(timeframe or "").upper()
    if tf_upper == "D1":
        return 0, "d1_shift_not_applied_in_fetch_mt5"

    tf_sec = float(_TF_SECONDS.get(tf_upper, 3600.0))
    cap = (
        float(stale_unshifted_age_sec)
        if stale_unshifted_age_sec is not None and stale_unshifted_age_sec > 0
        else float(_DEFAULT_STALE_UNSHIFTED_AGE_SEC)
    )

    broker_off = int(broker_offset_hours)
    broker_shift_s = float(broker_off * 3600)

    try:
        u_now = float(utc_now)
        newest = float(newest_bar_epoch)
    except (TypeError, ValueError):
        return 0, "no_tick_timezone_uncertain"

    unshifted_age = u_now - newest

    tick_t: float | None = None
    try:
        if tick_epoch is not None and float(tick_epoch) > 0:
            tick_t = float(tick_epoch)
    except (TypeError, ValueError):
        tick_t = None

    # Plausibly current forming bar on UTC-aligned native stamps
    if -300.0 <= unshifted_age <= 2.0 * tf_sec:
        # A broker-stamped (+offset) feed that lags by ~offset hours also lands in
        # this window, and treating its labels as UTC would relabel stale bars as
        # fresh — masking staleness from the freshness gates (observed 2026-06-09 on
        # forex H4 after a server restart). The live tick clock shares the bar
        # server clock: if its delta vs wall clock rounds to the configured broker
        # offset, the stamps are broker-aligned, so prefer the broker shift. This
        # only tightens freshness (stale bars stay visible); a genuinely UTC-stamped
        # feed has tick delta ~0 and keeps the unshifted path.
        if broker_off != 0 and tick_t is not None:
            tick_delta_hours = round((tick_t - u_now) / 3600.0)
            if tick_delta_hours == broker_off:
                return int(broker_shift_s), "broker_offset_applied"
        return 0, "utc_assumed_recent"

    shifted_age = u_now - (newest - broker_shift_s)
    if -300.0 <= shifted_age <= 2.0 * tf_sec:
        return int(broker_shift_s), "broker_offset_applied"

    if unshifted_age > cap:
        return 0, "stale_feed_skip_tick_tz"

    if tick_t is None:
        return 0, "no_tick_timezone_uncertain"

    diff_sec = tick_t - u_now
    offset_hours_rounded = round(diff_sec / 3600.0)
    cand = int(offset_hours_rounded * 3600)
    if abs(cand) > _MAX_ABS_TICK_TZ_SECONDS:
        return 0, "tick_tz_out_of_band"
    return cand, "tick_inferred_tz"
