"""Session heat aggregation contract.

Covers the bucket definitions, the sufficiency/labelling rules, run
de-duplication, graceful degradation, and the non-blocking cache used by the
live snapshot path. Pure-path only: no athena.py import, no broker, no scan.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena_app.services import session_heat as sh


# --- Fixtures ----------------------------------------------------------------


def _make_db(rows, runs=None) -> str:
    """Build a throwaway audit db. ``rows`` = (run_id, entry_time, result_r)."""
    tmp = Path(tempfile.mkdtemp(prefix="session_heat_")) / "audit.db"
    con = sqlite3.connect(str(tmp))
    con.execute(
        "CREATE TABLE backtest_runs_v3 (id INTEGER PRIMARY KEY, created_at TEXT, symbol TEXT, "
        "engine TEXT, style TEXT, contract_version TEXT)"
    )
    con.execute(
        "CREATE TABLE backtest_trades_v3 (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, "
        "entry_time TEXT, result_r REAL)"
    )
    for run in runs or [(1, "2026-01-01T00:00:00+00:00", "EUR/USD", "A", "intraday", "3.1.0")]:
        con.execute("INSERT INTO backtest_runs_v3 VALUES (?,?,?,?,?,?)", run)
    for run_id, entry_time, result_r in rows:
        con.execute(
            "INSERT INTO backtest_trades_v3 (run_id, entry_time, result_r) VALUES (?,?,?)",
            (run_id, entry_time, result_r),
        )
    con.commit()
    con.close()
    return str(tmp)


def _trades(run_id, hour_utc, results, day="2026-06-15"):
    return [(run_id, f"{day}T{hour_utc:02d}:30:00+00:00", r) for r in results]


@pytest.fixture(autouse=True)
def _clear_cache():
    sh.reset_session_heat_cache()
    yield
    sh.reset_session_heat_cache()


# --- Bucket contract ---------------------------------------------------------


def test_buckets_cover_every_hour_exactly_once():
    covered = []
    for bucket in sh.SESSION_BUCKETS:
        covered.extend(range(bucket.start_hour, bucket.end_hour))
    assert sorted(covered) == list(range(24))


def test_bucket_edges_are_documented_utc_windows():
    assert sh.session_bucket_for_hour(0) == "asia"
    assert sh.session_bucket_for_hour(6) == "asia"
    assert sh.session_bucket_for_hour(7) == "london"
    assert sh.session_bucket_for_hour(11) == "london"
    assert sh.session_bucket_for_hour(12) == "ny_am"
    assert sh.session_bucket_for_hour(15) == "ny_am"
    assert sh.session_bucket_for_hour(16) == "ny_pm"
    assert sh.session_bucket_for_hour(20) == "ny_pm"
    assert sh.session_bucket_for_hour(21) == "off_hours"
    assert sh.session_bucket_for_hour(23) == "off_hours"


def test_ict_killzones_fall_inside_their_documented_bucket():
    # Asian 01-05, London 07-10, NY AM 12-15 (UTC, EDT alignment).
    assert {sh.session_bucket_for_hour(h) for h in range(1, 5)} == {"asia"}
    assert {sh.session_bucket_for_hour(h) for h in range(7, 10)} == {"london"}
    assert {sh.session_bucket_for_hour(h) for h in range(12, 15)} == {"ny_am"}


def test_timestamp_parsing_handles_offsets_and_junk():
    assert sh.session_bucket_for_timestamp("2026-06-15T08:00:00+00:00") == "london"
    assert sh.session_bucket_for_timestamp("2026-06-15T08:00:00Z") == "london"
    # A real non-zero offset must be converted, not string-sliced.
    assert sh.session_bucket_for_timestamp("2026-06-15T08:00:00+05:00") == "asia"
    assert sh.session_bucket_for_timestamp("not-a-date") is None
    assert sh.session_bucket_for_timestamp(None) is None


def test_current_session_reports_position_within_the_window():
    now = datetime(2026, 6, 15, 8, 30, tzinfo=timezone.utc)
    cur = sh.current_session(now)
    assert cur["key"] == "london"
    assert cur["minutesElapsed"] == 90
    assert cur["minutesRemaining"] == 210
    assert cur["endsAtUtc"] == "2026-06-15T12:00:00Z"
    assert cur["isWeekend"] is False


# --- Sufficiency and labelling ----------------------------------------------


def test_cells_below_min_sample_are_insufficient_and_uncoloured():
    db = _make_db(_trades(1, 8, [1.0] * 10))  # 10 trades, min sample 30
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    row = next(m for m in heat["matrix"] if m["scope"] == "engine")
    london = row["cells"]["london"]
    assert london["trades"] == 10
    assert london["heat"] == sh.HEAT_INSUFFICIENT
    assert london["heatScore"] is None  # never colours a thin cell
    assert heat["status"] == "INSUFFICIENT"


def test_strong_and_weak_require_separation_from_the_baseline():
    # London clearly better than NY PM, both well above the sample floor.
    rows = _trades(1, 8, [1.0, 1.0, -0.5] * 20) + _trades(1, 17, [-1.0, 0.2, -1.0] * 20)
    db = _make_db(rows)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    row = next(m for m in heat["matrix"] if m["scope"] == "engine")
    assert row["cells"]["london"]["heat"] == sh.HEAT_STRONG
    assert row["cells"]["ny_pm"]["heat"] == sh.HEAT_WEAK
    assert row["bestSession"] == "london"
    assert row["worstSession"] == "ny_pm"


def test_identical_sessions_are_neutral_not_strong():
    rows = _trades(1, 8, [0.5, -0.5] * 30) + _trades(1, 17, [0.5, -0.5] * 30)
    db = _make_db(rows)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    row = next(m for m in heat["matrix"] if m["scope"] == "engine")
    assert row["cells"]["london"]["heat"] == sh.HEAT_NEUTRAL
    assert row["cells"]["ny_pm"]["heat"] == sh.HEAT_NEUTRAL


def test_metrics_match_hand_computed_values():
    db = _make_db(_trades(1, 8, [2.0, -1.0, -1.0, 2.0] * 10))
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    cell = next(m for m in heat["matrix"] if m["scope"] == "engine")["cells"]["london"]
    assert cell["trades"] == 40
    assert cell["wins"] == 20
    assert cell["losses"] == 20
    assert cell["winRate"] == 0.5
    assert cell["expectancyR"] == pytest.approx(0.5)
    assert cell["profitFactor"] == pytest.approx(2.0)   # 40R won / 20R lost
    assert cell["totalR"] == pytest.approx(20.0)


def test_profit_factor_is_none_without_a_losing_side():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    cell = next(m for m in heat["matrix"] if m["scope"] == "engine")["cells"]["london"]
    # Undefined, never infinity - an all-win cell must not read as a perfect window.
    assert cell["profitFactor"] is None
    assert cell["winRate"] == 1.0


# --- Run de-duplication ------------------------------------------------------


def test_rerun_of_the_same_scope_does_not_double_count():
    runs = [
        (1, "2026-01-01T00:00:00+00:00", "EUR/USD", "A", "intraday", "3.1.0"),
        (2, "2026-02-01T00:00:00+00:00", "EUR/USD", "A", "intraday", "3.1.0"),  # re-run
    ]
    rows = _trades(1, 8, [1.0] * 40) + _trades(2, 8, [-1.0] * 40)
    db = _make_db(rows, runs=runs)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    cell = next(m for m in heat["matrix"] if m["scope"] == "engine")["cells"]["london"]
    assert cell["trades"] == 40                        # not 80
    assert cell["expectancyR"] == pytest.approx(-1.0)  # newest run wins
    assert heat["coverage"]["runsUsed"] == 1


def test_distinct_symbols_are_both_counted():
    runs = [
        (1, "2026-01-01T00:00:00+00:00", "EUR/USD", "A", "intraday", "3.1.0"),
        (2, "2026-01-01T00:00:00+00:00", "GBP/USD", "A", "intraday", "3.1.0"),
    ]
    db = _make_db(_trades(1, 8, [1.0] * 20) + _trades(2, 8, [1.0] * 20), runs=runs)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    cell = next(m for m in heat["matrix"] if m["scope"] == "engine")["cells"]["london"]
    assert cell["trades"] == 40
    assert cell["symbols"] == 2


# --- Engine separation and score groups -------------------------------------


def test_engine_a_and_engine_b_stay_independent():
    runs = [
        (1, "2026-01-01T00:00:00+00:00", "EUR/USD", "A", "intraday", "3.1.0"),
        (2, "2026-01-01T00:00:00+00:00", "EUR/USD", "B", "intraday", "3.1.0"),
    ]
    db = _make_db(_trades(1, 8, [1.0] * 40) + _trades(2, 8, [-1.0] * 40), runs=runs)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    by_engine = {m["engine"]: m for m in heat["matrix"] if m["scope"] == "engine"}
    assert by_engine["A"]["cells"]["london"]["expectancyR"] == pytest.approx(1.0)
    assert by_engine["B"]["cells"]["london"]["expectancyR"] == pytest.approx(-1.0)


def test_score_group_rows_are_emitted_alongside_the_engine_row():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    scopes = {(m["engine"], m["scope"], m["scoreGroup"]) for m in heat["matrix"]}
    assert ("A", "engine", None) in scopes
    assert ("A", "scoreGroup", "forex_majors") in scopes


def test_pair_lookup_overrides_symbol_inference():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    seen: list[str] = []

    def lookup(symbol):
        seen.append(symbol)
        return {"display": symbol, "type": "crypto"}

    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None), pair_lookup=lookup)
    assert seen == ["EUR/USD"]
    groups = {m["scoreGroup"] for m in heat["matrix"] if m["scoreGroup"]}
    assert "forex_majors" not in groups  # the lookup, not the symbol string, decided


def test_pair_lookup_failure_falls_back_instead_of_raising():
    db = _make_db(_trades(1, 8, [1.0] * 40))

    def boom(_symbol):
        raise RuntimeError("lookup down")

    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None), pair_lookup=boom)
    assert heat["status"] in ("OK", "INSUFFICIENT")
    assert "forex_majors" in {m["scoreGroup"] for m in heat["matrix"] if m["scoreGroup"]}


# --- Graceful degradation ----------------------------------------------------


def test_empty_table_reports_no_data_without_raising():
    db = _make_db([])
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    assert heat["status"] == "NO_DATA"
    assert heat["matrix"] == []
    assert heat["coverage"]["trades"] == 0


def test_missing_tables_report_error_without_raising():
    tmp = Path(tempfile.mkdtemp(prefix="session_heat_empty_")) / "audit.db"
    sqlite3.connect(str(tmp)).close()
    heat = sh.build_session_heat(str(tmp), settings=sh.resolve_settings(None))
    assert heat["status"] == "ERROR"
    assert heat["error"]
    assert heat["matrix"] == []


def test_null_and_nonfinite_results_are_skipped():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO backtest_trades_v3 (run_id, entry_time, result_r) VALUES (1,?,NULL)",
        ("2026-06-15T08:30:00+00:00",),
    )
    con.execute(
        "INSERT INTO backtest_trades_v3 (run_id, entry_time, result_r) VALUES (1,?,?)",
        ("2026-06-15T08:30:00+00:00", float("inf")),
    )
    con.commit()
    con.close()
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    cell = next(m for m in heat["matrix"] if m["scope"] == "engine")["cells"]["london"]
    assert cell["trades"] == 40  # NULL filtered in SQL, inf filtered in Python


def test_lookback_window_excludes_older_trades():
    old_day = (datetime.now(timezone.utc) - timedelta(days=800)).strftime("%Y-%m-%d")
    new_day = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    rows = _trades(1, 8, [1.0] * 40, day=old_day) + _trades(1, 8, [-1.0] * 40, day=new_day)
    db = _make_db(rows)
    settings = sh.resolve_settings({"LIVE_DASHBOARD": {"SESSION_HEAT": {"LOOKBACK_DAYS": 365}}})
    heat = sh.build_session_heat(db, settings=settings)
    cell = next(m for m in heat["matrix"] if m["scope"] == "engine")["cells"]["london"]
    assert cell["trades"] == 40
    assert cell["expectancyR"] == pytest.approx(-1.0)


# --- Settings ----------------------------------------------------------------


def test_settings_merge_and_reject_junk():
    resolved = sh.resolve_settings({
        "LIVE_DASHBOARD": {"SESSION_HEAT": {"MIN_SAMPLE": 5, "HEAT_SCALE_R": "nonsense", "ENABLED": False}}
    })
    assert resolved["MIN_SAMPLE"] == 5
    assert resolved["HEAT_SCALE_R"] == sh.DEFAULTS["HEAT_SCALE_R"]
    assert resolved["ENABLED"] is False
    assert resolved["LOOKBACK_DAYS"] == sh.DEFAULTS["LOOKBACK_DAYS"]


def test_missing_config_block_uses_defaults():
    assert sh.resolve_settings(None) == sh.resolve_settings({}) == dict(sh.DEFAULTS)


# --- Card indicator ----------------------------------------------------------


def test_card_indicator_falls_back_to_engine_scope_when_group_is_thin():
    runs = [
        (1, "2026-01-01T00:00:00+00:00", "EUR/USD", "A", "intraday", "3.1.0"),
        (2, "2026-01-01T00:00:00+00:00", "BTC/USDT", "A", "intraday", "3.1.0"),
    ]
    # forex_majors is thin (5), crypto_btc is deep - the engine row clears the floor.
    db = _make_db(_trades(1, 8, [1.0] * 5) + _trades(2, 8, [1.0] * 40), runs=runs)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))

    thin = sh.card_indicator(heat, "A", "london", score_group="forex_majors")
    assert thin["scope"] == "engine"
    assert thin["heat"] != sh.HEAT_INSUFFICIENT
    assert "all groups" in thin["scopeLabel"]

    deep = sh.card_indicator(heat, "A", "london", score_group="crypto_btc")
    assert deep["scope"] == "scoreGroup"
    assert deep["scoreGroup"] == "crypto_btc"


def test_card_indicator_is_safe_on_an_unknown_engine():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    ind = sh.card_indicator(heat, "B", "london")
    assert ind["heat"] == sh.HEAT_INSUFFICIENT
    assert ind["tone"] == "unknown"
    assert ind["label"].endswith("No data")


def test_card_indicator_label_and_tone_track_the_heat_level():
    rows = _trades(1, 8, [1.0, 1.0, -0.5] * 20) + _trades(1, 17, [-1.0, 0.2, -1.0] * 20)
    db = _make_db(rows)
    heat = sh.build_session_heat(db, settings=sh.resolve_settings(None))
    strong = sh.card_indicator(heat, "A", "london")
    weak = sh.card_indicator(heat, "A", "ny_pm")
    assert strong["label"] == "London · Strong" and strong["tone"] == "strong"
    assert weak["label"] == "NY PM · Weak" and weak["tone"] == "weak"
    assert "expectancy" in strong["tooltip"]


# --- Non-blocking cache ------------------------------------------------------


def test_cold_cache_does_not_block_and_warms_in_background():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    settings = sh.resolve_settings(None)

    payload, meta = sh.get_session_heat(db, settings=settings, block=False)
    assert payload is None
    assert meta["state"] == "WARMING"

    deadline = time.time() + 10.0
    while time.time() < deadline:
        payload, meta = sh.get_session_heat(db, settings=settings, block=False)
        if payload is not None:
            break
        time.sleep(0.05)
    assert payload is not None, "background build never landed"
    assert meta["state"] == "FRESH"
    assert payload["status"] in ("OK", "INSUFFICIENT")


def test_blocking_build_returns_immediately_usable_payload():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    payload, meta = sh.get_session_heat(db, settings=sh.resolve_settings(None), block=True)
    assert meta["state"] == "BUILT"
    assert payload["status"] in ("OK", "INSUFFICIENT")


def test_stale_entry_is_served_while_a_rebuild_runs():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    settings = sh.resolve_settings(None)
    settings["CACHE_TTL_SEC"] = 1
    sh.get_session_heat(db, settings=settings, block=True)
    time.sleep(1.2)
    payload, meta = sh.get_session_heat(db, settings=settings, block=False)
    assert payload is not None          # stale data still answers
    assert meta["state"] == "STALE"
    assert meta["rebuilding"] is True


def test_concurrent_cold_reads_start_only_one_build():
    db = _make_db(_trades(1, 8, [1.0] * 40))
    settings = sh.resolve_settings(None)
    started: list[bool] = []

    def worker():
        _, meta = sh.get_session_heat(db, settings=settings, block=False)
        started.append(bool(meta.get("buildStarted")))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(started) == 1
