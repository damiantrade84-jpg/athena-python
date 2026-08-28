"""Attribution of live broker positions to GROK / SOL / OPUS / KIMI / OX Alpha.

These engines never write an ``audit_log`` execution row, so ``/api/open-trades-timed``
falls back to their own execution stores. The rules that matter: a real ticket is
authoritative, Bybit positions (which report ``positionIdx``, not the order id) fall
back to symbol/direction/entry inside a time window, paper fills never claim a live
position, and an ambiguous match yields no engine rather than a wrong one.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile

import engine_attribution as ea

NOW = 1_787_000_000.0
ISO_NOW = "2026-08-20T06:00:00+00:00"


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _coordinator_db(path: Path, table: str, rows: list[dict]) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            f"CREATE TABLE {table} (execution_id TEXT, venue TEXT, status TEXT, "
            "requested_at TEXT, completed_at TEXT, request_json TEXT, result_json TEXT)"
        )
        con.executemany(
            f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?)",
            [
                (
                    row.get("execution_id", "x"),
                    row["venue"],
                    row.get("status", "SUCCESS"),
                    row["requested_at"],
                    row.get("completed_at"),
                    json.dumps({"signal": row.get("signal", {})}),
                    json.dumps(row["result"]),
                )
                for row in rows
            ],
        )


def _mt5_record(engine: str, ticket: str, symbol: str, entry: float, ts: float) -> ea.ExecutionRecord:
    return ea.ExecutionRecord(
        engine=engine,
        venue="mt5",
        tickets=frozenset({ticket}),
        symbols=(symbol,),
        direction="LONG",
        entry=entry,
        ts=ts,
    )


# -- ticket matching --------------------------------------------------------


def test_mt5_position_is_attributed_by_ticket():
    records = [_mt5_record("grok", "26778949", "SPX500.s", 7681.0, NOW)]
    positions = [
        {"ticket": "26778949", "pair": "S&P 500", "symbol": "SPX500.s",
         "direction": "SHORT", "entry": 7681.0, "venue": "mt5", "open_ts": NOW}
    ]
    assert ea.attribute_positions(positions, records=records) == {0: "grok"}


def test_wrong_ticket_never_falls_through_to_price_matching():
    """A real ticket that is not in the record is a definite miss.

    Without this, a second MT5 leg opened by another engine at the same price
    would inherit the first engine's label.
    """
    records = [_mt5_record("grok", "26778949", "SPX500.s", 7681.0, NOW)]
    positions = [
        {"ticket": "26999999", "pair": "S&P 500", "symbol": "SPX500.s",
         "direction": "LONG", "entry": 7681.0, "venue": "mt5", "open_ts": NOW}
    ]
    assert ea.attribute_positions(positions, records=records) == {}


def test_venue_mismatch_blocks_a_ticket_collision():
    records = [_mt5_record("grok", "12345", "EURUSD.s", 1.1, NOW)]
    positions = [
        {"ticket": "12345", "pair": "EUR/USD", "symbol": "EURUSD.s",
         "direction": "LONG", "entry": 1.1, "venue": "bybit", "open_ts": NOW}
    ]
    assert ea.attribute_positions(positions, records=records) == {}


# -- Bybit fallback ---------------------------------------------------------


def _bybit_record(engine: str, ts: float, entry: float = 106.26) -> ea.ExecutionRecord:
    return ea.ExecutionRecord(
        engine=engine,
        venue="bybit",
        tickets=frozenset({"75D716CB-F573-4B58-B2BF-E78C810695B0"}),
        symbols=("SOL/USDT:USDT", "SOL/USDT"),
        direction="LONG",
        entry=entry,
        ts=ts,
    )


def _bybit_position(**overrides) -> dict:
    position = {
        "ticket": "0",  # Bybit reports positionIdx here, never the order id
        "pair": "SOL/USDT",
        "symbol": "SOL/USDT:USDT",
        "direction": "LONG",
        "entry": 106.26,
        "venue": "bybit",
        "open_ts": NOW,
    }
    position.update(overrides)
    return position


def test_bybit_position_matches_on_symbol_direction_entry():
    assert ea.attribute_positions(
        [_bybit_position()], records=[_bybit_record("sol", NOW)]
    ) == {0: "sol"}


def test_bybit_opposite_direction_is_not_attributed():
    assert ea.attribute_positions(
        [_bybit_position(direction="SHORT")], records=[_bybit_record("sol", NOW)]
    ) == {}


def test_bybit_entry_drift_beyond_tolerance_is_not_attributed():
    assert ea.attribute_positions(
        [_bybit_position(entry=106.26 * 1.02)], records=[_bybit_record("sol", NOW)]
    ) == {}


def test_bybit_fill_outside_the_open_time_window_is_not_attributed():
    stale = _bybit_record("sol", NOW - (6 * 3600))
    assert ea.attribute_positions([_bybit_position()], records=[stale]) == {}


def test_position_with_no_open_time_is_not_price_matched():
    assert ea.attribute_positions(
        [_bybit_position(open_ts=0)], records=[_bybit_record("sol", NOW)]
    ) == {}


# -- ambiguity --------------------------------------------------------------


def test_two_engines_matching_the_same_position_yields_no_engine():
    records = [_bybit_record("sol", NOW), _bybit_record("grok", NOW)]
    assert ea.attribute_positions([_bybit_position()], records=records) == {}


def test_two_records_from_the_same_engine_still_resolve():
    records = [_bybit_record("sol", NOW), _bybit_record("sol", NOW - 60)]
    assert ea.attribute_positions([_bybit_position()], records=records) == {0: "sol"}


# -- store loading ----------------------------------------------------------


def test_grok_and_sol_stores_load_with_ticket_legs_and_symbols():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_"))
    _coordinator_db(
        root / "grok_engine.db",
        "grok_executions",
        [
            {
                "venue": "mt5",
                "requested_at": ISO_NOW,
                "completed_at": ISO_NOW,
                "signal": {"pair": "S&P 500", "symbol": "^GSPC", "direction": "SHORT"},
                "result": {
                    "success": True, "mode": "demo", "ticket": 26778949,
                    "legs": [{"ticket": 26778949}], "tp2PositionTicket": 26778950,
                    "symbol": "SPX500.s", "direction": "SHORT", "entryPrice": 7681.0,
                },
            }
        ],
    )
    _coordinator_db(
        root / "sol_engine.db",
        "sol_executions",
        [
            {
                "venue": "bybit",
                "requested_at": ISO_NOW,
                "completed_at": ISO_NOW,
                "signal": {"pair": "SOL/USDT", "direction": "LONG"},
                "result": {
                    "success": True, "mode": "demo",
                    "ticket": "75d716cb-f573-4b58-b2bf-e78c810695b0",
                    "symbol": "SOL/USDT:USDT", "direction": "LONG", "entryPrice": 106.26,
                },
            }
        ],
    )

    records = ea.load_records(root=root, now=NOW)
    by_engine = {record.engine: record for record in records}
    assert set(by_engine) == {"grok", "sol"}
    assert by_engine["grok"].tickets == frozenset({"26778949", "26778950"})
    assert "S&P 500" in by_engine["grok"].symbols
    # Bybit order ids are cased inconsistently across the API; keys are upper.
    assert by_engine["sol"].tickets == frozenset({"75D716CB-F573-4B58-B2BF-E78C810695B0"})


def test_paper_fills_are_never_loaded():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_paper_"))
    _coordinator_db(
        root / "grok_engine.db",
        "grok_executions",
        [
            {
                "venue": "mt5",
                "requested_at": ISO_NOW,
                "completed_at": ISO_NOW,
                "result": {
                    "success": True, "mode": "paper", "ticket": "GROK-PAPER-ABC",
                    "symbol": "SPX500.s", "direction": "SHORT", "entryPrice": 7681.0,
                },
            }
        ],
    )
    assert ea.load_records(root=root, now=NOW) == []


def test_records_older_than_the_retention_window_are_dropped():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_old_"))
    ancient = _iso(NOW - (60 * 24 * 3600))
    _coordinator_db(
        root / "grok_engine.db",
        "grok_executions",
        [
            {
                "venue": "mt5",
                "requested_at": ancient,
                "completed_at": ancient,
                "result": {
                    "success": True, "mode": "demo", "ticket": 1,
                    "symbol": "EURUSD.s", "direction": "LONG", "entryPrice": 1.1,
                },
            }
        ],
    )
    assert ea.load_records(root=root, now=NOW) == []


def test_ox_alpha_journal_loads_only_filled_open_outcomes():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_ox_"))
    journal = root / "ox_alpha" / "_journal" / "ox_alpha_journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"ts": ISO_NOW, "kind": "scan", "engine": "OX_ALPHA"},
                {"ts": ISO_NOW, "kind": "open_outcome", "venue": "bybit",
                 "status": "failed", "result": {"success": False}},
                {"ts": ISO_NOW, "kind": "open_outcome", "venue": "bybit",
                 "status": "filled",
                 "result": {"success": True, "ticket": "890c4566", "symbol": "AAVE/USDT:USDT",
                            "direction": "SHORT", "entryPrice": 125.76}},
            ]
        ),
        encoding="utf-8",
    )
    records = ea.load_records(root=root, now=NOW)
    assert [r.engine for r in records] == ["ox_alpha"]
    assert records[0].tickets == frozenset({"890C4566"})


def test_opus_orders_load_and_rejected_orders_are_skipped():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_opus_"))
    with sqlite3.connect(root / "opus_store.sqlite3") as con:
        con.execute(
            "CREATE TABLE orders (order_id TEXT, broker TEXT, mode TEXT, symbol TEXT, "
            "direction TEXT, entry REAL, status TEXT, broker_ref TEXT, submitted_ts REAL)"
        )
        con.executemany(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("o1", "mt5", "live", "XAUUSD", "LONG", 3300.0, "filled", "555", NOW),
                ("o2", "mt5", "live", "XAUUSD", "SHORT", 3310.0, "rejected", "556", NOW),
                ("o3", "mt5", "paper", "XAUUSD", "LONG", 3320.0, "filled", "557", NOW),
            ],
        )
    records = ea.load_records(root=root, now=NOW)
    assert [r.tickets for r in records] == [frozenset({"555"})]


# -- record sink ------------------------------------------------------------


def test_record_execution_round_trips_through_load_records():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_sink_"))
    sink = root / "state" / "engine_attribution.jsonl"
    ea.record_execution(
        engine=ea.ENGINE_KIMI,
        venue="mt5",
        result={"success": True, "ticket": 26800001, "symbol": "EURUSD.s",
                "entryPrice": 1.1659},
        pair="EUR/USD",
        symbol="EURUSD",
        direction="LONG",
        path=sink,
    )
    records = ea.load_records(root=root, now=NOW)
    assert len(records) == 1
    assert records[0].engine == "kimi"
    assert records[0].tickets == frozenset({"26800001"})

    position = {"ticket": "26800001", "pair": "EUR/USD", "symbol": "EURUSD.s",
                "direction": "LONG", "entry": 1.1659, "venue": "mt5", "open_ts": NOW}
    assert ea.attribute_positions([position], records=records) == {0: "kimi"}


def test_record_execution_never_raises_on_a_bad_path():
    ea.record_execution(
        engine=ea.ENGINE_KIMI,
        venue="mt5",
        result={"success": True, "ticket": 1},
        path=Path("\x00invalid") / "sink.jsonl",
    )


def test_missing_stores_yield_no_records_and_no_error():
    root = Path(tempfile.mkdtemp(prefix="engine_attr_empty_"))
    assert ea.load_records(root=root, now=NOW) == []
    assert ea.attribute_positions([], records=[]) == {}
