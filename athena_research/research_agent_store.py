"""Research Agent persistence layer.

Stores proposed plans, validations, approved runs, comparisons, and recommendations.
Uses JSONL files to be consistent with the repo style and avoid DB lock issues.

Safety:
- Write failures must not crash UI/API.
- Does not store secrets.
- Does not overwrite previous research results.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_STORE_DIR = Path("logs/research_agent")
_PLANS_FILE = _STORE_DIR / "plans.jsonl"
_RUNS_FILE = _STORE_DIR / "runs.jsonl"

_MIN_PLANS_DIR = _STORE_DIR / "plans"
_MIN_RUNS_DIR = _STORE_DIR / "runs"


def _ensure_dir() -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    _MIN_PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _MIN_RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, record: dict[str, Any]) -> bool:
    """Append a single JSON line to a file. Returns True on success."""
    try:
        _ensure_dir()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return True
    except Exception as exc:
        log.warning("[ResearchAgentStore] Failed to write %s: %s", path, exc)
        return False


def _read_jsonl(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Read JSONL file and return records, newest first."""
    if not path.exists():
        return []
    try:
        records: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records[-limit:][::-1]
    except Exception as exc:
        log.warning("[ResearchAgentStore] Failed to read %s: %s", path, exc)
        return []


def persist_plan(
    plan_id: str,
    user_goal: str,
    plan_json: dict[str, Any],
    validation_json: dict[str, Any] | None = None,
) -> bool:
    """Persist a proposed plan. Returns True on success."""
    record = {
        "plan_id": plan_id,
        "created_at": _now_iso(),
        "user_goal": user_goal,
        "plan_json": plan_json,
        "validation_json": validation_json,
        "approved": False,
        "run_status": None,
        "run_output_path": None,
        "recommendation_json": None,
        "do_not_auto_apply": True,
    }
    ok = _append_jsonl(_PLANS_FILE, record)

    # Also write individual plan file
    try:
        plan_path = _MIN_PLANS_DIR / f"{plan_id}.json"
        plan_path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        log.warning("[ResearchAgentStore] Failed to write individual plan file: %s", exc)

    return ok


def persist_plan_run(
    plan_id: str,
    user_goal: str,
    plan_json: dict[str, Any],
    approved: bool = False,
    run_status: str = "pending",
    validation_json: dict[str, Any] | None = None,
    child_run_ids: list[str] | None = None,
) -> bool:
    """Persist a plan run (approved execution). Returns True on success."""
    record = {
        "plan_id": plan_id,
        "created_at": _now_iso(),
        "user_goal": user_goal,
        "plan_json": plan_json,
        "validation_json": validation_json,
        "approved": approved,
        "run_status": run_status,
        "child_run_ids": child_run_ids or [],
        "run_output_path": str(_STORE_DIR / "runs" / plan_id),
        "recommendation_json": None,
        "do_not_auto_apply": True,
    }
    ok = _append_jsonl(_RUNS_FILE, record)

    try:
        run_path = _MIN_RUNS_DIR / f"{plan_id}.json"
        run_path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        log.warning("[ResearchAgentStore] Failed to write individual run file: %s", exc)

    return ok


def persist_validation(plan_id: str, validation_json: dict[str, Any]) -> bool:
    """Update an existing plan with validation results."""
    try:
        # Write individual validation file
        val_path = _MIN_PLANS_DIR / f"{plan_id}_validation.json"
        val_path.write_text(json.dumps(validation_json, indent=2, default=str), encoding="utf-8")
        return True
    except Exception as exc:
        log.warning("[ResearchAgentStore] Failed to persist validation: %s", exc)
        return False


def persist_comparison(
    baseline_run_id: str,
    candidate_run_id: str,
    comparison_json: dict[str, Any],
) -> bool:
    """Persist a comparison between two runs."""
    record = {
        "comparison_id:": f"cmp_{baseline_run_id}_{candidate_run_id}",
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "created_at": _now_iso(),
        "comparison_json": comparison_json,
    }
    return _append_jsonl(_STORE_DIR / "comparisons.jsonl", record)


def persist_recommendations(
    plan_id: str,
    recommendation_json: list[dict[str, Any]],
) -> bool:
    """Persist recommendations for a plan."""
    record = {
        "plan_id": plan_id,
        "created_at": _now_iso(),
        "recommendation_json": recommendation_json,
    }
    return _append_jsonl(_STORE_DIR / "recommendations.jsonl", record)


def get_plans(limit: int = 20) -> list[dict[str, Any]]:
    """Return stored plans, newest first."""
    return _read_jsonl(_PLANS_FILE, limit)


def get_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return stored runs, newest first."""
    return _read_jsonl(_RUNS_FILE, limit)


def get_plan(plan_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a single plan by ID."""
    plan_path = _MIN_PLANS_DIR / f"{plan_id}.json"
    if plan_path.exists():
        try:
            return json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    for record in _read_jsonl(_PLANS_FILE, 500):
        if record.get("plan_id") == plan_id:
            return record
    return None


def get_run(plan_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a single run by plan_id."""
    run_path = _MIN_RUNS_DIR / f"{plan_id}.json"
    if run_path.exists():
        try:
            return json.loads(run_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    for record in _read_jsonl(_RUNS_FILE, 500):
        if record.get("plan_id") == plan_id:
            return record
    return None
