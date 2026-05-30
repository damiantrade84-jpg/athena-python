import pandas as pd
from athena_research import reporting


def test_exit_mode_columns_in_saved_allowlist():
    cols = set(reporting.OUTPUT_COLUMNS)
    assert "engine_a_exit_mode" in cols
    assert "engine_a_exit_parity" in cols


def test_group_agg_by_engine_a_exit_mode():
    # _group_agg must aggregate cleanly keyed by the new column
    df = pd.DataFrame(
        {
            "engine_a_exit_mode": ["traditional_static", "traditional_static", "adaptive_trail"],
            "status": ["STRONG_CANDIDATE", "WEAK_CANDIDATE", "STRONG_CANDIDATE"],
            "profit_factor": [1.2, 1.4, 0.9],
            "trade_count": [10, 12, 8],
        }
    )
    agg = reporting._group_agg(df, "engine_a_exit_mode")
    assert "engine_a_exit_mode" in agg.columns
    assert set(agg["engine_a_exit_mode"]) == {"traditional_static", "adaptive_trail"}
