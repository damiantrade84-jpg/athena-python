import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from athena_app.services.naked_scan_service import iter_naked_scan_rows


def test_iter_naked_scan_rows_wraps_single_debug_dict():
    row = {"debug": {"pair": "EUR/USD"}}

    assert iter_naked_scan_rows(row) == [row]


def test_iter_naked_scan_rows_filters_non_dict_iterables():
    row = {"display": "SPY", "confluenceScore": 7.0}

    assert iter_naked_scan_rows([row, "debug", None]) == [row]


def test_iter_naked_scan_rows_ignores_strings_and_none():
    assert iter_naked_scan_rows("debug") == []
    assert iter_naked_scan_rows(None) == []
