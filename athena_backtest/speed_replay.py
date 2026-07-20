"""Historical SpeedState replay for timeframe-policy parity.

Live Engine B resolves its timeframe roles with a pinned ``SpeedState``
(`scanner._resolve_scan_speed_state` -> `timeframe_policy.calculate_speed_state`),
chained bar-to-bar via ``previous=``. The old backtester never replayed this,
which was a disclosed parity gap. This module rebuilds the same chain from
confirmed historical bars only -- ``calculate_speed_state`` never reads wall
time, so the chain is fully deterministic for a fixed candle set.

Mirrors the live call shape (baseline policy classes + per-group thresholds
from config) with the live-only inputs that cannot be replayed left at their
"unavailable" defaults (spread, quote age, session, scheduled events); the
policy engine already degrades those deterministically via its ``missing``
accounting. Bar windows are trailing slices (H1x400 / M15x500) matching the
bounded fetch depth live hands the calculator, not full history.

States are computed lazily per closed-H1 epoch and memoized: the state only
changes when a new H1 bar closes, so trigger-TF bars between H1 closes share
one state object (same behavior as live's per-refresh pinning).
"""

from __future__ import annotations

import bisect
from typing import Any

_H1_WINDOW = 400
_M15_WINDOW = 500
# Chain warmup before the first requested state. Live's chain also starts
# cold (previous=None) from wherever the process booted; anchoring a fixed
# warmup before the first decision bar keeps the replay deterministic for a
# given backtest window without paying an O(full-history) chain build.
_CHAIN_WARMUP = 500


class SpeedStateReplay:
    def __init__(self, pair: dict, h1_rows: list[dict], m15_rows: list[dict]):
        self._pair = dict(pair)
        self._h1 = list(h1_rows or [])
        self._m15 = list(m15_rows or [])
        self._h1_epochs = [self._epoch(row) for row in self._h1]
        self._m15_epochs = [self._epoch(row) for row in self._m15]
        self._states: dict[int, Any] = {}
        self._chain_tip_index: int | None = None
        self._chain_tip_state: Any = None
        self._static = None  # lazily resolved thresholds/baselines

    @staticmethod
    def _epoch(row: dict) -> int:
        value = row.get("time")
        if isinstance(value, (int, float)):
            return int(value)
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return 0

    def _static_inputs(self) -> dict:
        if self._static is None:
            from config import CONFIG
            from engine_a_groups import resolve_score_group_by_type
            from timeframe_policy import (
                baseline_liquidity_for_group,
                resolve_timeframe_policy,
                thresholds_for_group,
            )

            score_group = resolve_score_group_by_type(self._pair)
            speed_thresholds, liquidity_thresholds = thresholds_for_group(CONFIG, score_group)
            baseline_policy = resolve_timeframe_policy(
                str(self._pair.get("display") or self._pair.get("symbol") or ""),
                self._pair.get("type", ""),
                score_group,
                "intraday",
            )
            self._static = {
                "baseline_speed_class": baseline_policy.baseline_speed_class,
                "baseline_liquidity_class": baseline_liquidity_for_group(CONFIG, score_group),
                "thresholds": speed_thresholds,
                "liquidity_thresholds": liquidity_thresholds,
            }
        return self._static

    def state_at(self, last_closed_h1_index: int) -> Any:
        """SpeedState using H1 bars up to and including `last_closed_h1_index`.

        Chained from the previous state exactly as live does; walking forward
        monotonically is O(window) per new H1 bar.
        """
        if last_closed_h1_index < 0 or last_closed_h1_index >= len(self._h1):
            return None
        cached = self._states.get(last_closed_h1_index)
        if cached is not None:
            return cached

        from timeframe_policy import calculate_speed_state

        start = (
            max(0, last_closed_h1_index - _CHAIN_WARMUP)
            if self._chain_tip_index is None
            else self._chain_tip_index + 1
        )
        if last_closed_h1_index < start:
            # Out-of-order lookup (shouldn't happen in a forward replay):
            # rebuild that point without disturbing the forward chain.
            start = last_closed_h1_index

        static = self._static_inputs()
        previous = self._chain_tip_state if self._chain_tip_index is not None else None
        state = previous
        for h1_index in range(start, last_closed_h1_index + 1):
            h1_epoch = self._h1_epochs[h1_index]
            h1_confirmed = self._h1[max(0, h1_index + 1 - _H1_WINDOW) : h1_index + 1]
            m15_end = bisect.bisect_right(self._m15_epochs, h1_epoch)
            m15_confirmed = self._m15[max(0, m15_end - _M15_WINDOW) : m15_end]
            state = calculate_speed_state(
                h1_confirmed,
                m15_confirmed,
                previous=state,
                last_closed_h1_open_time=h1_epoch or None,
                gap_status="normal",
                baseline_speed_class=static["baseline_speed_class"],
                baseline_liquidity_class=static["baseline_liquidity_class"],
                thresholds=static["thresholds"],
                liquidity_thresholds=static["liquidity_thresholds"],
            )
            self._states[h1_index] = state
        if self._chain_tip_index is None or last_closed_h1_index > self._chain_tip_index:
            self._chain_tip_index = last_closed_h1_index
            self._chain_tip_state = state
        return state
