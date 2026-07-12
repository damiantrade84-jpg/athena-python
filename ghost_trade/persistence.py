"""Dedicated transactional SQLite persistence for Ghost Trade."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .models import (
    AssetGroup,
    Direction,
    GhostInstrument,
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
