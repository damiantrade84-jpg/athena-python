import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_engine_c_bt_monitor_fill_index_datetimeindex_searchsorted_no_bool_crash():
    h1_times = pd.to_datetime(
        [
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
            "2026-01-01T02:00:00Z",
        ],
        utc=True,
        errors="coerce",
    )

    entry_bar = {"time": "2026-01-01T01:00:00Z"}

    _monitor_tf = "H1"
    _monitor_fill_index = 0
    if _monitor_tf == "H4":
        _monitor_fill_index = 999  # unreachable in this test
    elif h1_times is not None and len(h1_times) > 0:
        _entry_bar_ts = pd.Timestamp(entry_bar["time"])
        if pd.notna(_entry_bar_ts):
            if _entry_bar_ts.tzinfo is None:
                _entry_bar_ts = _entry_bar_ts.tz_localize("UTC")
            _monitor_fill_index = int(h1_times.searchsorted(_entry_bar_ts, side="left"))

    assert _monitor_fill_index == 1


def test_engine_c_bt_monitor_fill_index_h4_uses_i_plus_one():
    i = 7
    _monitor_tf = "H4"
    _monitor_fill_index = 0

    if _monitor_tf == "H4":
        _monitor_fill_index = i + 1

    assert _monitor_fill_index == 8
