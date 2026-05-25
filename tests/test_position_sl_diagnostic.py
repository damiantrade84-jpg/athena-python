import pytest

from athena_app.services.position_sl_diagnostic import (
    build_position_sl_diagnostic,
    current_r_multiple,
    pairs_match,
    pct_remaining_to_sl,
    primary_exit_while_in_loss,
    sl_alignment,
)


def test_pairs_match_trx_aliases():
    assert pairs_match("TRX/USDT", "TRXUSDT")
    assert pairs_match("TRX/USDT", "TRX/USDT:USDT")


def test_current_r_long_loss():
    # entry 0.36736, sl 0.363558, mark 0.365 -> negative R
    r = current_r_multiple("LONG", 0.36736, 0.363558, 0.365)
    assert r is not None
    assert r < 0


def test_pct_remaining_to_sl_long():
    pct = pct_remaining_to_sl("LONG", mark=0.365, sl=0.363558)
    assert pct is not None
    assert pct > 0
    assert pct < 5


def test_sl_alignment_match():
    align = sl_alignment(0.363558, 0.36356, 0.36736)
    assert align["match"] is True


def test_primary_exit_while_in_loss():
    info = primary_exit_while_in_loss(
        current_r=-0.4,
        activation_r=1.0,
        broker_sl=0.363558,
        in_profit=False,
    )
    assert info["primary"] == "broker_sl"
    assert "stopLoss" in info["summary"]


def test_build_position_sl_diagnostic_trx_shape():
    tcfg = {
        "tp_mode": "trailing_atr",
        "trail_activation_r": {"intraday": 1.0},
    }
    diag = build_position_sl_diagnostic(
        position={
            "pair": "TRX/USDT",
            "direction": "LONG",
            "entry": 0.36736,
            "sl": 0.363558,
            "tp": 0.375143,
            "markPrice": 0.365,
            "profit": -25.6,
            "ticket": "eda9181f",
        },
        audit={
            "id": 2330,
            "pair": "TRX/USDT",
            "direction": "LONG",
            "style": "intraday",
            "engine": "engine_a",
            "entry_price": 0.36736,
            "sl": 0.36355848698040716,
            "tp": 0.3751430260391858,
            "ticket": "eda9181f",
            "ts": "2026-05-25T09:29:19.562818+00:00",
        },
        tcfg=tcfg,
        style="intraday",
        venue="bybit",
        recomputed_levels={
            "sl": 0.36355,
            "atr": 0.0025,
            "sl_mult": 1.5,
            "regime_factor": 1.0,
        },
        max_sl_pct=0.08,
    )
    assert diag["pair"] == "TRX/USDT"
    assert diag["distance"]["closes_at_price"] == pytest.approx(0.363558, rel=1e-4)
    assert diag["distance"]["current_r"] is not None
    assert diag["distance"]["current_r"] < 0
    assert diag["exit_policy"]["primary_exit_in_loss"] == "broker_sl"
    assert diag["recomputed"]["within_cap"] is True
    assert "Closes at SL" in (diag["distance"].get("display_line") or "")
