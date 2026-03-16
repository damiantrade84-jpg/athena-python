"""auto_trader.py — Autonomous trade execution scheduler for Athena Pro.



Fires at H4 candle closes, runs the full scan, and auto-executes qualifying

signals through the same risk engine + executor path as manual execution.



Thread-safe. All athena references are passed in via factory functions to

avoid circular imports (auto_trader is imported BY athena, not the reverse).

"""

import json

import logging

import sqlite3

import threading

import time

from datetime import datetime, timezone, timedelta



log = logging.getLogger("athena.auto_trader")



# ── H4 + D1 schedule: (hour, minute) UTC ─────────────────────────────────────

_SCHEDULE = [(0,5),(4,5),(8,5),(12,5),(16,5),(20,5)]



# ── Session windows (UTC) for session-aware filtering ────────────────────────

_SESSIONS = {

    "london":          (7,  16),   # 07:00–16:00

    "new_york":        (13, 21),   # 13:00–21:00

    "london_ny_overlap": (13, 16), # 13:00–16:00

    "jse":             (7,  15),   # 07:00–15:00 (JSE)

    "us_regular":      (14, 21),   # 14:30–21:00 simplified

}





class AutoTrader:

    """Autonomous trading scheduler. Start with .enable(); stop with .disable()."""



    def __init__(self):

        self._enabled       = False

        self._running       = False

        self._thread        = None

        self._lock          = threading.Lock()



        self._trades_today  = 0

        self._last_date     = ""          # YYYY-MM-DD UTC

        self._last_scan_at  = None        # datetime

        self._last_exec_at  = None        # datetime

        self._last_meta_analysis = None  # datetime of last weekly meta-analysis

        self._last_exec_pair = ""

        self._last_exec_dir  = ""

        self._executed_slots: set = set() # "YYYY-MM-DD_HH" keys to prevent double-fire

        self._scan_now      = False       # fire immediate scan on next loop tick

        self._last_interval_scan = None   # for interval-based scanning



        # Injected at init time by athena.py to avoid circular imports

        self._run_scan_fn       = None    # callable: run_full_scan(style, asset_class)

        self._kill_switch_fn    = None    # callable: () -> bool

        self._test_mode_fn      = None    # callable: () -> bool

        self._audit_db          = None    # str path

        self._config_fn         = None    # callable: () -> dict  (returns CONFIG)



    def configure(self, run_scan_fn, kill_switch_fn, test_mode_fn,

                  audit_db: str, config_fn):

        """Called once by athena.py at startup to inject dependencies."""

        self._run_scan_fn    = run_scan_fn

        self._kill_switch_fn = kill_switch_fn

        self._test_mode_fn   = test_mode_fn

        self._audit_db       = audit_db

        self._config_fn      = config_fn



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

        return {

            "enabled":          self._enabled,

            "tradesToday":      self._trades_today,

            "maxDaily":         cfg.get("AUTO_TRADE_MAX_DAILY", 3),

            "minScore":         cfg.get("AUTO_TRADE_MIN_SCORE", {}),  # per-class dict

            "lastScanAt":       self._last_scan_at.isoformat() if self._last_scan_at else None,

            "lastExecutionAt":  self._last_exec_at.isoformat() if self._last_exec_at else None,

            "lastExecutionPair": self._last_exec_pair,

            "lastExecutionDir":  self._last_exec_dir,

            "nextScanAt":       self._next_scan_time().isoformat(),

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

                # Weekly meta-analysis: run once per week (Sunday 22:00 UTC)
                if now.weekday() == 6 and now.hour == 22:
                    if self._last_meta_analysis is None or (now - self._last_meta_analysis).days >= 6:
                        self._last_meta_analysis = now
                        try:
                            from ai_learning import run_meta_analysis
                            cfg = self._config_fn() if self._config_fn else {}
                            _meta_result = run_meta_analysis(
                                self._audit_db,
                                cfg.get("ANTHROPIC_KEY", ""),
                                cfg.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                            )
                            log.info(f"[AUTO] Weekly meta-analysis complete: {_meta_result.get('summary', '')[:100]}")
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

                interval_min = cfg.get("AUTO_TRADE_SCAN_INTERVAL_MIN", 30)  # default 30min



                # Interval-based scan (every N minutes)

                if self._last_interval_scan is None:

                    self._last_interval_scan = now



                elapsed = (now - self._last_interval_scan).total_seconds() / 60

                if elapsed >= interval_min:

                    # Convert UTC to local time for display only (don't affect scheduling logic)
                    from datetime import datetime
                    local_now = datetime.fromtimestamp(now.timestamp())
                    log.info(f"[AUTO] Interval scan ({interval_min}min) firing at {local_now.strftime('%H:%M')}")

                    self._run_auto_scan()

                    self._last_interval_scan = now



            except Exception as e:

                log.error(f"[AUTO] Scheduler loop error: {e}")



    def _reset_daily_counter(self, now: datetime):

        today = now.strftime("%Y-%m-%d")

        if today != self._last_date:

            self._last_date    = today

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

            log.info(f"[AUTO] Daily cap reached ({self._trades_today}/{max_daily}) — scan skipped")

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

            f"low_score={funnel.get('low_score',0)} closed={funnel.get('closed_exchange',0)} "

            f"inactive={funnel.get('inactive_pair',0)}"

        )

        if not signals:

            if watchlist:

                top = watchlist[0]

                log.info(f"[AUTO] Best near-miss: {top.get('pair')} score={top.get('confluenceScore')}/{top.get('maxScore')} ({top.get('direction')})")

            return



        # Sort by score descending — best signal first

        signals = sorted(signals, key=lambda s: s.get("confluenceScore", 0), reverse=True)

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

        """Check score gate + session filter."""

        score = signal.get("confluenceScore", 0)

        max_score = signal.get("maxScore", 13)

        asset_type = signal.get("type", "")



        # Per-class auto-trade minimum — different engines have different score scales:
        # factor engine (crypto/stock/commodity/index) → 0–3.0
        # forex engine → 0–1.0
        # AUTO_TRADE_MIN_SCORE can be a dict (per-class) or a flat float (legacy)
        _auto_min_cfg = cfg.get("AUTO_TRADE_MIN_SCORE", {})
        if isinstance(_auto_min_cfg, dict):
            auto_min = _auto_min_cfg.get(asset_type, _auto_min_cfg.get("crypto", 0.80))
        else:
            auto_min = float(_auto_min_cfg)  # backward compat with flat value

        class_mins = cfg.get("MIN_CONFLUENCE_CLASS", {})
        class_floor = class_mins.get(asset_type, cfg.get("MIN_CONFLUENCE", 0.70))

        min_score = max(auto_min, class_floor)



        log.info(f"[AUTO] {signal.get('pair')} candidate: score={score}/{max_score} min={min_score} dir={signal.get('direction')}")



        if score < min_score:

            return False, f"score {score:.1f} < min {min_score}"



        # Regime filter — only execute in TRENDING regime (37% WR vs 11% RANGING, 0% DEVELOPING)
        _trend_state = signal.get("trendState", "UNKNOWN")
        _regime = signal.get("regimeName", signal.get("regime", {}).get("label", "UNKNOWN") if isinstance(signal.get("regime"), dict) else "UNKNOWN")
        _blocked_regimes = {"DEAD RANGING", "RANGING", "DEVELOPING"}
        if _trend_state in _blocked_regimes or _regime.upper() in {"RANGING", "LOW_VOLATILITY"}:
            return False, f"Regime filter: {_trend_state}/{_regime} — only TRENDING allowed for auto-trade"

        now = datetime.now(timezone.utc)

        session_cfg = cfg.get("AUTO_TRADE_SESSIONS", {})

        allowed_sessions = session_cfg.get(asset_type, ["always"])



        if "always" not in allowed_sessions:

            current_sessions = _current_sessions(now)

            if not any(s in current_sessions for s in allowed_sessions):

                return False, f"outside trading session ({current_sessions})"



        if cfg.get("SENTIMENT_GATE_ENABLED", True):

            try:

                from sentiment_gate import check_sentiment

                sent = check_sentiment(signal.get("pair", ""), signal.get("direction", ""), asset_type)

                if not sent.get("allowed", True):

                    return False, sent.get("reason", "Sentiment block")

            except ImportError:

                pass



        if cfg.get("EVENT_RISK_ENABLED", True):

            try:

                from event_risk import check_event_risk

                ev_risk = check_event_risk(signal.get("pair", ""), asset_type, lookahead_hours=cfg.get("EVENT_RISK_HOURS", 4))

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
                # Notify Telegram of debate result
                try:
                    from telegram_notify import _send_message_async
                    _bull = debate.get("bull_conviction", "?")
                    _bear = debate.get("bear_conviction", "?")
                    _msg = (
                        f"🤖 *AI Debate: {signal.get('pair')} {signal.get('direction')}*\n"
                        f"Grade: *{_grade}*\n"
                        f"Bull: {_bull}/10 | Bear: {_bear}/10\n"
                        f"Score: {signal.get('confluenceScore', 0):.2f}\n"
                        f"_{_reasoning}_"
                    )
                    _send_message_async(_msg)
                except Exception:
                    pass
                if not _allowed:
                    return False, f"Debate: {_grade} — {_reasoning}"
                # Apply score adjustment from debate (optional tuning)
                _adj = debate.get("score_adjustment", 0.0)
                if _adj != 0.0:
                    signal["confluenceScore"] = max(0, signal.get("confluenceScore", 0) + _adj)
            except ImportError:
                log.debug("[AUTO] signal_debate not available — skipping")
            except Exception as _debate_err:
                log.warning(f"[AUTO] Debate failed (proceeding): {_debate_err}")

        return True, ""



    def _execute_signal(self, signal: dict, cfg: dict) -> bool:

        """Execute a single signal through the normal risk → executor path.

        On any failure, writes a tagged error row to audit_log for diagnosis.

        """

        pair      = signal.get("pair", "")

        direction = signal.get("direction", "")

        is_crypto = signal.get("type") == "crypto"



        try:

            from risk_engine import risk_check



            if is_crypto:

                from bybit_executor import (bybit_get_account, bybit_get_positions,

                                             bybit_get_symbol_info, bybit_execute)

                account = bybit_get_account()

                if not account:

                    self._write_error(signal, "BYBIT_NOT_CONNECTED")

                    return False

                pos_result  = bybit_get_positions()
                positions   = pos_result.get("positions", []) if isinstance(pos_result, dict) else (pos_result or [])

                symbol_info = bybit_get_symbol_info(pair)

                executor    = bybit_execute

            else:

                from mt5_executor import (mt5_get_account, mt5_get_positions,

                                           mt5_get_symbol_info, mt5_execute)

                account = mt5_get_account()

                if not account:

                    self._write_error(signal, "MT5_NOT_CONNECTED")

                    return False

                pos_result  = mt5_get_positions()
                positions   = pos_result.get("positions", []) if isinstance(pos_result, dict) else (pos_result or [])

                symbol_info = mt5_get_symbol_info(pair)

                if not symbol_info:

                    self._write_error(signal, f"SYMBOL_NOT_ON_BROKER:{pair}")

                    return False

                executor    = mt5_execute



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

                self._last_exec_at   = datetime.now(timezone.utc)

                self._last_exec_pair = pair

                self._last_exec_dir  = direction

                self._write_audit(signal, approval, result, cfg)

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

            with sqlite3.connect(self._audit_db, timeout=1.0) as con:

                con.execute(

                    """INSERT INTO audit_log

                       (ts, pair, score, direction, asset_class, regime,

                        entry_price, sl, tp, volume, risk_amount, risk_pct,

                        ticket, grade)

                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",

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

                    )

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

            with sqlite3.connect(self._audit_db, timeout=1.0) as con:

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

                    )

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

