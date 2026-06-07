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
