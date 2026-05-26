"""Engine A analyze_pair abort reason tracking (diagnostics only).

Records why ``analyze_pair`` exited before ``calc_confluence`` so scan stubs and
calibration telemetry can attribute stock/commodity blockers without log grep.
"""

from __future__ import annotations

import threading
from typing import Any

_ABORT_LOCK = threading.Lock()
_LAST_ABORT: dict[str, dict[str, Any]] = {}


def _pair_key(pair: dict[str, Any] | None) -> str:
    if not isinstance(pair, dict):
        return ""
    return str(pair.get("display") or pair.get("symbol") or "").strip()


def record_analyze_pair_abort(
    pair: dict[str, Any] | None,
    reason: str,
    *,
    detail: str | None = None,
    score_group: str | None = None,
) -> None:
    """Store the latest abort reason for a pair (scan worker reads via pop)."""
    key = _pair_key(pair)
    if not key:
        return
    payload = {
        "reason": str(reason or "unknown"),
        "detail": detail,
        "score_group": score_group,
        "pair": key,
        "symbol": pair.get("symbol") if isinstance(pair, dict) else None,
        "asset_type": pair.get("type") if isinstance(pair, dict) else None,
    }
    with _ABORT_LOCK:
        _LAST_ABORT[key] = payload


def pop_analyze_pair_abort(pair: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return and clear the last abort record for this pair."""
    key = _pair_key(pair)
    if not key:
        return None
    with _ABORT_LOCK:
        return _LAST_ABORT.pop(key, None)


def build_abort_stub_fields(abort: dict[str, Any] | None) -> dict[str, Any]:
    """Merge abort diagnostics into an Engine B-only scan stub."""
    if not isinstance(abort, dict):
        return {}
    reason = abort.get("reason")
    out: dict[str, Any] = {}
    if reason:
        out["engineAAbortReason"] = reason
        out["failureReason"] = reason
        out["scanDiagnostics"] = [
            {
                "code": f"engine_a_abort_{reason}",
                "detail": abort.get("detail") or reason,
            }
        ]
    if abort.get("score_group"):
        out["scoreGroup"] = abort.get("score_group")
    return out
