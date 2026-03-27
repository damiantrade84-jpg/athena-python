"""auto_trader.py — Autonomous trade execution scheduler for Athena Pro.



Fires at H4 candle closes, runs the full scan, and auto-executes qualifying

signals through the same risk engine + executor path as manual execution.



Thread-safe. All athena references are passed in via factory functions to

avoid circular imports (auto_trader is imported BY athena, not the reverse).

"""

import logging

import sqlite3

import threading

import time

from datetime import datetime, timezone, timedelta

from calibration import predict_calibrated_prob
from meta_learner import apply_meta_policy, meta_report
from stability_monitor import get_signal_stability_index, record_execution_event

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
    pct = signal.get("confluencePct")
    if pct is not None:
        try:
            return max(0.0, min(1.0, float(pct) / 100.0))
        except (TypeError, ValueError):
            pass
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
                "Informational only; live execution uses combinedConviction and "
                "AUTO_TRADE_MIN_CONVICTION."
            ),
            "minScoreDeprecatedForExecution": True,
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
                        try:
                            from ai_learning import run_meta_analysis

                            _meta_result = run_meta_analysis(
                                self._audit_db,
                                cfg_early.get("XAI_API_KEY", ""),
                                cfg_early.get("XAI_MODEL", "grok-4.20-0309-reasoning"),
                            )
                            log.info(
                                f"[AUTO] Weekly meta-analysis complete: {_meta_result.get('summary', '')[:100]}"
                            )
                        except Exception as _me:
                            log.warning(f"[AUTO] Weekly meta-analysis failed: {_me}")

                # Immediate scan on enable (first scan)

                if self._scan_now:
                    self._scan_now = False

                    log.info("[AUTO] Running immediate first scan...")

                    self._run_auto_scan()

                    self._last_interval_scan = now

                    continue

                cfg = self._config_fn() if self._config_fn else {}

                interval_min = cfg.get(
                    "AUTO_TRADE_SCAN_INTERVAL_MIN", 30
                )  # default 30min

                # Interval-based scan (every N minutes)

                if self._last_interval_scan is None:
                    self._last_interval_scan = now

                elapsed = (now - self._last_interval_scan).total_seconds() / 60

                if elapsed >= interval_min:
                    # Convert UTC to local time for display only (don't affect scheduling logic)
                    local_now = datetime.fromtimestamp(now.timestamp())
                    log.info(
                        f"[AUTO] Interval scan ({interval_min}min) firing at {local_now.strftime('%H:%M')}"
                    )

                    self._run_auto_scan()

                    self._last_interval_scan = now

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

        if self._trades_today >= max_daily:
            log.info(
                f"[AUTO] Daily cap reached ({self._trades_today}/{max_daily}) — scan skipped"
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

        # Re-stamp all signals with the scan-completion time.
        # analyze_pair() timestamps each signal individually; with 90 pairs / 3 workers,
        # the first pairs in the queue can be 3-5 min old by the time the scan returns.
        # These signals are still current — they were generated this scan cycle — so we
        # align all timestamps to now to prevent STALE_SIGNAL rejections in risk_check.
        _scan_ts = datetime.now(timezone.utc).isoformat()
        for _s in signals:
            _s["timestamp"] = _scan_ts

        # Sort by combined conviction (Engine A+B) descending — best signal first
        # Falls back to normalized confluenceScore if combinedConviction not present

        signals = sorted(
            signals, key=lambda s: s.get("combinedConviction", s.get("confluenceScore", 0) / s.get("maxScore", 3.0)), reverse=True
        )

        max_per_scan = cfg.get("AUTO_TRADE_MAX_PER_SCAN", 1)

        executed = 0

        for sig in signals:
            if executed >= max_per_scan:
                break

            if self._trades_today >= max_daily:
                break

            ok, reason = self._can_execute(sig, cfg)

            if not ok:
                log.info(f"[AUTO] {sig.get('pair')} skipped: {reason}")

                continue

            success = self._execute_signal(sig, cfg)

            if success:
                executed += 1

        log.info(f"[AUTO] Scan complete — {executed} trade(s) executed")

    def _can_execute(self, signal: dict, cfg: dict) -> tuple[bool, str]:
        """Check score gate + session filter. Uses combined Engine A+B conviction when available."""

        asset_type = signal.get("type", "")

        # ── Combined conviction scoring (Engine A + B) ──
        # combinedConviction is 0-1 scale: (A_norm * 0.6) + (B_norm * 0.4) when aligned
        # Falls back to A_norm * 0.6 when Engine B has no clear signal
        combined_conviction = signal.get("combinedConviction")
        engines_aligned = signal.get("enginesAligned", False)

        # Legacy fallback: normalize Engine A score to 0-1 if no combined score
        if combined_conviction is None:
            score = signal.get("confluenceScore", 0)
            max_score = signal.get("maxScore", 3.0)
            combined_conviction = min(score / max_score, 1.0) if max_score else 0

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

        # NOTE: For forex signals, trendState is set to the signal type ("TREND_PULLBACK",
        # "LONDON_BREAKOUT", "NONE") rather than a regime label. As a result, this
        # blocked-state filter has no practical effect on forex — quality gating for forex
        # is handled upstream by trend_gate and session_ok in forex_scoring.compute_forex_score().
        # To add forex-specific regime blocking, filter on signal.get("regimeName") instead.
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

        if cfg.get("SENTIMENT_GATE_ENABLED", True):
            try:
                from sentiment_gate import check_sentiment

                sent = check_sentiment(
                    signal.get("pair", ""), signal.get("direction", ""), asset_type
                )

                if not sent.get("allowed", True):
                    return False, sent.get("reason", "Sentiment block")

            except ImportError:
                pass

        if cfg.get("EVENT_RISK_ENABLED", True):
            try:
                from event_risk import check_event_risk

                ev_risk = check_event_risk(
                    signal.get("pair", ""),
                    asset_type,
                    lookahead_hours=cfg.get("EVENT_RISK_HOURS", 4),
                )

                if not ev_risk.get("allowed", True):
                    return False, ev_risk.get("reason", "Event risk block")

            except ImportError:
                pass

        # Signal debate gate — AI Bull/Bear/Judge evaluation before auto-execution
        if cfg.get("SIGNAL_DEBATE_ENABLED", True):
            try:
                from signal_debate import run_signal_debate

                debate = run_signal_debate(signal)
                _grade = debate.get("grade", "SKIP")
                _allowed = debate.get("allowed", True)
                _reasoning = debate.get("reasoning", "")
                log.info(f"[AUTO] {signal.get('pair')} debate: {_grade} — {_reasoning}")
                # AI debate Telegram notification disabled
                if not _allowed:
                    return False, f"Debate: {_grade} — {_reasoning}"
                # Apply score adjustment from debate (optional tuning)
                _adj = debate.get("score_adjustment", 0.0)
                if _adj != 0.0:
                    signal["confluenceScore"] = max(
                        0, signal.get("confluenceScore", 0) + _adj
                    )
            except ImportError:
                log.debug("[AUTO] signal_debate not available — skipping")
            except Exception as _debate_err:
                log.warning(f"[AUTO] Debate failed (proceeding): {_debate_err}")

        return True, ""

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
                    bybit_execute,
                )

                account = bybit_get_account()

                if not account:
                    self._write_error(signal, "BYBIT_NOT_CONNECTED")

                    return False

                pos_result = bybit_get_positions()
                positions = (
                    pos_result.get("positions", [])
                    if isinstance(pos_result, dict)
                    else (pos_result or [])
                )

                symbol_info = bybit_get_symbol_info(pair)

                executor = bybit_execute

            else:
                from mt5_executor import (
                    mt5_get_account,
                    mt5_get_positions,
                    mt5_get_symbol_info,
                    mt5_execute,
                )

                account = mt5_get_account()

                if not account or account.get("error"):
                    self._write_error(signal, "MT5_NOT_CONNECTED")

                    return False

                pos_result = mt5_get_positions()
                positions = (
                    pos_result.get("positions", [])
                    if isinstance(pos_result, dict)
                    else (pos_result or [])
                )

                symbol_info = mt5_get_symbol_info(pair)

                if not symbol_info:
                    self._write_error(signal, f"SYMBOL_NOT_ON_BROKER:{pair}")

                    return False

                executor = mt5_execute

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

            result = executor(signal, approval)

            if result.get("success"):
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

        try:
            with sqlite3.connect(self._audit_db, timeout=15.0) as con:
                con.execute(
                    """INSERT INTO audit_log

                       (ts, pair, score, direction, asset_class, regime,

                        entry_price, sl, tp, volume, risk_amount, risk_pct,

                        ticket, grade, signal_price_ref, slippage_bps)

                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        signal.get("pair"),
                        signal.get("confluenceScore"),
                        signal.get("direction"),
                        signal.get("type"),
                        signal.get("trendState"),
                        result.get("entryPrice") or signal.get("price"),
                        signal.get("sl"),
                        signal.get("tp1"),
                        result.get("volume"),
                        approval.risk_amount,
                        approval.risk_pct,
                        result.get("ticket"),
                        "AUTO" + ("-DEMO" if is_demo else ""),
                        result.get("signalPriceRef"),
                        result.get("slippageBps"),
                    ),
                )

                con.commit()

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
            with sqlite3.connect(self._audit_db, timeout=15.0) as con:
                con.execute(
                    """INSERT INTO audit_log

                       (ts, pair, score, direction, asset_class, regime,

                        entry_price, sl, tp, grade, error_tag)

                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        signal.get("pair"),
                        signal.get("confluenceScore"),
                        signal.get("direction"),
                        signal.get("type"),
                        signal.get("trendState"),
                        signal.get("price"),
                        signal.get("sl"),
                        signal.get("tp1"),
                        "AUTO-ERR" + ("-DEMO" if is_demo else ""),
                        error_tag,
                    ),
                )

                con.commit()

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
