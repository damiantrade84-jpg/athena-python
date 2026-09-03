"""Attribute a live broker position to the engine that opened it.

Engine A, Engine B and Engine D stamp ``engine`` on their ``audit_log`` row, so
``/api/open-trades-timed`` reads the engine straight off the audit match. GROK,
SOL, OPUS, KIMI and OX Alpha execute through their own coordinators and never
write an ``audit_log`` execution row, so a position one of them opened reaches
the dashboard with no engine at all and renders as "Unknown".

This module answers "which engine opened this position?" by reading each
engine's own execution record. It is display metadata only: it never writes to
an engine store, never participates in a gate, and returns nothing rather than
guessing when the evidence is ambiguous.

Matching is ticket-first. Bybit positions report ``ticket`` as ``positionIdx``
(usually ``0``) while Bybit fills return an order id, so a Bybit position can
only be matched on symbol + direction + entry price inside a time window around
its open. Two different engines matching the same position is treated as no
match - a wrong engine label is worse than an honest "Unknown".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

from symbol_matching import symbols_match

log = logging.getLogger("sentinel")

_ROOT = Path(__file__).resolve().parent

# Sink for engines that keep no durable execution store of their own.
RECORD_PATH = _ROOT / "state" / "engine_attribution.jsonl"

# Engine keys match the frontend's auditEngine.ts alias table.
ENGINE_GROK = "grok"
ENGINE_SOL = "sol"
ENGINE_OPUS = "opus"
ENGINE_KIMI = "kimi"
ENGINE_OX_ALPHA = "ox_alpha"
ENGINE_FABLE = "fable"

_CACHE_TTL_SEC = 5.0
# An execution record and its resulting position agree on open time to within
# broker fill latency; the window absorbs clock skew without letting an
# unrelated fill on the same symbol hours later claim the position.
_OPEN_TIME_WINDOW_SEC = 30 * 60.0
# Tighter than the 1% used for audit-row matching because this fallback has no
# ticket to fall back on.
_MAX_ENTRY_DRIFT = 0.005
# Records older than this cannot correspond to anything the brokers still list.
_MAX_RECORD_AGE_SEC = 30 * 24 * 3600.0


@dataclass(frozen=True)
class ExecutionRecord:
    """One engine-owned record of a broker fill."""

    engine: str
    venue: str
    tickets: frozenset = field(default_factory=frozenset)
    symbols: tuple = ()
    direction: str = ""
    entry: float = 0.0
    ts: float = 0.0


# -- parsing helpers --------------------------------------------------------


def _float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _epoch(value: Any) -> float:
    """Epoch seconds from an ISO string or a numeric timestamp."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _ticket_key(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text in ("", "0", "None") else text.upper()


def _tickets(*values: Any) -> frozenset:
    keys = set()
    for value in values:
        key = _ticket_key(value)
        if key:
            keys.add(key)
    return frozenset(keys)


def _symbols(*values: Any) -> tuple:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in ("LONG", "BUY"):
        return "LONG"
    if text in ("SHORT", "SELL"):
        return "SHORT"
    return ""


def _venue(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("bybit"):
        return "bybit"
    if text.startswith("mt5"):
        return "mt5"
    return text


def _json_obj(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# -- engine store adapters --------------------------------------------------


def _record_from_broker_result(
    engine: str,
    venue: Any,
    result: Mapping[str, Any],
    signal: Mapping[str, Any],
    ts: float,
) -> ExecutionRecord | None:
    """Shape a GROK/SOL/OX Alpha broker result into a record.

    Paper fills carry a synthetic ticket and never reach a broker, so they are
    dropped: attributing a live position to a paper fill would be a fabrication.
    """
    if not result.get("success"):
        return None
    if str(result.get("mode") or "").strip().lower() == "paper":
        return None
    raw_legs = result.get("legs")
    legs = raw_legs if isinstance(raw_legs, list) else []
    leg_tickets = [leg.get("ticket") for leg in legs if isinstance(leg, Mapping)]
    tickets = _tickets(result.get("ticket"), result.get("tp2PositionTicket"), *leg_tickets)
    symbols = _symbols(
        result.get("symbol"), signal.get("symbol"), signal.get("pair"), signal.get("display")
    )
    if not tickets and not symbols:
        return None
    return ExecutionRecord(
        engine=engine,
        venue=_venue(venue or result.get("venue") or signal.get("venue")),
        tickets=tickets,
        symbols=symbols,
        direction=_direction(result.get("direction") or signal.get("direction")),
        entry=_float(result.get("entryPrice")),
        ts=ts,
    )


def _load_coordinator_store(db_path: Path, table: str, engine: str) -> list:
    """GROK and SOL share one execution-store shape."""
    if not db_path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT venue, requested_at, completed_at, request_json, result_json "
                f"FROM {table} WHERE status = 'SUCCESS' "
                "ORDER BY requested_at DESC LIMIT 400"
            ).fetchall()
    except sqlite3.Error as exc:
        log.debug("[ENGINE-ATTR] %s store unavailable: %s", engine, exc)
        return []

    out = []
    for row in rows:
        result = _json_obj(row["result_json"])
        signal = _json_obj(_json_obj(row["request_json"]).get("signal"))
        ts = _epoch(row["completed_at"]) or _epoch(row["requested_at"])
        record = _record_from_broker_result(engine, row["venue"], result, signal, ts)
        if record is not None:
            out.append(record)
    return out


def _load_opus(db_path: Path) -> list:
    """OPUS records ``broker_ref`` as the venue's order id.

    A filled MT5 pending order keeps its order ticket as the position ticket, so
    ticket matching usually holds; the symbol/entry fallback covers the rest.
    """
    if not db_path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT broker, mode, symbol, direction, entry, status, broker_ref, submitted_ts "
                "FROM orders WHERE mode != 'paper' "
                "ORDER BY submitted_ts DESC LIMIT 400"
            ).fetchall()
    except sqlite3.Error as exc:
        log.debug("[ENGINE-ATTR] opus store unavailable: %s", exc)
        return []

    out = []
    for row in rows:
        if str(row["status"] or "").strip().lower() in ("rejected", "cancelled", "canceled"):
            continue
        tickets = _tickets(row["broker_ref"])
        symbols = _symbols(row["symbol"])
        if not tickets and not symbols:
            continue
        out.append(
            ExecutionRecord(
                engine=ENGINE_OPUS,
                venue=_venue(row["broker"]),
                tickets=tickets,
                symbols=symbols,
                direction=_direction(row["direction"]),
                entry=_float(row["entry"]),
                ts=_float(row["submitted_ts"]),
            )
        )
    return out


def _load_jsonl(path: Path, parse) -> list:
    if not path.is_file():
        return []
    out = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                record = parse(row)
                if record is not None:
                    out.append(record)
    except OSError as exc:
        log.debug("[ENGINE-ATTR] %s unavailable: %s", path.name, exc)
    return out


def _parse_ox_alpha_row(row: Mapping[str, Any]) -> ExecutionRecord | None:
    if str(row.get("kind") or "") != "open_outcome":
        return None
    if str(row.get("status") or "").strip().lower() != "filled":
        return None
    return _record_from_broker_result(
        ENGINE_OX_ALPHA,
        row.get("venue"),
        _json_obj(row.get("result")),
        {},
        _epoch(row.get("ts")),
    )


def _parse_recorded_row(row: Mapping[str, Any]) -> ExecutionRecord | None:
    engine = str(row.get("engine") or "").strip().lower()
    if not engine:
        return None
    tickets = _tickets(*(row.get("tickets") or []))
    symbols = _symbols(*(row.get("symbols") or []))
    if not tickets and not symbols:
        return None
    return ExecutionRecord(
        engine=engine,
        venue=_venue(row.get("venue")),
        tickets=tickets,
        symbols=symbols,
        direction=_direction(row.get("direction")),
        entry=_float(row.get("entry")),
        ts=_epoch(row.get("ts")),
    )


def load_records(*, root: Path | None = None, now: float | None = None) -> list:
    """Every engine-owned execution record, newest first. Never raises."""
    base = root or _ROOT
    records: list = []
    records += _load_coordinator_store(base / "grok_engine.db", "grok_executions", ENGINE_GROK)
    records += _load_coordinator_store(base / "sol_engine.db", "sol_executions", ENGINE_SOL)
    records += _load_coordinator_store(base / "fable_engine.db", "fable_executions", ENGINE_FABLE)
    records += _load_opus(base / "opus_store.sqlite3")
    records += _load_jsonl(
        base / "ox_alpha" / "_journal" / "ox_alpha_journal.jsonl", _parse_ox_alpha_row
    )
    records += _load_jsonl(base / "state" / "engine_attribution.jsonl", _parse_recorded_row)

    cutoff = (now if now is not None else time.time()) - _MAX_RECORD_AGE_SEC
    records = [r for r in records if r.ts >= cutoff]
    records.sort(key=lambda r: r.ts, reverse=True)
    return records


# -- cache ------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict = {"records": None, "ts": 0.0}


def _cached_records() -> list:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache["records"]
        if cached is not None and (now - _cache["ts"]) < _CACHE_TTL_SEC:
            return cached
    records = load_records()
    with _cache_lock:
        _cache["records"] = records
        _cache["ts"] = time.monotonic()
    return records


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["records"] = None
        _cache["ts"] = 0.0


# -- matching ---------------------------------------------------------------


def _symbols_overlap(record: ExecutionRecord, position: Mapping[str, Any]) -> bool:
    pos_symbols = _symbols(position.get("symbol"), position.get("pair"))
    return any(symbols_match(a, b) for a in record.symbols for b in pos_symbols)


def _matches(record: ExecutionRecord, position: Mapping[str, Any]) -> bool:
    venue = _venue(position.get("venue") or position.get("exchange"))
    if venue and record.venue and venue != record.venue:
        return False

    ticket = _ticket_key(position.get("ticket"))
    if ticket and record.tickets:
        # A real ticket that belongs to a different fill is a definite miss;
        # falling through to price matching here would let one MT5 leg claim
        # another engine's position on the same symbol.
        return ticket in record.tickets

    if not _symbols_overlap(record, position):
        return False

    direction = _direction(position.get("direction") or position.get("side"))
    if direction and record.direction and direction != record.direction:
        return False

    pos_entry = _float(position.get("entry") or position.get("entryPrice"))
    if pos_entry <= 0 or record.entry <= 0:
        return False
    if abs(record.entry - pos_entry) / abs(pos_entry) > _MAX_ENTRY_DRIFT:
        return False

    open_ts = _float(position.get("open_ts"))
    if open_ts <= 0 or record.ts <= 0:
        return False
    return abs(record.ts - open_ts) <= _OPEN_TIME_WINDOW_SEC


def attribute_positions(
    positions: Sequence[Mapping[str, Any]],
    *,
    records: Iterable | None = None,
) -> dict:
    """Map position index -> engine key for the positions that can be attributed.

    Each position needs ``ticket``, ``pair``/``symbol``, ``direction``, ``entry``,
    ``venue`` (or ``exchange``) and ``open_ts``. Positions with no match, or with
    matches from more than one engine, are simply absent from the result.
    """
    if not positions:
        return {}
    candidates = list(records) if records is not None else _cached_records()
    if not candidates:
        return {}

    resolved: dict = {}
    for index, position in enumerate(positions):
        engines = {record.engine for record in candidates if _matches(record, position)}
        if len(engines) == 1:
            resolved[index] = engines.pop()
        elif len(engines) > 1:
            log.debug(
                "[ENGINE-ATTR] ambiguous attribution for ticket=%s pair=%s: %s",
                position.get("ticket"),
                position.get("pair"),
                sorted(engines),
            )
    return resolved


# -- record sink (engines with no durable execution store) ------------------


def record_execution(
    *,
    engine: str,
    venue: str,
    result: Mapping[str, Any],
    pair: str = "",
    symbol: str = "",
    direction: str = "",
    path: Path | None = None,
) -> None:
    """Persist one broker fill so the dashboard can attribute it later.

    Best-effort by contract: attribution is display metadata, so a failure here
    must never surface on an execution path.
    """
    try:
        raw_legs = result.get("legs")
        legs = raw_legs if isinstance(raw_legs, list) else []
        leg_tickets = [leg.get("ticket") for leg in legs if isinstance(leg, Mapping)]
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "engine": str(engine or "").strip().lower(),
            "venue": _venue(venue),
            "tickets": sorted(
                _tickets(result.get("ticket"), result.get("tp2PositionTicket"), *leg_tickets)
            ),
            "symbols": list(_symbols(result.get("symbol"), symbol, pair)),
            "direction": _direction(direction or result.get("direction")),
            "entry": _float(result.get("entryPrice")),
        }
        if not row["engine"] or (not row["tickets"] and not row["symbols"]):
            return
        target = path or RECORD_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        invalidate_cache()
    except Exception as exc:  # noqa: BLE001 - attribution must never break trading
        log.debug("[ENGINE-ATTR] record failed for %s: %s", engine, exc)


__all__ = [
    "ENGINE_GROK",
    "ENGINE_KIMI",
    "ENGINE_OPUS",
    "ENGINE_OX_ALPHA",
    "ENGINE_SOL",
    "ExecutionRecord",
    "attribute_positions",
    "invalidate_cache",
    "load_records",
    "record_execution",
]
