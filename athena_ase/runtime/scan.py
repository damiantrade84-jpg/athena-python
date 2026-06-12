"""ASE runtime scan — Layer 1 candidates + predict_batch + demo execution bridge."""

from __future__ import annotations

import logging
import time
from typing import Any

from athena_ase.contracts import ASESignal
from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.execution.bridge import (
    ASEExecutionDeps,
    default_execution_deps,
    enforce_time_stops,
    execute_trade_signal,
)
from athena_ase.execution.journal import append_trade_signals, trade_journal_summary
from athena_ase.horizon import Horizon
from athena_ase.inference.predict import predict_batch, predict_no_candidate
from athena_ase.instruments import DEFAULT_INSTRUMENTS, Instrument, instrument_by_symbol, instruments_for_family
from athena_ase.runtime.health import scan_diagnostics
from athena_ase.signals.arbitrate import Candidate, FiredSignal, arbitrate
from athena_ase.signals.carry import compute_carry
from athena_ase.signals.common import BarSeries, bar_index_at_decision, load_bar_series
from athena_ase.signals.engine import iter_candidates
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


def _pair_meta_for_symbol(symbol: str) -> dict[str, Any] | None:
    inst = instrument_by_symbol(symbol)
    if inst is None:
        return None
    asset_type = {
        "forex": "forex",
        "crypto": "crypto",
        "commodity": "commodity",
        "equity": "stock",
        "index_etf": "etf",
    }.get(inst.family, "forex")
    return {
        "symbol": inst.symbol,
        "display": inst.display,
        "type": asset_type,
        "source": "bybit" if inst.family == "crypto" else "mt5",
    }


def run_ase_scan(
    *,
    family: str | None = None,
    horizon: Horizon = "intraday",
    symbols: list[str] | None = None,
    write_journal: bool = True,
    execute_trades: bool = False,
    ptis_root: str | None = None,
    execution_deps: ASEExecutionDeps | None = None,
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

    seen = {c.instrument for c in candidates}
    now_ms = int(time.time() * 1000)
    for inst in instruments:
        if inst.symbol not in seen:
            signals.append(
                predict_no_candidate(
                    inst,
                    horizon,
                    store=store,
                    decision_time_ms=now_ms,
                )
            )

    journal_error: str | None = None
    if write_journal:
        try:
            append_trade_signals(signals)
        except Exception as exc:
            journal_error = str(exc)
            log.warning("ASE trade journal write failed (scan continues): %s", exc)

    executions: list[dict[str, Any]] = []
    if execute_trades:
        deps = execution_deps or default_execution_deps()
        for sig in signals:
            if sig.decisionStatus != "TRADE":
                continue
            pair = _pair_meta_for_symbol(sig.instrument)
            try:
                executions.append(
                    execute_trade_signal(
                        sig,
                        pair=pair,
                        deps=deps,
                        write_journal=False,
                        journal_outcomes=write_journal,
                    )
                )
            except Exception as exc:
                log.warning("ASE execution failed for %s: %s", sig.instrument, exc)
                executions.append({"executed": False, "reason": str(exc), "instrument": sig.instrument})

    status_counts: dict[str, int] = {}
    for sig in signals:
        status_counts[sig.decisionStatus] = status_counts.get(sig.decisionStatus, 0) + 1

    payload: dict[str, Any] = {
        "success": True,
        "horizon": horizon,
        "family": family or "all",
        "candidateCount": len(candidates),
        "signalCount": len(signals),
        "instrumentCount": len(instruments),
        "statusCounts": status_counts,
        "deployment": "OPERATIONAL",
        "diagnostics": scan_diagnostics(store, horizon=horizon),
        "signals": [s.to_dict() for s in signals],
        "executions": executions,
        "journal": trade_journal_summary(),
    }
    if journal_error:
        payload["journalError"] = journal_error
    return payload


def run_ase_dual_horizon_scan(
    *,
    family: str | None = None,
    symbols: list[str] | None = None,
    write_journal: bool = True,
    execute_trades: bool = False,
    ptis_root: str | None = None,
    execution_deps: ASEExecutionDeps | None = None,
) -> dict[str, Any]:
    """Scan all instruments on intraday and swing horizons."""
    horizons: tuple[Horizon, ...] = ("intraday", "swing")
    by_horizon: dict[str, Any] = {}
    all_signals: list[dict[str, Any]] = []
    all_executions: list[dict[str, Any]] = []
    for horizon in horizons:
        result = run_ase_scan(
            family=family,
            horizon=horizon,
            symbols=symbols,
            write_journal=write_journal,
            execute_trades=execute_trades,
            ptis_root=ptis_root,
            execution_deps=execution_deps,
        )
        by_horizon[horizon] = result
        all_signals.extend(result.get("signals") or [])
        all_executions.extend(result.get("executions") or [])

    status_counts: dict[str, int] = {}
    for row in all_signals:
        status = str(row.get("decisionStatus") or "FLAT")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "success": True,
        "family": family or "all",
        "horizons": list(horizons),
        "signalCount": len(all_signals),
        "statusCounts": status_counts,
        "deployment": "OPERATIONAL",
        "byHorizon": by_horizon,
        "signals": all_signals,
        "executions": all_executions,
        "journal": trade_journal_summary(),
    }


def run_ase_full_scan_and_execute(
    *,
    ptis_root: str | None = None,
    execution_deps: ASEExecutionDeps | None = None,
) -> dict[str, Any]:
    """Full-universe dual-horizon scan with TRADE execution and time-stop refresh."""
    deps = execution_deps or default_execution_deps()
    payload = run_ase_dual_horizon_scan(
        write_journal=True,
        execute_trades=True,
        ptis_root=ptis_root,
        execution_deps=deps,
    )
    closed: list[dict[str, Any]] = []
    for venue in ("mt5", "bybit"):
        positions, _raw = deps.get_positions(venue)
        closed.extend(enforce_time_stops(positions, deps=deps))
    payload["timeStopsClosed"] = closed
    return payload


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
