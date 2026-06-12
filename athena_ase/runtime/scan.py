"""ASE runtime scan — Layer 1 candidates + predict_batch (shadow, no Engine C)."""

from __future__ import annotations

import logging
import time
from typing import Any

from athena_ase.contracts import ASESignal
from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.horizon import Horizon
from athena_ase.inference.predict import predict_batch, predict_no_candidate
from athena_ase.instruments import DEFAULT_INSTRUMENTS, Instrument, instrument_by_symbol, instruments_for_family
from athena_ase.registry.promotion import list_family_states
from athena_ase.shadow.journal import append_shadow_signals
from athena_ase.signals.arbitrate import Candidate
from athena_ase.signals.common import BarSeries, bar_index_at_decision, load_bar_series
from athena_ase.signals.engine import iter_candidates
from athena_ase.signals.arbitrate import FiredSignal, arbitrate
from athena_ase.signals.carry import compute_carry
from athena_ase.signals.meanrev import compute_meanrev
from athena_ase.signals.tsmom import compute_tsmom
from athena_ase.signals.xsec import compute_xsec

log = logging.getLogger("ase.runtime.scan")


def _latest_candidate(
    store: PTISStore,
    instrument: Instrument,
    horizon: Horizon,
    decision_time_ms: int,
    family_bars: dict[str, BarSeries],
) -> Candidate | None:
    lookback_ms = 180 * 24 * 3600 * 1000
    series = load_bar_series(store, instrument.symbol, horizon, decision_time_ms - lookback_ms, decision_time_ms)
    if series is None:
        return None
    idx = bar_index_at_decision(series, decision_time_ms)
    if idx is None:
        return None
    sig1 = float(series.sigma_bar[idx])
    if sig1 != sig1 or sig1 <= 0:
        return None

    tsm = compute_tsmom(series.close_log, series.sigma_bar, idx, horizon)
    fired: list[FiredSignal] = [FiredSignal("tsmom", tsm.direction, tsm.raw_strength)]
    if horizon == "swing":
        cr = compute_carry(store, instrument, decision_time_ms, sig1)
        fired.append(FiredSignal("carry", cr.direction, cr.raw_strength))
        if instrument.family in ("equity", "crypto", "index_etf"):
            xs = compute_xsec(instrument, family_bars, decision_time_ms, horizon)
            if not xs.disabled:
                fired.append(FiredSignal("xsec", xs.direction, xs.raw_strength))
    if horizon == "intraday" and instrument.family == "forex":
        mr = compute_meanrev(instrument, series.close_log, idx, tsm.blend)
        fired.append(FiredSignal("meanrev", mr.direction, mr.raw_strength))

    return arbitrate(
        instrument,
        horizon,
        decision_time_ms,
        idx,
        float(series.close_log[idx]),
        sig1,
        fired,
    )


def generate_live_candidates(
    store: PTISStore,
    instruments: tuple[Instrument, ...],
    *,
    horizon: Horizon,
    decision_time_ms: int | None = None,
) -> list[Candidate]:
    now_ms = decision_time_ms or int(time.time() * 1000)
    family_cache: dict[str, dict[str, BarSeries]] = {}
    out: list[Candidate] = []

    for inst in instruments:
        if inst.swing_only and horizon == "intraday":
            continue
        if horizon == "intraday" and inst.family not in ("forex", "crypto", "commodity"):
            if inst.family == "equity" and inst.subclass == "jse":
                continue
        if inst.family not in family_cache:
            family_cache[inst.family] = {}
            for peer in instruments_for_family(inst.family):
                lookback_ms = 180 * 24 * 3600 * 1000
                bs = load_bar_series(store, peer.symbol, horizon, now_ms - lookback_ms, now_ms)
                if bs is not None:
                    family_cache[inst.family][peer.symbol] = bs
        cand = _latest_candidate(store, inst, horizon, now_ms, family_cache.get(inst.family, {}))
        if cand is not None:
            out.append(cand)
    return out


def run_ase_scan(
    *,
    family: str | None = None,
    horizon: Horizon = "intraday",
    symbols: list[str] | None = None,
    write_journal: bool = True,
    ptis_root: str | None = None,
) -> dict[str, Any]:
    store = PTISStore(ptis_root or default_ptis_root())
    if symbols:
        instruments: tuple[Instrument, ...] = tuple(
            inst for sym in symbols if (inst := instrument_by_symbol(sym)) is not None
        )
    elif family:
        instruments = tuple(instruments_for_family(family))  # type: ignore[arg-type]
    else:
        instruments = DEFAULT_INSTRUMENTS

    candidates = generate_live_candidates(store, instruments, horizon=horizon)
    inst_map = {i.symbol: i for i in instruments}
    signals = predict_batch(candidates, store, inst_map)

    # Instruments without candidates → FLAT rows for visibility
    seen = {c.instrument for c in candidates}
    for inst in instruments:
        if inst.symbol not in seen:
            signals.append(predict_no_candidate(inst, horizon))

    if write_journal:
        append_shadow_signals([s for s in signals if s.decisionStatus in ("TRADE", "WATCH")])

    return {
        "success": True,
        "horizon": horizon,
        "family": family or "all",
        "candidateCount": len(candidates),
        "signalCount": len(signals),
        "deployment": list_family_states(),
        "signals": [s.to_dict() for s in signals],
    }


def run_ase_backfill_scan(
    store: PTISStore,
    *,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
    instruments: tuple[Instrument, ...] | None = None,
) -> list[ASESignal]:
    """Historical candidate stream for parity checks."""
    cands = list(
        iter_candidates(store, instruments, horizon=horizon, start_ms=start_ms, end_ms=end_ms)
    )
    inst_map = {i.symbol: i for i in (instruments or DEFAULT_INSTRUMENTS)}
    return predict_batch(cands, store, inst_map)
