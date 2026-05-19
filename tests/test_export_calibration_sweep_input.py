import sqlite3


def test_export_sweep_input_includes_score_fields(tmp_path):
    from tools.export_calibration_sweep_input import export_sweep_input

    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE audit_log (
            pair TEXT, asset_class TEXT, style TEXT, regime TEXT,
            score REAL, score_pct REAL, max_score REAL, pnl REAL,
            r_multiple REAL, engine TEXT, exit_time TEXT, ts TEXT,
            exit_price REAL, error_tag TEXT, direction TEXT,
            entry_price REAL, exit_reason TEXT, holding_period_hours REAL,
            sl REAL, tp REAL, tp2 REAL, tp_partial REAL, risk REAL,
            risk_amount REAL, risk_pct REAL, grade TEXT, edge_prob REAL,
            factors_json TEXT, votes_json TEXT, warnings_json TEXT,
            fee_cost REAL, signal_price_ref REAL, slippage_bps REAL,
            trend TEXT, trend_state TEXT, adx_pct REAL, btc_bias TEXT,
            session_name TEXT, ticket TEXT
        )
        """
    )
    con.execute(
        """
        INSERT INTO audit_log VALUES (
            'EUR/USD', 'forex', 'intraday', 'TRENDING',
            1.6, 80.0, 2.0, 12.0, 0.5, 'engine_a',
            '2026-05-01T00:00:00Z', '2026-05-01T00:00:00Z',
            1.2, NULL, 'LONG', 1.1, 'tp', 4.0,
            1.0, 1.2, 1.3, NULL, 0.01,
            10.0, 0.5, 'A', 0.7,
            '{"scores":{"trend":1.0}}', '{"bull":2}', '{"note":"x"}',
            0.1, 1.1, 0.2, 'up', 'TRENDING', 0.8, 'bull',
            'London', '123'
        )
        """
    )
    con.commit()
    con.close()

    out = tmp_path / "sweep.json"
    meta = export_sweep_input(audit_db=db, output=out)

    assert meta["closed_trade_count"] == 1
    text = out.read_text(encoding="utf-8")
    assert '"contains_score_fields": true' in text
    assert '"score": 1.6' in text
    assert '"score_pct": 0.8' in text
    assert '"direction": "LONG"' in text
    assert '"entry_price": 1.1' in text
    assert '"engine_a_factor_data"' in text
    assert '"engine_b_level_mode": null' in text
