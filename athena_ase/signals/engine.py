"""Layer 1 candidate generation over PTIS bar series."""

from __future__ import annotations

import logging
from typing import Iterator

from athena_ase.data.ptis import PTISStore
from athena_ase.features.build import vol_regime_ordinal
from athena_ase.horizon import Horizon, HORIZONS, is_intraday
from athena_ase.instruments import DEFAULT_INSTRUMENTS, Instrument, instruments_for_family
from athena_ase.settings import (
    intraday_families,
    layer1_vol_regime_extreme_ordinal,
    layer1_vol_regime_filter_enabled,
    layer1_vol_regime_min_strength,
)
from athena_ase.signals.arbitrate import Candidate, EventSpacingFilter, FiredSignal, arbitrate
from athena_ase.signals.carry import compute_carry
from athena_ase.signals.common import BarSeries, bar_index_at_decision, load_bar_series
from athena_ase.signals.meanrev import compute_meanrev
from athena_ase.signals.tsmom import compute_tsmom
from athena_ase.signals.xsec import compute_xsec

log = logging.getLogger("ase.signals.engine")


def _family_bar_cache(
    store: PTISStore,
    family: str,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
) -> dict[str, BarSeries]:
    out: dict[str, BarSeries] = {}
    for inst in instruments_for_family(family):  # type: ignore[arg-type]
        series = load_bar_series(store, inst.symbol, horizon, start_ms, end_ms)
        if series is not None:
            out[inst.symbol] = series
    return out


def _vol_regime_pass(series: BarSeries, idx: int, fired: list[FiredSignal]) -> bool:
    if not layer1_vol_regime_filter_enabled():
        return True
    sig = float(series.sigma_bar[idx])
    sig_l = float(series.sigma_long[idx])
    if sig != sig or sig_l != sig_l or sig_l <= 0:
        return True
    regime = vol_regime_ordinal(sig / sig_l)
    extreme = layer1_vol_regime_extreme_ordinal()
    if regime != extreme:
        return True
    max_strength = max((s.raw_strength for s in fired if s.direction != 0), default=0.0)
    return max_strength >= layer1_vol_regime_min_strength()


def iter_candidates(
    store: PTISStore,
    instruments: tuple[Instrument, ...] | None = None,
    *,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
) -> Iterator[Candidate]:
    cfg = HORIZONS[horizon]
    spacing = EventSpacingFilter()
    universe = instruments or DEFAULT_INSTRUMENTS
    family_cache: dict[str, dict[str, BarSeries]] = {}
    # Read at call time, not import time: config changes in a long-running
    # process must not require a module reload to take effect.
    intraday_allowed = intraday_families()

    for inst in universe:
        if inst.swing_only and is_intraday(horizon):
            continue
        # Intraday Layer-1 is limited to configured families (default forex/crypto/commodity).
        # Equity and index_etf use swing path (carry + xsec).
        if is_intraday(horizon) and inst.family not in intraday_allowed:
            continue
        series = load_bar_series(store, inst.symbol, horizon, start_ms, end_ms)
        if series is None or len(series.value_time) < max(cfg.tsmom_lookbacks) + 5:
            continue

        if inst.family not in family_cache:
            family_cache[inst.family] = _family_bar_cache(
                store, inst.family, horizon, start_ms, end_ms
            )

        for i in range(len(series.value_time)):
            decision_time_ms = int(series.available_time[i])
            if decision_time_ms < start_ms or decision_time_ms > end_ms:
                continue
            idx = bar_index_at_decision(series, decision_time_ms)
            if idx is None or idx != i:
                continue
            sig1 = float(series.sigma_bar[idx])
            if sig1 != sig1 or sig1 <= 0:
                continue

            tsm = compute_tsmom(series.close_log, series.sigma_bar, idx, horizon)
            fired: list[FiredSignal] = [
                FiredSignal("tsmom", tsm.direction, tsm.raw_strength),
            ]
            if horizon == "swing":
                cr = compute_carry(store, inst, decision_time_ms, sig1)
                fired.append(FiredSignal("carry", cr.direction, cr.raw_strength))
                if inst.family in ("equity", "crypto", "index_etf"):
                    xs = compute_xsec(
                        inst,
                        family_cache.get(inst.family, {}),
                        decision_time_ms,
                        horizon,
                    )
                    if not xs.disabled:
                        fired.append(FiredSignal("xsec", xs.direction, xs.raw_strength))
            if is_intraday(horizon) and inst.family == "forex":
                mr = compute_meanrev(inst, series.close_log, idx, tsm.blend)
                fired.append(FiredSignal("meanrev", mr.direction, mr.raw_strength))

            if not _vol_regime_pass(series, idx, fired):
                continue

            cand = arbitrate(
                inst,
                horizon,
                decision_time_ms,
                idx,
                float(series.close_log[idx]),
                sig1,
                fired,
            )
            if cand is None:
                continue
            if not spacing.allow(cand):
                continue
            spacing.record(cand)
            yield cand
