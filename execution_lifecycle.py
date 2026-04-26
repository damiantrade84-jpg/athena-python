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

    _vo = signal.get("vision_output") or signal.get("structured") or {}
    if isinstance(_vo, dict) and _vo.get("confirms_direction") is False:
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
        if pre.get("cancelled"):
            log.info(
                f"[LIFECYCLE] MT5 pre-cleanup removed {pre['cancelled']} stale pending(s) for {pair}"
            )

        exec_result = dict(mt5_execute(signal, approval) or {})
        phases.append({"name": "broker_execute", "success": bool(exec_result.get("success"))})

        if exec_result.get("success"):
            rec = mt5_reconcile_after_open(exec_result, signal)
            phases.append({"name": "post_fill_reconcile", "result": rec})
            if rec.get("allProtectionsPresent") is False:
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
