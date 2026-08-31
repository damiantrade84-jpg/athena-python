"""KIMI → Athena host execution bridge.

Routes a KIMI signal through the exact pipeline the other engines use:

    freshness evidence (canonical candle diagnostics)
    → demo gate (MT5 demo account / Bybit demo|testnet, explicit env override)
    → guardian.pre_trade_check
    → risk_engine.risk_check          (sizing, daily loss, drawdown, exposure)
    → execution_lifecycle.run_managed_execution
        → mt5_executor.mt5_execute / bybit_executor.bybit_execute

This module deliberately owns NO broker logic of its own — orders are placed
by the host executors with their spread/tick-age/drift/SL-TP defenses, and
sizing comes from the host risk engine (approval.volume), never from KIMI.

Availability: the host modules (config, risk_engine, guardian, …) must import
cleanly — that means the repo root on sys.path and a config that passes the
host's own boot safety validation (config.py raises SystemExit on an unsafe
live config without ATHENA_REAL_ORDERS_CONFIRM). Any failure → host execution
is unavailable and the engine stays on its paper broker. Fail-closed
throughout.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .datafeed import classify, is_crypto

_HOST_ROOT = Path(__file__).resolve().parents[2]   # athena-python/

_HOST_MODS = ("config", "risk_engine", "guardian", "execution_lifecycle",
              "mt5_executor", "bybit_executor")

_cache: dict[str, Any] = {"tried": False, "mods": None, "error": None}

# KIMI timeframe → host freshness-gate timeframe
_TF_MAP = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
           "1h": "H1", "4h": "H4", "1d": "D1"}

# datafeed.classify() → host signal "type"
_HOST_TYPE = {"forex": "forex", "metals": "commodity", "energy": "commodity",
              "indices": "index", "crypto": "crypto", "stock": "stock"}


def _reset_cache() -> None:
    """Tests only: drop the cached host import."""
    _cache.update(tried=False, mods=None, error=None)


def _host() -> tuple[dict[str, Any] | None, str | None]:
    """Import the host execution modules once. Catches BaseException because
    host config aborts the interpreter (SystemExit) on unsafe live configs."""
    if _cache["tried"]:
        return _cache["mods"], _cache["error"]
    _cache["tried"] = True
    try:
        root = str(_HOST_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import importlib
        mods = {name: importlib.import_module(name) for name in _HOST_MODS}
        mods["market_state"] = importlib.import_module(
            "athena_app.services.market_state")
        _cache["mods"] = mods
    except BaseException as exc:  # noqa: BLE001
        _cache["error"] = f"{type(exc).__name__}: {exc}"
    return _cache["mods"], _cache["error"]


def host_available() -> bool:
    if not Config.HOST_EXEC:
        return False
    mods, _ = _host()
    return mods is not None


def status() -> dict:
    """Snapshot payload for the UI."""
    if not Config.HOST_EXEC:
        return {"available": False, "reason": "disabled (KIMI_HOST_EXEC=0)",
                "demoOnly": True}
    mods, err = _host()
    if mods is None:
        return {"available": False, "reason": f"host import: {err}",
                "demoOnly": True}
    return {"available": True, "reason": "",
            "demoOnly": not Config.HOST_REAL_ORDERS}


_ACCT_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_ACCT_TTL_SEC = 15.0


def accounts(force: bool = False) -> dict:
    """Live MT5 and Bybit account equity, read-only.

    The terminal header used to print the PAPER ledger under a bare "EQUITY"
    label, which reads as the real account balance and is not. This reports
    what each connected venue actually says.

    The two are deliberately NOT summed. MT5 equity is denominated in the
    account currency (this account is ZAR) and Bybit equity in USDT; adding
    them would produce a number that means nothing. Each is reported with its
    own currency and its own demo/real flag.
    """
    now = time.time()
    if not force and _ACCT_CACHE["data"] is not None and             now - float(_ACCT_CACHE["ts"]) < _ACCT_TTL_SEC:
        return _ACCT_CACHE["data"]

    out: dict[str, Any] = {"ts": now, "mt5": None, "bybit": None}
    if not Config.HOST_EXEC:
        out["reason"] = "host bridge disabled (KIMI_HOST_EXEC=0)"
        _ACCT_CACHE.update(ts=now, data=out)
        return out

    mods, err = _host()
    if mods is None:
        out["reason"] = f"host import: {err}"
        _ACCT_CACHE.update(ts=now, data=out)
        return out

    try:
        raw = mods["mt5_executor"].mt5_get_account() or {}
        if raw.get("error"):
            out["mt5"] = {"available": False,
                          "error": str(raw.get("detail") or "MT5 unavailable")}
        else:
            out["mt5"] = {
                "available": True,
                "equity": float(raw.get("equity") or 0.0),
                "balance": float(raw.get("balance") or 0.0),
                "freeMargin": float(raw.get("freeMargin") or 0.0),
                "profit": float(raw.get("profit") or 0.0),
                "currency": str(raw.get("currency") or ""),
                "login": raw.get("login"),
                "server": raw.get("server"),
                # 'unknown' is reported as-is and never coerced to demo: this
                # broker's demo server is literally named "...-LIVE", so the
                # account environment is the only trustworthy signal.
                "environment": str(raw.get("accountEnvironment") or "unknown"),
            }
    except Exception as exc:  # noqa: BLE001 - a status read must never raise
        out["mt5"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        raw = mods["bybit_executor"].bybit_get_account() or {}
        if raw.get("error"):
            out["bybit"] = {"available": False,
                            "error": str(raw.get("detail") or "Bybit unavailable")}
        else:
            equity = raw.get("equity")
            if equity is None:
                equity = float(raw.get("total") or 0.0) + float(
                    raw.get("unrealized") or 0.0)
            out["bybit"] = {
                "available": True,
                "equity": float(equity or 0.0),
                "balance": float(raw.get("total") or 0.0),
                "free": float(raw.get("free") or 0.0),
                "currency": "USDT",
                "testnet": bool(raw.get("testnet")),
                "environment": "demo" if raw.get("demo") else "unknown",
            }
    except Exception as exc:  # noqa: BLE001
        out["bybit"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    _ACCT_CACHE.update(ts=now, data=out)
    return out


# ----------------------------------------------------------------------
# signal translation
# ----------------------------------------------------------------------
def _host_display(mods: dict, feed, symbol: str) -> str:
    """KIMI canonical symbol → Athena display name (the key the host symbol
    maps expect: 'EUR/USD', 'Dow Jones', …). Reverse-looks-up the effective
    MT5 map (incl. the user's broker-suffix overrides) by the RESOLVED broker
    symbol, so 'US30' finds 'Dow Jones' via 'US30.s'."""
    if is_crypto(symbol):
        return f"{symbol[:-4]}/USDT"
    resolved = None
    try:
        resolved = feed._mt5_resolve(symbol)
    except Exception:  # noqa: BLE001
        resolved = None
    if resolved:
        try:
            eff = mods["mt5_executor"]._effective_mt5_symbol_map()
            for disp, broker in eff.items():
                if str(broker).casefold() == str(resolved).casefold():
                    return disp
        except Exception:  # noqa: BLE001
            pass
    s = symbol.upper()
    if len(s) == 6 and s.isalpha():
        return f"{s[:3]}/{s[3:]}"
    return s


def _freshness_evidence(mods: dict, feed, symbol: str, host_pair: str,
                        host_type: str) -> tuple[dict, dict, dict, str | None]:
    """Re-fetch the three KIMI timeframes (≤5s cache) and build the host's
    canonical candle diagnostics. Fail-closed: any TF not 'fresh' → error."""
    diag_fn = mods["market_state"].candle_freshness_diagnostic
    freshness, consistency, fetch_meta = {}, {}, {}
    for kim_tf in (Config.TF_ENTRY, Config.TF_CONTEXT, Config.TF_BIAS):
        host_tf = _TF_MAP.get(kim_tf, kim_tf.upper())
        cs = feed.klines(symbol, kim_tf, max_age_sec=5.0)
        candles = [{"time": int(t) // 1000} for t in cs.t]
        pair = {"display": host_pair, "symbol": symbol, "type": host_type,
                "source": cs.source}
        diag = diag_fn(pair, host_tf, candles)
        severity = str(diag.get("stalenessSeverity") or "")
        freshness[host_tf] = dict(diag)
        last_epoch = diag.get("lastBarEpoch") or (int(cs.t[-1]) // 1000 if len(cs) else None)
        fetch_meta[host_tf] = {
            "lastBarEpoch": last_epoch,
            "lastBarIso": diag.get("lastBarIso"),
            "lastBarAgeSec": diag.get("lastBarAgeSec"),
            "stalenessSeverity": severity,
            "hasCurrentBucket": diag.get("hasCurrentBucket"),
            "lastBarStale": severity != "fresh",
            "source": cs.source,
        }
        if severity != "fresh":
            consistency[host_tf] = {"status": "ERROR_STALE", "severity": severity}
            return freshness, consistency, fetch_meta, \
                f"stale {host_tf} ({severity or 'unclassified'}, src {cs.source})"
        consistency[host_tf] = {"status": "OK"}
    return freshness, consistency, fetch_meta, None


def _host_kill_switch() -> bool:
    """Host kill switch when embedded in the athena process (module already
    loaded — reading the live global, not re-importing)."""
    for name in ("athena", "__main__"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "_kill_switch"):
            try:
                return bool(getattr(mod, "_kill_switch"))
            except Exception:  # noqa: BLE001
                pass
    return False


def _demo_gate(venue: str, account: dict) -> str | None:
    """Demo-first arming. Real accounts/testnet-less Bybit need an explicit
    KIMI_HOST_REAL_ORDERS=1 in the environment — same contract as the host's
    ATHENA_REAL_ORDERS_CONFIRM boot guard."""
    if Config.HOST_REAL_ORDERS:
        return None
    if venue == "mt5":
        env = str((account or {}).get("accountEnvironment") or "unknown")
        if env != "demo":
            return (f"MT5 account is {env} — KIMI host execution is demo-first "
                    f"(KIMI_HOST_REAL_ORDERS=1 to override)")
    else:
        # Trust what the executor VERIFIED about the account first; the env
        # vars are only a fallback for accounts that report neither flag.
        acct_demo = bool((account or {}).get("demo")
                         or (account or {}).get("testnet"))
        env_demo = os.environ.get("BYBIT_DEMO", "").lower() in ("true", "1", "yes")
        env_testnet = os.environ.get("BYBIT_TESTNET", "").lower() in ("true", "1", "yes")
        if not (acct_demo or env_demo or env_testnet):
            return ("Bybit is not in demo/testnet — set BYBIT_DEMO=true "
                    "(or KIMI_HOST_REAL_ORDERS=1 to override)")
    return None


def _audit(sig, result_ok: bool, detail: str) -> None:
    """Best-effort row in the host audit log; never affects the result."""
    try:
        import sqlite3
        db = _HOST_ROOT / "audit.db"
        if not db.is_file():
            return
        with sqlite3.connect(str(db), timeout=5.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "INSERT INTO audit_log(ts,pair,score,direction,style,grade,error_tag) "
                "VALUES(?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), sig.symbol,
                 sig.card.total, "LONG" if sig.direction > 0 else "SHORT",
                 "kimi", sig.card.grade, "KIMI-EXEC" if result_ok else "KIMI-REJECT",
                 ),
            )
    except Exception:  # noqa: BLE001
        pass


def _record_attribution(sig, venue: str, host_pair: str, result: dict) -> None:
    """Durably record the fill so the host dashboard can name KIMI as its engine.

    KIMI's own event feed is in-memory and the host audit row carries no ticket,
    so without this a KIMI position shows up on the open-trades panel as
    "Unknown". Display metadata only; never affects the result."""
    try:
        import engine_attribution
        engine_attribution.record_execution(
            engine=engine_attribution.ENGINE_KIMI,
            venue=venue,
            result=result,
            pair=host_pair,
            symbol=sig.symbol,
            direction="LONG" if sig.direction > 0 else "SHORT",
        )
    except Exception:  # noqa: BLE001
        pass


def _notify(sig, venue: str) -> None:
    try:
        from telegram_notify import notify_trade_opened
        notify_trade_opened(
            pair=sig.symbol,
            direction="LONG" if sig.direction > 0 else "SHORT",
            entry_price=float(sig.entry),
            stop_loss=float(sig.sl),
            take_profit=float(sig.tp1),
            style="intraday",
            engine="KIMI",
            exchange=venue,
        )
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------
# main entry
# ----------------------------------------------------------------------
def execute_signal(engine, sig) -> dict:
    """Execute one ACTIVE KIMI signal through the host pipeline.

    Called by Engine.execute() OUTSIDE the engine lock (broker IO is slow).
    Marks the signal EXECUTED and records the event on success.
    """
    mods, err = _host()
    if mods is None:
        return {"ok": False, "error": f"host execution unavailable: {err}"}

    if engine.risk.kill_switch or _host_kill_switch():
        return {"ok": False, "error": "kill switch engaged"}

    cls = classify(sig.symbol)
    host_type = _HOST_TYPE.get(cls)
    if host_type is None:
        return {"ok": False, "error": f"unclassifiable instrument {sig.symbol}"}
    venue = "bybit" if is_crypto(sig.symbol) else "mt5"
    host_pair = _host_display(mods, engine.feed, sig.symbol)

    # --- fresh decision data at execution time ---------------------------
    try:
        freshness, consistency, fetch_meta, fresh_err = _freshness_evidence(
            mods, engine.feed, sig.symbol, host_pair, host_type)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"freshness evidence failed: {exc}"}
    if fresh_err:
        _audit(sig, False, fresh_err)
        return {"ok": False, "error": f"stale data: {fresh_err}"}

    live_px = None
    if venue == "mt5":
        tk = engine.feed.mt5_tick(sig.symbol)
        if tk:
            live_px = tk["ask"] if sig.direction > 0 else tk["bid"]
    else:
        tk = engine.feed.tickers([sig.symbol]).get(sig.symbol)
        if tk:
            live_px = tk.get("price")
    ref_px = live_px or sig.entry

    exec_dict = {
        "pair": host_pair,
        "symbol": sig.symbol,
        "type": host_type,
        "direction": "LONG" if sig.direction > 0 else "SHORT",
        "price": ref_px,
        "entryReference": sig.entry,
        "sl": sig.sl, "tp": sig.tp1, "tp1": sig.tp1, "tp2": sig.tp2,
        # decision time is now: the engine re-affirms the setup with fresh
        # quotes + candle evidence at execution; creation kept for provenance
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signalCreatedAt": datetime.fromtimestamp(sig.created_at, tz=timezone.utc).isoformat(),
        "confluenceScore": sig.card.total, "maxScore": 100,
        "score": sig.card.total, "grade": sig.card.grade,
        "engine": "kimi", "style": "intraday",
        "source": "bybit" if venue == "bybit" else "mt5",
        "quoteTimestamp": time.time(),
        "candleFreshness": freshness,
        "candleConsistency": consistency,
        "candleFetchMeta": fetch_meta,
        "kimi": {"signalId": sig.id, "score": sig.card.total,
                 "grade": sig.card.grade, "components": sig.card.components},
    }

    # --- venue state ------------------------------------------------------
    if venue == "bybit":
        account = mods["bybit_executor"].bybit_get_account()
        if not account or account.get("error"):
            return {"ok": False, "error": "bybit account unavailable "
                    f"({(account or {}).get('detail', 'no keys / ccxt?')})"}
        pos_resp = mods["bybit_executor"].bybit_get_positions()
        if isinstance(pos_resp, dict) and pos_resp.get("error"):
            return {"ok": False, "error": "positions unavailable - cannot verify exposure"}
        symbol_info = mods["bybit_executor"].bybit_get_symbol_info(host_pair)
        if symbol_info and symbol_info.get("error"):
            symbol_info = None
    else:
        account = mods["mt5_executor"].mt5_get_account()
        if not account or account.get("error"):
            return {"ok": False, "error": "MT5 not connected - start the terminal"}
        pos_resp = mods["mt5_executor"].mt5_get_positions()
        if isinstance(pos_resp, dict) and pos_resp.get("error"):
            return {"ok": False, "error": "positions unavailable - cannot verify exposure"}
        symbol_info = mods["mt5_executor"].mt5_get_symbol_info(host_pair)
        if not symbol_info or symbol_info.get("error"):
            return {"ok": False,
                    "error": f"symbol '{host_pair}' not available on the MT5 broker"}

    gate_err = _demo_gate(venue, account)
    if gate_err:
        _audit(sig, False, gate_err)
        return {"ok": False, "error": gate_err}

    positions = (pos_resp.get("positions", [])
                 if isinstance(pos_resp, dict) else (pos_resp or []))

    # --- guardian ---------------------------------------------------------
    ok_guard, guard_reason = mods["guardian"].pre_trade_check(
        exec_dict, positions, account, pos_resp if isinstance(pos_resp, dict) else None)
    if not ok_guard:
        _audit(sig, False, guard_reason)
        return {"ok": False, "error": f"guardian: {guard_reason}"}

    # --- risk engine (owns sizing) ----------------------------------------
    approval = mods["risk_engine"].risk_check(
        signal=exec_dict,
        account_balance=float(account["balance"]),
        account_equity=float(account["equity"]),
        open_positions=positions,
        symbol_info=symbol_info,
        kill_switch=False,   # already evaluated above (kimi + host)
        execution_context="kimi_engine",
    )
    if approval is None or not getattr(approval, "approved", False):
        reason = getattr(approval, "reason", "risk_rejected") if approval else "risk_rejected"
        _audit(sig, False, reason)
        return {"ok": False, "error": f"risk: {reason}"}

    # --- managed execution (pre-cleanup → broker order → SL/TP reconcile) --
    result = mods["execution_lifecycle"].run_managed_execution(venue, exec_dict, approval)
    success = bool(result.get("success"))
    _audit(sig, success, "" if success else str(result.get("error", "executor_failed")))

    if not success:
        return {"ok": False, "error": str(result.get("error", "executor failed")),
                    "result": result}

    _record_attribution(sig, venue, host_pair, result)

    with engine.lock:
        sig.status = "EXECUTED"
        engine.signals.pop(sig.id, None)
        engine.signal_history.append(sig.to_dict())
        engine.signal_history = engine.signal_history[-120:]
        engine.events.append({
            "type": "HOST", "symbol": sig.symbol,
            "side": "LONG" if sig.direction > 0 else "SHORT",
            "venue": venue, "pair": host_pair,
            "qty": result.get("volume"), "ticket": result.get("ticket"),
            "entry": result.get("entryPrice"), "sl": sig.sl, "tp": sig.tp2,
            "approvalVolume": getattr(approval, "volume", None),
            "t": time.time(), "signalId": sig.id,
        })
        engine.events = engine.events[-100:]
    _notify(sig, venue)
    return {"ok": True, "venue": f"host-{venue}", "pair": host_pair,
            "result": {k: result.get(k) for k in
                       ("success", "ticket", "volume", "entryPrice", "sl", "tp",
                        "riskAmount", "riskPct")},
            "note": "position managed by Athena (on-broker SL/TP) - "
                    "track/close it from the main dashboard or MT5"}
