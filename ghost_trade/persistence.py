"""Dedicated transactional SQLite persistence for Ghost Trade."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .models import (
    AssetGroup,
    Direction,
    GhostInstrument,
    GhostPosition,
    GhostSignal,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)


class SignalConflictError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def signal_id_for(
    *,
    engine_version: str,
    venue: Venue,
    broker_symbol: str,
    style: Style,
    decision_time: datetime,
    confirmed_times: Mapping[str, str],
) -> str:
    identity = {
        "engineVersion": engine_version,
        "venue": venue.value,
        "brokerSymbol": broker_symbol,
        "style": style.value,
        "decisionTime": decision_time.isoformat(),
        "confirmedTimes": dict(sorted(confirmed_times.items())),
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS ghost_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ghost_scan_runs (
    scan_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    discovered_count INTEGER NOT NULL DEFAULT 0,
    scored_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    duration_ms REAL
);
CREATE TABLE IF NOT EXISTS ghost_scan_lease (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    owner TEXT NOT NULL,
    acquired_epoch REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ghost_instruments (
    venue TEXT NOT NULL,
    broker_symbol TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    asset_group TEXT NOT NULL,
    asset_subgroup TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    trade_enabled INTEGER NOT NULL,
    skip_reasons_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (venue, broker_symbol)
);
CREATE TABLE IF NOT EXISTS ghost_signals (
    signal_id TEXT PRIMARY KEY,
    signal_version TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    broker_symbol TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    asset_group TEXT NOT NULL,
    asset_subgroup TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    instrument_metadata_json TEXT NOT NULL,
    instrument_trade_enabled INTEGER NOT NULL,
    instrument_skip_reasons_json TEXT NOT NULL,
    style TEXT NOT NULL,
    direction TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    confirmed_score REAL NOT NULL,
    live_adjustment REAL NOT NULL,
    display_score REAL NOT NULL,
    direction_confidence REAL NOT NULL,
    entry_quality REAL NOT NULL,
    entry REAL,
    stop REAL,
    target REAL,
    raw_rr REAL,
    volatility_regime TEXT NOT NULL,
    can_execute INTEGER NOT NULL,
    status TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    components_json TEXT NOT NULL,
    confirmed_times_json TEXT NOT NULL,
    data_freshness TEXT NOT NULL,
    spread REAL,
    group_rank INTEGER,
    group_count INTEGER,
    global_rank INTEGER,
    global_count INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ghost_signal_components (
    signal_id TEXT NOT NULL,
    component_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (signal_id, component_name)
);
CREATE TABLE IF NOT EXISTS ghost_execution_attempts (
    execution_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    request_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS ghost_positions (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    asset_group TEXT NOT NULL,
    broker_position_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    initial_risk REAL NOT NULL,
    quantity REAL,
    opened_at TEXT,
    closed_at TEXT,
    volatility_regime TEXT NOT NULL,
    entry_quality REAL NOT NULL,
    signal_score REAL NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ghost_position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ghost_closed_trades (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    exit_price REAL NOT NULL,
    gross_r REAL NOT NULL,
    net_r REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ghost_runtime_settings (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    operator_context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ghost_signals_group_score ON ghost_signals(asset_group, confirmed_score DESC);
CREATE INDEX IF NOT EXISTS idx_ghost_signals_symbol ON ghost_signals(canonical_symbol, decision_time DESC);
CREATE INDEX IF NOT EXISTS idx_ghost_signals_status ON ghost_signals(status, can_execute);
CREATE INDEX IF NOT EXISTS idx_ghost_positions_status ON ghost_positions(status, venue);
CREATE INDEX IF NOT EXISTS idx_ghost_scans_started ON ghost_scan_runs(started_at DESC);
"""


class GhostRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(_MIGRATION_V1)
            connection.execute(
                "INSERT OR IGNORE INTO ghost_schema_version(version, applied_at) VALUES(1, ?)",
                (datetime.utcnow().isoformat() + "Z",),
            )

    def upsert_signal(self, signal: GhostSignal) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        instrument = signal.instrument
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ghost_instruments(
                    venue,broker_symbol,canonical_symbol,asset_group,asset_subgroup,
                    base_asset,quote_asset,metadata_json,trade_enabled,skip_reasons_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(venue,broker_symbol) DO UPDATE SET
                    canonical_symbol=excluded.canonical_symbol,
                    asset_group=excluded.asset_group,
                    asset_subgroup=excluded.asset_subgroup,
                    base_asset=excluded.base_asset,
                    quote_asset=excluded.quote_asset,
                    metadata_json=excluded.metadata_json,
                    trade_enabled=excluded.trade_enabled,
                    skip_reasons_json=excluded.skip_reasons_json,
                    updated_at=excluded.updated_at""",
                (
                    instrument.venue.value, instrument.broker_symbol,
                    instrument.canonical_symbol, instrument.asset_group.value,
                    instrument.asset_subgroup, instrument.base_asset,
                    instrument.quote_asset, _canonical_json(dict(instrument.metadata)),
                    int(instrument.trade_enabled), _canonical_json(list(instrument.skip_reasons)), now,
                ),
            )
            values = (
                signal.signal_id, signal.signal_version, signal.scan_id,
                instrument.venue.value, instrument.broker_symbol,
                instrument.canonical_symbol, instrument.asset_group.value,
                instrument.asset_subgroup, instrument.base_asset, instrument.quote_asset,
                _canonical_json(dict(instrument.metadata)), int(instrument.trade_enabled),
                _canonical_json(list(instrument.skip_reasons)), signal.style.value,
                signal.direction.value, signal.decision_time.isoformat(),
                signal.confirmed_score, signal.live_adjustment, signal.display_score,
                signal.direction_confidence, signal.entry_quality, signal.entry,
                signal.stop, signal.target, signal.raw_rr,
                signal.volatility_regime.value, int(signal.can_execute), signal.status.value,
                _canonical_json(list(signal.reasons)), _canonical_json(dict(signal.components)),
                _canonical_json(dict(signal.confirmed_times)), signal.data_freshness,
                signal.spread, signal.group_rank, signal.group_count,
                signal.global_rank, signal.global_count, now,
            )
            columns = (
                "signal_id", "signal_version", "scan_id", "venue", "broker_symbol",
                "canonical_symbol", "asset_group", "asset_subgroup", "base_asset",
                "quote_asset", "instrument_metadata_json", "instrument_trade_enabled",
                "instrument_skip_reasons_json", "style", "direction", "decision_time",
                "confirmed_score", "live_adjustment", "display_score",
                "direction_confidence", "entry_quality", "entry", "stop", "target",
                "raw_rr", "volatility_regime", "can_execute", "status", "reasons_json",
                "components_json", "confirmed_times_json", "data_freshness", "spread",
                "group_rank", "group_count", "global_rank", "global_count", "updated_at",
            )
            placeholders = ",".join("?" for _ in columns)
            connection.execute(
                f"""INSERT INTO ghost_signals({','.join(columns)}) VALUES({placeholders})
                ON CONFLICT(signal_id) DO UPDATE SET
                    signal_version=excluded.signal_version,
                    scan_id=excluded.scan_id,
                    live_adjustment=excluded.live_adjustment,
                    display_score=excluded.display_score,
                    can_execute=excluded.can_execute,
                    status=excluded.status,
                    reasons_json=excluded.reasons_json,
                    components_json=excluded.components_json,
                    data_freshness=excluded.data_freshness,
                    spread=excluded.spread,
                    group_rank=excluded.group_rank,
                    group_count=excluded.group_count,
                    global_rank=excluded.global_rank,
                    global_count=excluded.global_count,
                    updated_at=excluded.updated_at""",
                values,
            )
            connection.execute(
                "DELETE FROM ghost_signal_components WHERE signal_id=?",
                (signal.signal_id,),
            )
            connection.executemany(
                "INSERT INTO ghost_signal_components(signal_id,component_name,payload_json) VALUES(?,?,?)",
                [
                    (signal.signal_id, name, _canonical_json(payload))
                    for name, payload in signal.components.items()
                ],
            )

    def upsert_instrument(self, instrument: GhostInstrument) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ghost_instruments(
                    venue,broker_symbol,canonical_symbol,asset_group,asset_subgroup,
                    base_asset,quote_asset,metadata_json,trade_enabled,skip_reasons_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(venue,broker_symbol) DO UPDATE SET
                    canonical_symbol=excluded.canonical_symbol,
                    asset_group=excluded.asset_group,
                    asset_subgroup=excluded.asset_subgroup,
                    base_asset=excluded.base_asset,
                    quote_asset=excluded.quote_asset,
                    metadata_json=excluded.metadata_json,
                    trade_enabled=excluded.trade_enabled,
                    skip_reasons_json=excluded.skip_reasons_json,
                    updated_at=excluded.updated_at""",
                (
                    instrument.venue.value, instrument.broker_symbol,
                    instrument.canonical_symbol, instrument.asset_group.value,
                    instrument.asset_subgroup, instrument.base_asset,
                    instrument.quote_asset, _canonical_json(dict(instrument.metadata)),
                    int(instrument.trade_enabled), _canonical_json(list(instrument.skip_reasons)), now,
                ),
            )

    def list_instruments(self) -> list[GhostInstrument]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ghost_instruments ORDER BY canonical_symbol,broker_symbol"
            ).fetchall()
        return [
            GhostInstrument(
                venue=Venue(row["venue"]), broker_symbol=row["broker_symbol"],
                canonical_symbol=row["canonical_symbol"],
                asset_group=AssetGroup(row["asset_group"]),
                asset_subgroup=row["asset_subgroup"], base_asset=row["base_asset"],
                quote_asset=row["quote_asset"], metadata=json.loads(row["metadata_json"]),
                trade_enabled=bool(row["trade_enabled"]),
                skip_reasons=tuple(json.loads(row["skip_reasons_json"])),
            )
            for row in rows
        ]

    def create_scan(self, scan_id: str, started_at: datetime, sources: list[str]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ghost_scan_runs(
                    scan_id,started_at,status,sources_json
                ) VALUES(?,?,?,?)""",
                (scan_id, started_at.isoformat(), "RUNNING", _canonical_json(sources)),
            )

    def acquire_scan_lease(self, owner: str, *, stale_after_seconds: float = 1800.0) -> bool:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner,acquired_epoch FROM ghost_scan_lease WHERE singleton=1"
            ).fetchone()
            if row is not None and now - float(row["acquired_epoch"]) < stale_after_seconds:
                return False
            connection.execute(
                "INSERT OR REPLACE INTO ghost_scan_lease(singleton,owner,acquired_epoch) VALUES(1,?,?)",
                (owner, now),
            )
            return True

    def release_scan_lease(self, owner: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM ghost_scan_lease WHERE singleton=1 AND owner=?", (owner,)
            )

    def complete_scan(
        self,
        scan_id: str,
        *,
        completed_at: datetime,
        status: str,
        discovered_count: int,
        scored_count: int,
        skipped_count: int,
        errors: list[str],
        duration_ms: float,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE ghost_scan_runs SET
                    completed_at=?,status=?,discovered_count=?,scored_count=?,
                    skipped_count=?,errors_json=?,duration_ms=? WHERE scan_id=?""",
                (
                    completed_at.isoformat(), status, discovered_count, scored_count,
                    skipped_count, _canonical_json(errors), duration_ms, scan_id,
                ),
            )

    def latest_scan(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ghost_scan_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def _row_to_signal(self, row: sqlite3.Row) -> GhostSignal:
        instrument = GhostInstrument(
            venue=Venue(row["venue"]),
            broker_symbol=row["broker_symbol"],
            canonical_symbol=row["canonical_symbol"],
            asset_group=AssetGroup(row["asset_group"]),
            asset_subgroup=row["asset_subgroup"],
            base_asset=row["base_asset"],
            quote_asset=row["quote_asset"],
            metadata=json.loads(row["instrument_metadata_json"]),
            trade_enabled=bool(row["instrument_trade_enabled"]),
            skip_reasons=tuple(json.loads(row["instrument_skip_reasons_json"])),
        )
        return GhostSignal(
            signal_id=row["signal_id"], signal_version=row["signal_version"],
            scan_id=row["scan_id"], instrument=instrument, style=Style(row["style"]),
            direction=Direction(row["direction"]),
            decision_time=datetime.fromisoformat(row["decision_time"]),
            confirmed_score=row["confirmed_score"], live_adjustment=row["live_adjustment"],
            display_score=row["display_score"], direction_confidence=row["direction_confidence"],
            entry_quality=row["entry_quality"], entry=row["entry"], stop=row["stop"],
            target=row["target"], raw_rr=row["raw_rr"],
            volatility_regime=VolatilityRegime(row["volatility_regime"]),
            can_execute=bool(row["can_execute"]), status=SignalStatus(row["status"]),
            reasons=tuple(json.loads(row["reasons_json"])),
            components=json.loads(row["components_json"]),
            confirmed_times=json.loads(row["confirmed_times_json"]),
            data_freshness=row["data_freshness"], spread=row["spread"],
            group_rank=row["group_rank"], group_count=row["group_count"],
            global_rank=row["global_rank"], global_count=row["global_count"],
        )

    def get_signal(self, signal_id: str) -> GhostSignal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ghost_signals WHERE signal_id=?", (signal_id,)
            ).fetchone()
        return self._row_to_signal(row) if row is not None else None

    def count_signals(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM ghost_signals").fetchone()
        return int(row[0])

    def list_signals(
        self,
        *,
        asset_group: AssetGroup | None = None,
        direction: Direction | None = None,
        minimum_score: float | None = None,
        status: SignalStatus | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[GhostSignal]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("asset_group", asset_group.value if asset_group else None),
            ("direction", direction.value if direction else None),
            ("status", status.value if status else None),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        if minimum_score is not None:
            clauses.append("confirmed_score>=?")
            params.append(float(minimum_score))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend((max(1, min(int(limit), 1000)), max(0, int(offset))))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ghost_signals{where} ORDER BY confirmed_score DESC, canonical_symbol ASC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def dismiss_signal(self, signal_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE ghost_signals SET status=?, can_execute=0 WHERE signal_id=?",
                (SignalStatus.DISMISSED.value, signal_id),
            )
        return cursor.rowcount == 1

    def reserve_execution(
        self,
        signal_id: str,
        *,
        expected_version: str,
        execution_id: str,
        idempotency_key: str,
    ) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            signal = connection.execute(
                "SELECT signal_version FROM ghost_signals WHERE signal_id=?", (signal_id,)
            ).fetchone()
            if signal is None:
                raise SignalConflictError("signal_not_found")
            if signal["signal_version"] != expected_version:
                raise SignalConflictError("signal_version_mismatch")
            existing = connection.execute(
                "SELECT execution_id FROM ghost_execution_attempts WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return str(existing["execution_id"])
            connection.execute(
                """INSERT INTO ghost_execution_attempts(
                    execution_id,signal_id,idempotency_key,status,requested_at
                ) VALUES(?,?,?,?,?)""",
                (
                    execution_id, signal_id, idempotency_key, "RESERVED",
                    datetime.utcnow().isoformat() + "Z",
                ),
            )
        return execution_id

    def get_execution_attempt(self, execution_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ghost_execution_attempts WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["response"] = json.loads(result.pop("response_json"))
        return result

    def complete_execution(
        self,
        execution_id: str,
        *,
        status: str,
        response: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE ghost_execution_attempts SET
                    status=?,completed_at=?,response_json=?,error_code=?
                   WHERE execution_id=?""",
                (
                    status, datetime.utcnow().isoformat() + "Z",
                    _canonical_json(dict(response or {})), error_code, execution_id,
                ),
            )

    def open_shadow_position(self, signal: GhostSignal) -> GhostPosition | None:
        if signal.entry is None or signal.stop is None or signal.target is None:
            return None
        position_id = hashlib.sha256(f"SHADOW|{signal.signal_id}".encode("utf-8")).hexdigest()
        risk = abs(signal.entry - signal.stop)
        if risk <= 0:
            return None
        with self._connect() as connection:
            duplicate = connection.execute(
                """SELECT position_id FROM ghost_positions
                   WHERE canonical_symbol=? AND direction=? AND status='OPEN'""",
                (signal.instrument.canonical_symbol, signal.direction.value),
            ).fetchone()
            if duplicate is not None:
                return None
            connection.execute(
                """INSERT OR IGNORE INTO ghost_positions(
                    position_id,signal_id,venue,canonical_symbol,asset_group,
                    broker_position_id,mode,status,direction,entry,stop,target,
                    initial_risk,quantity,opened_at,closed_at,volatility_regime,
                    entry_quality,signal_score,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    position_id, signal.signal_id, signal.instrument.venue.value,
                    signal.instrument.canonical_symbol, signal.instrument.asset_group.value,
                    None, "SHADOW", "OPEN", signal.direction.value, signal.entry,
                    signal.stop, signal.target, risk, 1.0,
                    signal.decision_time.isoformat(), None,
                    signal.volatility_regime.value, signal.entry_quality,
                    signal.confirmed_score, "{}",
                ),
            )
        return self.get_position(position_id)

    def open_demo_position(
        self,
        signal: GhostSignal,
        *,
        execution_id: str,
        broker_order_id: str | None,
        broker_position_id: str,
        entry_price: float,
        quantity: float,
        payload: Mapping[str, Any] | None = None,
    ) -> GhostPosition:
        if signal.stop is None or signal.target is None:
            raise SignalConflictError("invalid_structural_geometry")
        risk = abs(entry_price - signal.stop)
        if risk <= 0:
            raise SignalConflictError("invalid_demo_risk")
        position_id = hashlib.sha256(
            f"DEMO|{execution_id}|{broker_position_id}".encode("utf-8")
        ).hexdigest()
        stored_payload = {
            **dict(payload or {}),
            "execution_id": execution_id,
            "broker_order_id": broker_order_id,
            "risk_fraction": float(
                (payload or {}).get("risk_fraction", 0.0) if payload else 0.0
            ),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ghost_positions(
                    position_id,signal_id,venue,canonical_symbol,asset_group,
                    broker_position_id,mode,status,direction,entry,stop,target,
                    initial_risk,quantity,opened_at,closed_at,volatility_regime,
                    entry_quality,signal_score,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    position_id, signal.signal_id, signal.instrument.venue.value,
                    signal.instrument.canonical_symbol, signal.instrument.asset_group.value,
                    broker_position_id, "DEMO", "OPEN", signal.direction.value,
                    entry_price, signal.stop, signal.target, risk, quantity,
                    datetime.utcnow().isoformat() + "Z", None,
                    signal.volatility_regime.value, signal.entry_quality,
                    signal.confirmed_score, _canonical_json(stored_payload),
                ),
            )
        position = self.get_position(position_id)
        if position is None:
            raise SignalConflictError("demo_position_persistence_failed")
        return position

    def _row_to_position(self, row: sqlite3.Row) -> GhostPosition:
        return GhostPosition(
            position_id=row["position_id"], signal_id=row["signal_id"],
            venue=Venue(row["venue"]), canonical_symbol=row["canonical_symbol"],
            asset_group=AssetGroup(row["asset_group"]), mode=row["mode"],
            status=row["status"], direction=Direction(row["direction"]),
            entry=row["entry"], stop=row["stop"], target=row["target"],
            initial_risk=row["initial_risk"], quantity=row["quantity"],
            opened_at=datetime.fromisoformat(row["opened_at"]) if row["opened_at"] else None,
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            volatility_regime=VolatilityRegime(row["volatility_regime"]),
            entry_quality=row["entry_quality"], signal_score=row["signal_score"],
            broker_position_id=row["broker_position_id"],
            payload=json.loads(row["payload_json"]),
        )

    def get_position(self, position_id: str) -> GhostPosition | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ghost_positions WHERE position_id=?", (position_id,)
            ).fetchone()
        return self._row_to_position(row) if row is not None else None

    def list_positions(self, *, status: str | None = None) -> list[GhostPosition]:
        sql = "SELECT * FROM ghost_positions"
        params: tuple[Any, ...] = ()
        if status is not None:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY opened_at DESC, position_id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_position(row) for row in rows]

    def close_position(
        self,
        position: GhostPosition,
        *,
        closed_at: datetime,
        exit_price: float,
        gross_r: float,
        exit_reason: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE ghost_positions SET status='CLOSED',closed_at=? WHERE position_id=? AND status='OPEN'",
                (closed_at.isoformat(), position.position_id),
            )
            connection.execute(
                """INSERT OR REPLACE INTO ghost_closed_trades(
                    position_id,signal_id,closed_at,exit_price,gross_r,net_r,exit_reason,payload_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    position.position_id, position.signal_id, closed_at.isoformat(),
                    exit_price, gross_r, gross_r, exit_reason,
                    _canonical_json(dict(payload or {})),
                ),
            )

    def set_position_status(self, position_id: str, status: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE ghost_positions SET status=? WHERE position_id=?",
                (status, position_id),
            )
        return cursor.rowcount == 1

    def list_closed_trades(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.*,p.direction,p.canonical_symbol,p.asset_group,p.mode,p.venue,
                          p.volatility_regime,p.entry_quality,p.signal_score,s.raw_rr
                   FROM ghost_closed_trades c
                   JOIN ghost_positions p USING(position_id)
                   JOIN ghost_signals s ON s.signal_id=p.signal_id
                   ORDER BY c.closed_at,c.position_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def set_runtime_setting(
        self,
        key: str,
        value: Any,
        *,
        operator_context: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ghost_runtime_settings(
                    setting_key,value_json,updated_at,operator_context_json
                ) VALUES(?,?,?,?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at,
                    operator_context_json=excluded.operator_context_json""",
                (
                    key, _canonical_json(value), datetime.utcnow().isoformat() + "Z",
                    _canonical_json(dict(operator_context or {})),
                ),
            )

    def get_runtime_setting(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ghost_runtime_settings WHERE setting_key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        return {
            "key": row["setting_key"],
            "value": json.loads(row["value_json"]),
            "updated_at": row["updated_at"],
            "operator_context": json.loads(row["operator_context_json"]),
        }
