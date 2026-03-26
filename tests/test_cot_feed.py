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


def test_asset_z_does_not_negative_cache_missing_series(monkeypatch):
    monkeypatch.setattr(cot_feed, "_get_net_series", lambda asset, as_of_date=None: [1, 2, 3])
    cot_feed._mem_cache.clear()

    value = cot_feed._asset_z("EUR")

    assert value is None
    assert "EUR" not in cot_feed._mem_cache
