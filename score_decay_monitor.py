"""Pure matching helpers for advisory open-position score decay."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


SUPPORTED_DECAY_ENGINES = frozenset({"engine_a", "engine_b", "engine_c", "scalp"})


def normalize_decay_pair(value: Any) -> str:
    return str(value or "").replace("/", "").replace(" ", "").upper()


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _row_matches_position(row: dict[str, Any], position: dict[str, Any]) -> bool:
    if normalize_decay_pair(row.get("pair")) != normalize_decay_pair(position.get("pair")):
        return False

    row_direction = str(row.get("direction") or "").upper()
    position_direction = str(position.get("direction") or "").upper()
    if not row_direction or row_direction != position_direction:
        return False

    venue = str(position.get("venue") or "").lower()
    if venue == "mt5":
        row_ticket = str(row.get("ticket") or "")
        position_ticket = str(position.get("ticket") or "")
        return bool(row_ticket and position_ticket and row_ticket == position_ticket)

    if venue == "bybit":
        row_entry = _safe_float(row.get("entry_price"))
        position_entry = _safe_float(
            position.get("entryPrice") or position.get("entry")
        )
        if not row_entry or not position_entry or row_entry <= 0 or position_entry <= 0:
            return False
        return abs(row_entry - position_entry) / row_entry <= 0.008

    return False


def _baseline_fingerprint(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("engine") or "").lower(),
        str(row.get("direction") or "").upper(),
        str(row.get("style") or "").lower(),
        _safe_float(row.get("score")),
        _safe_float(row.get("max_score")),
        _safe_float(row.get("score_pct")),
    )


def select_decay_audit_rows(
    audit_rows: Iterable[dict[str, Any]],
    broker_positions: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Select one unambiguous audit baseline for each broker-live pair.

    MT5 positions require an exact ticket. Bybit positions use pair, direction,
    and entry-price proximity because the broker position index is not the
    execution order id stored in ``audit_log``. Multiple live positions for one
    pair fail closed because the public decay response is currently pair-keyed.
    """
    rows_by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    positions_by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display_by_pair: dict[str, str] = {}

    for raw_row in audit_rows:
        row = dict(raw_row)
        key = normalize_decay_pair(row.get("pair"))
        if key:
            rows_by_pair[key].append(row)
            display_by_pair.setdefault(key, str(row.get("pair") or key))

    for raw_position in broker_positions:
        position = dict(raw_position)
        key = normalize_decay_pair(position.get("pair"))
        if key:
            positions_by_pair[key].append(position)
            display_by_pair[key] = str(position.get("pair") or display_by_pair.get(key) or key)

    selected: dict[str, dict[str, Any]] = {}
    for pair_key, positions in positions_by_pair.items():
        display = display_by_pair[pair_key]
        if len(positions) != 1:
            selected[display] = {
                "status": "UNAVAILABLE",
                "reason": "ambiguous_broker_positions",
                "pair": display,
                "positionCount": len(positions),
            }
            continue

        position = positions[0]
        matched = [
            row
            for row in rows_by_pair.get(pair_key, [])
            if _row_matches_position(row, position)
        ]
        if not matched:
            selected[display] = {
                "status": "UNAVAILABLE",
                "reason": "broker_position_has_no_matching_audit_baseline",
                "pair": display,
            }
            continue

        supported = [
            row
            for row in matched
            if str(row.get("engine") or "").lower() in SUPPORTED_DECAY_ENGINES
        ]
        if not supported:
            engines = sorted({str(row.get("engine") or "unknown").lower() for row in matched})
            selected[display] = {
                "status": "UNAVAILABLE",
                "reason": "unsupported_decay_engine",
                "pair": display,
                "engines": engines,
            }
            continue

        fingerprints = {_baseline_fingerprint(row) for row in supported}
        if len(fingerprints) != 1:
            selected[display] = {
                "status": "UNAVAILABLE",
                "reason": "ambiguous_audit_baselines",
                "pair": display,
                "baselineCount": len(supported),
            }
            continue

        row = max(
            supported,
            key=lambda item: (str(item.get("ts") or ""), int(item.get("id") or 0)),
        )
        venue = str(position.get("venue") or "unknown").lower()
        position_identity = (
            str(position.get("ticket") or "")
            if venue == "mt5"
            else f"{str(position.get('direction') or '').upper()}@{position.get('entryPrice') or position.get('entry')}"
        )
        selected[display] = {
            "status": "MATCHED",
            "pair": display,
            "row": row,
            "position": position,
            "positionKey": f"{venue}:{pair_key}:{position_identity}",
        }

    return selected
