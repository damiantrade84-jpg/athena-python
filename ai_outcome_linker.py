"""AI Outcome Linker — connects AI review decisions to actual trade/backtest outcomes.

Reads from:
  - ai_review_audit.jsonl (log_ai_review records)
  - ai_agent_chat.jsonl (AI chat turns)
  - learning_log (SQLite, trade outcomes linked during execution)
  - audit_log (SQLite, complete trade records with exit prices and R multiples)
  - research_agent outputs (research recommendations)

Safety:
  - Read-only. Never modifies trade state or config.
  - Missing outcome data is recorded with sample_valid=False and a reason.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_evaluation_contracts import AIEvaluationSample

log = logging.getLogger(__name__)

REVIEW_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "logs", "ai_review", "ai_review_audit.jsonl"
)
CHAT_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "logs", "ai_review", "ai_agent_chat.jsonl"
)
_DEFAULT_AUDIT_DB = os.path.join(os.path.dirname(__file__), "audit.db")
_DEFAULT_LEARNING_DB = os.path.join(os.path.dirname(__file__), "logs", "learning.db")

SURFACE_MAP = {
    "marcus_reid": "MARCUS",
    "chart_vision": "VISION",
    "signal_debate": "DEBATE",
    "strategist": "STRATEGIST",
    "chat": "CHAT",
    "engine_b_ai": "ENGINE_B_AI",
    "engine_c_ai": "ENGINE_C_AI",
    "research_ai": "RESEARCH_AGENT",
    "news_sentiment": "NEWS_SENTIMENT",
    "decay_ai": "DECAY_AI",
    "meta_analysis": "META_ANALYSIS",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _read_jsonl(path: str, limit: int = 10000) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        log.warning("[OutcomeLinker] Failed to read %s: %s", path, exc)
        return []
    return records[-limit:]


def _read_learning_logs(db_path: str, lookback_days: int = 30) -> list[dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    cutoff = (_now_utc() - timedelta(days=lookback_days)).isoformat()
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM learning_log WHERE ts > ? ORDER BY ts DESC",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("[OutcomeLinker] Learning DB read failed: %s", exc)
        return []


def _read_audit_logs(db_path: str, lookback_days: int = 30) -> list[dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    cutoff = (_now_utc() - timedelta(days=lookback_days)).isoformat()
    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM audit_log WHERE ts > ? ORDER BY ts DESC",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("[OutcomeLinker] Audit DB read failed: %s", exc)
        return []


def load_ai_review_samples(lookback_days: int = 30) -> list[dict[str, Any]]:
    """Load raw AI review records from JSONL logs."""
    records = _read_jsonl(REVIEW_LOG_PATH)
    chat_records = _read_jsonl(CHAT_LOG_PATH)

    # Filter by lookback
    cutoff = (_now_utc() - timedelta(days=lookback_days)).isoformat()
    filtered = []
    for r in records:
        ts = r.get("timestamp", "")
        if ts >= cutoff:
            filtered.append(r)

    chat_filtered = []
    for r in chat_records:
        ts = r.get("ts", "")
        if ts >= cutoff:
            chat_filtered.append(r)

    return filtered + chat_filtered


def normalize_ai_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize varied AI grade/decision formats into canonical fields."""
    out: dict[str, Any] = {}
    review_type = str(raw.get("review_type", "")).lower()

    # Map review_type to ai_surface
    explicit_surface = str(raw.get("ai_surface") or "").strip().upper()
    out["ai_surface"] = explicit_surface or SURFACE_MAP.get(review_type, review_type.upper()) or "UNKNOWN"

    # ai_review_state from review logger
    ai_state = str(raw.get("ai_review_state") or raw.get("decision") or "").upper()

    # Decision normalization
    if ai_state in ("CONFIRM", "STRONG_GO", "A+", "A"):
        out["ai_decision"] = "POSITIVE"
        out["ai_grade"] = "A"
    elif ai_state in ("CAUTION", "WEAK_GO", "B", "REVIEW"):
        out["ai_decision"] = "NEUTRAL"
        out["ai_grade"] = "B"
    elif ai_state in ("REJECT", "STRONG_AVOID", "C", "D", "F",
                       "CONTRADICTS", "POTENTIAL_REVERSAL", "BLOCKED"):
        out["ai_decision"] = "NEGATIVE"
        out["ai_grade"] = ai_state
    elif ai_state in ("NO_TRADE", "BLOCK"):
        out["ai_decision"] = "BLOCKED"
        out["ai_grade"] = "BLOCKED"
    elif ai_state in ("DATA_INSUFFICIENT", "REVIEW_INCOMPLETE", "SKIP"):
        out["ai_decision"] = "INSUFFICIENT"
        out["ai_grade"] = "INSUFFICIENT"
    else:
        out["ai_decision"] = ai_state or "UNKNOWN"
        out["ai_grade"] = ai_state or "UNKNOWN"

    # Confidence
    out["ai_confidence"] = raw.get("ai_confidence")

    # Block/downgrade
    out["ai_blocked"] = out["ai_decision"] in ("BLOCKED", "NEGATIVE")
    out["ai_downgraded"] = bool(raw.get("ai_changed_execution_permission", False))

    # Execution permission change
    before = bool(raw.get("execution_allowed_before_ai", False))
    after = bool(raw.get("execution_allowed_after_ai", False))
    out["deterministic_decision_before_ai"] = before
    out["deterministic_decision_after_ai"] = after

    # Parse success
    out["parse_success"] = bool(raw.get("parse_success", True))

    # Contradictions
    raw_contradictions = raw.get("contradiction_flags") or raw.get("contradictions") or []
    out["contradiction_flags"] = raw_contradictions if isinstance(raw_contradictions, list) else []

    # Freshness
    out["data_freshness"] = raw.get("candle_freshness_status") or raw.get("data_freshness")
    out["vision_freshness"] = raw.get("vision_freshness")

    return out


def link_samples_to_outcomes(
    samples: list[dict[str, Any]],
    audit_db: str | None = None,
    learning_db: str | None = None,
) -> list[dict[str, Any]]:
    """Link AI review samples to actual trade outcomes.

    Linking priority:
    1. trace_id match in audit_log / learning_log
    2. symbol + timestamp proximity
    3. ticket reference

    Returns AIEvaluationSample-compatible dicts.
    """
    db_path = audit_db or os.environ.get("ATHENA_AUDIT_DB") or _DEFAULT_AUDIT_DB
    learn_path = learning_db or os.environ.get("ATHENA_LEARNING_DB") or _DEFAULT_LEARNING_DB

    audit_trades = _read_audit_logs(db_path, lookback_days=90)
    learning_rows = _read_learning_logs(learn_path, lookback_days=90)

    linked: list[dict[str, Any]] = []

    for sample in samples:
        norm = normalize_ai_decision(sample)
        sample_id = f"eval_{abs(hash(json.dumps(sample, default=str)))}"

        trace_id = (
            sample.get("trace_id")
            or norm.get("trace_id")
            or sample.get("thread_id")
        )
        symbol = sample.get("symbol", norm.get("symbol", ""))
        timestamp = sample.get("timestamp") or sample.get("ts", "")

        # Try linking by trace_id
        matched_outcome = None
        for trade in audit_trades:
            if trace_id and trade.get("ticket") == trace_id:
                matched_outcome = trade
                break
            if symbol and trade.get("pair", "").upper() in (symbol.upper(),) and timestamp:
                # Check time proximity (±24h)
                trade_ts = trade.get("ts", "")
                if trade_ts and timestamp:
                    try:
                        t1 = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        t2 = datetime.fromisoformat(trade_ts.replace("Z", "+00:00"))
                        if abs((t1 - t2).total_seconds()) < 86400:
                            matched_outcome = trade
                            break
                    except (ValueError, TypeError):
                        continue

        # Try learning_log if not matched
        if matched_outcome is None:
            for row in learning_rows:
                if trace_id and row.get("ticket") == trace_id:
                    matched_outcome = row
                    break
                if symbol and row.get("pair", "").upper() in (symbol.upper(),):
                    row_ts = row.get("trade_ts") or row.get("ts", "")
                    if row_ts and timestamp:
                        try:
                            t1 = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                            t2 = datetime.fromisoformat(row_ts.replace("Z", "+00:00"))
                            if abs((t1 - t2).total_seconds()) < 86400:
                                matched_outcome = row
                                break
                        except (ValueError, TypeError):
                            continue

        result: dict[str, Any] = {
            "sample_id": sample_id[:32],
            "trace_id": trace_id or sample.get("thread_id"),
            "symbol": symbol,
            "asset_type": sample.get("asset_type", norm.get("asset_type", "")),
            "engine": sample.get("engine", norm.get("engine", "")),
            "timeframe": sample.get("timeframe", norm.get("timeframe", "")),
            "setup_type": norm.get("setup_type", ""),
            "ai_surface": norm.get("ai_surface", "UNKNOWN"),
            "ai_decision": norm.get("ai_decision"),
            "ai_grade": norm.get("ai_grade"),
            "ai_confidence": norm.get("ai_confidence"),
            "ai_blocked": norm.get("ai_blocked", False),
            "ai_downgraded": norm.get("ai_downgraded", False),
            "ai_requested_confirmation": False,
            "review_type": sample.get("review_type", ""),
            "deterministic_decision_before_ai": norm.get("deterministic_decision_before_ai", False),
            "deterministic_decision_after_ai": norm.get("deterministic_decision_after_ai", False),
            "contradiction_flags": norm.get("contradiction_flags", []),
            "parse_success": norm.get("parse_success", True),
            "data_freshness": norm.get("data_freshness"),
            "vision_freshness": norm.get("vision_freshness"),
            # Outcome fields
            "actual_outcome": None,
            "realized_r": None,
            "exit_reason": None,
            "holding_bars": None,
            "outcome_timestamp": None,
            "sample_valid": False,
            "missing_outcome_reason": None,
            "is_simulated": False,
        }

        if matched_outcome:
            r_mult = matched_outcome.get("r_multiple") or matched_outcome.get("r_multiple")
            if r_mult is not None:
                result["realized_r"] = float(r_mult)
                if float(r_mult) > 0:
                    result["actual_outcome"] = "WIN"
                elif float(r_mult) < 0:
                    result["actual_outcome"] = "LOSS"
                else:
                    result["actual_outcome"] = "BREAKEVEN"
            else:
                result["actual_outcome"] = matched_outcome.get("outcome")

            result["exit_reason"] = matched_outcome.get("exit_reason")
            result["outcome_timestamp"] = matched_outcome.get("exit_time") or matched_outcome.get("ts")
            result["holding_bars"] = matched_outcome.get("holding_period_hours")
            result["sample_valid"] = result["actual_outcome"] is not None
            if not result["sample_valid"]:
                result["missing_outcome_reason"] = "Outcome record found but no R multiple or win/loss indicator."
        else:
            result["missing_outcome_reason"] = "No matching trade outcome found in audit or learning logs."
            result["sample_valid"] = False

        linked.append(result)

    return linked


def classify_block_quality(sample: dict[str, Any]) -> dict[str, Any]:
    """Classify whether an AI block/downgrade/allow was useful or harmful.

    Definitions:
      useful_block: AI blocked/downgraded and trade outcome was LOSS
                    or trade would have violated risk/freshness.
      false_block:  AI blocked/downgraded and trade outcome was WIN.
      harmful_allow: AI allowed/positive and trade outcome was LOSS.
      neutral:      Insufficient outcome data or BREAKEVEN.
    """
    ai_blocked = bool(sample.get("ai_blocked", False))
    ai_downgraded = bool(sample.get("ai_downgraded", False))
    ai_decision = str(sample.get("ai_decision", "")).upper()
    outcome = str(sample.get("actual_outcome") or "").upper()
    sample_valid = bool(sample.get("sample_valid", False))
    realized_r = sample.get("realized_r")

    result: dict[str, Any] = {
        "block_type": "neutral",
        "useful_block": False,
        "false_block": False,
        "harmful_allow": False,
    }

    if not sample_valid or not outcome:
        result["block_type"] = "neutral"
        result["note"] = "No valid outcome to classify."
        return result

    ai_was_negative = ai_blocked or ai_downgraded or ai_decision in ("BLOCKED", "NEGATIVE")
    ai_was_positive = not ai_was_negative and ai_decision in ("POSITIVE",)

    if not ai_was_negative and not ai_was_positive:
        result["block_type"] = "neutral"
        result["note"] = "AI decision was neutral or insufficient."
        return result

    if ai_was_negative:
        if outcome == "LOSS":
            result["block_type"] = "useful_block"
            result["useful_block"] = True
        elif outcome == "WIN":
            result["block_type"] = "false_block"
            result["false_block"] = True
        else:
            result["block_type"] = "neutral"
            result["note"] = "Blocked but outcome is breakeven."

    if ai_was_positive:
        if outcome == "LOSS":
            result["block_type"] = "harmful_allow"
            result["harmful_allow"] = True
        elif outcome == "WIN":
            result["block_type"] = "neutral"
            result["note"] = "Allowed correctly."
        else:
            result["block_type"] = "neutral"

    return result
