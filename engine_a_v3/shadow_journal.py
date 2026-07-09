"""Shadow journal for Engine A v3 live-vs-backtest reconciliation.

The validation pipeline (engine_a_v3/validation.py, workflow.py) proves the
scorer works on historical windows. It has never been checked against what
the live scan loop actually produces on the same bars. This module gives the
live path a cheap, best-effort way to leave a trail: one compact JSONL record
per scored signal, keyed by the exact candle provenance hash the score was
computed from. An offline replayer (scripts/engine_a_v3_shadow_replay.py) can
later re-run the scorer against cached candles for the same cutoff and diff
the result against what was recorded here.

Design constraints:
- Called from the live scan hot path (athena.py analyze_pair). Must never
  raise, never block, never slow the caller meaningfully.
- Journals are diagnostic only. They gate nothing and are not read by any
  execution or promotion path.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_journal_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "Athena" / "research" / "engine_a_v3" / "shadow"


def _journal_path(root: Path, when: datetime) -> Path:
    return root / f"{when.strftime('%Y-%m-%d')}.jsonl"


def build_shadow_entry(
    pair: dict[str, Any],
    candles: dict[str, list[dict]],
    signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the journal record for one scored signal. Returns None if the
    signal lacks enough context to be useful (e.g. it never reached scoring).
    """
    last_confirmed = signal.get("lastConfirmedCandleTs")
    if not last_confirmed:
        return None
    from engine_a_v3.profile import scorer_sha256
    from engine_a_v3.validation import provenance_sha256

    try:
        dataset_sha = provenance_sha256(candles)
    except Exception:
        return None
    return {
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "symbol": signal.get("symbol"),
        "display": signal.get("pair") or signal.get("display"),
        "type": signal.get("type"),
        "scoreGroup": signal.get("scoreGroup"),
        "horizon": signal.get("horizon") or signal.get("style"),
        "lastConfirmedCandleTs": last_confirmed,
        "decision": signal.get("decision"),
        "qualified": signal.get("qualified"),
        "direction": signal.get("direction"),
        "confluenceScore": signal.get("confluenceScore"),
        "confluenceThreshold": signal.get("confluenceThreshold"),
        "rejectionReasons": list(signal.get("rejectionReasons") or ()),
        "datasetProvenanceSha256": dataset_sha,
        "implementationSha256": scorer_sha256(),
        "barCounts": {tf: len(rows or []) for tf, rows in candles.items()},
    }


def record_shadow_entry(
    pair: dict[str, Any],
    candles: dict[str, list[dict]],
    signal: dict[str, Any],
    *,
    root: str | os.PathLike[str] | None = None,
) -> None:
    """Best-effort append. Never raises — a journaling failure must not
    affect the live scan."""
    try:
        entry = build_shadow_entry(pair, candles, signal)
        if entry is None:
            return
        journal_root = Path(root) if root else default_journal_root()
        journal_root.mkdir(parents=True, exist_ok=True)
        path = _journal_path(journal_root, datetime.now(timezone.utc))
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        return


def read_journal(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def compare_entry_to_rescore(entry: dict[str, Any], rescored: dict[str, Any]) -> dict[str, Any]:
    """Diff a journaled live signal against an offline rescore of the same
    (provenance-matched) candles. Used by the offline replayer."""
    from engine_a_v3.validation import provenance_sha256

    try:
        rescored_sha = provenance_sha256(rescored.get("_candles") or {})
    except Exception:
        rescored_sha = None
    provenance_match = rescored_sha is not None and rescored_sha == entry.get(
        "datasetProvenanceSha256"
    )
    fields = ("decision", "direction", "qualified", "confluenceScore")
    drift = {
        field: {"live": entry.get(field), "replay": rescored.get(field)}
        for field in fields
        if entry.get(field) != rescored.get(field)
    }
    return {
        "symbol": entry.get("symbol"),
        "horizon": entry.get("horizon"),
        "lastConfirmedCandleTs": entry.get("lastConfirmedCandleTs"),
        "provenanceMatch": provenance_match,
        "implementationMatch": rescored.get("implementationSha256")
        == entry.get("implementationSha256"),
        "drift": drift,
        "matched": provenance_match and not drift,
    }
