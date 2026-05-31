import pytest


def _reset_vol_db(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_DB_PATH", str(tmp_path / "vol_skew_cache.db"))
    module._mem_cache.clear()
    module._series_mem_cache.clear()
    module._init_db()


def test_vol_skew_z_is_point_in_time_and_missing_mapping_returns_none(tmp_path, monkeypatch):
    import vol_skew_feed

    _reset_vol_db(vol_skew_feed, tmp_path, monkeypatch)
    monkeypatch.setitem(vol_skew_feed.CONFIG, "ENGINE_A_VOL_SKEW_Z_WINDOW", 6)
    monkeypatch.setitem(vol_skew_feed.CONFIG, "ENGINE_A_VOL_SKEW_TERM_WEIGHT", 0.0)

    vol_skew_feed._write_series(
        "GVZCLS",
        [
            ("2024-01-01", 10.0),
            ("2024-01-02", 11.0),
            ("2024-01-03", 12.0),
            ("2024-01-04", 13.0),
            ("2024-01-05", 14.0),
            ("2024-01-08", 20.0),
            ("2025-01-01", 20.0),
            ("2025-01-02", 21.0),
            ("2025-01-03", 22.0),
            ("2025-01-06", 23.0),
            ("2025-01-07", 24.0),
            ("2025-01-08", 10.0),
        ],
    )

    first = vol_skew_feed.get_vol_skew_z("XAU/USD", "2024-01-08")
    second = vol_skew_feed.get_vol_skew_z("XAU/USD", "2025-01-08")

    assert isinstance(first, float)
    assert isinstance(second, float)
    assert first != second
    assert vol_skew_feed.get_vol_skew_z("EUR/USD") is None


def test_vol_skew_z_falls_back_from_vxn_to_vix_and_blends_term_structure(tmp_path, monkeypatch):
    import vol_skew_feed

    _reset_vol_db(vol_skew_feed, tmp_path, monkeypatch)
    monkeypatch.setitem(vol_skew_feed.CONFIG, "ENGINE_A_VOL_SKEW_Z_WINDOW", 6)
    monkeypatch.setitem(vol_skew_feed.CONFIG, "ENGINE_A_VOL_SKEW_TERM_WEIGHT", 0.3)

    vol_skew_feed._write_series(
        "VIXCLS",
        [
            ("2025-01-01", 10.0),
            ("2025-01-02", 11.0),
            ("2025-01-03", 12.0),
            ("2025-01-06", 13.0),
            ("2025-01-07", 14.0),
            ("2025-01-08", 20.0),
        ],
    )
    vol_skew_feed._write_series(
        "VXVCLS",
        [
            ("2025-01-01", 20.0),
            ("2025-01-02", 19.0),
            ("2025-01-03", 18.0),
            ("2025-01-06", 17.0),
            ("2025-01-07", 16.0),
            ("2025-01-08", 15.0),
        ],
    )

    result = vol_skew_feed.get_vol_skew_z("NASDAQ-100", "2025-01-08")

    assert isinstance(result, float)
    assert result > 0


def test_vol_skew_z_fail_closed_on_fetch_error(tmp_path, monkeypatch):
    import vol_skew_feed

    _reset_vol_db(vol_skew_feed, tmp_path, monkeypatch)
    monkeypatch.setattr(
        vol_skew_feed,
        "_fetch_fred",
        lambda _series_id: (_ for _ in ()).throw(RuntimeError("fred down")),
    )

    assert vol_skew_feed.get_vol_skew_z("XAU/USD") is None
