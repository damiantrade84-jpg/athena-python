"""substitute_levels_atr_series: atrDiagnostics must describe the feed that produced the levels ATR."""

from atr_diagnostics import substitute_levels_atr_series


def test_substitutes_only_the_atr_timeframe_series():
    d1, h4, h1 = [{"time": "d1"}], [{"time": "h4"}], [{"time": "h1"}]
    bybit = [{"time": "bybit"}]

    out_d1, out_h4, out_h1 = substitute_levels_atr_series("D1", d1, h4, h1, bybit)
    assert out_d1 is bybit and out_h4 is h4 and out_h1 is h1

    out_d1, out_h4, out_h1 = substitute_levels_atr_series("H4", d1, h4, h1, bybit)
    assert out_d1 is d1 and out_h4 is bybit and out_h1 is h1

    out_d1, out_h4, out_h1 = substitute_levels_atr_series("H1", d1, h4, h1, bybit)
    assert out_d1 is d1 and out_h4 is h4 and out_h1 is bybit


def test_no_levels_feed_candles_keeps_signal_series():
    d1, h4, h1 = [1], [2], [3]
    assert substitute_levels_atr_series("H4", d1, h4, h1, None) == (d1, h4, h1)
    assert substitute_levels_atr_series("H4", d1, h4, h1, []) == (d1, h4, h1)


def test_unknown_or_missing_tf_keeps_signal_series():
    d1, h4, h1 = [1], [2], [3]
    bybit = [9]
    assert substitute_levels_atr_series("M15", d1, h4, h1, bybit) == (d1, h4, h1)
    assert substitute_levels_atr_series(None, d1, h4, h1, bybit) == (d1, h4, h1)
