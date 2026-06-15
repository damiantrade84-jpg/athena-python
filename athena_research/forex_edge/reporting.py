from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pandas as pd

from athena_research.forex_edge.models import InvalidResearchInputError


RUN_FILES = (
    "run_manifest.json",
    "eligibility.json",
    "quality.json",
    "trials.jsonl",
    "metrics.json",
    "equity_or_event_returns.parquet",
    "report.md",
)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_bytes(content)
        if path.exists() and path.read_bytes() != content:
            raise RuntimeError(f"run artifact conflict: {path}")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _write_parquet_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        ordered = frame.reindex(sorted(frame.columns), axis=1).sort_values(
            list(frame.columns)[:1]
        )
        ordered.to_parquet(temp, index=False)
        new_content = temp.read_bytes()
        if path.exists() and path.read_bytes() != new_content:
            raise RuntimeError(f"run artifact conflict: {path}")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def write_run_artifacts(
    output_root: Path,
    payload: dict[str, object],
) -> tuple[Path, ...]:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise InvalidResearchInputError("run_id required")
    run_dir = output_root / "runs" / run_id
    manifest_path = run_dir / "run_manifest.json"
    eligibility_path = run_dir / "eligibility.json"
    quality_path = run_dir / "quality.json"
    trials_path = run_dir / "trials.jsonl"
    metrics_path = run_dir / "metrics.json"
    returns_path = run_dir / "equity_or_event_returns.parquet"
    report_path = run_dir / "report.md"

    _write_atomic(manifest_path, _canonical_json(payload["manifest"]))
    _write_atomic(eligibility_path, _canonical_json(payload["eligibility"]))
    _write_atomic(quality_path, _canonical_json(payload["quality"]))
    trials = payload["trials"]
    if not isinstance(trials, list):
        raise InvalidResearchInputError("trials must be a list")
    trial_bytes = b"".join(_canonical_json(trial) for trial in trials)
    _write_atomic(trials_path, trial_bytes)
    _write_atomic(metrics_path, _canonical_json(payload["metrics"]))
    returns = payload["returns"]
    if not isinstance(returns, pd.DataFrame):
        raise InvalidResearchInputError("returns must be a DataFrame")
    _write_parquet_atomic(returns_path, returns)
    metrics = payload["metrics"]
    status = metrics.get("study_status") if isinstance(metrics, dict) else "UNKNOWN"
    report = (
        f"# Forex Edge Research Run\n\n"
        f"study_status: {status}\n\n"
        "production_eligible: false\n"
    ).encode("utf-8")
    _write_atomic(report_path, report)
    return tuple(run_dir / name for name in RUN_FILES)
