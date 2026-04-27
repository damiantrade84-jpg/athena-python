"""
Athena Research Lab — Autopilot Session Manager

Orchestrates a full Research Autopilot Session:
  Discovery → Plan → Validation → AI Review → Final Verdict

Sessions are persisted to:
  logs/research_lab/autopilot_sessions/<session_id>/session.json

Run isolation contract:
  - Every run created by this session writes session_id, market_group,
    trading_style, research_depth, role, and parent_run_id into run_meta.json.
  - Result endpoints filter by session_id from run_meta, never by global state.
  - Crypto sessions never contain forex rows; forex sessions never contain crypto.

Safety: no live execution imports, no production config writes.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

_SESSIONS_DIR = Path("logs/research_lab/autopilot_sessions")
_OUTPUT_DIR = Path("logs/research_lab")

# In-memory registry of live sessions keyed by session_id
_active_sessions: dict[str, "AutopilotSession"] = {}

GROUP_SYMBOLS: dict[str, list[str]] = {
    "crypto":  ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"],
    "forex":   ["EUR/USD", "GBP/USD", "AUD/USD", "USD/JPY"],
    "metals":  ["XAU/USD", "XAG/USD"],
    "indices": ["US30", "NAS100", "GER30"],
    "stocks":  ["AAPL", "TSLA", "NVDA", "MSFT"],
    "custom":  ["BTC/USDT", "EUR/USD"],
}

DEPTH_MODE: dict[str, str] = {
    "quick":    "tiny",
    "standard": "medium",
    "deep":     "large",
}

VALID_STATUSES = [
    "CREATED", "DISCOVERY_RUNNING", "DISCOVERY_COMPLETE",
    "PLAN_GENERATED", "VALIDATION_RUNNING", "VALIDATION_COMPLETE",
    "AI_REVIEW_RUNNING", "FINALIZING", "COMPLETE",
    "FAILED", "CANCELLED",
]

VALID_VERDICTS = [
    "IMPLEMENTATION_CANDIDATE", "VALIDATION_CANDIDATE", "WEAK_EDGE",
    "REJECTED", "NEEDS_MORE_DATA", "PIPELINE_FAILED",
]


# ─── Verdict rules ────────────────────────────────────────────────────────────

def _compute_verdict_from_df(df: pd.DataFrame, validation_run_count: int) -> str:
    """
    Pure deterministic verdict from aggregated ranked_strategies rows.

    Rules (in priority order):
    - NEEDS_MORE_DATA  : fewer than 10 total trades OR empty df
    - PIPELINE_FAILED  : never returned here (caller sets it on exception)
    - IMPLEMENTATION_CANDIDATE : >= 1 STRONG_CANDIDATE row
                                 AND >= 2 unique symbols with net_return > 0
                                 AND at least 1 validation run completed
    - VALIDATION_CANDIDATE     : >= 1 STRONG_CANDIDATE but not enough breadth yet,
                                 OR weak_count >= 1 with >= 2 positive symbols
    - WEAK_EDGE                : at least 1 WEAK_CANDIDATE, no STRONG
    - REJECTED                 : all rows are REJECT / NEEDS_MORE_DATA with no positives
    - NEEDS_MORE_DATA          : fallback
    """
    if df is None or df.empty:
        return "NEEDS_MORE_DATA"

    statuses = (
        df["status"].str.upper() if "status" in df.columns
        else pd.Series([], dtype=str)
    )
    total_trades = int(df["trade_count"].sum()) if "trade_count" in df.columns else 0

    if total_trades < 10:
        return "NEEDS_MORE_DATA"

    strong_count = int((statuses == "STRONG_CANDIDATE").sum())
    weak_count = int((statuses == "WEAK_CANDIDATE").sum())
    reject_count = int((statuses == "REJECT").sum())

    positive_symbols = 0
    if "symbol" in df.columns and "net_return" in df.columns:
        try:
            positive_symbols = int(df[df["net_return"] > 0]["symbol"].nunique())
        except Exception:
            positive_symbols = 0

    if strong_count >= 1 and positive_symbols >= 2 and validation_run_count >= 1:
        return "IMPLEMENTATION_CANDIDATE"

    if strong_count >= 1:
        return "VALIDATION_CANDIDATE"

    if weak_count >= 1 and positive_symbols >= 2:
        return "VALIDATION_CANDIDATE"

    if weak_count >= 1:
        return "WEAK_EDGE"

    if reject_count > 0:
        return "REJECTED"

    return "NEEDS_MORE_DATA"


# ─── Session class ────────────────────────────────────────────────────────────

class AutopilotSession:
    """Represents one complete research autopilot session."""

    def __init__(
        self,
        session_id: str,
        market_group: str,
        trading_style: str,
        research_depth: str,
        output_dir: Path = _OUTPUT_DIR,
        sessions_dir: Path = _SESSIONS_DIR,
    ):
        self.session_id = session_id
        self.market_group = market_group.lower()
        self.trading_style = trading_style.lower()
        self.research_depth = research_depth.lower()
        self.output_dir = Path(output_dir)
        self.sessions_dir = Path(sessions_dir)

        self.status: str = "CREATED"
        self.current_phase: str = "CREATED"
        self.progress_pct: int = 0

        self.discovery_run_id: Optional[str] = None
        self.plan_id: Optional[str] = None
        self.validation_run_ids: list[str] = []
        self.followup_run_ids: list[str] = []

        self.ai_review_status: str = "PENDING"
        self.final_verdict: str = ""
        self.final_summary: str = ""
        self.best_clusters: list[dict] = []
        self.weak_clusters: list[dict] = []
        self.rejected_clusters: list[dict] = []
        self.do_not_implement: list[str] = []
        self.next_research_action: str = ""
        self.next_research_params: dict = {}  # structured params for "Run Next Action" button
        self.errors: list[str] = []

        # Optional discovery overrides (used when launched from "Run Next Action")
        self._symbols_override: Optional[list[str]] = None
        self._timeframes_override: Optional[list[str]] = None
        self._families_override: Optional[list[str]] = None

        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.updated_at: str = self.created_at

        self._cancelled = False
        self._thread: Optional[threading.Thread] = None

    # ── Public interface ───────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the orchestration thread."""
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"autopilot-session-{self.session_id}",
        )
        self._thread.start()

    def stop(self) -> None:
        """Request cancellation. Running sub-runs complete before the session aborts."""
        self._cancelled = True
        self.status = "CANCELLED"
        self.current_phase = "CANCELLED"
        self._save()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "market_group": self.market_group,
            "trading_style": self.trading_style,
            "research_depth": self.research_depth,
            "status": self.status,
            "current_phase": self.current_phase,
            "progress_pct": self.progress_pct,
            "discovery_run_id": self.discovery_run_id,
            "plan_id": self.plan_id,
            "validation_run_ids": self.validation_run_ids,
            "followup_run_ids": self.followup_run_ids,
            "ai_review_status": self.ai_review_status,
            "final_verdict": self.final_verdict,
            "final_summary": self.final_summary,
            "best_clusters": self.best_clusters,
            "weak_clusters": self.weak_clusters,
            "rejected_clusters": self.rejected_clusters,
            "do_not_implement": self.do_not_implement,
            "next_research_action": self.next_research_action,
            "next_research_params": self.next_research_params,
            "errors": self.errors,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ── Persistence ────────────────────────────────────────────────────────

    def _save(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
        session_dir = self.sessions_dir / self.session_id
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            path = session_dir / "session.json"
            path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except Exception as e:
            log.error("[autopilot_session] Failed to save session %s: %s", self.session_id, e)

    @classmethod
    def load(cls, session_id: str, sessions_dir: Path = _SESSIONS_DIR) -> Optional["AutopilotSession"]:
        path = sessions_dir / session_id / "session.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        sess = cls.__new__(cls)
        sess.session_id = data.get("session_id", session_id)
        sess.market_group = data.get("market_group", "crypto")
        sess.trading_style = data.get("trading_style", "intra")
        sess.research_depth = data.get("research_depth", "standard")
        sess.output_dir = _OUTPUT_DIR
        sess.sessions_dir = sessions_dir
        sess.status = data.get("status", "UNKNOWN")
        sess.current_phase = data.get("current_phase", "UNKNOWN")
        sess.progress_pct = data.get("progress_pct", 0)
        sess.discovery_run_id = data.get("discovery_run_id")
        sess.plan_id = data.get("plan_id")
        sess.validation_run_ids = data.get("validation_run_ids", [])
        sess.followup_run_ids = data.get("followup_run_ids", [])
        sess.ai_review_status = data.get("ai_review_status", "PENDING")
        sess.final_verdict = data.get("final_verdict", "")
        sess.final_summary = data.get("final_summary", "")
        sess.best_clusters = data.get("best_clusters", [])
        sess.weak_clusters = data.get("weak_clusters", [])
        sess.rejected_clusters = data.get("rejected_clusters", [])
        sess.do_not_implement = data.get("do_not_implement", [])
        sess.next_research_action = data.get("next_research_action", "")
        sess.next_research_params = data.get("next_research_params", {})
        sess.errors = data.get("errors", [])
        sess._symbols_override = None
        sess._timeframes_override = None
        sess._families_override = None
        sess.created_at = data.get("created_at", "")
        sess.updated_at = data.get("updated_at", "")
        sess._cancelled = False
        sess._thread = None
        return sess

    # ── Internal helpers ───────────────────────────────────────────────────

    def _phase(self, status: str, pct: int) -> None:
        self.status = status
        self.current_phase = status
        self.progress_pct = pct
        self._save()
        log.info("[autopilot_session] %s → %s (%d%%)", self.session_id, status, pct)

    def _fail(self, reason: str) -> None:
        self.errors.append(reason)
        self.status = "FAILED"
        self.current_phase = "FAILED"
        if not self.final_verdict:
            self.final_verdict = "PIPELINE_FAILED"
        if not self.final_summary:
            self.final_summary = f"Session failed: {reason}"
        self._save()
        log.error("[autopilot_session] %s FAILED: %s", self.session_id, reason)

    def _tag_run_meta(self, run_id: str, role: str, parent_run_id: Optional[str] = None) -> None:
        """Write session provenance fields into run_meta.json."""
        meta_path = self.output_dir / run_id / "run_meta.json"
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            else:
                meta = {}
            meta["session_id"] = self.session_id
            meta["market_group"] = self.market_group
            meta["trading_style"] = self.trading_style
            meta["research_depth"] = self.research_depth
            meta["role"] = role
            if parent_run_id:
                meta["parent_run_id"] = parent_run_id
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("[autopilot_session] Could not tag run_meta for %s: %s", run_id, e)

    # ── Orchestration ──────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception as e:
            import traceback as _tb
            tb = _tb.format_exc()
            log.error("[autopilot_session] Unhandled error in session %s:\n%s", self.session_id, tb)
            self._fail(str(e))

    def _run_inner(self) -> None:
        from athena_research.run_manager import run_research, _DEFAULT_CONFIG
        from athena_research.autopilot import generate_auto_plan, RESEARCH_STYLE_PROFILES

        if self._cancelled:
            return

        # Use caller-supplied overrides (from "Run Next Action") when present
        symbols = (self._symbols_override
                   if self._symbols_override
                   else GROUP_SYMBOLS.get(self.market_group, GROUP_SYMBOLS["crypto"]))
        mode = DEPTH_MODE.get(self.research_depth, "medium")

        profile = RESEARCH_STYLE_PROFILES.get(self.trading_style, RESEARCH_STYLE_PROFILES["intra"])
        timeframes = (self._timeframes_override
                      if self._timeframes_override
                      else profile["timeframes"])
        families = (self._families_override
                    if self._families_override
                    else profile["strategy_families"])

        # ── Phase 1: Discovery ─────────────────────────────────────────────
        self._phase("DISCOVERY_RUNNING", 5)
        discovery_run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.discovery_run_id = discovery_run_id
        self._save()

        try:
            run_research(
                mode=mode,
                config_path=_DEFAULT_CONFIG,
                output_dir=self.output_dir,
                run_id=discovery_run_id,
                direction="both",
                run_ai_review=False,
                symbols=symbols,
                timeframes=timeframes,
                families=families,
            )
        except Exception as e:
            self._fail(f"Discovery run failed: {e}")
            return

        self._tag_run_meta(discovery_run_id, role="discovery")
        self._phase("DISCOVERY_COMPLETE", 30)

        if self._cancelled:
            return

        # ── Phase 2: Plan ──────────────────────────────────────────────────
        try:
            plan = generate_auto_plan(
                run_id=discovery_run_id,
                output_dir=self.output_dir,
                planner_mode="balanced",
                max_tests=5,
            )
            self.plan_id = plan.get("plan_id", "")
        except Exception as e:
            log.warning("[autopilot_session] Plan generation failed (%s) — skipping validation", e)
            plan = {"tests": []}
            self.plan_id = ""

        self._phase("PLAN_GENERATED", 40)
        if self._cancelled:
            return

        # ── Phase 3: Validation ────────────────────────────────────────────
        tests = [t for t in plan.get("tests", []) if t.get("selected_by_default", True)]
        if tests:
            self._phase("VALIDATION_RUNNING", 45)
            child_ids = self._run_validations(tests, discovery_run_id, mode)
            self.validation_run_ids = child_ids
        else:
            log.info("[autopilot_session] No validation tests selected — skipping")

        self._phase("VALIDATION_COMPLETE", 75)
        if self._cancelled:
            return

        # ── Phase 4: AI Review ─────────────────────────────────────────────
        self._phase("AI_REVIEW_RUNNING", 80)
        self._do_ai_review(discovery_run_id)

        # ── Phase 5: Finalize ──────────────────────────────────────────────
        self._phase("FINALIZING", 90)
        self._finalize()

        self._phase("COMPLETE", 100)

    def _run_validations(
        self, tests: list[dict], discovery_run_id: str, mode: str
    ) -> list[str]:
        """Run validation child tests; return list of created run_ids."""
        from athena_research.run_manager import run_research, _DEFAULT_CONFIG

        child_ids: list[str] = []

        def _run_one(t_spec: dict) -> str:
            child_run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
            try:
                run_research(
                    mode=t_spec.get("mode", "small"),
                    config_path=_DEFAULT_CONFIG,
                    output_dir=self.output_dir,
                    run_id=child_run_id,
                    direction=(t_spec.get("directions", ["both"])[0]
                               if t_spec.get("directions") else "both"),
                    run_ai_review=False,
                    symbols=t_spec.get("symbols"),
                    timeframes=t_spec.get("timeframes"),
                    families=t_spec.get("families"),
                    strategies=t_spec.get("strategies"),
                )
                self._tag_run_meta(child_run_id, role="validation",
                                   parent_run_id=discovery_run_id)
            except Exception as e:
                log.warning("[autopilot_session] Validation child %s failed: %s", child_run_id, e)
                self.errors.append(f"Validation child {child_run_id}: {e}")
            return child_run_id

        max_workers = min(3, len(tests))
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {pool.submit(_run_one, t): t for t in tests}
            for fut in as_completed(futures):
                try:
                    cid = fut.result()
                    child_ids.append(cid)
                except Exception as e:
                    log.warning("[autopilot_session] Validation future error: %s", e)
                if self._cancelled:
                    break

        return child_ids

    def _do_ai_review(self, discovery_run_id: str) -> None:
        """Run AI review on the discovery run; mark PARSE_FAILED on JSON error."""
        try:
            from athena_research.ai_analyst import run_ai_analysis
            run_dir = self.output_dir / discovery_run_id
            if not run_dir.exists():
                self.ai_review_status = "SKIPPED"
                return
            result = run_ai_analysis(run_dir=run_dir)
            self.ai_review_status = "COMPLETE"
            # Extract do_not_implement / next_test hint from action_plan if present
            action_path = run_dir / "ai_action_plan.json"
            if action_path.exists():
                try:
                    ap = json.loads(action_path.read_text(encoding="utf-8"))
                    self.do_not_implement = ap.get("do_not_do_next", [])
                    hint = ap.get("next_tiny_test", {})
                    if hint:
                        symbols = hint.get("symbols", [])
                        timeframes = hint.get("timeframes", [])
                        families = hint.get("strategy_families", [])
                        reason = hint.get("reason", "")
                        # Build structured params the button can act on
                        self.next_research_params = {
                            "market_group": _infer_market_group(symbols, self.market_group),
                            "trading_style": _infer_trading_style(timeframes, self.trading_style),
                            "research_depth": "quick",
                            "symbols": symbols,
                            "timeframes": timeframes,
                            "families": families,
                            "reason": reason,
                        }
                        self.next_research_action = (
                            f"Run {families} on {symbols} at {timeframes}"
                            + (f" — {reason}" if reason else "")
                        )
                except Exception:
                    pass
        except Exception as e:
            log.warning("[autopilot_session] AI review failed: %s", e)
            self.ai_review_status = "PARSE_FAILED"
            self.errors.append(f"AI review: {e}")

    def _finalize(self) -> None:
        """Aggregate results and compute deterministic final verdict."""
        run_ids = [self.discovery_run_id] + list(self.validation_run_ids)
        all_frames: list[pd.DataFrame] = []

        for rid in run_ids:
            if not rid:
                continue
            ranked_path = self.output_dir / rid / "ranked_strategies.csv"
            if ranked_path.exists():
                try:
                    df = pd.read_csv(ranked_path)
                    all_frames.append(df)
                except Exception as e:
                    log.warning("[autopilot_session] Could not read ranked for %s: %s", rid, e)

        combined = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()

        # Populate cluster summaries
        if not combined.empty:
            statuses = (combined["status"].str.upper()
                        if "status" in combined.columns else pd.Series([], dtype=str))

            def _rows_to_dicts(mask: pd.Series) -> list[dict]:
                sub = combined[mask][["strategy_name", "symbol", "timeframe",
                                       "net_return", "status"]].copy()
                sub = sub.head(10)
                return json.loads(sub.fillna("").to_json(orient="records"))

            try:
                self.best_clusters = _rows_to_dicts(statuses == "STRONG_CANDIDATE")
                self.weak_clusters = _rows_to_dicts(statuses == "WEAK_CANDIDATE")
                self.rejected_clusters = _rows_to_dicts(statuses == "REJECT")
            except Exception:
                pass

        verdict = _compute_verdict_from_df(combined, len(self.validation_run_ids))
        self.final_verdict = verdict

        # Build summary text
        total = len(combined)
        strong = int((combined["status"].str.upper() == "STRONG_CANDIDATE").sum()) if not combined.empty and "status" in combined.columns else 0
        weak = int((combined["status"].str.upper() == "WEAK_CANDIDATE").sum()) if not combined.empty and "status" in combined.columns else 0
        self.final_summary = (
            f"Session {self.session_id} | {self.market_group.title()} {self.trading_style.title()} "
            f"| {total} configs | {strong} strong | {weak} weak | Verdict: {verdict}"
        )
        if not self.next_research_action:
            self.next_research_action = _suggest_next_action(verdict, self.trading_style)

        # Build deterministic next_research_params if AI didn't supply them
        if not self.next_research_params:
            self.next_research_params = _build_fallback_next_params(
                verdict, self.market_group, self.trading_style, combined
            )

        self._save()


# ─── Next-action helpers ─────────────────────────────────────────────────────

def _infer_market_group(symbols: list[str], fallback: str) -> str:
    """Guess market group from a symbol list; fall back to session's group."""
    if not symbols:
        return fallback
    s = symbols[0]
    if "USDT" in s or "BTC" in s or "ETH" in s:
        return "crypto"
    if any(c in s for c in ["/", "USD", "EUR", "GBP", "JPY", "AUD"]) and "XAU" not in s:
        return "forex"
    if "XAU" in s or "XAG" in s:
        return "metals"
    return fallback


def _infer_trading_style(timeframes: list[str], fallback: str) -> str:
    """Guess trading style from a timeframe list; fall back to session's style."""
    if not timeframes:
        return fallback
    tfs = set(t.upper() for t in timeframes)
    if tfs & {"M1", "M5", "M15"}:
        return "scalp"
    if tfs & {"H1", "H4"} and "D1" not in tfs:
        return "intra"
    if "D1" in tfs:
        return "swing"
    return fallback


def _build_fallback_next_params(
    verdict: str,
    market_group: str,
    trading_style: str,
    combined: "pd.DataFrame",
) -> dict:
    """Build actionable next_research_params from deterministic verdict + results."""
    from athena_research.autopilot import RESEARCH_STYLE_PROFILES

    # Pick symbols: top positive-return symbols from this session, or group defaults
    symbols: list[str] = []
    if not combined.empty and "symbol" in combined.columns and "net_return" in combined.columns:
        try:
            top = (
                combined[combined["net_return"] > 0]
                .groupby("symbol")["net_return"]
                .mean()
                .sort_values(ascending=False)
                .head(3)
                .index.tolist()
            )
            symbols = top
        except Exception:
            pass
    if not symbols:
        symbols = GROUP_SYMBOLS.get(market_group, [])[:3]

    # Pick families: the ones that produced the best results; default from profile
    families: list[str] = []
    if not combined.empty and "family" in combined.columns and "net_return" in combined.columns:
        try:
            best_fam = (
                combined[combined["net_return"] > 0]
                .groupby("family")["net_return"]
                .mean()
                .sort_values(ascending=False)
                .head(2)
                .index.tolist()
            )
            families = best_fam
        except Exception:
            pass
    if not families:
        profile = RESEARCH_STYLE_PROFILES.get(trading_style, RESEARCH_STYLE_PROFILES["intra"])
        families = profile["strategy_families"][:2]

    profile = RESEARCH_STYLE_PROFILES.get(trading_style, RESEARCH_STYLE_PROFILES["intra"])
    timeframes = profile["timeframes"]

    # Choose depth: deeper if verdict suggests we need more data
    depth = "standard" if verdict in ("WEAK_EDGE", "NEEDS_MORE_DATA") else "quick"

    reason_map = {
        "IMPLEMENTATION_CANDIDATE": "Confirm top candidates with wider symbol coverage",
        "VALIDATION_CANDIDATE": "Broaden validation before live consideration",
        "WEAK_EDGE": "Alternative family sweep to find stronger edge",
        "REJECTED": "Pivot to different families — current ones rejected",
        "NEEDS_MORE_DATA": "Rerun with more symbols/bars to build confidence",
        "PIPELINE_FAILED": "Retry after fixing session errors",
    }

    return {
        "market_group": market_group,
        "trading_style": trading_style,
        "research_depth": depth,
        "symbols": symbols,
        "timeframes": timeframes,
        "families": families,
        "reason": reason_map.get(verdict, "Follow-up run"),
    }


# ─── Suggest next action ──────────────────────────────────────────────────────

def _suggest_next_action(verdict: str, style: str) -> str:
    if verdict == "IMPLEMENTATION_CANDIDATE":
        return f"Forward-test top {style} candidates in paper/demo mode. Review Engine A/B signal alignment."
    if verdict == "VALIDATION_CANDIDATE":
        return f"Run deeper validation sweep across more symbols for {style} style before considering live use."
    if verdict == "WEAK_EDGE":
        return f"Run alternative family pivot for {style} style, or try different timeframe combinations."
    if verdict == "REJECTED":
        return f"Current {style} strategy families not viable. Consider parameter search or different market group."
    if verdict == "NEEDS_MORE_DATA":
        return "Increase bar count or symbol count and rerun. Ensure data cache is populated."
    return "Review session errors and rerun with ATHENA_DISABLE_TELEGRAM=1."


# ─── Public session API (called from routes) ──────────────────────────────────

def start_session(
    market_group: str,
    trading_style: str,
    research_depth: str,
    sessions_dir: Path = _SESSIONS_DIR,
    output_dir: Path = _OUTPUT_DIR,
    symbols_override: Optional[list[str]] = None,
    timeframes_override: Optional[list[str]] = None,
    families_override: Optional[list[str]] = None,
) -> dict:
    """Create and start a new autopilot session. Returns session state dict."""
    session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    sess = AutopilotSession(
        session_id=session_id,
        market_group=market_group,
        trading_style=trading_style,
        research_depth=research_depth,
        output_dir=output_dir,
        sessions_dir=sessions_dir,
    )
    sess._symbols_override = symbols_override or None
    sess._timeframes_override = timeframes_override or None
    sess._families_override = families_override or None
    _active_sessions[session_id] = sess
    sess._save()
    sess.start()
    return sess.to_dict()


def get_session_status(
    session_id: str,
    sessions_dir: Path = _SESSIONS_DIR,
) -> Optional[dict]:
    """Return current session state (in-memory first, then disk)."""
    if session_id in _active_sessions:
        return _active_sessions[session_id].to_dict()
    sess = AutopilotSession.load(session_id, sessions_dir)
    if sess is None:
        return None
    return sess.to_dict()


def get_session_result(
    session_id: str,
    output_dir: Path = _OUTPUT_DIR,
    sessions_dir: Path = _SESSIONS_DIR,
) -> Optional[dict]:
    """Return session state + scoped run rows (only rows from this session)."""
    state = get_session_status(session_id, sessions_dir)
    if state is None:
        return None

    # Gather ranked rows scoped to this session only
    run_ids = []
    if state.get("discovery_run_id"):
        run_ids.append(state["discovery_run_id"])
    run_ids.extend(state.get("validation_run_ids", []))
    run_ids.extend(state.get("followup_run_ids", []))

    scoped_rows: list[dict] = []
    for rid in run_ids:
        ranked_path = output_dir / rid / "ranked_strategies.csv"
        if ranked_path.exists():
            try:
                df = pd.read_csv(ranked_path)
                # Double-check market group isolation: filter symbols belonging to session group
                allowed = set(GROUP_SYMBOLS.get(state["market_group"], []))
                if allowed and "symbol" in df.columns:
                    df = df[df["symbol"].isin(allowed)]
                import math as _math
                def _clean(obj):
                    if isinstance(obj, dict):
                        return {k: _clean(v) for k, v in obj.items()}
                    if isinstance(obj, list):
                        return [_clean(v) for v in obj]
                    if isinstance(obj, float) and (_math.isnan(obj) or _math.isinf(obj)):
                        return None
                    return obj
                scoped_rows.extend(_clean(df.to_dict(orient="records")))
            except Exception as e:
                log.warning("[autopilot_session] Could not load ranked for %s: %s", rid, e)

    result = dict(state)
    result["scoped_rows"] = scoped_rows
    result["scoped_row_count"] = len(scoped_rows)
    return result


def stop_session(
    session_id: str,
    sessions_dir: Path = _SESSIONS_DIR,
) -> Optional[dict]:
    """Request cancellation of a live session."""
    if session_id in _active_sessions:
        _active_sessions[session_id].stop()
        return _active_sessions[session_id].to_dict()
    sess = AutopilotSession.load(session_id, sessions_dir)
    if sess is None:
        return None
    sess.stop()
    return sess.to_dict()


def list_sessions(sessions_dir: Path = _SESSIONS_DIR) -> list[dict]:
    """Return all sessions (in-memory live + persisted on disk)."""
    seen: set[str] = set()
    sessions: list[dict] = []

    # Live sessions first
    for sid, sess in _active_sessions.items():
        seen.add(sid)
        sessions.append(sess.to_dict())

    # Disk sessions
    sd = Path(sessions_dir)
    if sd.exists():
        for d in sorted(sd.iterdir(), reverse=True):
            if not d.is_dir() or d.name in seen:
                continue
            path = d / "session.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    sessions.append(data)
                    seen.add(d.name)
                except Exception:
                    pass

    return sessions
