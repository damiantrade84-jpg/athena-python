import sqlite3
import time

import carry_feed


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
