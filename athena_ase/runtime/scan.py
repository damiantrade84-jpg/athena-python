"""ASE runtime scan — Layer 1 candidates + predict_batch + demo execution bridge."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable

from athena_ase.contracts import ASESignal
from athena_ase.data.ingest.runner import run_ingest
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
from athena_ase.signals.common import (
    BarSeries,
    bar_index_at_decision,
    live_bars_stale,
    load_bar_series,
)
from athena_ase.signals.engine import iter_candidates
from athena_ase.signals.meanrev import compute_meanrev
from athena_ase.signals.tsmom import compute_tsmom
from athena_ase.signals.xsec import compute_xsec

log = logging.getLogger("ase.runtime.scan")

DEFAULT_SCAN_INGEST_SOURCES = ("mt5_live", "bybit")
# Scan-time ingest is a freshness *top-up*, not a historical backfill. Bybit
# OI/funding history is fetched over the network and its cache key churns with
# wall-clock now(), so a wide window re-fetches thousands of paginated requests
# on every scan. Keep the scan-time window small; the scheduled/manual ingest
# job still backfills the full history via run_ingest's default lookback.
DEFAULT_SCAN_BYBIT_LOOKBACK_DAYS = 3
# Hard wall-clock budget so a slow/hung feed can never stall a scan. On overrun
# the scan proceeds with existing PTIS data (fail-open).
DEFAULT_SCAN_INGEST_BUDGET_S = 45.0


def _attach_lower_tf_shadow_ase(
    signals: list[Any],
    signal_dicts: list[dict[str, Any]],
    lower_tf_fetch: Callable[..., Any] | None,
) -> None:
    """Attach DIAGNOSTIC-ONLY lower-TF (M30/M15/M5) shadow context per signal.

    No-op (output unchanged) unless ``LOWER_TF_SHADOW`` is enabled for "ase".
    The shadow NEVER affects ASE decisionStatus, direction, SL/TP, or any
    pass/block gate — it is reported on the signal dict only. When no lower-TF
    fetcher is supplied, the shadow is reported as BLOCKED (never falls back to
    H1 or to ASE's own D1/H4/H1 candidates).
    """
    try:
        from lower_tf_shadow import (
            compute_lower_tf_shadow,
            lower_tf_shadow_enabled,
        )

        if not lower_tf_shadow_enabled("ase"):
            return
    except Exception as exc:  # pragma: no cover - import guard
        log.debug("ASE lower_tf_shadow unavailable: %s", exc)
        return

    cache: dict[str, dict[str, Any]] = {}
    for sig, d in zip(signals, signal_dicts):
        symbol = getattr(sig, "instrument", "") or d.get("instrument", "")
        direction = getattr(sig, "direction", None) or d.get("direction")
        cache_key = f"{symbol}|{direction}"
        if cache_key not in cache:
            pair = _pair_meta_for_symbol(symbol)
            try:
                cache[cache_key] = compute_lower_tf_shadow(
                    pair=pair or {"symbol": symbol, "display": symbol},
                    source=(pair or {}).get("source"),
                    fetch_candles=lower_tf_fetch,
                    direction=direction,
                    component="ase",
                )
            except Exception as exc:  # pragma: no cover - belt
                log.debug("ASE lower_tf_shadow failed for %s: %s", symbol, exc)
                cache[cache_key] = {
                    "lower_tf_status": "BLOCKED",
                    "diagnostic_only": True,
                    "lower_tf_notes": f"shadow error: {exc}",
                }
        d["lowerTfShadow"] = cache[cache_key]


def _maybe_ingest(
    store: PTISStore,
    sources: Iterable[str] | None,
    *,
    bybit_lookback_days: int = DEFAULT_SCAN_BYBIT_LOOKBACK_DAYS,
    bybit_symbols: list[str] | None = None,
    budget_s: float = DEFAULT_SCAN_INGEST_BUDGET_S,
) -> dict[str, Any] | None:
    """Refresh PTIS before a scan. Fail-open: log and return error metadata on failure.

    Runs in a daemon worker bounded by ``budget_s`` so a slow or hung feed can
    never block the scan; on timeout the scan continues with existing data.
    """
    if sources is None:
        return None
    selected = tuple(sources)
    if not selected:
        return None

    import threading

    outcome: dict[str, Any] = {}

    def _run() -> None:
        try:
            ingest_kwargs: dict[str, Any] = {
                "store": store,
                "sources": selected,
                "bybit_lookback_days": bybit_lookback_days,
                "write_audit": False,
            }
            if bybit_symbols is not None:
                ingest_kwargs["bybit_symbols"] = bybit_symbols
            outcome["result"] = run_ingest(
                **ingest_kwargs,
            )
        except BaseException as exc:  # noqa: BLE001 - fail-open; legacy config
            # validation raises a SystemExit subclass, which must not kill the
            # ingest worker silently either.
            outcome["error"] = str(exc)

    worker = threading.Thread(target=_run, name="ase-scan-ingest", daemon=True)
    worker.start()
    worker.join(timeout=budget_s)
    if worker.is_alive():
        log.warning(
            "ASE ingest exceeded %.0fs budget (scan continues with existing data)",
            budget_s,
        )
        return {"timeout": True, "budgetSeconds": budget_s}
    if "error" in outcome:
        log.warning("ASE ingest failed (scan continues): %s", outcome["error"])
        return {"error": outcome["error"]}
    return {"result": outcome.get("result")}


def _resolve_instruments(
    *,
    family: str | None = None,
    symbols: list[str] | None = None,
) -> tuple[Instrument, ...]:
    if symbols:
        return tuple(inst for sym in symbols if (inst := instrument_by_symbol(sym)) is not None)
    if family:
        return tuple(instruments_for_family(family))  # type: ignore[arg-type]
    return DEFAULT_INSTRUMENTS


def _scan_ingest_sources_for_instruments(
    sources: Iterable[str] | None,
    instruments: tuple[Instrument, ...],
) -> tuple[str, ...] | None:
    if sources is None:
        return None
    selected = tuple(sources)
    if selected != DEFAULT_SCAN_INGEST_SOURCES:
        return selected
    if instruments and not any(inst.family == "crypto" for inst in instruments):
        return ("mt5_live",)
    if instruments and all(inst.family == "crypto" for inst in instruments):
        # Crypto-only scans skip the MT5 leg but keep Bybit: klines are the
        # sole live crypto OHLC path into PTIS (the Binance backtest-cache
        # ingest goes stale whenever the app stops backfilling).
        return ("bybit",)
    return selected


def _bybit_symbols_for_scan(instruments: tuple[Instrument, ...]) -> list[str] | None:
    symbols = [inst.symbol for inst in instruments if inst.family == "crypto"]
    return symbols or None


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
    # Fail closed on dead feeds: signals computed from bars weeks old are
    # fiction even though every per-bar computation still "works".
    if live_bars_stale(series, horizon, decision_time_ms):
        log.debug("ASE stale bars for %s/%s — candidate suppressed", instrument.symbol, horizon)
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
    ingest_sources: Iterable[str] | None = DEFAULT_SCAN_INGEST_SOURCES,
    lower_tf_fetch: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    store = PTISStore(ptis_root or default_ptis_root())
    instruments = _resolve_instruments(family=family, symbols=symbols)
    ingest_sources_selected = _scan_ingest_sources_for_instruments(ingest_sources, instruments)
    ingest_result = _maybe_ingest(
        store,
        ingest_sources_selected,
        bybit_symbols=(
            _bybit_symbols_for_scan(instruments)
            if ingest_sources_selected and "bybit" in ingest_sources_selected
            else None
        ),
    )

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

    signal_dicts = [s.to_dict() for s in signals]
    # Diagnostic-only lower-TF shadow context (default OFF; no-op when disabled).
    _attach_lower_tf_shadow_ase(signals, signal_dicts, lower_tf_fetch)

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
        "signals": signal_dicts,
        "executions": executions,
        "journal": trade_journal_summary(),
    }
    if ingest_result is not None:
        payload["ingestResult"] = ingest_result
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
    ingest_sources: Iterable[str] | None = DEFAULT_SCAN_INGEST_SOURCES,
) -> dict[str, Any]:
    """Scan all instruments on intraday and swing horizons."""
    horizons: tuple[Horizon, ...] = ("intraday", "swing")
    store = PTISStore(ptis_root or default_ptis_root())
    instruments = _resolve_instruments(family=family, symbols=symbols)
    ingest_sources_selected = _scan_ingest_sources_for_instruments(ingest_sources, instruments)
    ingest_result = _maybe_ingest(
        store,
        ingest_sources_selected,
        bybit_symbols=(
            _bybit_symbols_for_scan(instruments)
            if ingest_sources_selected and "bybit" in ingest_sources_selected
            else None
        ),
    )

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
            ingest_sources=None,
        )
        by_horizon[horizon] = result
        all_signals.extend(result.get("signals") or [])
        all_executions.extend(result.get("executions") or [])

    status_counts: dict[str, int] = {}
    for row in all_signals:
        status = str(row.get("decisionStatus") or "FLAT")
        status_counts[status] = status_counts.get(status, 0) + 1

    payload: dict[str, Any] = {
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
    if ingest_result is not None:
        payload["ingestResult"] = ingest_result
    return payload


def run_ase_full_scan_and_execute(
    *,
    ptis_root: str | None = None,
    execution_deps: ASEExecutionDeps | None = None,
    ingest_sources: Iterable[str] | None = DEFAULT_SCAN_INGEST_SOURCES,
) -> dict[str, Any]:
    """Full-universe dual-horizon scan with TRADE execution and time-stop refresh."""
    deps = execution_deps or default_execution_deps()
    payload = run_ase_dual_horizon_scan(
        write_journal=True,
        execute_trades=True,
        ptis_root=ptis_root,
        execution_deps=deps,
        ingest_sources=ingest_sources,
    )
    closed: list[dict[str, Any]] = []
    for venue in ("mt5", "bybit"):
        try:
            positions, _raw = deps.get_positions(venue)
        except Exception as exc:
            log.warning("ASE time-stop position read failed for %s: %s", venue, exc)
            continue
        for pos in positions:
            pos.setdefault("venue", venue)
        closed.extend(enforce_time_stops(positions, deps=deps))
    payload["timeStopsClosed"] = closed
    try:
        from athena_ase.execution.reconcile import reconcile_filled_from_ptis

        payload["reconciledFills"] = reconcile_filled_from_ptis()
    except Exception as exc:
        log.warning("ASE fill reconciliation failed (non-fatal): %s", exc)
        payload["reconciledFills"] = 0
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
