"""Data freshness monitor for EdgeLab (research-only; no live execution imports)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _find_latest_manifest(repo: Path) -> Path | None:
    manifests = repo / "data" / "manifests"
    if not manifests.is_dir():
        return None
    files = sorted(manifests.glob("*.json"), reverse=True)
    return files[0] if files else None


def _check_symbol_tf(
    repo: Path,
    symbol: str,
    timeframe: str,
    *,
    max_age_hours: float,
    min_bars: int,
) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {"symbol": symbol, "timeframe": timeframe}

    manifest = _find_latest_manifest(repo)
    if manifest:
        details["manifest_path"] = str(manifest.relative_to(repo))
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            details["manifest_date"] = manifest.stem
            age_days = (_utcnow() - datetime.strptime(manifest.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
            details["manifest_age_days"] = age_days
            if age_days > max(7, max_age_hours / 24):
                warnings.append(f"frozen_manifest_stale:{manifest.stem}")
        except (ValueError, json.JSONDecodeError):
            warnings.append("manifest_parse_failed")
    else:
        warnings.append("no_frozen_manifest_found")

    candle_roots = [
        repo / "data" / "candles",
        repo / "data" / "frozen",
        repo / "athena_research" / "data",
    ]
    found_path = None
    for root in candle_roots:
        if not root.exists():
            continue
        for pattern in (f"**/{symbol}*{timeframe}*", f"**/{symbol}*"):
            matches = list(root.glob(pattern))
            if matches:
                found_path = max(matches, key=lambda p: p.stat().st_mtime)
                break
        if found_path:
            break

    if found_path:
        mtime = datetime.fromtimestamp(found_path.stat().st_mtime, tz=timezone.utc)
        age_h = (_utcnow() - mtime).total_seconds() / 3600.0
        details["data_path"] = str(found_path.relative_to(repo))
        details["data_age_hours"] = round(age_h, 2)
        if age_h > max_age_hours:
            blocking.append(f"data_file_stale:{age_h:.1f}h")
    else:
        warnings.append("no_local_candle_file_detected")
        details["data_quality_penalty"] = 0.5

    score = 100.0
    score -= len(blocking) * 25.0
    score -= len(warnings) * 10.0
    score = max(0.0, min(100.0, score))

    can_run = len(blocking) == 0 and score >= 40.0
    action = "proceed" if can_run else "block_engine_tests"
    if not can_run and blocking:
        action = "fix_data_before_research"
    elif not can_run:
        action = "review_warnings_before_research"

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "freshness_score": round(score, 2),
        "blocking_issues": blocking,
        "warnings": warnings,
        "recommended_action": action,
        "can_run_engine_tests": can_run,
        "details": details,
        "data_quality_penalty": max(0.0, (100.0 - score) / 100.0),
    }


def run_freshness_checks(
    repo: Path,
    *,
    symbols: list[str],
    timeframes: list[str],
    max_age_hours: float = 48,
    min_bars: int = 200,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if dry_run:
        return [
            {
                "symbol": symbols[0] if symbols else "DIAG",
                "timeframe": timeframes[0] if timeframes else "H4",
                "freshness_score": 85.0,
                "blocking_issues": [],
                "warnings": ["dry_run_freshness_stub"],
                "recommended_action": "proceed",
                "can_run_engine_tests": True,
                "details": {"dry_run": True},
                "data_quality_penalty": 0.0,
            }
        ]

    results = []
    for symbol in symbols:
        for tf in timeframes:
            results.append(
                _check_symbol_tf(
                    repo,
                    symbol,
                    tf,
                    max_age_hours=max_age_hours,
                    min_bars=min_bars,
                )
            )
    return results
