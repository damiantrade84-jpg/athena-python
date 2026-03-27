import sqlite3
import uuid
from pathlib import Path

from lottery_engine import (
    _ticket_matches_constraints,
    build_lottery_dashboard,
    compare_generator_modes,
    compute_pair_lift,
    compute_number_frequency,
    compute_positional_distribution,
    compute_recommended_sum_range,
    compute_rolling_frequency,
    compute_overdue_numbers,
    compute_pair_frequency,
    compute_triplet_frequency,
    flag_anomalous_draws,
    generate_tickets,
    generate_wheel,
    history_rows,
    score_ticket,
    simulate_generator,
)
from lottery_service import ensure_lottery_schema


def _db_path():
    return Path.cwd() / f"lottery-engine-test-{uuid.uuid4().hex}.db"


def _seed_draws(db_path: Path):
    seeded_rows = [
        ("2026-01-01", 1, 2, 3, 10, 20, 30, 5),
        ("2026-01-08", 1, 2, 4, 11, 21, 31, 6),
        ("2026-01-15", 1, 2, 5, 12, 22, 32, 7),
        ("2026-01-22", 1, 2, 6, 13, 23, 33, 8),
        ("2026-01-29", 1, 2, 7, 14, 24, 34, 9),
        ("2026-02-05", 1, 3, 4, 15, 25, 35, 10),
        ("2026-02-12", 1, 3, 5, 16, 26, 36, 11),
        ("2026-02-19", 1, 3, 6, 17, 27, 37, 12),
        ("2026-02-26", 1, 3, 7, 18, 28, 38, 13),
        ("2026-03-05", 1, 2, 3, 19, 29, 39, 14),
    ]
    with sqlite3.connect(db_path) as con:
        ensure_lottery_schema(con)
        for draw_date, n1, n2, n3, n4, n5, n6, bonus in seeded_rows:
            con.execute(
                """
                INSERT INTO lottery_draws
                (game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("lotto", draw_date, n1, n2, n3, n4, n5, n6, bonus, "seed.csv", f"{draw_date}T00:00:00"),
            )
        con.commit()


def test_dashboard_and_frequency_pipeline():
    db_path = _db_path()
    _seed_draws(db_path)

    board = build_lottery_dashboard("lotto", db_path=str(db_path))
    assert board["total_draws"] == 10
    assert board["first_draw_date"] == "2026-01-01"
    assert board["last_draw_date"] == "2026-03-05"
    assert len(board["hot_numbers"]) > 0
    assert len(board["number_frequency"]) == 52
    assert board["recommended_sum_range"]["min"] < board["recommended_sum_range"]["max"]
    assert len(board["positional_distribution"]) == 6
    assert len(board["rolling_frequency"]) == 52

    freq = compute_number_frequency("lotto", db_path=str(db_path))
    hit_one = [x for x in freq["main_number_frequency"] if x["number"] == 1][0]
    assert hit_one["count"] == 10
    assert len(freq["decade_groups"]) > 0

    overdue = compute_overdue_numbers("lotto", db_path=str(db_path))
    first_overdue = overdue["overdue"][0]
    assert "draws_since_seen" in first_overdue


def test_pairs_triplets_and_history():
    db_path = _db_path()
    _seed_draws(db_path)

    pairs = compute_pair_frequency("lotto", db_path=str(db_path))
    triplets = compute_triplet_frequency("lotto", db_path=str(db_path))
    hist = history_rows("lotto", db_path=str(db_path), limit=10)

    assert pairs["draw_count"] == 10
    assert triplets["draw_count"] == 10
    assert len(pairs["pairs"]) > 0
    assert len(triplets["triplets"]) > 0
    assert len(hist["history"]) == 10


def test_generator_scoring_and_simulation():
    db_path = _db_path()
    _seed_draws(db_path)

    generated = generate_tickets(
        "lotto",
        mode="balanced_mix",
        ticket_count=4,
        include_bonus=True,
        filters={"max_consecutive_run": 4},
        db_path=str(db_path),
    )
    assert generated["ticket_count"] == 4
    assert len(generated["tickets"]) == 4

    scored = score_ticket("lotto", generated["tickets"][0], include_bonus=True, db_path=str(db_path))
    assert "labels" in scored
    assert "pair_strength" in scored

    sim = simulate_generator(
        "lotto",
        mode="pure_random",
        tickets_per_draw=3,
        include_bonus=True,
        ticket_cost=5.0,
        payout_table={"3": 10, "4": 75, "5": 500, "6": 20000},
        db_path=str(db_path),
    )
    assert sim["draws_simulated"] == 10
    assert sim["tickets_generated"] == 30
    assert "hit_distribution" in sim

    cmp_payload = compare_generator_modes(
        "lotto",
        modes=["pure_random", "hot_bias", "anti_crowd"],
        tickets_per_draw=2,
        db_path=str(db_path),
    )
    assert len(cmp_payload["modes"]) == 3


def test_generator_retry_cap_falls_back_for_impossible_constraints():
    db_path = _db_path()
    _seed_draws(db_path)

    generated = generate_tickets(
        "lotto",
        mode="pair_bias",
        ticket_count=3,
        filters={"odd_even_target": "6:0", "low_high_target": "6:0", "max_sum": 10},
        db_path=str(db_path),
    )

    assert generated["ticket_count"] == 3
    assert generated["fallback_tickets_used"] >= 1

    rules = {
        "main_count": 6,
        "main_max": 52,
    }
    filters = {
        "enforce_positional_bands": True,
        "positional_bands": [
            {"min": 1, "max": 2},
            {"min": 2, "max": 3},
            {"min": 3, "max": 10},
            {"min": 10, "max": 20},
            {"min": 20, "max": 35},
            {"min": 30, "max": 45},
        ],
    }
    assert _ticket_matches_constraints(
        {"numbers": [1, 2, 5, 14, 24, 34]},
        rules,
        filters,
        recent_draw_numbers=[],
    )
    assert not _ticket_matches_constraints(
        {"numbers": [10, 11, 12, 13, 14, 15]},
        rules,
        filters,
        recent_draw_numbers=[],
    )


def test_new_lottery_analytics_and_wheel():
    db_path = _db_path()
    _seed_draws(db_path)

    sum_range = compute_recommended_sum_range("lotto", db_path=str(db_path))
    assert sum_range["draw_count"] > 0
    assert sum_range["p15"] < sum_range["p85"]

    positional = compute_positional_distribution("lotto", db_path=str(db_path))
    assert len(positional["positions"]) == 6

    rolling = compute_rolling_frequency("lotto", window=5, db_path=str(db_path))
    assert rolling["window_used"] == 5
    assert len(rolling["numbers"]) == 52
    assert all(isinstance(row["z_score"], (int, float)) or row["z_score"] is None for row in rolling["numbers"])

    pair_lift = compute_pair_lift("lotto", limit=10, db_path=str(db_path))
    assert len(pair_lift["pairs"]) > 0
    assert all(row["lift"] > 0 for row in pair_lift["pairs"])
    assert all(0 <= row["confidence_ab"] <= 1 for row in pair_lift["pairs"])

    anomalies = flag_anomalous_draws("lotto", z_threshold=1.5, db_path=str(db_path))
    assert anomalies["anomalous_count"] <= anomalies["draw_count"]

    wheel = generate_wheel("lotto", chosen_numbers=[1, 2, 3, 4, 5, 6, 7, 8], guarantee_if=3)
    assert wheel["coverage_verified"] is True
    assert wheel["ticket_count"] > 0

    try:
        generate_wheel("lotto", chosen_numbers=[1, 2, 3, 4, 5], guarantee_if=3)
        assert False, "Expected ValueError for too few chosen numbers"
    except ValueError:
        pass

    try:
        generate_wheel("lotto", chosen_numbers=[1, 2, 3, 4, 5, 6], guarantee_if=7)
        assert False, "Expected ValueError for invalid guarantee_if"
    except ValueError:
        pass
