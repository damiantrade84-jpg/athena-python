"""guardian.py — Pre-flight invariant checker for Athena.

Runs two classes of checks:
1. Boot checks (static): verify code structure hasn't regressed
2. Pre-trade checks (runtime): verify live state before execution

Usage:
    from guardian import boot_check, pre_trade_check

    # On server start:
    report = boot_check()
    if not report["passed"]:
        for f in report["failures"]:
            log.critical(f"[GUARDIAN] BOOT FAIL: {f}")

    # Before every trade:
    ok, reason = pre_trade_check(signal, positions, account)
    if not ok:
        log.critical(f"[GUARDIAN] PRE-TRADE BLOCK: {reason}")
        # Do NOT proceed to risk_check or execution
"""

import inspect
import logging
import os
import re
import time

log = logging.getLogger("sentinel")

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════════════════════════════════
# BOOT CHECKS — Static code analysis (run once on startup)
# ═══════════════════════════════════════════════════════════════════════

def _read_file(filename: str) -> str:
    """Read a project file's contents."""
    path = os.path.join(_PROJECT_ROOT, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"__READ_ERROR__: {e}"


def _check_direction_from_weighted_score() -> tuple[bool, str]:
    """BUG[1] guard: direction must be derived from weighted dir_score, not unweighted dir_sum."""
    src = _read_file("factor_scoring.py")
    if "__READ_ERROR__" in src:
        return False, f"Cannot read factor_scoring.py: {src}"

    # The old broken pattern: direction set immediately after dir_sum, before dir_score computation
    # Look for `direction = "LONG" if dir_sum` appearing BEFORE `dir_score +=` 
    lines = src.split("\n")
    dir_sum_line = None
    direction_from_dirsum_line = None
    dir_score_line = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("dir_sum = sum(active_dir"):
            dir_sum_line = i
        if stripped.startswith("direction = ") and "dir_sum" in stripped:
            direction_from_dirsum_line = i
        if "dir_score +=" in stripped and "weights.get" in stripped:
            dir_score_line = i

    if direction_from_dirsum_line is not None and dir_score_line is not None:
        if direction_from_dirsum_line < dir_score_line:
            return False, (
                f"factor_scoring.py line {direction_from_dirsum_line + 1}: direction derived from "
                f"unweighted dir_sum BEFORE weighted dir_score is computed (line {dir_score_line + 1}). "
                f"Direction can flip vs actual weighted signal."
            )
    return True, "Direction derived from weighted score"


def _check_no_tp_first_dead_code() -> tuple[bool, str]:
    """BUG[5] guard: no TP-first same-bar policy in backtest loops."""
    src = _read_file("backtest_runner.py")
    if "__READ_ERROR__" in src:
        return False, f"Cannot read backtest_runner.py: {src}"
    if "TP checked first" in src:
        # Find the line number
        for i, line in enumerate(src.split("\n")):
            if "TP checked first" in line:
                return False, (
                    f"backtest_runner.py line {i + 1}: dead TP-first code block still present. "
                    f"Remove to prevent future confusion with _resolve_barrier_exit conservative policy."
                )
    return True, "No TP-first dead code"


def _check_positions_fail_closed() -> tuple[bool, str]:
    """BUG[2]/[3] guard: positions error must block execution, not coerce to empty list."""
    failures = []
    for filename in ["execution.py", "auto_trader.py", "athena.py"]:
        src = _read_file(filename)
        if "__READ_ERROR__" in src:
            failures.append(f"Cannot read {filename}")
            continue
        lines = src.split("\n")
        for i, line in enumerate(lines):
            # Find positions fetch calls
            stripped = line.strip()
            if any(fn in stripped for fn in [
                "bybit_get_positions()", "mt5_get_positions()"
            ]) and "def " not in stripped and "#" not in stripped.split("=")[0]:
                # Check if the NEXT non-blank line is a fail-closed guard
                found_guard = False
                for j in range(i + 1, min(i + 5, len(lines))):
                    next_line = lines[j].strip()
                    if not next_line:
                        continue
                    if '.get("error")' in next_line or "error" in next_line and "return" in next_line:
                        found_guard = True
                        break
                    if "positions" in next_line and ".get(" in next_line:
                        # Hit the coercion line before a guard — fail-open
                        break
                    break
                if not found_guard:
                    failures.append(
                        f"{filename} line {i + 1}: positions fetch without fail-closed guard"
                    )
    if failures:
        return False, "; ".join(failures)
    return True, "All positions fetches have fail-closed guards"


def _check_usdjpy_in_cluster() -> tuple[bool, str]:
    """BUG[4] guard: USD/JPY must be in a correlation cluster."""
    try:
        from scoring import _PAIR_TO_CLUSTER
        cluster = _PAIR_TO_CLUSTER.get("USD/JPY")
        if cluster is None:
            return False, "USD/JPY not in any correlation cluster — bypasses MAX_CORRELATED_POSITIONS"
        return True, f"USD/JPY in cluster '{cluster}'"
    except Exception as e:
        return False, f"Cannot import scoring._PAIR_TO_CLUSTER: {e}"


def _check_single_crypto_cap_application() -> tuple[bool, str]:
    """BUG guard: CRYPTO_FACTOR_WEIGHT_CAPS should be applied exactly once."""
    src = _read_file("factor_scoring.py")
    if "__READ_ERROR__" in src:
        return False, f"Cannot read factor_scoring.py: {src}"
    count = src.count("CRYPTO_FACTOR_WEIGHT_CAPS")
    if count > 2:  # 1 in code + 1 in comment is OK; >2 means duplicate application
        return False, f"CRYPTO_FACTOR_WEIGHT_CAPS referenced {count} times — check for double application"
    return True, f"CRYPTO_FACTOR_WEIGHT_CAPS referenced {count} times"


def _check_forex_bt_routing() -> tuple[bool, str]:
    """Guard: all 3 BT loops should route forex to compute_forex_score."""
    src = _read_file("backtest_runner.py")
    if "__READ_ERROR__" in src:
        return False, f"Cannot read backtest_runner.py: {src}"
    count = src.count("compute_forex_score")
    if count < 3:
        return False, (
            f"backtest_runner.py has {count} references to compute_forex_score (expected >= 3). "
            f"H4 and/or H1 forex backtests may use wrong scoring engine."
        )
    return True, f"compute_forex_score referenced {count} times in backtest_runner.py"


def _check_regime_state_not_hardcoded() -> tuple[bool, str]:
    """Guard: forex BT res dicts should not hardcode regime state to 1."""
    src = _read_file("backtest_runner.py")
    if "__READ_ERROR__" in src:
        return False, f"Cannot read backtest_runner.py: {src}"
    lines = src.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == '"state": 1,' and i > 0:
            # Check context: is this inside a forex res dict?
            context = "\n".join(lines[max(0, i - 5):i + 3])
            if "final_score" in context and "forex" in context.lower():
                return False, (
                    f"backtest_runner.py line {i + 1}: regime state hardcoded to 1 (RANGING) "
                    f"in forex backtest result dict"
                )
    return True, "No hardcoded regime state in forex BT dicts"


def _check_risk_check_before_executors() -> tuple[bool, str]:
    """Guard: every executor call must be preceded by risk_check."""
    failures = []
    for filename in ["execution.py", "auto_trader.py", "athena.py"]:
        src = _read_file(filename)
        if "__READ_ERROR__" in src:
            continue
        # Check that live execution paths are preceded by risk_check
        lines = src.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(
                ex in stripped
                for ex in [
                    "bybit_execute(",
                    "mt5_execute(",
                    "run_managed_execution(",
                ]
            ) and "def " not in stripped and "import" not in stripped and "#" not in stripped:
                found_risk = False
                for j in range(max(0, i - 55), i):
                    if "risk_check(" in lines[j]:
                        found_risk = True
                        break
                if not found_risk:
                    failures.append(
                        f"{filename} line {i + 1}: execution call without preceding risk_check"
                    )
    if failures:
        return False, "; ".join(failures)
    return True, "All execution calls preceded by risk_check"


def _check_executors_use_approval_volume() -> tuple[bool, str]:
    """Guard: executors must use approval.volume, never self-size."""
    for filename in ["bybit_executor.py", "mt5_executor.py"]:
        src = _read_file(filename)
        if "__READ_ERROR__" in src:
            continue
        if "approval.volume" not in src:
            return False, f"{filename}: does not reference approval.volume — may be self-sizing"
    return True, "Both executors use approval.volume"


def _check_audit_log_schema_decay_aware() -> tuple[bool, str]:
    """Guard: audit_log table must have max_score and score_pct for Engine B decay monitoring."""
    import sqlite3
    db_path = os.path.join(_PROJECT_ROOT, "audit.db")
    if not os.path.exists(db_path):
        return True, "audit.db not found yet (first run?)"
    try:
        with sqlite3.connect(db_path, timeout=1.0) as con:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(audit_log)")
            cols = [row[1] for row in cur.fetchall()]
            missing = [c for c in ["max_score", "score_pct"] if c not in cols]
            if missing:
                return False, f"audit_log table missing columns: {', '.join(missing)}"
            return True, "audit_log schema is decay-aware"
    except Exception as e:
        return False, f"Failed to check audit_log schema: {e}"


def _check_meta_adaptation_safety_boundary() -> tuple[bool, str]:
    """Guard: adaptive alpha surfaces must stay separated from fixed safety invariants."""
    src = _read_file("meta_learner.py")
    if "__READ_ERROR__" in src:
        return False, f"Cannot read meta_learner.py: {src}"

    required_tokens = [
        "_ADAPTIVE_ALPHA_SURFACES",
        "FIXED_SAFETY_INVARIANTS",
        "fixedSafetyBoundary",
        "calibratedExpectancyWeighting",
        "regimeStyleWeighting",
    ]
    missing = [tok for tok in required_tokens if tok not in src]
    if missing:
        return False, (
            "meta_learner.py missing explicit adaptive/safety boundary markers: "
            + ", ".join(missing)
        )

    # Adaptive policy is not allowed to directly mutate hard safety controls.
    forbidden_safety_knobs = [
        "MAX_SL_PCT",
        "DRAWDOWN_STOP_THRESHOLD",
        "SIGNAL_MAX_AGE_SEC",
        "risk_check(",
        "run_managed_execution(",
    ]
    touched = [k for k in forbidden_safety_knobs if k in src]
    if touched:
        return False, (
            "meta_learner.py references safety-critical controls that must remain non-adaptive: "
            + ", ".join(touched)
        )

    return True, "Adaptive alpha and fixed safety boundary is explicit and locked"


# ── Master boot check ───────────────────────────────────────────────────

_BOOT_CHECKS = [
    ("direction_weighted", _check_direction_from_weighted_score),
    ("no_tp_first", _check_no_tp_first_dead_code),
    ("positions_fail_closed", _check_positions_fail_closed),
    ("usdjpy_cluster", _check_usdjpy_in_cluster),
    ("single_crypto_cap", _check_single_crypto_cap_application),
    ("forex_bt_routing", _check_forex_bt_routing),
    ("regime_not_hardcoded", _check_regime_state_not_hardcoded),
    ("risk_check_before_exec", _check_risk_check_before_executors),
    ("executors_approval_vol", _check_executors_use_approval_volume),
    ("audit_log_decay_schema", _check_audit_log_schema_decay_aware),
    ("meta_adaptation_safety_boundary", _check_meta_adaptation_safety_boundary),
]


def boot_check() -> dict:
    """Run all static invariant checks. Call once on server start.

    Returns:
        dict with:
            passed: bool — True if ALL checks pass
            failures: list[str] — human-readable failure descriptions
            checks: dict[str, dict] — per-check results
            elapsed_ms: float
    """
    t0 = time.time()
    results = {}
    failures = []

    for name, fn in _BOOT_CHECKS:
        try:
            ok, detail = fn()
            results[name] = {"passed": ok, "detail": detail}
            if not ok:
                failures.append(f"[{name}] {detail}")
                log.critical(f"[GUARDIAN] BOOT FAIL [{name}]: {detail}")
            else:
                log.info(f"[GUARDIAN] BOOT OK [{name}]: {detail}")
        except Exception as e:
            results[name] = {"passed": False, "detail": f"Exception: {e}"}
            failures.append(f"[{name}] Exception: {e}")
            log.critical(f"[GUARDIAN] BOOT ERROR [{name}]: {e}")

    elapsed = (time.time() - t0) * 1000
    passed = len(failures) == 0

    if passed:
        log.info(f"[GUARDIAN] All {len(_BOOT_CHECKS)} boot checks PASSED ({elapsed:.0f}ms)")
    else:
        log.critical(
            f"[GUARDIAN] {len(failures)}/{len(_BOOT_CHECKS)} boot checks FAILED ({elapsed:.0f}ms)"
        )

    return {
        "passed": passed,
        "failures": failures,
        "checks": results,
        "elapsed_ms": round(elapsed, 1),
        "total_checks": len(_BOOT_CHECKS),
    }


# ═══════════════════════════════════════════════════════════════════════
# PRE-TRADE CHECKS — Runtime validation (run before every execution)
# ═══════════════════════════════════════════════════════════════════════

def pre_trade_check(
    signal: dict,
    positions: list,
    account: dict,
    positions_raw: dict | None = None,
) -> tuple[bool, str]:
    """Validate signal + state before execution. Returns (ok, reason).

    Args:
        signal: Full signal dict from analyze_pair
        positions: List of open position dicts
        account: Account dict with balance, equity
        positions_raw: Raw positions response from get_positions() (to check error flag)

    Returns:
        (True, "OK") if all checks pass
        (False, "reason") if any check fails — do NOT proceed to risk_check
    """
    # ── Check 0: Positions state is known ──────────────────────────────
    if positions_raw is not None:
        if isinstance(positions_raw, dict) and positions_raw.get("error"):
            detail = positions_raw.get("detail", "unknown")
            return False, f"POSITIONS_UNKNOWN: {detail}"

    # ── Check 1: Account is connected and has balance ──────────────────
    if not account:
        return False, "ACCOUNT_MISSING"
    balance = account.get("balance", 0)
    equity = account.get("equity", 0)
    if balance <= 0:
        return False, f"ZERO_BALANCE: {balance}"
    if equity <= 0:
        return False, f"ZERO_EQUITY: {equity}"

    # ── Check 2: Signal has required fields ────────────────────────────
    required = ["pair", "direction", "price", "sl", "tp1"]
    missing = [f for f in required if not signal.get(f)]
    if missing:
        return False, f"SIGNAL_MISSING_FIELDS: {', '.join(missing)}"

    direction = signal.get("direction", "").upper()
    if direction not in ("LONG", "SHORT"):
        return False, f"INVALID_DIRECTION: {signal.get('direction')}"

    # ── Check 3: SL/TP on correct side of entry ───────────────────────
    price = float(signal.get("price", 0))
    sl = float(signal.get("sl", 0))
    tp1 = float(signal.get("tp1", 0))

    if price <= 0 or sl <= 0 or tp1 <= 0:
        return False, f"INVALID_LEVELS: price={price}, sl={sl}, tp1={tp1}"

    if direction == "LONG":
        if sl >= price:
            return False, f"LONG_SL_ABOVE_ENTRY: sl={sl} >= price={price}"
        if tp1 <= price:
            return False, f"LONG_TP_BELOW_ENTRY: tp1={tp1} <= price={price}"
    else:
        if sl <= price:
            return False, f"SHORT_SL_BELOW_ENTRY: sl={sl} <= price={price}"
        if tp1 >= price:
            return False, f"SHORT_TP_ABOVE_ENTRY: tp1={tp1} >= price={price}"

    # ── Check 4: Direction matches directional score sign ──────────────
    dir_score = signal.get("factorDiagnostics", {}).get("directionalScore")
    if dir_score is not None and dir_score != 0:
        expected_dir = "LONG" if dir_score > 0 else "SHORT"
        if direction != expected_dir:
            return False, (
                f"DIRECTION_SCORE_MISMATCH: direction={direction} but "
                f"directionalScore={dir_score:.4f} implies {expected_dir}"
            )

    # ── Check 5: SL distance within MAX_SL_PCT ────────────────────────
    try:
        from config import CONFIG
        asset_type = signal.get("type", "")
        max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(asset_type, 0.08)
        sl_dist_pct = abs(price - sl) / price
        if sl_dist_pct > max_sl_pct * 1.05:  # 5% tolerance for rounding
            return False, (
                f"SL_EXCEEDS_CAP: {sl_dist_pct:.1%} > {max_sl_pct:.1%} for {asset_type}"
            )
    except Exception:
        pass  # Degrade gracefully if config unavailable

    # ── Check 6: Signal freshness ──────────────────────────────────────
    ts_str = signal.get("timestamp")
    if ts_str:
        try:
            from datetime import datetime, timezone
            sig_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - sig_time).total_seconds()
            max_age = 600  # 10 minutes — generous for pre-trade check
            if age > max_age:
                return False, f"STALE_SIGNAL: {age:.0f}s old (max {max_age}s)"
        except Exception:
            pass  # Non-fatal — risk_check also validates freshness

    # ── Check 7: Drawdown circuit breaker ──────────────────────────────
    try:
        from config import CONFIG
        dd_stop = CONFIG.get("DRAWDOWN_STOP_THRESHOLD", 0.15)
        if equity > 0 and balance > 0:
            # Simple drawdown estimate from balance vs equity
            if equity < balance * (1 - dd_stop):
                return False, f"DRAWDOWN_EXCEEDED: equity={equity:.2f} vs balance={balance:.2f}"
    except Exception:
        pass

    return True, "OK"


# ═══════════════════════════════════════════════════════════════════════
# INTEGRATION HELPERS
# ═══════════════════════════════════════════════════════════════════════

def guardian_summary() -> dict:
    """Return a JSON-serializable summary for the dashboard API."""
    report = boot_check()
    return {
        "guardian_passed": report["passed"],
        "guardian_failures": report["failures"],
        "guardian_check_count": report["total_checks"],
        "guardian_elapsed_ms": report["elapsed_ms"],
    }
