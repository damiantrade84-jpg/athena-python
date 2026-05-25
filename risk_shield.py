"""risk_shield.py — Self-healing execution shield for Athena.

Wraps execution paths with state verification, position reconciliation,
and circuit breakers. The last line of defence before capital is deployed.

Usage:
    from risk_shield import shielded_execute, shield_status

    # Instead of calling risk_check + executor directly:
    result = shielded_execute(
        signal=signal,
        account_fn=bybit_get_account,
        positions_fn=bybit_get_positions,
        symbol_info_fn=lambda: bybit_get_symbol_info(pair),
        executor_fn=bybit_execute,
        risk_check_fn=risk_check,
        kill_switch=False,
    )
    # result has success, error, approval, execution_result, shield_checks

    # Dashboard status:
    status = shield_status()
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

log = logging.getLogger("sentinel")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")

# ═══════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER — Auto-disable after repeated failures
# ═══════════════════════════════════════════════════════════════════════

_breaker_lock = threading.Lock()
_failure_count: int = 0
_last_failure_ts: float = 0.0
_breaker_open: bool = False
_breaker_open_until: float = 0.0

# Config
_BREAKER_THRESHOLD = 3       # consecutive failures to trip breaker
_BREAKER_COOLDOWN_SEC = 300  # 5 minutes cooldown after trip
_FAILURE_WINDOW_SEC = 600    # failures must be within 10 minutes to count


def _record_failure(reason: str):
    """Record an execution failure. Trip breaker if threshold reached."""
    global _failure_count, _last_failure_ts, _breaker_open, _breaker_open_until
    now = time.time()
    with _breaker_lock:
        # Reset counter if too much time has passed since last failure
        if now - _last_failure_ts > _FAILURE_WINDOW_SEC:
            _failure_count = 0
        _failure_count += 1
        _last_failure_ts = now
        if _failure_count >= _BREAKER_THRESHOLD and not _breaker_open:
            _breaker_open = True
            _breaker_open_until = now + _BREAKER_COOLDOWN_SEC
            log.critical(
                f"[SHIELD] CIRCUIT BREAKER TRIPPED: {_failure_count} consecutive failures "
                f"({reason}). Trading disabled for {_BREAKER_COOLDOWN_SEC}s."
            )


def _record_success():
    """Record a successful execution. Reset failure counter."""
    global _failure_count, _breaker_open
    with _breaker_lock:
        _failure_count = 0
        if _breaker_open and time.time() >= _breaker_open_until:
            _breaker_open = False
            log.info("[SHIELD] Circuit breaker RESET after successful execution")


def _breaker_check() -> tuple[bool, str]:
    """Check if circuit breaker allows execution. Returns (allowed, reason)."""
    with _breaker_lock:
        if not _breaker_open:
            return True, "OK"
        now = time.time()
        if now >= _breaker_open_until:
            # Cooldown expired — allow one attempt (half-open state)
            return True, "HALF_OPEN"
        remaining = int(_breaker_open_until - now)
        return False, f"CIRCUIT_BREAKER: {remaining}s remaining"


# ═══════════════════════════════════════════════════════════════════════
# POSITION RECONCILIATION
# ═══════════════════════════════════════════════════════════════════════

def _reconcile_positions(
    positions_fn: Callable,
    pair: str,
) -> tuple[bool, list, str]:
    """Fetch and validate positions. Returns (ok, positions_list, reason).

    Fail-closed: if positions cannot be fetched or error is flagged, return False.
    """
    try:
        raw = positions_fn()
    except Exception as e:
        return False, [], f"POSITIONS_FETCH_EXCEPTION: {e}"

    if raw is None:
        return False, [], "POSITIONS_RETURNED_NONE"

    if isinstance(raw, dict):
        if raw.get("error"):
            detail = raw.get("detail", "unknown")
            return False, [], f"POSITIONS_ERROR: {detail}"
        positions = raw.get("positions", [])
        if not isinstance(positions, list):
            return False, [], f"POSITIONS_NOT_LIST: {type(positions)}"
        return True, positions, "OK"

    if isinstance(raw, list):
        return True, raw, "OK"

    return False, [], f"POSITIONS_UNEXPECTED_TYPE: {type(raw)}"


def _validate_account(
    account_fn: Callable,
) -> tuple[bool, dict, str]:
    """Fetch and validate account. Returns (ok, account_dict, reason)."""
    try:
        account = account_fn()
    except Exception as e:
        return False, {}, f"ACCOUNT_FETCH_EXCEPTION: {e}"

    if not account:
        return False, {}, "ACCOUNT_RETURNED_EMPTY"

    if isinstance(account, dict) and account.get("error"):
        return False, {}, f"ACCOUNT_ERROR: {account.get('detail', 'unknown')}"

    balance = account.get("balance", 0)
    equity = account.get("equity", 0)

    if balance <= 0:
        return False, account, f"ZERO_BALANCE: {balance}"
    if equity <= 0:
        return False, account, f"ZERO_EQUITY: {equity}"

    return True, account, "OK"


# ═══════════════════════════════════════════════════════════════════════
# POST-TRADE VERIFICATION
# ═══════════════════════════════════════════════════════════════════════

def _verify_execution(
    result: dict,
    signal: dict,
) -> dict:
    """Verify execution result matches expectations. Returns verification dict."""
    checks = {}

    if not result.get("success"):
        checks["filled"] = False
        checks["error"] = result.get("error", "unknown")
        return checks

    checks["filled"] = True

    # Verify direction
    exec_dir = result.get("direction", "")
    sig_dir = signal.get("direction", "")
    checks["direction_match"] = exec_dir == sig_dir

    # Verify SL was set
    exec_sl = result.get("sl", 0)
    checks["sl_set"] = exec_sl != 0

    # Verify volume is non-zero
    exec_vol = result.get("volume", 0)
    checks["volume_positive"] = exec_vol > 0

    # Slippage check
    entry = result.get("entryPrice") or result.get("entry_price", 0)
    sig_price = float(signal.get("price", 0) or 0)
    if entry > 0 and sig_price > 0:
        slip_pct = abs(entry - sig_price) / sig_price
        checks["slippage_pct"] = round(slip_pct, 6)
        checks["slippage_acceptable"] = slip_pct < 0.02  # 2% max slippage

    checks["all_ok"] = all(
        v for k, v in checks.items()
        if k not in ("slippage_pct", "error") and isinstance(v, bool)
    )

    return checks


# ═══════════════════════════════════════════════════════════════════════
# SHIELDED EXECUTE — The main entry point
# ═══════════════════════════════════════════════════════════════════════

def shielded_execute(
    signal: dict,
    account_fn: Callable,
    positions_fn: Callable,
    symbol_info_fn: Callable,
    executor_fn: Callable,
    risk_check_fn: Callable,
    kill_switch: bool = False,
    sizing_override: float = 1.0,
    is_manual: bool = False,
    volume_mode: str | None = None,
    execution_context: str | None = None,
) -> dict:
    """Execute a trade with full state verification and circuit breaking.

    This replaces the raw risk_check + executor pattern with a defended version.

    Returns dict with:
        success: bool
        error: str or None
        approval: RiskApproval dict or None
        execution_result: executor result or None
        shield_checks: dict of pre/post verification results
    """
    shield_checks = {}
    pair = signal.get("pair", "UNKNOWN")
    prefix = f"[SHIELD] {pair}"

    # ── Circuit breaker ────────────────────────────────────────────────
    breaker_ok, breaker_reason = _breaker_check()
    shield_checks["circuit_breaker"] = breaker_reason
    if not breaker_ok:
        log.warning(f"{prefix} BLOCKED: {breaker_reason}")
        return {
            "success": False,
            "error": breaker_reason,
            "approval": None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }

    # ── Account verification ───────────────────────────────────────────
    acct_ok, account, acct_reason = _validate_account(account_fn)
    shield_checks["account"] = acct_reason
    if not acct_ok:
        _record_failure(acct_reason)
        log.warning(f"{prefix} BLOCKED: {acct_reason}")
        return {
            "success": False,
            "error": acct_reason,
            "approval": None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }

    # ── Position reconciliation ────────────────────────────────────────
    pos_ok, positions, pos_reason = _reconcile_positions(positions_fn, pair)
    shield_checks["positions"] = pos_reason
    if not pos_ok:
        _record_failure(pos_reason)
        log.warning(f"{prefix} BLOCKED: {pos_reason}")
        return {
            "success": False,
            "error": pos_reason,
            "approval": None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }

    # ── Symbol info ────────────────────────────────────────────────────
    try:
        symbol_info = symbol_info_fn()
    except Exception as e:
        shield_checks["symbol_info"] = f"EXCEPTION: {e}"
        _record_failure(f"SYMBOL_INFO_EXCEPTION: {e}")
        return {
            "success": False,
            "error": f"SYMBOL_INFO_EXCEPTION: {e}",
            "approval": None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }
    shield_checks["symbol_info"] = "OK" if symbol_info else "MISSING"

    # ── Pre-trade validation (guardian) ─────────────────────────────────
    try:
        from guardian import pre_trade_check
        ptc_ok, ptc_reason = pre_trade_check(
            signal, positions, account, positions_raw=None
        )
        shield_checks["guardian"] = ptc_reason
        if not ptc_ok:
            log.warning(f"{prefix} GUARDIAN BLOCK: {ptc_reason}")
            return {
                "success": False,
                "error": f"GUARDIAN: {ptc_reason}",
                "approval": None,
                "execution_result": None,
                "shield_checks": shield_checks,
            }
    except ImportError:
        shield_checks["guardian"] = "NOT_INSTALLED"
    except Exception as e:
        shield_checks["guardian"] = f"ERROR: {e}"
        # Non-fatal — guardian is advisory, risk_check is authoritative

    # ── Risk check ─────────────────────────────────────────────────────
    try:
        approval = risk_check_fn(
            signal=signal,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=kill_switch,
            sizing_override=sizing_override,
            is_manual_override=is_manual,
            volume_mode=volume_mode,
            execution_context=execution_context or ("manual" if is_manual else "auto"),
        )
    except Exception as e:
        _record_failure(f"RISK_CHECK_EXCEPTION: {e}")
        return {
            "success": False,
            "error": f"RISK_CHECK_EXCEPTION: {e}",
            "approval": None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }

    shield_checks["risk_check"] = approval.reason if hasattr(approval, "reason") else "OK"

    if not approval.approved:
        return {
            "success": False,
            "error": f"RISK_REJECTED: {approval.reason}",
            "approval": approval.to_dict() if hasattr(approval, "to_dict") else None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }

    # ── Execute ────────────────────────────────────────────────────────
    try:
        result = executor_fn(signal, approval)
    except Exception as e:
        _record_failure(f"EXECUTOR_EXCEPTION: {e}")
        log.error(f"{prefix} EXECUTOR EXCEPTION: {e}")
        return {
            "success": False,
            "error": f"EXECUTOR_EXCEPTION: {e}",
            "approval": approval.to_dict() if hasattr(approval, "to_dict") else None,
            "execution_result": None,
            "shield_checks": shield_checks,
        }

    # ── Post-trade verification ────────────────────────────────────────
    verification = _verify_execution(result, signal)
    shield_checks["post_trade"] = verification

    if result.get("success"):
        _record_success()
        if not verification.get("all_ok"):
            log.warning(f"{prefix} POST-TRADE WARNING: {verification}")
    else:
        _record_failure(f"EXECUTOR_FAILED: {result.get('error', 'unknown')}")

    return {
        "success": result.get("success", False),
        "error": result.get("error"),
        "approval": approval.to_dict() if hasattr(approval, "to_dict") else None,
        "execution_result": result,
        "shield_checks": shield_checks,
    }


def shield_status() -> dict:
    """Return current shield state for dashboard display."""
    with _breaker_lock:
        return {
            "circuit_breaker_open": _breaker_open,
            "failure_count": _failure_count,
            "breaker_threshold": _BREAKER_THRESHOLD,
            "cooldown_remaining": max(0, int(_breaker_open_until - time.time())) if _breaker_open else 0,
            "last_failure_ts": _last_failure_ts,
        }


def reset_breaker():
    """Manual circuit breaker reset (dashboard action)."""
    global _failure_count, _breaker_open, _breaker_open_until
    with _breaker_lock:
        _failure_count = 0
        _breaker_open = False
        _breaker_open_until = 0.0
        log.info("[SHIELD] Circuit breaker MANUALLY RESET")
