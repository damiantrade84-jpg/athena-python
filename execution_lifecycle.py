"""Shared execution lifecycle above broker executors (MT5 / Bybit).

Semantics (same across venues):
1. Pre-trade: cancel stale pending/entry children for this symbol (hygiene).
2. Parent: open via broker ``mt5_execute`` / ``bybit_execute`` (risk-approved volume).
3. Post-fill: reconcile protective exits (SL/TP) so no open parent is unmanaged.

Broker-specific API calls stay in ``mt5_executor`` / ``bybit_executor``; this module
only orchestrates and attaches a ``lifecycle`` envelope to the result dict.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("sentinel")

LIFECYCLE_VERSION = 1


def _paper_soak_blocks_real_orders() -> bool:
    try:
        from config import CONFIG
    except Exception:
        return False
    paper_soak = CONFIG.get("PAPER_SOAK")
    if not isinstance(paper_soak, dict):
        return False
    if bool(paper_soak.get("ENABLED", False)):
        return True
    return paper_soak.get("REAL_ORDERS_ALLOWED") is False


def _vision_blocks_execution(signal: dict[str, Any]) -> bool:
    candidates = [
        signal.get("vision_output"),
        signal.get("structured"),
    ]
    for payload in candidates:
        if not isinstance(payload, dict):
            continue
        structured = payload.get("structured") if isinstance(payload.get("structured"), dict) else {}
        if payload.get("confirms_direction") is False:
            return True
        if structured.get("confirms_direction") is False:
            return True
    ai_arbitration = signal.get("ai_arbitration")
    if isinstance(ai_arbitration, dict) and ai_arbitration.get("execution_allowed") is False:
        return True
    return False


def run_managed_execution(
    venue: str,
    signal: dict[str, Any],
    approval: Any,
) -> dict[str, Any]:
    """Run pre-cleanup → broker execute → post-reconcile for ``mt5`` or ``bybit``.

    ``approval`` must be a risk_engine ``RiskApproval`` with ``approved=True``.
    """
    v = (venue or "").strip().lower()
    if v not in ("mt5", "bybit"):
        return {"success": False, "error": f"UNKNOWN_VENUE:{venue}"}

    pair = (signal.get("pair") or signal.get("symbol") or "").strip()
    phases: list[dict[str, Any]] = []

    if _paper_soak_blocks_real_orders():
        return {
            "success": False,
            "error": "PAPER_SOAK_BLOCKED_REAL_ORDER",
            "error_tag": "paper_soak_blocked",
        }

    if _vision_blocks_execution(signal):
        log.warning(f"[VISION-VETO] Vetoing execution for {pair} due to conflicting direction.")
        try:
            import sqlite3
            from datetime import datetime, timezone
            _db = "audit.db"
            with sqlite3.connect(_db, timeout=1.0) as _con:
                _con.execute("PRAGMA journal_mode=WAL")
                _con.execute(
                    "INSERT INTO audit_log(ts, pair, direction, grade, error_tag, reasoning) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        pair,
                        signal.get("direction", "UNKNOWN"),
                        "VISION-VETO",
                        "vision_veto",
                        "AI Vision direction conflict",
                    )
                )
        except Exception as _audit_exc:
            log.warning(f"[VISION-VETO] Failed to insert vision veto into audit_log: {_audit_exc}")

        return {"success": False, "error": "AI Vision direction conflict", "error_tag": "vision_veto"}


    if v == "mt5":
        from mt5_executor import (
            mt5_cancel_pending_athena_orders,
            mt5_execute,
            mt5_reconcile_after_open,
        )

        pre = mt5_cancel_pending_athena_orders(pair)
        phases.append({"name": "pre_cleanup_pending", "result": pre})
        if pre.get("error"):
            return {
                "success": False,
                "error": f"PRE_CLEANUP_FAILED:{pre.get('detail') or pre}",
                "lifecycle": {
                    "version": LIFECYCLE_VERSION,
                    "venue": v,
                    "pair": pair,
                    "phases": phases,
                },
            }
        if pre.get("cancelled"):
            log.info(
                f"[LIFECYCLE] MT5 pre-cleanup removed {pre['cancelled']} stale pending(s) for {pair}"
            )

        exec_result = dict(mt5_execute(signal, approval) or {})
        phases.append({"name": "broker_execute", "success": bool(exec_result.get("success"))})

        if exec_result.get("success"):
            rec = mt5_reconcile_after_open(exec_result, signal)
            phases.append({"name": "post_fill_reconcile", "result": rec})
            if rec.get("error") or rec.get("allProtectionsPresent") is not True:
                exec_result["success"] = False
                exec_result["error"] = "MT5_PROTECTION_RECONCILE_FAILED"
                exec_result["reconcile"] = rec
                exec_result.setdefault("lifecycleWarnings", []).append(
                    "mt5_missing_sl_tp_after_fill"
                )

        exec_result["lifecycle"] = {
            "version": LIFECYCLE_VERSION,
            "venue": v,
            "pair": pair,
            "phases": phases,
        }
        return exec_result

    from bybit_executor import (
        bybit_cancel_stale_entry_orders,
        bybit_execute,
        bybit_map_symbol,
        bybit_reconcile_after_open,
    )

    ccxt_sym = bybit_map_symbol(pair) or bybit_map_symbol(signal.get("symbol") or "")
    pre = bybit_cancel_stale_entry_orders(ccxt_sym)
    phases.append({"name": "pre_cleanup_stale_orders", "result": pre})
    if pre.get("error"):
        return {
            "success": False,
            "error": f"PRE_CLEANUP_FAILED:{pre.get('detail') or pre}",
            "lifecycle": {
                "version": LIFECYCLE_VERSION,
                "venue": v,
                "pair": pair,
                "phases": phases,
            },
        }
    if pre.get("cancelled"):
        log.info(
            f"[LIFECYCLE] Bybit pre-cleanup removed {pre['cancelled']} stale order(s) for {ccxt_sym}"
        )

    exec_result = dict(bybit_execute(signal, approval) or {})
    phases.append({"name": "broker_execute", "success": bool(exec_result.get("success"))})

    if exec_result.get("success"):
        rec = bybit_reconcile_after_open(exec_result, signal)
        phases.append({"name": "post_fill_reconcile", "result": rec})
        if rec.get("repaired"):
            log.warning(
                f"[LIFECYCLE] Bybit post-fill repaired missing SL/TP on {ccxt_sym}"
            )
        if rec.get("error"):
            exec_result.setdefault("lifecycleWarnings", []).append(
                f"reconcile_error:{rec.get('detail', rec)}"
            )

    exec_result["lifecycle"] = {
        "version": LIFECYCLE_VERSION,
        "venue": v,
        "pair": pair,
        "phases": phases,
    }
    return exec_result
