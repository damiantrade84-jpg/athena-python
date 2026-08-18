"""TCI - Temporal Conviction Index.

Intraday markets are not stationary across the clock. The same setup that pays
during the London/NY overlap is close to a coin flip at 03:00 UTC, when the
book is thin, ranges are compressed and any break is likely to be revisited.
Systems that ignore this pay full transaction costs to trade noise for a third
of every day.

TCI learns two quantities per time-of-day bucket, from the instrument's own
history rather than from a hardcoded session table:

    activity     typical absolute move in that bucket, relative to the day
    continuation P(next bar continues the current bar's sign) in that bucket

and multiplies them into a single conditioning gain. It is deliberately NOT a
directional signal - it never says which way to trade, only how much the clock
supports acting at all. Buckets with too few samples fall back to neutral
rather than inventing an edge from four observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

import opus.config as config
from opus.indicators.base import MarketContext, TF_SECONDS


@dataclass
class TemporalState:
    """Conditioning gain from the clock."""

    gain: float                # multiplier applied to conviction
    bucket: int                # minute-of-day bucket index
    activity: float            # relative to the instrument's own daily mean
    continuation: float        # P(sign persists) in this bucket
    samples: int
    fallback: bool = False

    def as_dict(self) -> dict:
        return {
            "gain": round(float(self.gain), 4),
            "bucket": int(self.bucket),
            "bucketLabel": _bucket_label(self.bucket),
            "activity": round(float(self.activity), 4),
            "continuation": round(float(self.continuation), 4),
            "samples": int(self.samples),
            "fallback": bool(self.fallback),
        }


def _bucket_label(bucket: int) -> str:
    minutes = int(bucket) * int(config.indicator("TCI")["bucket_minutes"])
    return f"{minutes // 60:02d}:{minutes % 60:02d}Z"


def _bucket_of(ts: float, bucket_minutes: int) -> int:
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return (dt.hour * 60 + dt.minute) // bucket_minutes


def compute(ctx: MarketContext) -> TemporalState:
    cfg = config.indicator("TCI")
    bucket_minutes = int(cfg["bucket_minutes"])
    min_samples = int(cfg["min_samples"])
    candles = ctx.structure
    n = len(candles)

    tf_sec = TF_SECONDS.get(candles.timeframe.upper(), 900)
    max_bars = int(float(cfg["history_days"]) * 86400 / max(tf_sec, 1))
    start = max(0, n - max_bars)
    close = candles.close[start:]
    ts = candles.ts[start:]
    m = close.shape[0]

    now_ts = ctx.now if ctx.now > 0 else (float(ts[-1]) if m else 0.0)
    current_bucket = _bucket_of(now_ts, bucket_minutes) if now_ts > 0 else 0

    if m < max(30, min_samples * 2):
        return TemporalState(1.0, current_bucket, 1.0, 0.5, 0, fallback=True)

    rets = np.diff(np.log(np.where(close > 0, close, np.nan)))
    valid = np.isfinite(rets)
    if int(valid.sum()) < min_samples * 2:
        return TemporalState(1.0, current_bucket, 1.0, 0.5, 0, fallback=True)

    # rets[i] is the move INTO bar i+1, so bucket it by that bar's timestamp.
    buckets = np.array(
        [_bucket_of(float(t), bucket_minutes) for t in ts[1:]], dtype=int
    )
    abs_rets = np.abs(rets)
    global_activity = float(np.nanmean(abs_rets[valid]))
    if not np.isfinite(global_activity) or global_activity <= 0:
        return TemporalState(1.0, current_bucket, 1.0, 0.5, 0, fallback=True)

    mask = (buckets == current_bucket) & valid
    samples = int(mask.sum())
    if samples < min_samples:
        return TemporalState(
            1.0, current_bucket, 1.0, 0.5, samples, fallback=True
        )

    raw_activity = float(np.nanmean(abs_rets[mask]) / global_activity)

    # Continuation: of the moves in this bucket, how often did the NEXT move
    # keep the same sign? Above 0.5 means follow-through, below means the
    # bucket mean-reverts and breakouts there are traps.
    idx = np.flatnonzero(mask)
    idx = idx[idx + 1 < rets.shape[0]]
    raw_continuation = 0.5
    pair_samples = 0
    if idx.shape[0] >= min_samples:
        s0 = np.sign(rets[idx])
        s1 = np.sign(rets[idx + 1])
        both = (s0 != 0) & (s1 != 0)
        pair_samples = int(both.sum())
        if pair_samples > 0:
            raw_continuation = float(np.mean(s0[both] == s1[both]))

    # Shrink both estimates toward their null values by sample size. A bucket
    # with 16 observations routinely produces a continuation rate of 0.19 or
    # 0.81 from pure noise; taken at face value that becomes a large, entirely
    # spurious gain. Shrinkage means a bucket has to earn its multiplier with
    # sample count, and the estimate degrades gracefully to neutral instead of
    # switching off at an arbitrary min_samples cliff.
    n0 = float(cfg.get("shrinkage_n0", 40))
    continuation = (pair_samples * raw_continuation + n0 * 0.5) / (pair_samples + n0)
    activity = (samples * raw_activity + n0 * 1.0) / (samples + n0)

    # Gain: activity scaled and floored, tilted by follow-through. The floor
    # stops a dead bucket from zeroing an otherwise valid thesis outright -
    # that is the regime layer's job, not the clock's.
    floor = float(cfg["activity_floor"])
    activity_gain = float(np.clip(activity, floor, 1.75))
    cont_scale = float(cfg["continuation_scale"])
    cont_gain = 1.0 + (continuation - 0.5) / max(cont_scale, 1e-6) * 0.25
    cont_gain = float(np.clip(cont_gain, 0.6, 1.4))

    gain = float(np.clip(activity_gain * cont_gain, floor, 1.85))
    return TemporalState(
        gain=gain,
        bucket=current_bucket,
        activity=activity,
        continuation=continuation,
        samples=samples,
        fallback=False,
    )


__all__ = ["TemporalState", "compute"]
