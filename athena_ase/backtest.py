"""Standalone ASE backtest — no Engine A/B/C routing."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.horizon import HORIZONS, K_SL, K_TP, Horizon
from athena_ase.instruments import DEFAULT_INSTRUMENTS, Instrument, instrument_by_symbol
from athena_ase.runtime.scan import _pair_meta_for_symbol
from config import CONFIG

log = logging.getLogger("ase.backtest")

_HORIZONS: tuple[Horizon, ...] = ("intraday", "swing")


def _resolve_horizons(horizon: str | None) -> tuple[Horizon, ...]:
    raw = str(horizon or "both").strip().lower()
    if raw in ("both", "all", "dual"):
        configured = CONFIG.get("ASE_BT_HORIZONS")
        if isinstance(configured, (list, tuple)) and configured:
            out: list[Horizon] = []
            for item in configured:
                hz = str(item).strip().lower()
                if hz in _HORIZONS and hz not in out:
                    out.append(hz)  # type: ignore[arg-type]
            if out:
                return tuple(out)
        return _HORIZONS
    if raw in _HORIZONS:
        return (raw,)  # type: ignore[return-value]
    return _HORIZONS


def _resolve_instrument(pair: dict[str, Any]) -> Instrument | None:
    symbol = str(pair.get("symbol") or pair.get("display") or "").replace("/", "")
    return instrument_by_symbol(symbol)


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _barrier_prices(outcome: Any) -> dict[str, float]:
    """Return exact prices used by the ASE triple-barrier simulator."""
    entry_log = float(outcome.entry_log)
    direction = int(outcome.direction)
    horizon = str(outcome.horizon)
    cfg = HORIZONS[horizon]  # type: ignore[index]
    r_unit = K_SL * float(outcome.sigma_bar) * math.sqrt(cfg.max_hold_bars)
    entry = math.exp(entry_log)
    if direction > 0:
        sl = math.exp(entry_log - K_SL * r_unit)
        tp = math.exp(entry_log + K_TP * r_unit)
    else:
        sl = math.exp(entry_log + K_SL * r_unit)
        tp = math.exp(entry_log - K_TP * r_unit)
    return {"entry": entry, "sl": sl, "tp": tp}


def _signal_dict_field(sig: Any, name: str) -> dict[str, Any]:
    value = getattr(sig, name, None)
    return value if isinstance(value, dict) else {}


def _non_trade_reason(sig: Any, status: str, direction: str) -> str:
    data_quality = _signal_dict_field(sig, "dataQuality")
    model_health = _signal_dict_field(sig, "modelHealth")
    prediction_diagnostics = _signal_dict_field(sig, "predictionDiagnostics")
    for source, key in (
        (data_quality, "blocker"),
        (model_health, "blocker"),
        (model_health, "tradeCapReason"),
        (prediction_diagnostics, "tradeCapReason"),
        (model_health, "errorReason"),
        (prediction_diagnostics, "error"),
    ):
        value = source.get(key)
        if value:
            return str(value)
    if direction not in ("LONG", "SHORT"):
        return "direction_none"
    return f"{status.lower()}_without_trade"


def _sample_non_trade_signal(cand: Any, sig: Any, status: str, direction: str) -> dict[str, Any]:
    data_quality = _signal_dict_field(sig, "dataQuality")
    model_health = _signal_dict_field(sig, "modelHealth")
    prediction_diagnostics = _signal_dict_field(sig, "predictionDiagnostics")
    sample: dict[str, Any] = {
        "instrument": getattr(cand, "instrument", ""),
        "horizon": getattr(cand, "horizon", ""),
        "status": status,
        "direction": direction,
        "expectedNetR": float(getattr(sig, "expectedNetR", 0.0) or 0.0),
        "probabilityPositive": float(getattr(sig, "probabilityPositive", 0.0) or 0.0),
    }
    optional_fields = {
        "blocker": data_quality.get("blocker") or model_health.get("blocker"),
        "modelErrorReason": model_health.get("errorReason"),
        "tradeCapReason": model_health.get("tradeCapReason")
        or prediction_diagnostics.get("tradeCapReason"),
        "predictionError": prediction_diagnostics.get("error"),
    }
    for key, value in optional_fields.items():
        if value:
            sample[key] = str(value)
    return sample


def run_ase_pair_backtest(
    pair: dict[str, Any],
    *,
    horizon: str = "both",
    lookback_days: int | None = None,
    ptis_root: str | None = None,
    skip_demo_gate: bool = True,
) -> dict[str, Any]:
    """Run ASE Layer-1 + predict_batch backtest for one instrument across horizons."""
    if not bool(CONFIG.get("ASE_BT_ENABLED", True)):
        return {"error": "ASE backtest disabled in config (ASE_BT_ENABLED=false)"}

    inst = _resolve_instrument(pair)
    if inst is None:
        display = pair.get("display") or pair.get("symbol") or "?"
        return {"error": f"{display} is not in the ASE instrument universe"}

    days = lookback_days
    if days is None:
        try:
            days = int(CONFIG.get("ASE_BT_LOOKBACK_DAYS", 365) or 365)
        except (TypeError, ValueError):
            days = 365
    days = max(30, days)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    store = PTISStore(ptis_root or default_ptis_root())
    horizons = _resolve_horizons(horizon)

    from athena_ase.inference.predict import predict_batch
    from athena_ase.signals.engine import iter_candidates
    from athena_research.ase.event_backtest import simulate_candidate

    trades: list[dict[str, Any]] = []
    candidate_count = 0
    signal_counts: dict[str, int] = {}
    non_trade_reasons: dict[str, int] = {}
    sample_non_trade_signals: list[dict[str, Any]] = []

    for hz in horizons:
        candidates = list(
            iter_candidates(store, (inst,), horizon=hz, start_ms=start_ms, end_ms=end_ms)
        )
        candidate_count += len(candidates)
        if not candidates:
            continue

        signals = predict_batch(
            candidates,
            store,
            {inst.symbol: inst},
            skip_demo_gate=skip_demo_gate,
        )
        for cand, sig in zip(candidates, signals):
            status = str(sig.decisionStatus or "FLAT")
            signal_counts[status] = signal_counts.get(status, 0) + 1
            direction = str(getattr(sig, "direction", "NONE") or "NONE")
            if status != "TRADE" or direction not in ("LONG", "SHORT"):
                reason = _non_trade_reason(sig, status, direction)
                non_trade_reasons[reason] = non_trade_reasons.get(reason, 0) + 1
                if len(sample_non_trade_signals) < 5:
                    sample_non_trade_signals.append(
                        _sample_non_trade_signal(cand, sig, status, direction)
                    )
                continue
            outcome = simulate_candidate(cand, store, inst)
            if outcome is None:
                continue
            levels = _barrier_prices(outcome)
            decision_iso = _ms_to_iso(outcome.decision_time_ms)
            trades.append(
                {
                    "resultR": round(float(outcome.net_R), 4),
                    "r_multiple": round(float(outcome.net_R), 4),
                    "date": decision_iso,
                    "time": decision_iso,
                    "entryTime": decision_iso,
                    "direction": "LONG" if outcome.direction > 0 else "SHORT",
                    "horizon": hz,
                    "instrument": outcome.instrument,
                    "family": outcome.family,
                    "exit_reason": outcome.exit_reason,
                    "hold_bars": outcome.hold_bars,
                    "entry": round(levels["entry"], 8),
                    "sl": round(levels["sl"], 8),
                    "tp": round(levels["tp"], 8),
                    "tp1": round(levels["tp"], 8),
                    "grossR": float(outcome.gross_R),
                    "costR": float(outcome.cost_R),
                    "maeR": float(outcome.mae_R),
                    "mfeR": float(outcome.mfe_R),
                    "expectedNetR": float(sig.expectedNetR),
                    "probabilityPositive": float(sig.probabilityPositive),
                    "score": float(sig.expectedNetR),
                    "engine": "ASE",
                }
            )

    diagnostics = {
        "candidateCount": candidate_count,
        "signalCounts": signal_counts,
        "horizons": list(horizons),
        "lookbackDays": days,
        "instrument": inst.symbol,
        "family": inst.family,
    }
    if non_trade_reasons:
        diagnostics["nonTradeReasons"] = dict(
            sorted(non_trade_reasons.items(), key=lambda item: (-item[1], item[0]))
        )
    if sample_non_trade_signals:
        diagnostics["sampleNonTradeSignals"] = sample_non_trade_signals

    if not trades:
        top_reason = ""
        if non_trade_reasons:
            reason, count = max(non_trade_reasons.items(), key=lambda item: item[1])
            top_reason = f"; top blocker: {reason}={count}"
        return {
            "error": f"No ASE TRADE outcomes for {pair.get('display', inst.display)} "
            f"({candidate_count} candidates, horizons={list(horizons)}{top_reason})",
            "diagnostics": {**diagnostics, "tradeCount": 0},
        }

    return {
        "trades": trades,
        "diagnostics": {
            **diagnostics,
            "tradeCount": len(trades),
        },
    }


def ase_backtest_pairs() -> list[dict[str, Any]]:
    """Pair dicts for every instrument in the ASE universe."""
    pairs: list[dict[str, Any]] = []
    for inst in DEFAULT_INSTRUMENTS:
        meta = _pair_meta_for_symbol(inst.symbol)
        if meta:
            pairs.append(meta)
    return pairs
