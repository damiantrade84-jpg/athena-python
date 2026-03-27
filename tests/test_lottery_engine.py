import sqlite3
import uuid
from pathlib import Path

from lottery_engine import (
    build_lottery_dashboard,
    compare_generator_modes,
    compute_number_frequency,
    compute_overdue_numbers,
    compute_pair_frequency,
    compute_triplet_frequency,
    generate_tickets,
    history_rows,
    score_ticket,
    simulate_generator,
)
from lottery_service import ensure_lottery_schema


def _db_path():
    return Path.cwd() / f"lottery-engine-test-{uuid.uuid4().hex}.db"


def _seed_draws(db_path: Path):
    with sqlite3.connect(db_path) as con:
        ensure_lottery_schema(con)
        con.execute(
            """
            INSERT INTO lottery_draws
            (game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("lotto", "2026-01-01", 1, 2, 3, 10, 20, 30, 5, "seed.csv", "2026-01-01T00:00:00"),
        )
        con.execute(
            """
            INSERT INTO lottery_draws
            (game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("lotto", "2026-01-08", 2, 3, 4, 11, 21, 31, 6, "seed.csv", "2026-01-08T00:00:00"),
        )
        con.execute(
            """
            INSERT INTO lottery_draws
            (game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("lotto", "2026-01-15", 3, 4, 5, 12, 22, 32, 7, "seed.csv", "2026-01-15T00:00:00"),
        )
        con.commit()


def test_dashboard_and_frequency_pipeline():
    db_path = _db_path()
    _seed_draws(db_path)

    board = build_lottery_dashboard("lotto", db_path=str(db_path))
    assert board["total_draws"] == 3
    assert board["first_draw_date"] == "2026-01-01"
    assert board["last_draw_date"] == "2026-01-15"
    assert len(board["hot_numbers"]) > 0
    assert len(board["number_frequency"]) == 52

    freq = compute_number_frequency("lotto", db_path=str(db_path))
    hit_three = [x for x in freq["main_number_frequency"] if x["number"] == 3][0]
    assert hit_three["count"] == 3
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

    assert pairs["draw_count"] == 3
    assert triplets["draw_count"] == 3
    assert len(pairs["pairs"]) > 0
    assert len(triplets["triplets"]) > 0
    assert len(hist["history"]) == 3


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
    assert sim["draws_simulated"] == 3
    assert sim["tickets_generated"] == 9
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
