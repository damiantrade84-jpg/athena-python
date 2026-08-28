"""OX Alpha runtime: universe scan, status snapshot, gated execution.

Scan is read-only analysis. Execution always re-scans the requested pair fresh,
re-validates the setup server-side, and routes through ox_alpha.bridge (demo
gate -> guardian -> risk_check -> broker). The pair universe and its score
groups come from the running app via an injected provider, so this module never
imports the monolith.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Mapping, Sequence

from ox_alpha import bridge as ox_bridge
from ox_alpha import feed, journal
from ox_alpha.config import OxSettings, load_settings
from ox_alpha.engine import analyze_pair
from ox_alpha.profiles import profile_for_group

log = logging.getLogger("ox_alpha.runtime")

ENGINE_ID = "ox_alpha"
ANALYTICAL_TFS = ("M15", "H1")

_pair_provider: Callable[[], list[dict]] | None = None
_market_open_checker: Callable[[Mapping[str, Any]], dict] | None = None


def set_pair_provider(fn: Callable[[], list[dict]] | None) -> None:
    """Inject the app's pair registry (e.g. enabled ALL_PAIRS)."""
    global _pair_provider
    _pair_provider = fn


def set_market_open_checker(fn: Callable[[Mapping[str, Any]], dict] | None) -> None:
    """Override the market-open proof (tests / custom venue checks)."""
    global _market_open_checker
    _market_open_checker = fn


def market_open_state(pair: Mapping[str, Any]) -> dict:
    """Broker-truth proof that the pair's market is tradable right now.

    crypto venues trade 24/7. MT5 pairs reuse the platform's canonical
    tick-age/trade-mode check. Unknown sources have no broker-truth helper —
    they are not claimed open; freshness gates still apply.
    """
    if str(pair.get("type") or "").lower() == "crypto":
        return {"open": True, "reason": "crypto_24_7"}
    if str(pair.get("source") or "").lower() == "mt5":
        # The broker check needs the broker's own symbol. The catalog `symbol`
        # is a research id (EURUSD=X, ^GSPC, DIS.US) that MT5 has never heard
        # of, and asking for a tick on it returns MARKET_CLOSED_NO_TICK on a
        # wide-open market.
        broker_symbol = feed.mt5_broker_symbol(pair)
        if not broker_symbol:
            return {"open": False, "reason": "no_mt5_mapping"}
        try:
            from scalp_engine import mt5_market_open_state

            return mt5_market_open_state(broker_symbol)
        except Exception as exc:
            return {"open": False, "reason": f"market_open_check_error:{exc}"}
    return {"open": False, "reason": "no_broker_check_available"}


def _pairs() -> list[dict]:
    if _pair_provider is None:
        return []
    try:
        return [p for p in (_pair_provider() or []) if isinstance(p, dict)]
    except Exception as exc:
        log.warning("OX Alpha pair provider failed: %s", exc)
        return []


def _score_group(pair: Mapping[str, Any]) -> str | None:
    group = pair.get("score_group") or pair.get("scoreGroup")
    if group:
        return str(group)
    try:
        from scoring import get_pair_score_group

        return get_pair_score_group(dict(pair))
    except Exception:
        return None


def _settings(config: dict | None) -> OxSettings:
    if config is not None:
        return load_settings(config)
    try:
        from config import CONFIG

        return load_settings(CONFIG)
    except Exception:
        return load_settings(None)


def _policy_timeframes(pair: Mapping[str, Any], group: str | None) -> tuple[list[str], Any]:
    """Resolve the authoritative policy and return its required closed TFs.

    Falls back to the engine's analytical ladder when the policy module cannot
    load (tests / research contexts); the fallback never widens beyond M15/H1.
    """
    display = str(pair.get("display") or pair.get("symbol") or "")
    asset_type = str(pair.get("type") or "")
    try:
        from timeframe_policy import resolve_timeframe_policy

        policy = resolve_timeframe_policy(
            display, asset_type, group, "intraday", engine_id=ENGINE_ID
        )
        tfs = {tf.value for tf in policy.required_closed_tfs}
        tfs.update(ANALYTICAL_TFS)
        ordered = sorted(tfs, key=lambda t: {"M15": 0, "M30": 1, "H1": 2, "H4": 3, "D1": 4}.get(t, 5))
        return ordered, policy
    except Exception as exc:
        log.debug("OX Alpha policy resolution fell back for %s: %s", display, exc)
        return list(ANALYTICAL_TFS), None


def scan_pair(
    pair: Mapping[str, Any],
    *,
    settings: OxSettings | None = None,
    config: dict | None = None,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Full read-only evaluation of one pair: feed -> score -> policy stamp."""
    cfg = settings or _settings(config)
    group = _score_group(pair)
    profile = profile_for_group(group)
    pair_mutable = dict(pair)
    pair_mutable["score_group"] = group

    # Broker-truth market-open proof BEFORE any scoring work: a closed market
    # must never emit TRADE/WATCH, whatever its cached bars look like.
    open_state = (_market_open_checker or market_open_state)(pair_mutable)

    tf_list, policy = _policy_timeframes(pair_mutable, group)
    limits = {
        "M15": cfg.candle_limit_m15,
        "H1": cfg.candle_limit_h1,
    }
    states, freshness = feed.load_market_states(
        pair_mutable, tf_list, limits, time_now=time_now
    )

    quote = feed.load_quote(
        pair_mutable, max_age_sec=cfg.quote_max_age_sec, time_now=time_now
    )
    signal = analyze_pair(
        pair_mutable,
        states,
        profile=profile,
        settings=cfg,
        freshness={tf: ok for tf, ok in freshness.items() if tf in tf_list},
        quote=quote,
        time_now=time_now,
    )
    signal["marketOpen"] = bool(open_state.get("open"))
    signal["marketOpenReason"] = str(open_state.get("reason") or "")
    gates = signal.setdefault("gates", [])
    gates.insert(0, {
        "name": "market_open",
        "passed": bool(open_state.get("open")),
        "detail": str(open_state.get("reason") or ""),
        "hard": True,
    })
    if not open_state.get("open"):
        signal["decision"] = "NO_SIGNAL"
        signal["direction"] = None
        signal["playType"] = None
        signal["executable"] = False
        signal["reason"] = "MARKET_CLOSED"
        signal["entryReadiness"] = "UNAVAILABLE"
        signal["entryReadinessReason"] = (
            f"market_closed:{open_state.get('reason')}"
        )
    signal["scoreGroup"] = group
    signal["requiredTimeframes"] = tf_list

    pd = feed.pair_dict(pair_mutable)
    try:
        from timeframe_policy import attach_timeframe_policy_payload

        attach_timeframe_policy_payload(
            signal,
            pd,
            "intraday",
            engine=ENGINE_ID,
            market_states=states,
            config=config,
        )
    except Exception as exc:
        log.debug("OX Alpha policy stamp failed for %s: %s", pd.get("display"), exc)
        signal["entryReadiness"] = "UNAVAILABLE"
        signal["entryReadinessReason"] = f"policy_stamp_failed:{exc}"

    if not open_state.get("open"):
        signal["entryReadiness"] = "UNAVAILABLE"
        signal["entryReadinessReason"] = (
            f"market_closed:{open_state.get('reason')}"
        )
    elif quote is None and str(signal.get("entryReadiness") or "").upper() != "UNAVAILABLE":
        signal["entryReadiness"] = "PENDING"
        signal["entryReadinessReason"] = "quote_unavailable"
    signal["executable"] = bool(
        signal.get("decision") == "TRADE"
        and str(signal.get("entryReadiness") or "").upper() == "READY"
        and bool(open_state.get("open"))
        and quote is not None
        and str(signal.get("priceSource") or "") == "live_quote_mid"
    )
    return signal


def scan_universe(
    pairs: Sequence[Mapping[str, Any]] | None = None,
    *,
    settings: OxSettings | None = None,
    config: dict | None = None,
    workers: int | None = None,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Scan the full injected universe (or an explicit subset). Read-only."""
    started = time.time()
    cfg = settings or _settings(config)
    universe = [dict(p) for p in (pairs if pairs is not None else _pairs())]
    signals: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def _work(pair: dict) -> tuple[str, dict[str, Any]]:
        display = str(pair.get("display") or pair.get("symbol") or "?")
        if not cfg.enabled:
            return "skipped", {"pair": display, "reason": "engine_disabled"}
        try:
            return "signal", scan_pair(pair, settings=cfg, config=config, time_now=time_now)
        except Exception as exc:
            log.exception("OX Alpha scan failed for %s", display)
            return "skipped", {"pair": display, "reason": f"scan_error:{exc}"}

    max_workers = max(1, workers or cfg.scan_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, p): p for p in universe}
        for fut in as_completed(futures):
            kind, row = fut.result()
            if kind == "signal":
                signals.append(row)
            else:
                skipped.append(row)

    counts = {"TRADE": 0, "WATCH": 0, "NO_SIGNAL": 0}
    for s in signals:
        d = str(s.get("decision") or "NO_SIGNAL")
        counts[d] = counts.get(d, 0) + 1
    signals.sort(key=lambda s: -(float(s.get("score") or 0.0)))
    envelope = {
        "success": True,
        "engine": "OX_ALPHA",
        "engineLabel": "OX Alpha",
        "deployment": "DEMO_ONLY",
        "scannedAt": time.time(),
        "elapsedSec": round(time.time() - started, 2),
        "totalPairs": len(universe),
        "signals": signals,
        "skipped": skipped,
        "counts": counts,
    }
    journal.append({
        "kind": "scan",
        "engine": "OX_ALPHA",
        "totalPairs": len(universe),
        "counts": counts,
    })
    return envelope


def status(
    pairs: Sequence[Mapping[str, Any]] | None = None,
    *,
    config: dict | None = None,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Read-only snapshot for the panel (same data as scan_universe)."""
    return scan_universe(pairs, config=config, time_now=time_now)


def find_pair(symbol: str, pairs: Sequence[Mapping[str, Any]] | None = None) -> dict | None:
    want = str(symbol or "").strip().upper()
    if not want:
        return None
    universe = [dict(p) for p in (pairs if pairs is not None else _pairs())]
    for p in universe:
        if str(p.get("display") or "").upper() == want or str(p.get("symbol") or "").upper() == want:
            return p
    return None


def execute_signal(
    symbol: str,
    direction: str,
    *,
    deps: ox_bridge.OxExecDeps | None = None,
    pairs: Sequence[Mapping[str, Any]] | None = None,
    config: dict | None = None,
    min_rr_override: float | None = None,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Server-authoritative execution: fresh re-scan -> match -> gated open."""
    pair = find_pair(symbol, pairs)
    if pair is None:
        return {"executed": False, "reason": f"unknown_symbol:{symbol}"}
    fresh = scan_pair(pair, config=config, time_now=time_now)
    want_dir = str(direction or "").upper()
    if fresh.get("decision") != "TRADE":
        return {
            "executed": False,
            "reason": "SETUP_INVALIDATED",
            "freshSignal": {
                "decision": fresh.get("decision"),
                "direction": fresh.get("direction"),
                "score": fresh.get("score"),
            },
        }
    fresh_dir = str(fresh.get("direction") or "").upper()
    if fresh_dir != want_dir:
        return {
            "executed": False,
            "reason": f"SIGNAL_FLIPPED:{fresh_dir}",
            "freshSignal": {"direction": fresh_dir, "score": fresh.get("score")},
        }
    profile = profile_for_group(fresh.get("scoreGroup"))
    result = ox_bridge.open_trade(
        fresh,
        deps=deps or ox_bridge.default_deps(),
        min_rr=min_rr_override if min_rr_override is not None else profile.min_rr,
    )
    result["freshSignal"] = {
        "display": fresh.get("display"),
        "direction": fresh.get("direction"),
        "score": fresh.get("score"),
        "playType": fresh.get("playType"),
        "entryRef": fresh.get("entryRef"),
        "sl": fresh.get("sl"),
        "tp1": fresh.get("tp1"),
        "tp2": fresh.get("tp2"),
        "rr1": fresh.get("rr1"),
        "entryReadiness": fresh.get("entryReadiness"),
    }
    return result
