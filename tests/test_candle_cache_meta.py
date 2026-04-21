from candles_cache import _annotate_fetch_meta_with_bar_freshness


def test_annotate_fetch_meta_with_bar_freshness_marks_recent_bar():
    meta = {}
    candles = [{"time": 1_000, "close": 1.0}]

    out = _annotate_fetch_meta_with_bar_freshness(
        meta,
        candles,
        "H1",
        now=2_200.0,
    )

    assert out["lastBarTime"] == 1_000
    assert out["lastBarAgeSec"] == 1200.0
    assert out["lastBarStale"] is False


def test_annotate_fetch_meta_with_bar_freshness_marks_stale_bar():
    meta = {}
    candles = [{"time": 0, "close": 1.0}]

    out = _annotate_fetch_meta_with_bar_freshness(
        meta,
        candles,
        "H1",
        now=37_200.0,
    )

    assert out["lastBarAgeSec"] == 37200.0
    assert out["lastBarStale"] is True
