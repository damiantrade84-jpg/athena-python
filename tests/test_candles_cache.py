from candles_cache import resample_from_h1


def _hourly_series(start_hour: int, count: int) -> list[dict]:
    rows = []
    for i in range(count):
        hour = start_hour + i
        rows.append(
            {
                "time": f"2026-03-25T{hour:02d}:00:00Z",
                "open": 1.0 + i,
                "high": 1.5 + i,
                "low": 0.5 + i,
                "close": 1.2 + i,
                "vol": 100 + i,
            }
        )
    return rows


def test_h4_resample_uses_utc_boundaries_by_default():
    candles = _hourly_series(16, 8)

    resampled = resample_from_h1(candles, "H4", 20)

    assert [c["time"][11:16] for c in resampled] == ["16:00", "20:00"]


def test_h4_resample_can_shift_forex_alignment_by_one_hour():
    candles = _hourly_series(16, 8)

    resampled = resample_from_h1(
        candles,
        "H4",
        20,
        alignment_offset_hours=1,
    )

    assert [c["time"][11:16] for c in resampled] == ["13:00", "17:00", "21:00"]
