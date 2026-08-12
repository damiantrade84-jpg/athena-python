"""Engine A score-group / TF-policy / specialist routing table."""
from __future__ import annotations

from engine_a_groups import resolve_score_group_by_type, resolve_timeframe_policy_group
from engine_a_v3.routing import route_specialist


def test_us_index_etfs_share_the_stock_volume_group_and_ladder():
    for display in ("SPY", "QQQ", "DIA", "SOXX"):
        pair = {"display": display, "type": "etf", "symbol": f"{display}.US"}
        assert resolve_score_group_by_type(pair) == "us_stock_single", display
        assert resolve_timeframe_policy_group(pair) == "us_stock_single", display
        route = route_specialist(pair)
        assert route.family == "equity_etf", display
        assert route.subclass == "us_stock_single", display


def test_cash_index_cfds_stay_on_index_scoring():
    pair = {"display": "NASDAQ-100", "type": "index", "symbol": "NAS100"}
    assert resolve_score_group_by_type(pair) == "us_indices_trackers"
    assert resolve_timeframe_policy_group(pair) == "equity_index_fast"
    route = route_specialist(pair)
    assert route.family == "index"


def test_sol_stays_with_alt_majors_not_btc_fast_ladder():
    pair = {"display": "SOL/USDT", "type": "crypto", "symbol": "SOLUSDT"}
    assert resolve_score_group_by_type(pair) == "crypto_alt_majors"
    assert resolve_timeframe_policy_group(pair) == "crypto_alt_majors"
    route = route_specialist(pair)
    assert route.family == "crypto"
    assert route.subclass == "alt_majors"
    assert route.setup_ids("intraday") == (
        "crypto_contraction_breakout_retest",
        "crypto_trend_pullback",
    )


def test_btc_keeps_the_fast_major_policy_group():
    pair = {"display": "BTC/USDT", "type": "crypto", "symbol": "BTCUSDT"}
    assert resolve_score_group_by_type(pair) == "crypto_btc"
    assert resolve_timeframe_policy_group(pair) == "crypto_majors_fast"


def test_energy_and_equity_intraday_also_declare_a_pullback():
    oil = route_specialist({"display": "WTI Oil", "type": "commodity", "symbol": "USOIL"})
    assert oil.setup_ids("intraday") == (
        "energy_oil_breakout_retest",
        "energy_oil_trend_pullback",
    )
    aapl = route_specialist({"display": "AAPL", "type": "stock", "symbol": "AAPL.US"})
    assert aapl.setup_ids("intraday")[0].endswith("opening_range_gap_continuation")
    assert aapl.setup_ids("intraday")[-1] == "us_stock_single_trend_pullback"
    nas = route_specialist({"display": "NASDAQ-100", "type": "index", "symbol": "NAS100"})
    assert nas.setup_ids("intraday")[0].endswith("opening_range_gap_continuation")
    assert nas.setup_ids("intraday")[-1] == "us_indices_trend_pullback"


def test_gdx_does_not_inherit_london_gold_specialist():
    pair = {"display": "GDX", "type": "etf", "symbol": "GDX.US"}
    assert resolve_score_group_by_type(pair) == "precious_trackers"
    route = route_specialist(pair)
    assert route.family == "equity_etf"
    assert route.subclass == "us_stock_single"
    assert "opening_range" in route.setup_ids("intraday")[0]


def test_xau_zar_keeps_thin_commodity_scoring_but_uses_metal_setup():
    pair = {"display": "XAU/ZAR", "type": "commodity", "symbol": "XAUZAR=X"}
    assert resolve_score_group_by_type(pair) == "commodity_other"
    assert resolve_timeframe_policy_group(pair) == "thin_metals_base_softs"
    route = route_specialist(pair)
    assert route.family == "commodity"
    assert route.subclass == "precious"
    assert route.setup_ids("intraday")[0] == "precious_london_ny_breakout_retest"
    assert "precious_trend_pullback" in route.setup_ids("intraday")


def test_xau_usd_keeps_the_xau_specialist():
    pair = {"display": "XAU/USD", "type": "commodity", "symbol": "XAUUSD"}
    assert resolve_score_group_by_type(pair) == "precious_trackers"
    assert resolve_timeframe_policy_group(pair) == "liquid_metals"
    route = route_specialist(pair)
    assert route.family == "commodity"
    assert route.subclass == "xau"
    assert route.setup_ids("intraday")[0] == "precious_london_ny_breakout_retest"
    assert "precious_trend_pullback" in route.setup_ids("intraday")
