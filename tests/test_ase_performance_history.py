from athena_app.services.performance_ase import ase_journal_records_for_performance


def test_ase_filled_journal_rows_map_to_trade_history_rows():
    rows = [
        {
            "instrument": "EURUSD",
            "direction": "LONG",
            "horizon": "intraday",
            "executionStatus": "filled",
            "decisionTimeMs": 1_700_000_000_000,
            "entryReference": 1.1000,
            "sl": 1.0900,
            "tp1": 1.1300,
            "realizedNetR": 2.0,
            "reconciledAt": "2026-07-07T12:00:00+00:00",
            "recordedAt": "2026-07-07T10:00:00+00:00",
            "orderId": "ase-1",
        },
        {
            "instrument": "GBPUSD",
            "direction": "SHORT",
            "horizon": "swing",
            "executionStatus": "rejected",
            "decisionTimeMs": 1_700_000_100_000,
            "entryReference": 1.3000,
            "sl": 1.3100,
            "tp1": 1.2800,
        },
    ]

    mapped = ase_journal_records_for_performance(rows)

    assert len(mapped) == 1
    trade = mapped[0]
    assert trade["pair"] == "EURUSD"
    assert trade["engine"] == "ase"
    assert trade["engine_resolved"] == "ase"
    assert trade["style"] == "intraday"
    assert trade["r_multiple"] == 2.0
    assert trade["pnl"] == 2.0
    assert trade["exit_reason"] == "ASE_RECONCILED"
    assert trade["exit_price"] == 1.1200


def test_ase_unreconciled_fills_can_appear_in_trade_history_without_performance_r():
    rows = [
        {
            "instrument": "EURUSD",
            "direction": "LONG",
            "horizon": "intraday",
            "executionStatus": "filled",
            "decisionTimeMs": 1_700_000_000_000,
            "entryReference": 1.1000,
            "sl": 1.0900,
            "tp1": 1.1300,
            "realizedNetR": None,
            "recordedAt": "2026-07-07T10:00:00+00:00",
            "orderId": "ase-1",
        },
    ]

    assert ase_journal_records_for_performance(rows) == []

    history = ase_journal_records_for_performance(rows, realized_only=False)

    assert len(history) == 1
    assert history[0]["engine"] == "ase"
    assert history[0]["exit_reason"] == "ASE_FILLED_UNRECONCILED"
    assert history[0]["r_multiple"] is None
    assert history[0]["pnl"] is None
