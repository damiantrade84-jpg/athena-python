import logging
import sqlite3
import time

import carry_feed
from config import CONFIG


def test_historical_rate_lookup_uses_in_memory_series_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "carry_cache.db"
    monkeypatch.setattr(carry_feed, "_DB_PATH", str(db_path))
    carry_feed._mem_cache.clear()
    carry_feed._rate_cache.clear()
    carry_feed._series_mem_cache.clear()
    carry_feed._init_db()

    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT INTO rate_series (series_id, obs_date, rate) VALUES (?,?,?)",
            [
                ("DFF", "2024-01-01", 5.0),
                ("DFF", "2024-02-01", 5.1),
                ("DFF", "2024-03-01", 5.2),
                ("DFF", "2024-04-01", 5.3),
            ],
        )
        con.execute(
            "INSERT INTO carry_meta (series_id, last_fetch) VALUES (?,?)",
            ("DFF", time.time()),
        )
        con.commit()

    assert carry_feed._get_rate_for_key("USD", as_of_date="2024-04-15") == 5.3
    assert carry_feed._get_rate_series_for_key("USD", months=4, as_of_date="2024-04-15") == [
        5.0,
        5.1,
        5.2,
        5.3,
    ]

    def fail_connect(*_args, **_kwargs):
        raise AssertionError("historical cache lookup should not reopen SQLite")

    monkeypatch.setattr(carry_feed.sqlite3, "connect", fail_connect)

    assert carry_feed._get_rate_for_key("USD", as_of_date="2024-04-15") == 5.3
    assert carry_feed._get_rate_series_for_key("USD", months=4, as_of_date="2024-04-15") == [
        5.0,
        5.1,
        5.2,
        5.3,
    ]


def test_static_carry_fallback_warns_when_as_of_stale(monkeypatch, caplog):
    carry_feed._static_carry_warned = False
    monkeypatch.setitem(CONFIG, "CARRY_STATIC_RATES_AS_OF", "2020-01-01")
    monkeypatch.setitem(CONFIG, "CARRY_STATIC_RATES_MAX_AGE_DAYS", 30)
    monkeypatch.setattr(carry_feed, "_FRED_CURRENCY_SERIES", {"SGD": "MISSING_SERIES"})
    monkeypatch.setattr(carry_feed, "_get_latest_rate", lambda _sid: None)
    carry_feed._rate_cache.clear()

    with caplog.at_level(logging.WARNING, logger="sentinel"):
        rate = carry_feed._get_rate_for_key("SGD")

    assert rate == carry_feed._STATIC_RATES["SGD"]
    assert any("CARRY_STATIC_RATES_AS_OF" in rec.message for rec in caplog.records)


def test_carry_history_aligns_daily_and_monthly_series_by_month(tmp_path, monkeypatch):
    db_path = tmp_path / "carry_cache.db"
    monkeypatch.setattr(carry_feed, "_DB_PATH", str(db_path))
    carry_feed._mem_cache.clear()
    carry_feed._rate_cache.clear()
    carry_feed._series_mem_cache.clear()
    carry_feed._init_db()

    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT INTO rate_series (series_id, obs_date, rate) VALUES (?,?,?)",
            [
                ("DFF", "2023-11-10", 4.8),
                ("DFF", "2023-12-15", 4.9),
                ("DFF", "2024-01-15", 5.1),
                ("DFF", "2024-02-10", 5.2),
                ("DFF", "2024-03-20", 5.3),
                ("DFF", "2024-04-15", 5.4),
                ("IRSTCI01JPM156N", "2023-11-30", 0.05),
                ("IRSTCI01JPM156N", "2023-12-31", 0.08),
                ("IRSTCI01JPM156N", "2024-01-31", 0.10),
                ("IRSTCI01JPM156N", "2024-02-29", 0.20),
                ("IRSTCI01JPM156N", "2024-03-31", 0.30),
                ("IRSTCI01JPM156N", "2024-04-30", 0.40),
            ],
        )
        con.executemany(
            "INSERT INTO carry_meta (series_id, last_fetch) VALUES (?,?)",
            [("DFF", time.time()), ("IRSTCI01JPM156N", time.time())],
        )
        con.commit()

    import frozen_data

    monkeypatch.setattr(frozen_data, "active_data_as_of", lambda: None)
    monkeypatch.setattr(frozen_data, "read_frozen_factor_value", lambda *args, **kwargs: None)

    usd_map = carry_feed._get_monthly_rate_map_for_key("USD", months=6, as_of_date="2024-04-30")
    jpy_map = carry_feed._get_monthly_rate_map_for_key("JPY", months=6, as_of_date="2024-04-30")
    assert usd_map["2024-01"] == 5.1
    assert usd_map["2024-04"] == 5.4
    assert jpy_map["2024-01"] == 0.10
    assert jpy_map["2024-04"] == 0.40
    assert set(usd_map.keys()) == set(jpy_map.keys())

    z = carry_feed.get_carry_z("USD/JPY", as_of_date="2024-04-30")
    assert z != 0.0


def test_gbp_aud_use_short_rate_fred_series():
    assert carry_feed._FRED_CURRENCY_SERIES["GBP"] == "IRSTCI01GBM156N"
    assert carry_feed._FRED_CURRENCY_SERIES["AUD"] == "IRSTCI01AUM156N"
