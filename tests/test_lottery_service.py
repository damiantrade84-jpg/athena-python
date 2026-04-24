import sqlite3
import tempfile
from pathlib import Path
import pytest

from lottery_service import (
    clear_lottery_draws,
    ensure_lottery_schema,
    fetch_lottery_draws,
    import_lottery_csv,
    lottery_stats,
    parse_lottery_csv,
)


@pytest.fixture
def local_tmpdir():
    """Local temp directory fixture using tempfile for better permission handling."""
    tmpdir = Path(tempfile.mkdtemp(prefix="lottery_test_"))
    yield tmpdir


def test_parse_lottery_csv_filters_invalid_rows():
    csv_text = """draw_date,n1,n2,n3,n4,n5,n6,bonus
2026-01-01,5,4,3,2,1,6,7
bad-date,1,2,3,4,5,6,7
2026-01-03,1,1,3,4,5,6,7
2026-01-04,1,2,3,4,5,,7
"""
    result = parse_lottery_csv(csv_text, "lotto")

    assert result["valid_rows"] == 1
    assert result["invalid_rows"] == 3
    assert result["preview"][0]["n1"] == 1
    assert result["preview"][0]["n6"] == 6
    assert result["error_counts"]["invalid_date"] == 1
    assert result["error_counts"]["duplicate_numbers"] == 1
    assert result["error_counts"]["missing_numbers"] == 1


def test_import_and_stats_round_trip(local_tmpdir):
    db_path = local_tmpdir / "lottery-test.db"
    with sqlite3.connect(db_path) as con:
        ensure_lottery_schema(con)
        con.commit()

    csv_text = """draw_date,n1,n2,n3,n4,n5,bonus
2026-01-01,9,7,1,5,3,11
2026-01-02,10,8,2,6,4,12
"""
    result = import_lottery_csv(str(db_path), "powerball", csv_text, source_file="pb.csv")

    assert result["imported_rows"] == 2
    assert result["duplicates_skipped"] == 0

    draws = fetch_lottery_draws(str(db_path), game="powerball", limit=10)["draws"]
    assert len(draws) == 2
    assert draws[0]["source_file"] == "pb.csv"

    stats = lottery_stats(str(db_path), "powerball")
    assert stats["draw_count"] == 2
    assert stats["latest_draw_date"] == "2026-01-02"
    assert any(item["number"] == 2 and item["count"] == 1 for item in stats["main_number_frequency"])
    assert any(item["number"] == 11 and item["count"] == 1 for item in stats["bonus_frequency"])


def test_replace_mode_and_clear(local_tmpdir):
    db_path = local_tmpdir / "lottery-test.db"
    import_lottery_csv(
        str(db_path),
        "daily_lotto",
        "draw_date,n1,n2,n3,n4,n5\n2026-01-01,5,4,3,2,1\n",
    )
    replaced = import_lottery_csv(
        str(db_path),
        "daily_lotto",
        "draw_date,n1,n2,n3,n4,n5\n2026-01-02,10,9,8,7,6\n",
        mode="replace",
    )

    draws = fetch_lottery_draws(str(db_path), game="daily_lotto", limit=10)["draws"]
    assert replaced["deleted_rows"] == 1
    assert len(draws) == 1
    assert draws[0]["draw_date"] == "2026-01-02"

    cleared = clear_lottery_draws(str(db_path), game="daily_lotto")
    assert cleared["deleted_rows"] == 1
    assert fetch_lottery_draws(str(db_path), game="daily_lotto", limit=10)["draws"] == []
