import cot_feed


def test_parse_weekly_fin_no_header_matches_nzd_and_zar_contract_names():
    text = "\n".join(
        [
            '"NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE","240101","2026-03-17","","","","","0","0","0","0","0","0","0","150","50"',
            '"S AFRICAN RAND - CHICAGO MERCANTILE EXCHANGE","240101","2026-03-17","","","","","0","0","0","0","0","0","0","80","20"',
        ]
    )

    parsed = cot_feed._parse_weekly_fin_no_header(text)

    assert parsed["NZD"]["2026-03-17"] == 100
    assert parsed["ZAR"]["2026-03-17"] == 60


def test_parse_weekly_fin_matches_new_zealand_dollar_alias():
    text = (
        '"NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE","240101","2026-03-17",'
        '"","","","","0","0","0","0","0","0","0","40","10"'
    )
    parsed = cot_feed._parse_weekly_fin_no_header(text)
    assert parsed["NZD"]["2026-03-17"] == 30


def test_get_cot_net_marks_no_coverage_when_history_too_thin(monkeypatch):
    monkeypatch.setattr(cot_feed, "_row_count", lambda asset: 2)
    monkeypatch.setattr(cot_feed, "_get_net_series", lambda asset, weeks=104, as_of_date=None: [1, 2])
    monkeypatch.setattr(cot_feed, "_asset_z", lambda key, as_of_date=None: None)
    cot_feed._mem_cache.clear()

    detail = cot_feed.get_cot_net("NZD/USD")
    assert detail["_cot_coverage"] == "no_coverage"
    assert "min rows=2" in detail["_cot_note"]


def test_softs_pair_formulas_are_non_empty():
    for display in ("Cocoa", "Coffee", "Corn", "Cotton", "Soybeans", "Sugar", "Wheat", "Cattle"):
        formula = cot_feed._PAIR_FORMULA.get(display)
        assert formula, f"missing COT formula for {display}"
        assert len(formula) >= 1


def test_asset_z_does_not_negative_cache_missing_series(monkeypatch):
    monkeypatch.setattr(cot_feed, "_get_net_series", lambda asset, as_of_date=None: [1, 2, 3])
    cot_feed._mem_cache.clear()

    value = cot_feed._asset_z("EUR")

    assert value is None
    assert "EUR" not in cot_feed._mem_cache
