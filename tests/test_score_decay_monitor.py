from score_decay_monitor import select_decay_audit_rows


def _row(**overrides):
    row = {
        "id": 1,
        "ts": "2026-07-22T10:00:00+00:00",
        "pair": "EUR/USD",
        "direction": "LONG",
        "ticket": "123",
        "entry_price": 1.17,
        "engine": "engine_a",
        "style": "intraday",
        "score": 2.1,
        "max_score": 3.0,
        "score_pct": 70.0,
    }
    row.update(overrides)
    return row


def test_mt5_requires_exact_ticket():
    position = {
        "venue": "mt5",
        "pair": "EURUSD",
        "direction": "LONG",
        "ticket": "999",
    }

    selected = select_decay_audit_rows([_row()], [position])

    assert selected["EURUSD"]["status"] == "UNAVAILABLE"
    assert selected["EURUSD"]["reason"] == "broker_position_has_no_matching_audit_baseline"


def test_mt5_selects_matching_ticket_not_orphan_pair_row():
    position = {
        "venue": "mt5",
        "pair": "EURUSD",
        "direction": "LONG",
        "ticket": "123",
    }

    selected = select_decay_audit_rows(
        [_row(id=2, ticket="orphan", score=2.8), _row(id=3)],
        [position],
    )

    assert selected["EURUSD"]["status"] == "MATCHED"
    assert selected["EURUSD"]["row"]["id"] == 3


def test_bybit_rejects_stale_entry_baseline():
    position = {
        "venue": "bybit",
        "pair": "APT/USDT",
        "direction": "LONG",
        "entryPrice": 5.0,
    }

    selected = select_decay_audit_rows(
        [_row(pair="APT/USDT", entry_price=4.5, engine="engine_b")],
        [position],
    )

    assert selected["APT/USDT"]["status"] == "UNAVAILABLE"
    assert selected["APT/USDT"]["reason"] == "broker_position_has_no_matching_audit_baseline"


def test_ambiguous_baselines_fail_closed():
    position = {
        "venue": "bybit",
        "pair": "APT/USDT",
        "direction": "LONG",
        "entryPrice": 5.0,
    }
    rows = [
        _row(pair="APT/USDT", entry_price=5.0, engine="engine_b", score=7.0),
        _row(pair="APT/USDT", entry_price=5.01, engine="engine_b", score=8.0),
    ]

    selected = select_decay_audit_rows(rows, [position])

    assert selected["APT/USDT"]["status"] == "UNAVAILABLE"
    assert selected["APT/USDT"]["reason"] == "ambiguous_audit_baselines"


def test_unsupported_engine_fails_closed():
    position = {
        "venue": "mt5",
        "pair": "EURUSD",
        "direction": "LONG",
        "ticket": "123",
    }

    selected = select_decay_audit_rows([_row(engine="external")], [position])

    assert selected["EURUSD"]["status"] == "UNAVAILABLE"
    assert selected["EURUSD"]["reason"] == "unsupported_decay_engine"


def test_multiple_broker_positions_for_pair_fail_closed():
    positions = [
        {"venue": "bybit", "pair": "APT/USDT", "direction": "LONG", "entryPrice": 5.0},
        {"venue": "bybit", "pair": "APT/USDT", "direction": "SHORT", "entryPrice": 5.2},
    ]

    selected = select_decay_audit_rows([], positions)

    assert selected["APT/USDT"]["status"] == "UNAVAILABLE"
    assert selected["APT/USDT"]["reason"] == "ambiguous_broker_positions"
