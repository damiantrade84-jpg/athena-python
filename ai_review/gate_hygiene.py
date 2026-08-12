"""Review-layer gate hygiene (P2-1, P2-2, P2-3).

Scope boundary: this module NEVER changes Engine A's own pass/fail decision.
Engine A predicates arrive already evaluated; here they are only re-rendered for
the review panel so the displayed ratio is honest:

* P2-1 ``active_session_utc`` is re-checked against scan time. Engine A evaluates
  it against the last confirmed candle, so a 06:38 UTC scan whose confirmed H4
  candle is 01:00 UTC renders FAIL inside a 06:00-21:00 window. Both values are
  surfaced and the disagreement is named rather than silently overwritten.
* P2-2 ``context_trend_long`` / ``context_trend_short`` are mutually exclusive.
  Both failing means "neutral", which is one observation, not two independent
  failures. They collapse into a single tri-state gate counted once.
* P2-3 gates whose feed is excluded for the asset group (volume on groups whose
  quant weight is 0.0) render ``NA`` and leave the denominator entirely.

The Engine A evaluation defects these render around are reported separately;
fixing them in ``engine_a_v3/setups.py`` would change live signal generation and
is out of this fix pack's scope.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


# "London/NY 06:00-21:00 UTC" / "US cash session 13:00-21:00 UTC"
_SESSION_WINDOW_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")

_CONTEXT_TREND_GATES = ("context_trend_long", "context_trend_short")
_VOLUME_GATES = ("volume_supports_direction", "volume_confirms", "volume_quality")

PASS = "PASS"
FAIL = "FAIL"
NA = "NA"


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_session_window(label: Any) -> tuple[int, int] | None:
    """Extract the UTC hour window from an Engine A session gate label."""
    match = _SESSION_WINDOW_RE.search(str(label or ""))
    if not match:
        return None
    start_hour, _start_min, end_hour, _end_min = (int(g) for g in match.groups())
    if not (0 <= start_hour <= 24 and 0 <= end_hour <= 24):
        return None
    return start_hour, end_hour


def in_session(moment: datetime | None, window: tuple[int, int] | None) -> bool | None:
    if moment is None or window is None:
        return None
    start_hour, end_hour = window
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= moment.hour < end_hour
    # Window wraps midnight (e.g. 22:00-06:00).
    return moment.hour >= start_hour or moment.hour < end_hour


def volume_feed_excluded(score_group: Any, quant_weights_by_group: Any) -> bool:
    """True when the asset group's volume component carries zero weight.

    ``ENGINE_A_QUANT_WEIGHTS_BY_GROUP[group].volume == 0.0`` is the authoritative
    declaration that the volume feed is not used for that group (config.yaml
    zeroed these after the 2026-08-09 audit found the feed can never clear live
    provenance). A gate cannot be excluded as unreliable and still fail.
    """
    if not isinstance(quant_weights_by_group, dict):
        return False
    entry = quant_weights_by_group.get(str(score_group or ""))
    if not isinstance(entry, dict) or "volume" not in entry:
        return False
    weight = entry.get("volume")
    if weight is None:
        return False
    try:
        return float(weight) == 0.0
    except (TypeError, ValueError):
        return False


def _predicate_rows(predicates: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in predicates or []:
        if isinstance(item, dict):
            rows.append(item)
        else:
            name = getattr(item, "name", None)
            if name is None:
                continue
            rows.append(
                {
                    "name": name,
                    "passed": getattr(item, "passed", None),
                    "actual": getattr(item, "actual", None),
                    "expected": getattr(item, "expected", None),
                }
            )
    return rows


def normalize_review_gates(
    *,
    predicates: Any,
    score_group: Any = None,
    scan_timestamp: Any = None,
    quant_weights_by_group: Any = None,
) -> dict[str, Any]:
    """Render Engine A predicates as an honest applicable-gate view.

    Returns the normalized gate list plus ``applicablePassed`` /
    ``applicableTotal``. Engine A's decision is untouched; ``engineValue`` on
    each row preserves what Engine A actually decided.
    """
    rows = _predicate_rows(predicates)
    volume_excluded = volume_feed_excluded(score_group, quant_weights_by_group)
    scan_dt = _parse_iso(scan_timestamp)

    gates: list[dict[str, Any]] = []
    notes: list[str] = []
    session_recheck: dict[str, Any] | None = None
    trend_rows = {
        row.get("name"): row for row in rows if row.get("name") in _CONTEXT_TREND_GATES
    }

    for row in rows:
        name = str(row.get("name") or "")
        engine_passed = row.get("passed")

        # P2-2: handled once, after the loop.
        if name in _CONTEXT_TREND_GATES:
            continue

        # P2-3: excluded feed leaves the denominator.
        if name in _VOLUME_GATES and volume_excluded:
            gates.append(
                {
                    "name": name,
                    "status": NA,
                    "applicable": False,
                    "engineValue": engine_passed,
                    "reason": (
                        f"Volume feed is not used for asset group "
                        f"{score_group!r} (quant volume weight 0.0); gate excluded "
                        f"from the denominator"
                    ),
                }
            )
            notes.append(f"{name} rendered N/A — excluded volume feed")
            continue

        # P2-1: re-check the session gate against scan time.
        if name == "active_session_utc":
            window = parse_session_window(row.get("expected"))
            review_active = in_session(scan_dt, window)
            session_recheck = {
                "engineValue": engine_passed,
                "engineEvaluatedAt": row.get("actual"),
                "reviewValue": review_active,
                "reviewEvaluatedAt": scan_dt.isoformat() if scan_dt else None,
                "window": list(window) if window else None,
                "basis": "scan_timestamp",
                "disagrees": (
                    review_active is not None
                    and engine_passed is not None
                    and bool(review_active) != bool(engine_passed)
                ),
            }
            if session_recheck["disagrees"]:
                notes.append(
                    "active_session_utc disagrees: Engine A evaluated it against the "
                    f"last confirmed candle ({row.get('actual')}) while scan time "
                    f"({session_recheck['reviewEvaluatedAt']}) is "
                    f"{'inside' if review_active else 'outside'} {row.get('expected')}"
                )
            status = (
                PASS
                if review_active
                else FAIL
                if review_active is not None
                else (PASS if engine_passed else FAIL)
            )
            gates.append(
                {
                    "name": name,
                    "status": status,
                    "applicable": True,
                    "engineValue": engine_passed,
                    "reason": row.get("expected"),
                    "evaluatedAgainst": "scan_timestamp"
                    if review_active is not None
                    else "engine_a_confirmed_candle",
                }
            )
            continue

        gates.append(
            {
                "name": name,
                "status": PASS if engine_passed else FAIL,
                "applicable": True,
                "engineValue": engine_passed,
                "reason": row.get("expected"),
            }
        )

    # P2-2: single tri-state context_trend gate, counted once.
    context_trend = None
    if trend_rows:
        long_passed = bool((trend_rows.get("context_trend_long") or {}).get("passed"))
        short_passed = bool((trend_rows.get("context_trend_short") or {}).get("passed"))
        if long_passed and not short_passed:
            state = "LONG"
        elif short_passed and not long_passed:
            state = "SHORT"
        else:
            # Both false = neutral. Both true is contradictory and also not a
            # directional reading; either way it is one observation.
            state = "NEUTRAL"
        context_trend = state
        gates.append(
            {
                "name": "context_trend",
                "status": FAIL if state == "NEUTRAL" else PASS,
                "applicable": True,
                "state": state,
                "engineValue": {"long": long_passed, "short": short_passed},
                "reason": (
                    "context_trend_long/short are mutually exclusive and are counted "
                    "once as a tri-state gate"
                ),
                "collapsedFrom": list(_CONTEXT_TREND_GATES),
            }
        )
        if state == "NEUTRAL":
            notes.append(
                "context_trend is NEUTRAL — one reading, counted once (was rendered "
                "as two independent failures)"
            )

    applicable = [gate for gate in gates if gate.get("applicable")]
    passed = [gate for gate in applicable if gate.get("status") == PASS]

    return {
        "gates": gates,
        "applicablePassed": len(passed),
        "applicableTotal": len(applicable),
        "display": f"{len(passed)}/{len(applicable)}",
        "excludedCount": len(gates) - len(applicable),
        "contextTrend": context_trend,
        "sessionRecheck": session_recheck,
        "notes": notes,
    }
