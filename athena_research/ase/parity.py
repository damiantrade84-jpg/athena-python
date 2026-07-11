"""Research vs runtime parity hashing (ASE v2.1 §8, T3.5)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from athena_ase.data.ptis import PTISStore
from athena_ase.horizon import Horizon
from athena_ase.instruments import DEFAULT_INSTRUMENTS
from athena_ase.inference.predict import predict_batch
from athena_ase.signals.engine import iter_candidates


def _hash_payload(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def feature_matrix_hash(feature_audit_rows: list[dict[str, Any]]) -> str:
    """Hash the ordered schemas and feature values consumed by inference."""
    return _hash_payload(feature_audit_rows)


def parity_hashes(
    store: PTISStore,
    *,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
    max_candidates: int = 200,
) -> dict[str, str]:
    """Compute feature+prediction hashes from runtime backfill path."""
    inst_map = {i.symbol: i for i in DEFAULT_INSTRUMENTS}
    candidates = list(
        iter_candidates(store, DEFAULT_INSTRUMENTS, horizon=horizon, start_ms=start_ms, end_ms=end_ms)
    )
    if max_candidates and len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]
    feature_rows: list[dict[str, Any]] = []
    runtime_signals = predict_batch(
        candidates,
        store,
        inst_map,
        feature_audit_rows=feature_rows,
    )

    return {
        "featureHash": feature_matrix_hash(feature_rows),
        "predictionHash": _hash_payload([s.to_dict() for s in runtime_signals]),
        "candidateCount": str(len(candidates)),
        "signalCount": str(len(runtime_signals)),
    }


def run_parity_check(
    store: PTISStore,
    *,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
    research_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    runtime = parity_hashes(store, horizon=horizon, start_ms=start_ms, end_ms=end_ms)
    ok = True
    mismatches: list[str] = []
    if research_hashes:
        for key in ("featureHash", "predictionHash"):
            if key in research_hashes and research_hashes[key] != runtime.get(key):
                ok = False
                mismatches.append(key)
    return {
        "ok": ok,
        "runtime": runtime,
        "research": research_hashes or {},
        "mismatches": mismatches,
    }
