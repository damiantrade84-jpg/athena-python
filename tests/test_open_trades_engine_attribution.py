"""/api/open-trades-timed names the engine that opened each live position.

Engine A/B/D are read off the matched ``audit_log`` row. GROK, SOL, OPUS, KIMI
and OX Alpha write no audit execution row, so the endpoint attributes them from
their own execution stores instead of shipping an unlabelled row the dashboard
renders as "Unknown".
"""

from __future__ import annotations

import bybit_executor
import engine_attribution
import mt5_executor
import timed_exit_monitor

from tests.test_scalp_execution import _load_athena_module

OPEN_TIME = 1_787_665_719

GROK_POSITION = {
    "ticket": "26778949",
    "pair": "S&P 500",
    "symbol": "SPX500.s",
    "direction": "SHORT",
    "profit": 12.0,
    "entry": 7681.0,
    "sl": 7700.0,
    "tp": 7600.0,
    "volume": 0.01,
    "open_time": OPEN_TIME,
}

GROK_RECORD = engine_attribution.ExecutionRecord(
    engine="grok",
    venue="mt5",
    tickets=frozenset({"26778949"}),
    symbols=("SPX500.s", "S&P 500"),
    direction="SHORT",
    entry=7681.0,
    ts=float(OPEN_TIME),
)


def _setup(monkeypatch, *, positions, audit_row, records, bybit_positions=()):
    athena_module = _load_athena_module()
    for cache in (
        athena_module._ott_audit_cache,
        athena_module._ott_mt5_cache,
        athena_module._ott_bybit_cache,
    ):
        cache.update({k: (None if k == "result" else ([] if k == "rows" else 0.0)) for k in cache})

    monkeypatch.setattr(
        mt5_executor, "mt5_get_positions",
        lambda **_kwargs: {"error": False, "positions": positions},
    )
    monkeypatch.setattr(
        bybit_executor, "bybit_get_positions",
        lambda: {"error": False, "positions": list(bybit_positions)},
    )
    monkeypatch.setattr(
        timed_exit_monitor, "_load_recent_audit_rows",
        lambda _db: [audit_row] if audit_row else [],
    )
    monkeypatch.setattr(
        timed_exit_monitor, "_match_audit_row_for_position",
        lambda _p, _rows: audit_row,
    )
    monkeypatch.setattr(
        athena_module, "_unresolved_audit_rows_for_display",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(engine_attribution, "_cached_records", lambda: list(records))

    resp = athena_module.app.test_client().get("/api/open-trades-timed")
    assert resp.status_code == 200
    return resp.get_json()


def test_grok_position_without_an_audit_row_is_attributed_to_grok(monkeypatch):
    payload = _setup(
        monkeypatch, positions=[GROK_POSITION], audit_row=None, records=[GROK_RECORD]
    )
    row = payload["positions"][0]
    assert row["engine"] == "grok"
    assert row["engine_resolved"] == "grok"
    assert row["engine_source"] == "engine_store"
    # Attribution is display metadata: it must not invent an audit engine, which
    # drives style and the timed-exit windows on this same row.
    assert row["audit_engine"] is None
    assert row["style"] == ""


def test_audit_stamped_engine_is_never_overwritten_by_attribution(monkeypatch):
    """An Engine A row keeps its audited engine even when a store record matches."""
    audit_row = {
        "ticket": "26778949",
        "engine": "engine_a",
        "style": "intraday",
        "ts": "2026-08-25T13:48:38+00:00",
    }
    payload = _setup(
        monkeypatch, positions=[GROK_POSITION], audit_row=audit_row, records=[GROK_RECORD]
    )
    row = payload["positions"][0]
    assert row["engine"] == "engine_a"
    assert row["engine_resolved"] == "engine_a"
    assert "engine_source" not in row


def test_unattributable_position_stays_unlabelled(monkeypatch):
    payload = _setup(monkeypatch, positions=[GROK_POSITION], audit_row=None, records=[])
    row = payload["positions"][0]
    assert row["engine"] is None
    assert row["engine_resolved"] is None


def test_bybit_position_without_open_time_uses_exact_fable_execution_record(monkeypatch):
    position = {
        "ticket": "0",  # Bybit's one-way position index, not the fill id.
        "pair": "AVAX/USDT",
        "symbol": "AVAX/USDT:USDT",
        "direction": "LONG",
        "profit": 0.0,
        "entry": 7.489,
        "sl": 7.459,
        "tp": 7.572,
        "volume": 450.4,
    }
    record = engine_attribution.ExecutionRecord(
        engine="fable",
        venue="bybit",
        tickets=frozenset({"9423C0BE-A9C6-4CE4-B7E0-F911B744CF3C"}),
        symbols=("AVAX/USDT:USDT", "AVAXUSDT", "AVAX/USDT"),
        direction="LONG",
        entry=7.489,
        # Demonstrate that the fallback does not assume the broker poll time
        # is when the order opened.
        ts=float(OPEN_TIME - (6 * 3600)),
    )
    payload = _setup(
        monkeypatch,
        positions=[],
        bybit_positions=[position],
        audit_row=None,
        records=[record],
    )
    row = payload["positions"][0]
    assert row["engine"] == "fable"
    assert row["engine_resolved"] == "fable"
    assert row["engine_source"] == "engine_store"


def test_attribution_reports_its_own_timing(monkeypatch):
    payload = _setup(
        monkeypatch, positions=[GROK_POSITION], audit_row=None, records=[GROK_RECORD]
    )
    assert payload["_server_timing_ms"]["attribution_ms"] >= 0
