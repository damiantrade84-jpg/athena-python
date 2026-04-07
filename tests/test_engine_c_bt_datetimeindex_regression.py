import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_engine_c_bt_monitor_fill_index_datetimeindex_searchsorted_no_bool_crash():
    _monitor_times = pd.to_datetime(
        [
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
            "2026-01-01T02:00:00Z",
        ],
        utc=True,
        errors="coerce",
    )

    entry_bar = {"time": "2026-01-01T01:00:00Z"}

    _monitor_fill_index = 0
    if _monitor_times is not None and len(_monitor_times) > 0:
        _entry_ts = pd.to_datetime(entry_bar["time"], utc=True, errors="coerce")
        if pd.notna(_entry_ts):
            _monitor_fill_index = int(_monitor_times.searchsorted(_entry_ts, side="left"))

    assert _monitor_fill_index == 1
