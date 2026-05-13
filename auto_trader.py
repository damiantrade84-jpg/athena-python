"""auto_trader.py — Autonomous trade execution scheduler for Athena Pro.



Fires at H4 candle closes, runs the full scan, and auto-executes qualifying

signals through the same risk engine + executor path as manual execution.



Thread-safe. All athena references are passed in via factory functions to

avoid circular imports (auto_trader is imported BY athena, not the reverse).

"""

import json

import logging

import threading

import time

from datetime import datetime, timezone, timedelta

from calibration import predict_calibrated_prob
from meta_learner import apply_meta_policy, meta_report
from sqlite_instrumentation import (
    timed_sqlite_connect,
    timed_sqlite_commit,
    timed_sqlite_execute_write,
)
from stability_monitor import get_signal_stability_index, record_execution_event
from timed_exit_monitor import start_monitor

log = logging.getLogger("athena.auto_trader")


def _auto_trade_min_conviction_config(cfg: dict) -> dict:
    """Return normalized live auto-trade conviction config."""
    raw = cfg.get("AUTO_TRADE_MIN_CONVICTION", {"default": 0.50})
    if isinstance(raw, dict):
        out = {}
        for key, value in raw.items():
            try:
                out[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        if not out:
            out["default"] = 0.50
        elif "default" not in out:
            out["default"] = 0.50
        return out
    try:
        return {"default": float(raw)}
    except (TypeError, ValueError):
        return {"default": 0.50}


def _auto_trade_min_conviction(cfg: dict, asset_type: str) -> float:
    conf = _auto_trade_min_conviction_config(cfg)
    return conf.get(asset_type, conf.get("default", 0.50))


def _auto_trade_live_gate_display(cfg: dict) -> str:
    conf = _auto_trade_min_conviction_config(cfg)
    if list(conf.keys()) == ["default"]:
        base = conf["default"]
        return (
            f"combinedConviction >= {base:.2f} "
            f"(aligned >= {base * 0.85:.3f})"
        )

    parts = []
    for key, value in conf.items():
        if key == "default":
            continue
        parts.append(f"{key} {value:.2f}")
    if "default" in conf:
        parts.append(f"default {conf['default']:.2f}")
    return "combinedConviction by asset (" + ", ".join(parts) + "; aligned x0.85)"


def _signal_engine(signal: dict) -> str:
    engine = str(signal.get("engine") or "").strip().lower()
    if engine == "scalp":
        return "scalp"
    if engine in ("engine_c", "consensus"):
        return "engine_c"
    return "engine_a"


def _signal_expected_prob(signal: dict) -> float | None:
    """Best-effort normalized expectancy proxy for SSI execution samples."""
    for key in ("calibratedProbability", "combinedConviction", "scoreNorm"):
        raw = signal.get(key)
        if raw is None:
            continue
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            continue
    try:
        score = float(signal.get("confluenceScore"))
        max_score = float(signal.get("maxScore", 0) or 0)
    except (TypeError, ValueError):
        return None
    if max_score > 0:
        return max(0.0, min(1.0, score / max_score))
    if 0.0 <= score <= 1.0:
        return score
    return None


def _signal_factor_scores(signal: dict) -> dict:
    raw = signal.get("factorScores")
    if isinstance(raw, dict):
        return raw
    raw = signal.get("factor_scores")
    return raw if isinstance(raw, dict) else {}


def _signal_factor_weights(signal: dict) -> dict:
    raw = signal.get("factorWeights")
    if isinstance(raw, dict):
        return raw
    raw = signal.get("factor_weights")
    return raw if isinstance(raw, dict) else {}


def _current_combined_conviction(signal: dict) -> float:
    """Return the execution conviction implied by the signal's current scores."""
    try:
        score = float(signal.get("confluenceScore", 0) or 0)
        max_score = float(signal.get("maxScore", 0) or 0)
    except (TypeError, ValueError):
        return 0.0

    if max_score > 0:
        a_norm = max(0.0, min(score / max_score, 1.0))
    elif 0.0 <= score <= 1.0:
        a_norm = score
    else:
        a_norm = 0.0

    if signal.get("enginesAligned", False):
        try:
            b_score = float(signal.get("engine_b_score", 0) or 0)
            b_max = float(signal.get("engine_b_max", 0) or 0)
        except (TypeError, ValueError):
            b_score = 0.0
            b_max = 0.0
        b_norm = max(0.0, min(b_score / b_max, 1.0)) if b_max > 0 else 0.0
        return round((a_norm * 0.6) + (b_norm * 0.4), 4)

    return round(a_norm * 0.6, 4)


def _a_only_reject_reason(
    signal: dict,
    combined_conviction: float,
    auto_min_conviction: float,
) -> str | None:
    """Explain the A-only auto gate in Engine A score terms when possible."""
    if signal.get("enginesAligned") is not False:
        return None

    try:
        score = float(signal.get("confluenceScore", 0) or 0)
        max_score = float(signal.get("maxScore", 0) or 0)
    except (TypeError, ValueError):
        return None
    if max_score <= 0 or score < 0:
        return None

    a_norm = score / max_score
    if a_norm <= 0:
        return None
    weight = combined_conviction / a_norm
    if weight <= 0:
        return None

    required_score = (auto_min_conviction / weight) * max_score
    return (
        f"A-only conviction {combined_conviction:.3f} < min {auto_min_conviction:.3f} "
        f"(Engine A score {score:.2f}/{max_score:.1f}; requires about "
        f"{required_score:.2f}/{max_score:.1f} at A-only weight {weight:.2f})"
    )


def _normalize_pair_key(value: str | None) -> str:
    """Normalize pair or symbol identifiers for duplicate-position matching."""
    if value is None:
        return ""
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _signal_matches_position(signal: dict, position: dict) -> bool:
    """Return True when a broker position refers to the same instrument."""
    signal_keys = {
        _normalize_pair_key(signal.get("pair")),
        _normalize_pair_key(signal.get("symbol")),
    }
    position_keys = {
        _normalize_pair_key(position.get("pair")),
        _normalize_pair_key(position.get("symbol")),
    }
    signal_keys.discard("")
    position_keys.discard("")
    return bool(signal_keys & position_keys)


# ── H4 + D1 schedule: (hour, minute) UTC ─────────────────────────────────────

_SCHEDULE = [(0, 5), (4, 5), (8, 5), (12, 5), (16, 5), (20, 5)]


# ── Session windows (UTC) for session-aware filtering ────────────────────────

_SESSIONS = {
    "london": (7, 16),  # 07:00–16:00
    "new_york": (13, 21),  # 13:00–21:00
    "london_ny_overlap": (13, 16),  # 13:00–16:00
    "jse": (7, 15),  # 07:00–15:00 (JSE)
    "us_regular": (14, 21),  # 14:30–21:00 simplified
}


class AutoTrader:
    """Autonomous trading scheduler. Start with .enable(); stop with .disable()."""

    def __init__(self):

        self._enabled = False

        self._running = False

        self._thread = None

        self._lock = threading.Lock()

        self._trades_today = 0

        self._last_date = ""  # YYYY-MM-DD UTC

        self._last_scan_at = None  # datetime

        self._last_exec_at = None  # datetime

        self._last_meta_analysis = None  # datetime of last weekly meta-analysis

        self._last_exec_pair = ""

        self._last_exec_dir = ""

        self._executed_slots: set = set()  # "YYYY-MM-DD_HH" keys to prevent double-fire

        self._scan_now = False  # fire immediate scan on next loop tick

        self._last_interval_scan = None  # for interval-based scanning

        # Injected at init time by athena.py to avoid circular imports

        self._run_scan_fn = None  # callable: run_full_scan(style, asset_class)

        self._kill_switch_fn = None  # callable: () -> bool

        self._test_mode_fn = None  # callable: () -> bool

        self._audit_db = None  # str path

        self._config_fn = None  # callable: () -> dict  (returns CONFIG)

    def configure(
        self, run_scan_fn, kill_switch_fn, test_mode_fn, audit_db: str, config_fn
    ):
        """Called once by athena.py at startup to inject dependencies."""

        self._run_scan_fn = run_scan_fn

        self._kill_switch_fn = kill_switch_fn

        self._test_mode_fn = test_mode_fn

        self._audit_db = audit_db

        self._config_fn = config_fn

        # Start timed exit monitor (hybrid triple-barrier C-barrier)
        if audit_db:
            start_monitor(audit_db, config_fn)

    # ── Public control ────────────────────────────────────────────────────────

    def enable(self):

        with self._lock:
            self._enabled = True

            self._scan_now = True  # trigger immediate first scan

            if not self._running:
                self._start_thread()

        log.warning("[AUTO] Auto-trader ENABLED — first scan will fire in ~30s")

    def disable(self):

        with self._lock:
            self._enabled = False

        log.warning("[AUTO] Auto-trader DISABLED")

    def toggle(self):

        with self._lock:
            was_enabled = self._enabled

        if was_enabled:
            self.disable()

        else:
            self.enable()

    def get_status(self) -> dict:

        cfg = self._config_fn() if self._config_fn else {}
        scan_min_score = cfg.get("AUTO_TRADE_MIN_SCORE", {})
        min_conviction = _auto_trade_min_conviction_config(cfg)

        return {
            "enabled": self._enabled,
            "tradesToday": self._trades_today,
            "maxDaily": cfg.get("AUTO_TRADE_MAX_DAILY", 3),
            "minScore": scan_min_score,
            "scanMinScore": scan_min_score,
            "minScoreExecutionNote": (
                "DEPRECATED — informational only. Changing this has no effect on execution. "
                "Live execution uses combinedConviction and AUTO_TRADE_MIN_CONVICTION."
            ),
            "minScoreDeprecatedForExecution": True,
            "minScoreDeprecated": True,
            "minConviction": min_conviction,
            "liveGateMetric": "combinedConviction",
            "liveGateAlignedDiscount": 0.85,
            "liveGateDisplay": _auto_trade_live_gate_display(cfg),
            "lastScanAt": self._last_scan_at.isoformat()
            if self._last_scan_at
            else None,
            "lastExecutionAt": self._last_exec_at.isoformat()
            if self._last_exec_at
            else None,
            "lastExecutionPair": self._last_exec_pair,
            "lastExecutionDir": self._last_exec_dir,
            "nextScanAt": self._next_scan_time().isoformat(),
            "signalStability": get_signal_stability_index(db_path=self._audit_db)
            if self._audit_db
            else None,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _start_thread(self):

        self._running = True

        self._thread = threading.Thread(
            target=self._scheduler_loop, name="AutoTrader", daemon=True
        )

        self._thread.start()

        log.info("[AUTO] Scheduler thread started")

    def _scheduler_loop(self):

        while self._running:
            try:
                time.sleep(30)

                if not self._enabled:
                    continue

                now = datetime.now(timezone.utc)

                self._reset_daily_counter(now)

                cfg_early = self._config_fn() if self._config_fn else {}

                # Weekly meta-analysis: Sunday 22:00 UTC (xAI). Respects META_ANALYSIS_ENABLED.
                if cfg_early.get("META_ANALYSIS_ENABLED", True) and now.weekday() == 6 and now.hour == 22:
                    if (
                        self._last_meta_analysis is None
                        or (now - self._last_meta_analysis).days >= 6
                    ):
                        self._last_meta_analysis = now
                        threading.Thread(
                            target=self._run_weekly_meta_analysis,
                            args=(cfg_early,),
                            daemon=True,
                            name="AUTO-MetaAnalysis",
                        ).start()

                # Immediate scan on enable (first scan)

                with self._lock:
                    scan_now = self._scan_now
                if scan_now:
                    with self._lock:
                        self._scan_now = False
                        self._last_interval_scan = now

                    log.info("[AUTO] Running immediate first scan...")

                    self._run_auto_scan()

                    continue

                cfg = self._config_fn() if self._config_fn else {}

                interval_min = cfg.get(
                    "AUTO_TRADE_SCAN_INTERVAL_MIN", 30
                )  # default 30min

                # Interval-based scan (every N minutes)

                with self._lock:
                    last_interval_scan = self._last_interval_scan
                if last_interval_scan is None:
                    with self._lock:
                        self._last_interval_scan = now
                    continue

                elapsed = (now - last_interval_scan).total_seconds() / 60

                if elapsed >= interval_min:
                    with self._lock:
                        self._last_interval_scan = now

                    # Convert UTC to local time for display only (don't affect scheduling logic)
                    local_now = datetime.fromtimestamp(now.timestamp())
                    log.info(
                        f"[AUTO] Interval scan ({interval_min}min) firing at {local_now.strftime('%H:%M')}"
                    )

                    self._run_auto_scan()

            except Exception as e:
                log.error(f"[AUTO] Scheduler loop error: {e}")

    def _reset_daily_counter(self, now: datetime):

        today = now.strftime("%Y-%m-%d")

        if today != self._last_date:
            self._last_date = today

            self._trades_today = 0

    def _next_scan_time(self) -> datetime:

        cfg = self._config_fn() if self._config_fn else {}

        interval_min = cfg.get("AUTO_TRADE_SCAN_INTERVAL_MIN", 30)

        if self._last_interval_scan:
            return self._last_interval_scan + timedelta(minutes=interval_min)

        return datetime.now(timezone.utc) + timedelta(minutes=interval_min)

    def _run_weekly_meta_analysis(self, cfg: dict) -> None:
        """Run weekly meta-analysis in a background thread to avoid blocking the scheduler loop."""
        try:
            from ai_learning import run_meta_analysis

            _meta_result = run_meta_analysis(
                self._audit_db,
                cfg.get("XAI_API_KEY", ""),
                cfg.get("XAI_MODEL", "grok-4.20-0309-reasoning"),
            )
            log.info(
                f"[AUTO] Weekly meta-analysis complete: {_meta_result.get('summary', '')[:100]}"
            )
        except Exception as _me:
            log.warning(f"[AUTO] Weekly meta-analysis failed: {_me}")

    def _run_auto_scan(self):
        """Run a full scan and auto-execute qualifying signals."""

        if not self._run_scan_fn:
            log.warning("[AUTO] run_scan_fn not configured — skipping")

            return

        if self._kill_switch_fn and self._kill_switch_fn():
            log.info("[AUTO] Kill switch active — scan skipped")

            return

        cfg = self._config_fn() if self._config_fn else {}

        max_daily = cfg.get("AUTO_TRADE_MAX_DAILY", 3)

        with self._lock:
            trades_today = self._trades_today
        if trades_today >= max_daily:
            log.info(
                f"[AUTO] Daily cap reached ({trades_today}/{max_daily}) — scan skipped"
            )

            return

        self._last_scan_at = datetime.now(timezone.utc)

        log.info("[AUTO] Running scan...")

        try:
            result = self._run_scan_fn(style="auto")

        except Exception as e:
            log.error(f"[AUTO] Scan failed: {e}")

            return

        if not result.get("success"):
            log.info(f"[AUTO] Scan returned no success: {result.get('error', '')}")

            return

        signals = result.get("tradeSignals", [])

        watchlist = result.get("watchlist", [])

        funnel = result.get("scanFunnel", {})

        log.info(
            f"[AUTO] Scan done — passed={len(signals)} watchlist={len(watchlist)} "
            f"low_score={funnel.get('low_score', 0)} closed={funnel.get('closed_exchange', 0)} "
            f"inactive={funnel.get('inactive_pair', 0)}"
        )

        if not signals:
            if watchlist:
                top = watchlist[0]

                log.info(
                    f"[AUTO] Best near-miss: {top.get('pair')} score={top.get('confluenceScore')}/{top.get('maxScore')} ({top.get('direction')})"
                )

            return

        # Re-stamp signals generated during this scan cycle to scan-completion time.
        # analyze_pair() timestamps each signal individually; with 90 pairs / 3 workers,
        # the first pairs in the queue can be 3-5 min old by the time the scan returns.
        # Guard: only re-stamp signals within AUTO_TRADE_MAX_SCAN_AGE_SEC of scan start;
        # anything older was not produced in this cycle and must not bypass freshness checks.
        _scan_ts = datetime.now(timezone.utc)
        _scan_ts_iso = _scan_ts.isoformat()
        _max_scan_window = cfg.get("AUTO_TRADE_MAX_SCAN_AGE_SEC", 900)  # 15 min hard cap
        for _s in signals:
            try:
                _sig_age = (_scan_ts - datetime.fromisoformat(
                    _s["timestamp"].replace("Z", "+00:00")
                )).total_seconds()
            except Exception:
                _sig_age = 0
            if _sig_age <= _max_scan_window:
                _s["timestamp"] = _scan_ts_iso
            else:
                log.warning(
                    f"[AUTO] Signal {_s.get('pair')} is {_sig_age:.0f}s old "
                    f"(> {_max_scan_window}s cap) — not re-stamping; will be rejected by risk_check"
                )

        # Sort by combined conviction (Engine A+B) descending — best signal first
        # Falls back to normalized confluenceScore if combinedConviction not present

        signals = sorted(signals, key=_current_combined_conviction, reverse=True)

        max_per_scan = cfg.get("AUTO_TRADE_MAX_PER_SCAN", 1)

        executed = 0
        positions_cache: dict[str, list[dict] | None] = {}

        for sig in signals:
            if executed >= max_per_scan:
                break

            with self._lock:
                if self._trades_today >= max_daily:
                    break

            ok, reason = self._can_execute(sig, cfg)

            if not ok:
                log.info(f"[AUTO] {sig.get('pair')} skipped: {reason}")

                continue

            open_positions = self._get_cached_open_positions(
                sig.get("type", ""), positions_cache
            )
            if open_positions is not None:
                # Block same-pair duplicates
                if any(_signal_matches_position(sig, pos) for pos in open_positions):
                    log.info(
                        f"[AUTO] {sig.get('pair')} skipped: already holding open position"
                    )
                    continue

                # Block when venue is already at max positions — avoids wasting the full
                # execution path (AI debate, sentiment, risk_check) on a guaranteed rejection
                _max_open = int(cfg.get("MAX_OPEN_POSITIONS", 5))
                if len(open_positions) >= _max_open:
                    log.info(
                        f"[AUTO] {sig.get('pair')} skipped: {len(open_positions)} open "
                        f"positions >= MAX_OPEN_POSITIONS {_max_open}"
                    )
                    continue

            success = self._execute_signal(sig, cfg)

            if success:
                executed += 1

        log.info(f"[AUTO] Scan complete — {executed} trade(s) executed")

    def _hydrate_conductor_routing(self, signal: dict) -> None:
        """Attach deterministic conductor routing to signal when audit DB is configured."""
        _audit = getattr(self, "_audit_db", None) or ""
        if not _audit:
            return
        ex = signal.get("conductor")
        if isinstance(ex, dict) and "skip_signal" in ex and "run_debate" in ex:
            return
        try:
            from conductor import conductor_orchestrate, extract_conductor_microstructure

            _vd, _sr = extract_conductor_microstructure(signal)
            plan = conductor_orchestrate(
                signal,
                signal.get("regime", "UNKNOWN"),
                _audit,
                volume_divergence=_vd,
                stop_run=_sr,
                news_risk=None,
            )
            signal["conductor"] = plan.get("routing", {})
            signal["conductor_context"] = plan.get("context", {})
        except Exception as _e:
            log.warning("[AUTO] conductor hydrate failed: %s", _e)

    def _can_execute(self, signal: dict, cfg: dict) -> tuple[bool, str]:
        """Check score gate + session filter. Uses combined Engine A+B conviction when available.

        Full-scan trade-tier labeling does not require Engine B alignment when
        ``ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED`` is false; autopilot still
        evaluates ``combinedConviction`` and alignment bonus independently here.
        """

        asset_type = signal.get("type", "")

        # ── Combined conviction scoring (Engine A + B) ──
        # combinedConviction is 0-1 scale: (A_norm * 0.6) + (B_norm * 0.4) when aligned
        # Falls back to A_norm * 0.6 when Engine B has no clear signal
        combined_conviction = signal.get("combinedConviction")
        engines_aligned = signal.get("enginesAligned", False)

        # Fallback: recompute the same execution conviction contract used elsewhere.
        if combined_conviction is None:
            combined_conviction = _current_combined_conviction(signal)
            log.warning(
                f"[AUTO] {signal.get('pair')} missing combinedConviction — "
                f"fallback recompute produced {combined_conviction:.3f}"
            )

        # Auto-trade minimum conviction (0-1 scale)
        # Default: 0.50 = requires decent Engine A score OR Engine A+B alignment
        # Higher values (0.60-0.70) = stricter, prefer aligned signals
        auto_min_conviction = _auto_trade_min_conviction(cfg, asset_type)

        # Bonus threshold for aligned signals (both engines agree)
        # Aligned signals can pass at lower conviction since structure confirms
        if engines_aligned:
            auto_min_conviction = auto_min_conviction * 0.85  # 15% discount for alignment

        # Engine B data for logging
        b_score = signal.get("engine_b_score", 0)
        b_max = signal.get("engine_b_max", 5)
        b_verdict = signal.get("engine_b_verdict", "N/A")
        calibration = predict_calibrated_prob(
            combined_conviction,
            engine=_signal_engine(signal),
            asset_class=asset_type,
            style=signal.get("style"),
            max_score=1.0,
            db_path=self._audit_db,
        )
        signal["calibratedProbability"] = calibration.get("calibrated_prob")
        signal["calibration"] = calibration
        meta = meta_report(signal, db_path=self._audit_db) if self._audit_db else meta_report(signal, records=[])
        signal.update(apply_meta_policy(signal, meta))
        meta_delta = float(signal.get("metaThresholdDelta", 0.0) or 0.0)
        auto_min_conviction = max(0.0, min(1.0, auto_min_conviction + meta_delta))

        log.info(
            f"[AUTO] {signal.get('pair')} candidate: conviction={combined_conviction:.3f} min={auto_min_conviction:.3f} "
            f"cal={calibration.get('calibrated_prob')} scope={calibration.get('scope_used')} "
            f"fallback={calibration.get('fallback_reason')} "
            f"meta_w={signal.get('metaEngineWeight')} meta_d={signal.get('metaThresholdDelta')} "
            f"meta_suspend={signal.get('metaSuspended')} "
            f"aligned={engines_aligned} A={signal.get('confluenceScore', 0):.2f}/{signal.get('maxScore', 3)} "
            f"B={b_score}/{b_max} ({b_verdict}) dir={signal.get('direction')}"
        )

        if signal.get("metaSuspended"):
            return False, "meta policy suspended this engine bucket"

        if combined_conviction < auto_min_conviction:
            a_only_reason = _a_only_reject_reason(
                signal,
                float(combined_conviction),
                float(auto_min_conviction),
            )
            if a_only_reason:
                return False, a_only_reason
            return False, f"conviction {combined_conviction:.3f} < min {auto_min_conviction:.3f}"

        # Regime filter — only execute in TRENDING regime (37% WR vs 11% RANGING, 0% DEVELOPING)
        _trend_state = signal.get("trendState", "UNKNOWN")
        _regime = signal.get(
            "regimeName",
            signal.get("regime", {}).get("label", "UNKNOWN")
            if isinstance(signal.get("regime"), dict)
            else "UNKNOWN",
        )
        _blocked_state_cfg = cfg.get("AUTO_TRADE_BLOCKED_TREND_STATES", {})
        if isinstance(_blocked_state_cfg, dict):
            _blocked_states = set(
                _blocked_state_cfg.get(
                    asset_type, _blocked_state_cfg.get("default", ["DEAD RANGING", "RANGING"])
                )
            )
        else:
            _blocked_states = set(_blocked_state_cfg or ["DEAD RANGING", "RANGING"])

        _blocked_regime_cfg = cfg.get("AUTO_TRADE_BLOCKED_REGIMES", {})
        if isinstance(_blocked_regime_cfg, dict):
            _blocked_regimes = {
                str(v).upper()
                for v in _blocked_regime_cfg.get(
                    asset_type, _blocked_regime_cfg.get("default", ["RANGING"])
                )
            }
        else:
            _blocked_regimes = {str(v).upper() for v in (_blocked_regime_cfg or ["RANGING"])}

        # NOTE: Engine A v2 now routes forex through the shared factor engine as well.
        # trendState is still not a reliable forex regime label, so this blocked-state
        # filter has little practical effect on forex. If forex-specific regime blocking
        # is needed, filter on signal.get("regimeName") instead.
        if _trend_state in _blocked_states or _regime.upper() in _blocked_regimes:
            return (
                False,
                f"Regime filter: {_trend_state}/{_regime} blocked for auto-trade",
            )

        now = datetime.now(timezone.utc)

        # ── Market hours guard — block execution when market is closed ──────
        utc_weekday = now.weekday()  # 0=Mon, 6=Sun
        utc_total = now.hour * 60 + now.minute

        if asset_type in ("forex", "index", "commodity"):
            # Forex/forex-adjacent: closed Fri 22:00 UTC → Sun 22:00 UTC
            forex_closed = (
                utc_weekday == 5  # all Saturday
                or (utc_weekday == 6 and utc_total < 22 * 60)  # Sunday before 22:00
                or (utc_weekday == 4 and utc_total >= 22 * 60)  # Friday after 22:00
            )
            if forex_closed:
                return False, f"MARKET_CLOSED: {asset_type} market is closed (weekend)"

        if asset_type in ("stock", "index"):
            # Equity markets: only Mon-Fri
            if utc_weekday >= 5:
                return False, f"MARKET_CLOSED: {asset_type} market is closed (weekend)"
            # JSE/LSE/DAX: 07:00–15:30 UTC | NYSE: 13:30–20:00 UTC
            # Use a broad window Mon-Fri 07:00–21:00 UTC to cover all equity sessions
            if not (7 * 60 <= utc_total < 21 * 60):
                return False, f"MARKET_CLOSED: {asset_type} market hours are 07:00–21:00 UTC"

        # crypto is always open — no guard needed

        session_cfg = cfg.get("AUTO_TRADE_SESSIONS", {})

        allowed_sessions = session_cfg.get(asset_type, ["always"])

        if "always" not in allowed_sessions:
            current_sessions = _current_sessions(now)

            if not any(s in current_sessions for s in allowed_sessions):
                return False, f"outside trading session ({current_sessions})"

        self._hydrate_conductor_routing(signal)
        _routing_pre = signal.get("conductor") if isinstance(signal.get("conductor"), dict) else {}
        if _routing_pre.get("skip_signal"):
            _rsn = _routing_pre.get("reasons", [])
            return False, f"CONDUCTOR_SKIP: {'; '.join(str(x) for x in _rsn)}"

        if cfg.get("SENTIMENT_GATE_ENABLED", True):
            try:
                from sentiment_gate import check_sentiment

                _cond = signal.get("conductor")
                _do_sent = not isinstance(_cond, dict) or _cond.get("run_sentiment", True)
                if _do_sent:
                    sent = check_sentiment(
                        signal.get("pair", ""), signal.get("direction", ""), asset_type
                    )

                    if not sent.get("allowed", False):
                        return False, sent.get("reason", "Sentiment block")

            except ImportError:
                return False, "Sentiment gate unavailable"

        if cfg.get("EVENT_RISK_ENABLED", True):
            try:
                from event_risk import check_event_risk

                ev_risk = check_event_risk(
                    signal.get("pair", ""),
                    asset_type,
                    lookahead_hours=cfg.get("EVENT_RISK_HOURS", 4),
                )

                if not ev_risk.get("allowed", False):
                    return False, ev_risk.get("reason", "Event risk block")

            except ImportError:
                return False, "Event risk gate unavailable"

        # Signal debate gate — AI Bull/Bear/Judge evaluation before auto-execution
        if cfg.get("SIGNAL_DEBATE_ENABLED", True):
            try:
                from ai_reconciliation import arbitrate_ai
                from signal_debate import run_signal_debate

                _co = signal.get("conductor")
                routing = _co if isinstance(_co, dict) else {}

                if isinstance(routing, dict) and routing.get("run_debate") is False:
                    debate = {
                        "grade": "SKIP",
                        "reasoning": "conductor: run_debate false — debate not routed",
                        "allowed": True,
                        "trace_id": signal.get("trace_id"),
                    }
                else:
                    debate = run_signal_debate(signal)
                _grade = debate.get("grade", "SKIP")
                _ai_decision = arbitrate_ai(
                    {"debate": debate, "trace_id": debate.get("trace_id") or signal.get("trace_id")},
                    require_trace_id=True,
                    required_sources=("debate",),
                )
                signal["ai_arbitration"] = _ai_decision
                _allowed = bool(_ai_decision.get("execution_allowed"))
                _reasoning = debate.get("reasoning", "")
                log.info(f"[AUTO] {signal.get('pair')} debate: {_grade} — {_reasoning}")
                # AI debate Telegram notification disabled
                if not _allowed:
                    return False, f"AI arbitration: {_ai_decision.get('reason')} (debate={_grade})"
                # Apply score adjustment — central clamp in signal_debate; defense-in-depth
                _adj_eff = float(
                    debate.get("score_penalty", debate.get("score_adjustment", 0.0)) or 0.0
                )
                if _adj_eff > 0:
                    log.critical(
                        "[AUTO] %s debate returned positive adjustment %.4f — forcing 0 (CRIT-002)",
                        signal.get("pair"),
                        _adj_eff,
                    )
                    _adj_eff = 0.0
                _adj_raw = float(
                    debate.get("score_adjustment_raw")
                    if debate.get("score_adjustment_raw") is not None
                    else _adj_eff
                )
                _adj = _adj_eff
                _debate_adjustment_clamped = _adj_raw > 0.0 and _adj == 0.0
                if _adj != 0.0:
                    signal["confluenceScore"] = max(
                        0, signal.get("confluenceScore", 0) + _adj
                    )
                    signal["combinedConviction"] = _current_combined_conviction(signal)
                    if float(signal.get("confluenceScore", 0) or 0) <= 0:
                        return False, "Debate reduced Engine A score to 0.0"
                    if signal["combinedConviction"] < auto_min_conviction:
                        return (
                            False,
                            f"debate-adjusted conviction {signal['combinedConviction']:.3f} < min {auto_min_conviction:.3f}",
                        )
                if _debate_adjustment_clamped:
                    log.debug(
                        "[AUTO] %s debate score_adjustment=%.3f clamped to 0.0 (downgrade-only policy)",
                        signal.get("pair"), _adj_raw,
                    )
                signal["_debate_adjustment_clamped"] = _debate_adjustment_clamped

                try:
                    from ai_review_logger import (
                        extract_engine_d_audit_state,
                        log_ai_review,
                        map_debate_grade_to_ai_state,
                        REVIEW_TYPE_SIGNAL_DEBATE,
                    )
                    from prompt_versions import get_prompt_version

                    log_ai_review(
                        symbol=signal.get("pair", "?"),
                        asset_type=asset_type,
                        review_type=REVIEW_TYPE_SIGNAL_DEBATE,
                        model=cfg.get("DEBATE_MODEL", "unknown"),
                        provider="xAI",
                        prompt_version=get_prompt_version("debate_judge"),
                        input_packet={
                            "pair": signal.get("pair"),
                            "trace_id": debate.get("trace_id") or signal.get("trace_id"),
                            "grade": _grade,
                            "reasoning": (debate.get("reasoning") or "")[:500],
                            "bull_conviction": debate.get("bull_conviction"),
                            "bear_conviction": debate.get("bear_conviction"),
                            "score": signal.get("confluenceScore"),
                            "score_adjustment": debate.get("score_adjustment"),
                            "arbitration": _ai_decision,
                        },
                        has_chart_image=False,
                        candle_freshness_status=(
                            (signal.get("dataFreshness") or {}).get("reason") or "unknown"
                        ),
                        engine_a_state=signal.get("confluenceScore"),
                        engine_b_state=None,
                        engine_c_state=signal.get("combinedConviction"),
                        engine_d_state=extract_engine_d_audit_state(signal),
                        risk_state=None,
                        ai_review_state=map_debate_grade_to_ai_state(_grade),
                        ai_confidence=None,
                        contradictions_count=0,
                        missing_information_count=0,
                        parse_success=True,
                        schema_valid=True,
                        execution_allowed_before_ai=True,
                        execution_allowed_after_ai=_allowed,
                        final_action="debate_gate",
                        trace_id=debate.get("trace_id") or signal.get("trace_id"),
                    )
                except Exception as _log_err:
                    log.debug("[AI_AUDIT] Debate audit log failed: %s", _log_err)

            except ImportError:
                log.debug("[AUTO] signal_debate not available — skipping")
                return False, "AI debate unavailable"
            except Exception as _debate_err:
                log.warning(f"[AUTO] Debate failed (blocking): {_debate_err}")
                return False, f"AI debate failed: {_debate_err}"

        # ── SL distance pre-check (mirrors risk_engine MAX_SL_PCT gate) ──────
        # Reject here — before AI debate, sentiment, and event-risk calls —
        # so over-wide stops on volatile pairs (XAG/USD, Sugar) don't waste
        # the full execution pipeline only to fail at risk_check.
        _entry = float(signal.get("price") or signal.get("livePrice") or 0)
        _sl = float(signal.get("sl") or 0)
        if _entry and _sl and _entry != _sl:
            _sl_pct = abs(_entry - _sl) / abs(_entry)
            try:
                from risk_engine import resolve_max_sl_pct

                _max_sl_pct, _max_sl_source = resolve_max_sl_pct(signal, asset_type, cfg)
            except Exception:
                _max_sl_pct = float((cfg.get("MAX_SL_PCT") or {}).get(asset_type, 0.05))
                _max_sl_source = f"asset:{asset_type}"
            if _sl_pct > _max_sl_pct:
                return (
                    False,
                    f"SL distance {_sl_pct:.1%} exceeds MAX_SL_PCT {_max_sl_pct:.1%} for {asset_type} ({_max_sl_source})",
                )

        return True, ""

    def _get_cached_open_positions(
        self, asset_type: str, positions_cache: dict[str, list[dict] | None]
    ) -> list[dict] | None:
        """Load broker open positions once per venue during a scan.

        Returns an empty list when positions cannot be confirmed (logs warning).
        Execution fails closed downstream in risk_check, so this precheck only
        suppresses known duplicate-pair attempts — a miss here is not a safety gap.
        """
        venue = "bybit" if asset_type == "crypto" else "mt5"
        if venue in positions_cache:
            return positions_cache[venue]

        try:
            if venue == "bybit":
                from bybit_executor import bybit_get_positions

                pos_result = bybit_get_positions()
            else:
                from mt5_executor import mt5_get_positions

                pos_result = mt5_get_positions()
        except Exception as e:
            log.warning(f"[AUTO] {venue.upper()} positions precheck failed: {e}")
            positions_cache[venue] = []
            return []

        if isinstance(pos_result, dict) and pos_result.get("error"):
            log.warning(
                f"[AUTO] {venue.upper()} positions precheck unavailable: "
                f"{pos_result.get('detail', 'unknown')}"
            )
            positions_cache[venue] = []
            return []

        positions = (
            pos_result.get("positions", [])
            if isinstance(pos_result, dict)
            else (pos_result or [])
        )
        positions_cache[venue] = positions
        return positions

    def _execute_signal(self, signal: dict, cfg: dict) -> bool:
        """Execute a single signal through the normal risk → executor path.

        On any failure, writes a tagged error row to audit_log for diagnosis.

        """

        pair = signal.get("pair", "")

        direction = signal.get("direction", "")

        is_crypto = signal.get("type") == "crypto"
        ssi_engine = _signal_engine(signal)

        try:
            from risk_engine import risk_check

            if is_crypto:
                from bybit_executor import (
                    bybit_get_account,
                    bybit_get_positions,
                    bybit_get_symbol_info,
                )

                account = bybit_get_account()

                if not account:
                    self._write_error(signal, "BYBIT_NOT_CONNECTED")

                    return False

                pos_result = bybit_get_positions()
                if isinstance(pos_result, dict) and pos_result.get("error"):
                    self._write_error(signal, "BYBIT_POSITIONS_UNAVAILABLE")
                    return False
                positions = (
                    pos_result.get("positions", [])
                    if isinstance(pos_result, dict)
                    else (pos_result or [])
                )

                symbol_info = bybit_get_symbol_info(pair)

                if not symbol_info or symbol_info.get("error"):
                    self._write_error(signal, "BYBIT_SYMBOL_UNAVAILABLE")

                    return False

                _exec_venue = "bybit"

            else:
                from mt5_executor import (
                    mt5_get_account,
                    mt5_get_positions,
                    mt5_get_symbol_info,
                )

                account = mt5_get_account()

                if not account or account.get("error"):
                    self._write_error(signal, "MT5_NOT_CONNECTED")

                    return False

                pos_result = mt5_get_positions()
                if isinstance(pos_result, dict) and pos_result.get("error"):
                    self._write_error(signal, "MT5_POSITIONS_UNAVAILABLE")
                    return False
                positions = (
                    pos_result.get("positions", [])
                    if isinstance(pos_result, dict)
                    else (pos_result or [])
                )

                symbol_info = mt5_get_symbol_info(pair)

                if not symbol_info:
                    self._write_error(signal, f"SYMBOL_NOT_ON_BROKER:{pair}")

                    return False

                _exec_venue = "mt5"

            from execution_lifecycle import run_managed_execution

            sizing_override = cfg.get("AUTO_TRADE_SIZING_OVERRIDE", 1.0)

            approval = risk_check(
                signal=signal,
                account_balance=account["balance"],
                account_equity=account["equity"],
                open_positions=positions,
                symbol_info=symbol_info,
                kill_switch=self._kill_switch_fn() if self._kill_switch_fn else False,
                sizing_override=sizing_override,
            )

            if not approval.approved:
                self._write_error(signal, f"RISK:{approval.reason}")

                return False

            from guardian import pre_trade_check as _guardian_pre_trade
            _ptc_ok, _ptc_reason = _guardian_pre_trade(signal, positions, account, pos_result)
            if not _ptc_ok:
                log.warning(f"[AUTO] {pair} GUARDIAN BLOCKED: {_ptc_reason}")
                self._write_error(signal, f"GUARDIAN:{_ptc_reason}")
                return False

            result = run_managed_execution(_exec_venue, signal, approval)

            if result.get("success"):
                with self._lock:
                    self._trades_today += 1
                    self._last_exec_at = datetime.now(timezone.utc)
                    self._last_exec_pair = pair
                    self._last_exec_dir = direction

                self._write_audit(signal, approval, result, cfg)
                record_execution_event(
                    engine=ssi_engine,
                    success=True,
                    score=signal.get("confluenceScore"),
                    max_score=signal.get("maxScore", 1.0),
                    expected_prob=_signal_expected_prob(signal),
                    slippage_bps=result.get("slippageBps"),
                    db_path=self._audit_db,
                    meta={
                        "pair": pair,
                        "direction": direction,
                        "calibrated_probability": signal.get("calibratedProbability"),
                        "runtime_only_metric": True,
                    },
                )

                # Send Telegram notification for successful auto-execution
                try:
                    import telegram_notify
                    
                    # Get AI analysis if available
                    ai_analysis = signal.get("ai_analysis", {})
                    telegram_notify.notify_signal_with_ai(
                        pair=signal.get("pair", ""),
                        direction=signal.get("direction", ""),
                        score=signal.get("confluenceScore", 0),
                        max_score=signal.get("maxScore", 3.0),
                        entry=signal.get("price", 0),
                        sl=signal.get("sl", 0),
                        tp1=signal.get("tp1", 0),
                        rr1=signal.get("rr1", 0),
                        regime=signal.get("trendState", ""),
                        signal_class=signal.get("signalClass", ""),
                        ai_grade=ai_analysis.get("grade", ""),
                        ai_verdict=ai_analysis.get("verdict", ""),
                        ai_edge_prob=ai_analysis.get("edgeProbability", 0),
                        ai_risk=ai_analysis.get("riskLevel", ""),
                        ai_warnings=ai_analysis.get("warnings", []),
                        is_auto_executed=True,
                    )
                except Exception as tn_e:
                    log.debug(f"[AUTO] Telegram notification failed: {tn_e}")

                log.warning(
                    f"[AUTO] EXECUTED: {pair} {direction} "
                    f"ticket={result.get('ticket')} vol={result.get('volume')}"
                )

                return True

            else:
                err = result.get("error", "UNKNOWN")

                self._write_error(signal, f"EXEC:{err}")

                return False

        except Exception as e:
            self._write_error(signal, f"EXCEPTION:{str(e)[:120]}")

            log.error(f"[AUTO] _execute_signal error for {pair}: {e}")

            return False

    def _write_audit(self, signal: dict, approval, result: dict, cfg: dict):
        """Write auto-trade to audit_log."""

        if not self._audit_db:
            return

        is_demo = self._test_mode_fn() if self._test_mode_fn else False

        def _infer_asset_class(pair: str) -> str | None:
            """Infer asset class from pair name when signal['type'] is missing."""
            if not pair:
                return None
            p = pair.upper()
            if "/USDT" in p or "/BTC" in p or "/ETH" in p:
                return "crypto"
            if any(p.startswith(fx) for fx in (
                "EUR/", "GBP/", "USD/", "AUD/", "NZD/", "CAD/", "CHF/", "JPY/",
                "EUR", "GBP", "AUD", "NZD", "CAD", "CHF"
            )) and "/" in p:
                return "forex"
            if p in ("XAU/USD", "XAG/USD", "OIL", "BRENT", "SUGAR", "WHEAT",
                     "CORN", "COFFEE", "COTTON", "NATURAL GAS", "COCOA"):
                return "commodity"
            if any(x in p for x in ("SPX", "NDX", "DAX", "FTSE", "NQ", "ES",
                                      "SP500", "US30", "UK100", "GER40")):
                return "index"
            return "stock"

        def _resolve_audit_style(style: str | None, asset_class: str | None) -> str:
            """Normalise style for audit_log. Maps 'auto' and None to a concrete style."""
            if style in ("scalp", "intraday", "swing"):
                return style
            # 'auto' or None — infer from asset class
            if (asset_class or "").lower() == "crypto":
                return "intraday"   # crypto auto defaults to intraday
            if (asset_class or "").lower() == "forex":
                return "intraday"
            return "intraday"       # safe default for commodity/index/stock

        def _audit_legs() -> list[dict]:
            raw_legs = result.get("legs") if isinstance(result.get("legs"), list) else []
            if not raw_legs:
                return [
                    {
                        "ticket": result.get("ticket"),
                        "entryPrice": result.get("entryPrice") or signal.get("price"),
                        "tp": signal.get("tp1"),
                        "volume": result.get("volume"),
                        "riskAmount": approval.risk_amount,
                        "riskPct": approval.risk_pct,
                    }
                ]
            total_volume = sum(float(leg.get("volume") or 0.0) for leg in raw_legs)
            legs = []
            for leg in raw_legs:
                leg_volume = float(leg.get("volume") or 0.0)
                ratio = (leg_volume / total_volume) if total_volume > 0 else 0.0
                legs.append(
                    {
                        "ticket": leg.get("ticket"),
                        "entryPrice": leg.get("entryPrice") or result.get("entryPrice") or signal.get("price"),
                        "tp": leg.get("tp") or signal.get("tp1"),
                        "volume": leg_volume,
                        "riskAmount": approval.risk_amount * ratio,
                        "riskPct": approval.risk_pct * ratio,
                    }
                )
            return legs

        try:
            _audit_engine = _signal_engine(signal)
            _eng_b_data = signal.get("engine_b") or signal.get("naked_data") or {}
            _factors = {
                "scores": _signal_factor_scores(signal),
                "weights": _signal_factor_weights(signal),
                "disabled": signal.get("disabledFactors"),
                "regime": signal.get("regimeName"),
                "combined_conviction": signal.get("combinedConviction"),
                "calibrated_probability": signal.get("calibratedProbability"),
            }
            _max_score = (
                _eng_b_data.get("max_possible")
                if _audit_engine == "engine_b"
                else signal.get("maxScore")
            )
            _score_pct = (
                _eng_b_data.get("pct")
                if _audit_engine == "engine_b"
                else (
                    signal.get("confluenceScore") / _max_score
                    if _max_score and _max_score > 0
                    else None
                )
            )
            with timed_sqlite_connect(
                self._audit_db, timeout=15.0, label="auto.audit_success.connect"
            ) as con:
                for _leg in _audit_legs():
                    timed_sqlite_execute_write(
                        con,
                        "INSERT INTO audit_log"
                        "(ts, pair, score, engine, direction, asset_class, regime,"
                        " entry_price, sl, tp, volume, risk_amount, risk_pct,"
                        " ticket, grade, signal_price_ref, slippage_bps,"
                        " max_score, score_pct, factors_json, edge_prob, style, fee_cost)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            signal.get("pair"),
                            signal.get("confluenceScore"),
                            _audit_engine,
                            signal.get("direction"),
                            signal.get("type") or _infer_asset_class(signal.get("pair", "")),
                            signal.get("trendState"),
                            _leg.get("entryPrice"),
                            signal.get("sl"),
                            _leg.get("tp"),
                            _leg.get("volume"),
                            _leg.get("riskAmount"),
                            _leg.get("riskPct"),
                            _leg.get("ticket"),
                            "AUTO" + ("-DEMO" if is_demo else ""),
                            result.get("signalPriceRef"),
                            result.get("slippageBps"),
                            _max_score,
                            _score_pct,
                            json.dumps(_factors),
                            signal.get("calibratedProbability", signal.get("edgeProbability")),
                            _resolve_audit_style(signal.get("style"), signal.get("type")),
                            result.get("feeCost"),
                        ),
                        label="auto.audit_success.insert",
                    )

                timed_sqlite_commit(con, label="auto.audit_success.commit")

        except Exception as e:
            log.debug(f"[AUTO] audit write failed: {e}")

    def _write_error(self, signal: dict, error_tag: str):
        """Write a failed auto-trade attempt to audit_log with an error_tag for diagnosis."""

        if not self._audit_db:
            return

        is_demo = self._test_mode_fn() if self._test_mode_fn else False

        log.warning(f"[AUTO] {signal.get('pair')} FAILED — {error_tag}")

        try:
            record_execution_event(
                engine=_signal_engine(signal),
                success=False,
                score=signal.get("confluenceScore"),
                max_score=signal.get("maxScore", 1.0),
                expected_prob=_signal_expected_prob(signal),
                db_path=self._audit_db,
                meta={
                    "pair": signal.get("pair"),
                    "direction": signal.get("direction"),
                    "error": error_tag,
                    "calibrated_probability": signal.get("calibratedProbability"),
                    "runtime_only_metric": True,
                },
            )
        except Exception as _ssi_err:
            log.debug(f"[SSI] auto-trader failure sample skipped: {_ssi_err}")

        try:
            with timed_sqlite_connect(
                self._audit_db, timeout=15.0, label="auto.audit_error.connect"
            ) as con:
                timed_sqlite_execute_write(
                    con,
                    """INSERT INTO audit_log

                       (ts, pair, score, engine, direction, asset_class, regime,

                        entry_price, sl, tp, grade, error_tag)

                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        signal.get("pair"),
                        signal.get("confluenceScore"),
                        _signal_engine(signal),
                        signal.get("direction"),
                        signal.get("type"),
                        signal.get("trendState"),
                        signal.get("price"),
                        signal.get("sl"),
                        signal.get("tp1"),
                        "AUTO-ERR" + ("-DEMO" if is_demo else ""),
                        error_tag,
                    ),
                    label="auto.audit_error.insert",
                )

                timed_sqlite_commit(con, label="auto.audit_error.commit")

        except Exception as e:
            log.debug(f"[AUTO] error audit write failed: {e}")


# ── Session helpers ───────────────────────────────────────────────────────────


def _current_sessions(now: datetime) -> list[str]:
    """Return list of currently active session names."""

    h = now.hour

    active = []

    for name, (start, end) in _SESSIONS.items():
        if start <= h < end:
            active.append(name)

    return active or ["off_hours"]


# ── Module-level singleton ────────────────────────────────────────────────────

auto_trader = AutoTrader()
