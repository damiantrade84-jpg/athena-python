import csv
import io
import itertools
import logging
import math
import os
import random
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import mean, median

from lottery_service import LOTTERY_GAME_SPECS, ensure_lottery_schema


log = logging.getLogger("sentinel")


# Alias for backward compatibility locally
LOTTERY_GAME_RULES = LOTTERY_GAME_SPECS

# TODO(Audit): audit.db is currently shared across trading, vision, advisory, and lottery modules.
# Future refactoring should decouple lottery data into its own dedicated database.
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")


def set_lottery_db_path(db_path: str) -> None:
    global _DEFAULT_DB_PATH
    _DEFAULT_DB_PATH = db_path


def _normalize_game(game: str) -> str:
    game_key = str(game or "").strip().lower()
    if game_key not in LOTTERY_GAME_RULES or game_key not in LOTTERY_GAME_SPECS:
        raise ValueError(f"Unsupported game: {game}")
    return game_key


def _coerce_date(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"Invalid date (expected YYYY-MM-DD): {raw}")


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or _DEFAULT_DB_PATH, timeout=15.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    ensure_lottery_schema(con)
    return con


def _number_universe(game: str, include_bonus: bool = False) -> dict:
    rules = LOTTERY_GAME_RULES[game]
    payload = {
        "main_numbers": list(range(rules["main_min"], rules["main_max"] + 1)),
        "bonus_numbers": [],
    }
    if include_bonus and rules["has_bonus"] and rules["bonus_min"] and rules["bonus_max"]:
        payload["bonus_numbers"] = list(range(rules["bonus_min"], rules["bonus_max"] + 1))
    return payload


def load_lottery_draws(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    game_key = _normalize_game(game)
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    if start and end and start > end:
        raise ValueError("start_date cannot be after end_date")

    where = ["game = ?"]
    params: list = [game_key]
    if start:
        where.append("draw_date >= ?")
        params.append(start)
    if end:
        where.append("draw_date <= ?")
        params.append(end)

    with _connect(db_path) as con:
        rows = con.execute(
            f"""
            SELECT id, game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at
            FROM lottery_draws
            WHERE {" AND ".join(where)}
            ORDER BY draw_date ASC, id ASC
            """
            ,
            params,
        ).fetchall()

    rules = LOTTERY_GAME_RULES[game_key]
    main_keys = [f"n{i}" for i in range(1, rules["main_count"] + 1)]
    draws = []
    invalid_rows = 0
    for row in rows:
        raw_main = []
        valid = True
        for key in main_keys:
            value = row[key]
            if value is None:
                valid = False
                break
            raw_main.append(int(value))
        if not valid:
            invalid_rows += 1
            continue

        numbers = sorted(raw_main)
        bonus = int(row["bonus"]) if row["bonus"] is not None else None
        draws.append(
            {
                "id": row["id"],
                "game": row["game"],
                "draw_date": row["draw_date"],
                "numbers": numbers,
                "bonus": bonus,
                "source_file": row["source_file"],
                "imported_at": row["imported_at"],
            }
        )

    return {
        "game": game_key,
        "start_date": start,
        "end_date": end,
        "draws": draws,
        "invalid_rows_skipped": invalid_rows,
        "rules": rules,
    }


def _main_counter(draws: list[dict]) -> Counter:
    counter = Counter()
    for draw in draws:
        counter.update(draw["numbers"])
    return counter


def _bonus_counter(draws: list[dict]) -> Counter:
    counter = Counter()
    for draw in draws:
        if draw.get("bonus") is not None:
            counter[int(draw["bonus"])] += 1
    return counter


def _average_gap_by_number(draws: list[dict], universe: list[int]) -> dict[int, float | None]:
    occurrences = defaultdict(list)
    for idx, draw in enumerate(draws):
        for number in draw["numbers"]:
            occurrences[number].append(idx)

    output: dict[int, float | None] = {}
    for number in universe:
        idxs = occurrences.get(number, [])
        if len(idxs) < 2:
            output[number] = None
            continue
        gaps = [b - a for a, b in zip(idxs, idxs[1:])]
        output[number] = round(mean(gaps), 3) if gaps else None
    return output


def _percentile(values: list[float | int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0.0, min(100.0, float(pct))) / 100.0 * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def _rounded_metric(value: float | int | None, digits: int = 3) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), digits)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def _mean_stddev(values: list[float | int]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    numeric = [float(v) for v in values]
    n = len(numeric)
    avg = sum(numeric) / n
    if n < 2:
        return avg, 0.0
    variance = sum((v - avg) ** 2 for v in numeric) / (n - 1)
    return avg, math.sqrt(variance)


def _decade_bucket(number: int) -> str:
    if number <= 10:
        return "1-10"
    low = (number // 10) * 10
    return f"{low}-{low + 9}"


def compute_number_frequency(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    include_bonus: bool = False,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    universe = _number_universe(loaded["game"], include_bonus=include_bonus)
    main_counter = _main_counter(draws)
    avg_gap = _average_gap_by_number(draws, universe["main_numbers"])
    decade_counter = Counter()
    for number, count in main_counter.items():
        decade_counter[_decade_bucket(number)] += count

    main_frequency = [
        {
            "number": number,
            "count": int(main_counter.get(number, 0)),
            "avg_gap_draws": avg_gap.get(number),
        }
        for number in universe["main_numbers"]
    ]

    result = {
        "game": loaded["game"],
        "draw_count": len(draws),
        "start_date": loaded["start_date"],
        "end_date": loaded["end_date"],
        "main_number_frequency": sorted(main_frequency, key=lambda x: (x["number"])),
        "decade_groups": [
            {"bucket": bucket, "count": count}
            for bucket, count in sorted(decade_counter.items(), key=lambda item: item[0])
        ],
    }

    if include_bonus:
        bonus_counter = _bonus_counter(draws)
        result["bonus_frequency"] = [
            {"number": number, "count": int(bonus_counter.get(number, 0))}
            for number in universe["bonus_numbers"]
        ]

    return result


def compute_hot_cold_numbers(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 10,
    include_bonus: bool = False,
    db_path: str | None = None,
) -> dict:
    freq = compute_number_frequency(
        game,
        start_date=start_date,
        end_date=end_date,
        include_bonus=include_bonus,
        db_path=db_path,
    )
    main_rows = freq["main_number_frequency"]
    hot = sorted(main_rows, key=lambda row: (-row["count"], row["number"]))[:limit]
    cold = sorted(main_rows, key=lambda row: (row["count"], row["number"]))[:limit]
    return {
        "game": freq["game"],
        "draw_count": freq["draw_count"],
        "hot_numbers": hot,
        "cold_numbers": cold,
    }


def compute_overdue_numbers(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    include_bonus: bool = False,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    universe = _number_universe(loaded["game"], include_bonus=include_bonus)
    last_seen = {number: None for number in universe["main_numbers"]}
    for idx, draw in enumerate(draws):
        for number in draw["numbers"]:
            last_seen[number] = idx

    total = len(draws)
    overdue = []
    if total == 0:
        return {
            "game": loaded["game"],
            "draw_count": 0,
            "overdue": [],
        }
    for number in universe["main_numbers"]:
        idx = last_seen[number]
        if idx is None:
            since = total
            seen_date = None
        else:
            since = (total - 1) - idx
            seen_date = draws[idx]["draw_date"]
        overdue.append(
            {
                "number": number,
                "draws_since_seen": int(since),
                "last_seen_date": seen_date,
            }
        )

    result = {
        "game": loaded["game"],
        "draw_count": total,
        "overdue": sorted(overdue, key=lambda row: (-row["draws_since_seen"], row["number"])),
    }
    if include_bonus:
        bonus_last_seen = {}
        for idx, draw in enumerate(draws):
            if draw.get("bonus") is not None:
                bonus_last_seen[int(draw["bonus"])] = idx
        bonus_overdue = []
        for number in universe["bonus_numbers"]:
            idx = bonus_last_seen.get(number)
            if idx is None:
                since = total
                seen_date = None
            else:
                since = (total - 1) - idx
                seen_date = draws[idx]["draw_date"]
            bonus_overdue.append(
                {
                    "number": number,
                    "draws_since_seen": int(since),
                    "last_seen_date": seen_date,
                }
            )
        result["bonus_overdue"] = sorted(
            bonus_overdue, key=lambda row: (-row["draws_since_seen"], row["number"])
        )
    return result


def compute_pair_frequency(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 200,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    pair_counter = Counter()
    for draw in loaded["draws"]:
        for pair in itertools.combinations(draw["numbers"], 2):
            pair_counter[pair] += 1
    pairs = [
        {"pair": [int(a), int(b)], "count": int(count)}
        for (a, b), count in sorted(pair_counter.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "pairs": pairs[: max(1, min(int(limit or 200), 2000))],
    }


def compute_triplet_frequency(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 200,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    triplet_counter = Counter()
    for draw in loaded["draws"]:
        for triplet in itertools.combinations(draw["numbers"], 3):
            triplet_counter[triplet] += 1
    triplets = [
        {"triplet": [int(a), int(b), int(c)], "count": int(count)}
        for (a, b, c), count in sorted(triplet_counter.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "triplets": triplets[: max(1, min(int(limit or 200), 2000))],
    }


def compute_sum_distribution(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    sums = [sum(draw["numbers"]) for draw in loaded["draws"]]
    sum_counter = Counter(sums)
    summary = {
        "min_sum": min(sums) if sums else None,
        "max_sum": max(sums) if sums else None,
        "avg_sum": round(mean(sums), 3) if sums else None,
        "median_sum": median(sums) if sums else None,
    }
    distribution = [
        {"sum": int(value), "count": int(count)}
        for value, count in sorted(sum_counter.items(), key=lambda item: item[0])
    ]
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "summary": summary,
        "distribution": distribution,
    }


def compute_recommended_sum_range(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
    pct_low: float = 15.0,
    pct_high: float = 85.0,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    sums = [sum(draw["numbers"]) for draw in loaded["draws"]]
    p_low = _rounded_metric(_percentile(sums, pct_low), 3)
    p_high = _rounded_metric(_percentile(sums, pct_high), 3)
    coverage_pct = round(pct_high - pct_low, 1)
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "min_sum": min(sums) if sums else None,
        "max_sum": max(sums) if sums else None,
        "p_low": p_low,
        "p_high": p_high,
        "recommended_min": p_low,
        "recommended_max": p_high,
        "coverage_pct": coverage_pct,
    }


def compute_positional_distribution(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    rules = LOTTERY_GAME_RULES[loaded["game"]]
    positions = []
    for idx in range(rules["main_count"]):
        values = [draw["numbers"][idx] for draw in loaded["draws"] if len(draw["numbers"]) > idx]
        positions.append(
            {
                "position": idx + 1,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "mean": _rounded_metric(mean(values), 3) if values else None,
                "median": _rounded_metric(median(values), 3) if values else None,
                "p10": _rounded_metric(_percentile(values, 10), 3),
                "p25": _rounded_metric(_percentile(values, 25), 3),
                "p75": _rounded_metric(_percentile(values, 75), 3),
                "p90": _rounded_metric(_percentile(values, 90), 3),
            }
        )
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "positions": positions,
    }


def compute_rolling_frequency(
    game: str,
    window: int = 50,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    window_used = max(1, min(int(window or 50), len(draws))) if draws else 0
    recent_draws = draws[-window_used:] if window_used else []
    all_counter = _main_counter(draws)
    recent_counter = _main_counter(recent_draws)
    universe = _number_universe(loaded["game"])

    rows = []
    for number in universe["main_numbers"]:
        recent_count = int(recent_counter.get(number, 0))
        alltime_count = int(all_counter.get(number, 0))
        expected_recent = ((alltime_count / len(draws)) * window_used) if draws and window_used else 0.0
        z_score = None
        if expected_recent > 0 and window_used > 0:
            p = alltime_count / len(draws)
            binom_stddev = math.sqrt(window_used * p * (1.0 - p))
            if binom_stddev > 0:
                z_score = (recent_count - expected_recent) / binom_stddev
        trend = "neutral"
        if z_score is not None and z_score > 1.5:
            trend = "heating"
        elif z_score is not None and z_score < -1.5:
            trend = "cooling"
        rows.append(
            {
                "number": number,
                "recent_count": recent_count,
                "alltime_count": alltime_count,
                "expected_recent": _rounded_metric(expected_recent, 4),
                "z_score": _rounded_metric(z_score, 4) if z_score is not None else None,
                "trend": trend,
            }
        )

    # Chi-squared goodness-of-fit test: does the recent window deviate
    # from a uniform distribution across all numbers?
    chi2_stat = None
    chi2_p_value = None
    universe_size = len(universe["main_numbers"])
    total_recent_picks = sum(r["recent_count"] for r in rows)
    if total_recent_picks > 0 and universe_size > 1:
        expected_uniform = total_recent_picks / universe_size
        if expected_uniform > 0:
            chi2_stat = round(
                sum(
                    ((r["recent_count"] - expected_uniform) ** 2) / expected_uniform
                    for r in rows
                ),
                4,
            )
            # Degrees of freedom = universe_size - 1
            # Approximate p-value using the regularised incomplete gamma function
            # For large df, use Wilson-Hilferty normal approximation
            df = universe_size - 1
            if df > 0 and chi2_stat >= 0:
                try:
                    # scipy may not be available — use the approximation
                    z_wh = ((chi2_stat / df) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * df))) / math.sqrt(2.0 / (9.0 * df))
                    # Standard normal CDF approximation
                    chi2_p_value = round(0.5 * (1.0 + math.erf(-z_wh / math.sqrt(2.0))), 6)
                except (ValueError, ZeroDivisionError):
                    chi2_p_value = None

    return {
        "game": loaded["game"],
        "draw_count": len(draws),
        "window_used": window_used,
        "chi2_stat": chi2_stat,
        "chi2_p_value": chi2_p_value,
        "chi2_interpretation": (
            "Significant deviation from uniform (p < 0.05)" if chi2_p_value is not None and chi2_p_value < 0.05
            else "No significant deviation from uniform" if chi2_p_value is not None
            else None
        ),
        "numbers": sorted(
            rows,
            key=lambda row: (
                row["z_score"] is None,
                -(float(row["z_score"]) if row["z_score"] is not None else 0.0),
                row["number"],
            ),
        ),
    }


def compute_odd_even_distribution(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    pattern_counter = Counter()
    for draw in loaded["draws"]:
        odd = sum(1 for number in draw["numbers"] if number % 2 == 1)
        even = len(draw["numbers"]) - odd
        pattern_counter[f"{odd}:{even}"] += 1
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "patterns": [
            {"pattern": pattern, "count": int(count)}
            for pattern, count in sorted(pattern_counter.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def compute_low_high_distribution(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    max_number = LOTTERY_GAME_RULES[loaded["game"]]["main_max"]
    threshold = max_number // 2
    pattern_counter = Counter()
    for draw in loaded["draws"]:
        low = sum(1 for number in draw["numbers"] if number <= threshold)
        high = len(draw["numbers"]) - low
        pattern_counter[f"{low}:{high}"] += 1
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "threshold": threshold,
        "patterns": [
            {"pattern": pattern, "count": int(count)}
            for pattern, count in sorted(pattern_counter.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def compute_consecutive_stats(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    breakdown_counter = Counter()
    pair_total = 0
    with_consecutive = 0
    max_streak = 0

    for draw in loaded["draws"]:
        nums = draw["numbers"]
        pairs = 0
        current_streak = 1
        best_streak = 1
        for idx in range(1, len(nums)):
            if nums[idx] == nums[idx - 1] + 1:
                pairs += 1
                current_streak += 1
                best_streak = max(best_streak, current_streak)
            else:
                current_streak = 1
        if pairs > 0:
            with_consecutive += 1
        pair_total += pairs
        max_streak = max(max_streak, best_streak)
        breakdown_counter[pairs] += 1

    draw_count = len(loaded["draws"])
    return {
        "game": loaded["game"],
        "draw_count": draw_count,
        "draws_with_consecutive": with_consecutive,
        "draws_with_consecutive_pct": round((with_consecutive / draw_count) * 100, 2) if draw_count else 0.0,
        "average_consecutive_pairs": round(pair_total / draw_count, 4) if draw_count else 0.0,
        "max_consecutive_streak": max_streak if draw_count else 0,
        "pair_breakdown": [
            {"consecutive_pairs": int(key), "count": int(value)}
            for key, value in sorted(breakdown_counter.items(), key=lambda item: item[0])
        ],
    }


def compute_repeat_from_last_draw_stats(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    overlaps = []
    overlap_counter = Counter()
    for idx in range(1, len(draws)):
        prev_set = set(draws[idx - 1]["numbers"])
        cur_set = set(draws[idx]["numbers"])
        overlap_size = len(prev_set.intersection(cur_set))
        overlap_counter[overlap_size] += 1
        overlaps.append(
            {
                "draw_date": draws[idx]["draw_date"],
                "previous_draw_date": draws[idx - 1]["draw_date"],
                "repeat_count": overlap_size,
            }
        )

    return {
        "game": loaded["game"],
        "draw_count": len(draws),
        "comparisons": len(overlaps),
        "average_repeat_count": round(mean([x["repeat_count"] for x in overlaps]), 4) if overlaps else 0.0,
        "distribution": [
            {"repeat_count": int(k), "count": int(v)}
            for k, v in sorted(overlap_counter.items(), key=lambda item: item[0])
        ],
        "recent_overlap": overlaps[-20:],
    }


def compute_pair_lift(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    draw_count = len(draws)
    main_counter = _main_counter(draws)
    pair_counter = Counter()
    for draw in draws:
        for pair in itertools.combinations(draw["numbers"], 2):
            pair_counter[pair] += 1

    pairs = []
    for (a, b), observed in pair_counter.items():
        freq_a = int(main_counter.get(a, 0))
        freq_b = int(main_counter.get(b, 0))
        if freq_a < 5 or freq_b < 5 or draw_count <= 0:
            continue
        expected = (freq_a * freq_b) / draw_count
        if expected <= 0:
            continue
        pairs.append(
            {
                "pair": [int(a), int(b)],
                "observed": int(observed),
                "freq_a": freq_a,
                "freq_b": freq_b,
                "expected": _rounded_metric(expected, 4),
                "lift": _rounded_metric(observed / expected, 4),
                "confidence_ab": _rounded_metric(observed / freq_a, 4),
                "confidence_ba": _rounded_metric(observed / freq_b, 4),
            }
        )

    return {
        "game": loaded["game"],
        "draw_count": draw_count,
        "pairs": sorted(
            pairs,
            key=lambda row: (-float(row["lift"]), -int(row["observed"]), row["pair"]),
        )[: max(1, min(int(limit or 100), 2000))],
    }


def flag_anomalous_draws(
    game: str,
    z_threshold: float = 2.5,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    rules = LOTTERY_GAME_RULES[loaded["game"]]
    threshold = float(z_threshold or 2.5)
    low_cutoff = rules["main_max"] // 2

    metric_rows = []
    for draw in draws:
        numbers = draw["numbers"]
        odd_count = sum(1 for number in numbers if number % 2 == 1)
        low_count = sum(1 for number in numbers if number <= low_cutoff)
        metric_rows.append(
            {
                "draw_sum": sum(numbers),
                "odd_ratio": odd_count / rules["main_count"],
                "low_ratio": low_count / rules["main_count"],
                "spread": max(numbers) - min(numbers),
            }
        )

    metric_stats = {}
    for metric in ("draw_sum", "odd_ratio", "low_ratio", "spread"):
        avg, stddev = _mean_stddev([row[metric] for row in metric_rows])
        metric_stats[metric] = {
            "mean": _rounded_metric(avg, 4),
            "stddev": _rounded_metric(stddev, 4),
        }

    anomalous = []
    for draw, metrics in zip(draws, metric_rows):
        z_scores = {}
        flags = []
        for metric, value in metrics.items():
            stat = metric_stats[metric]
            stddev = stat["stddev"]
            z_score = 0.0
            if stddev not in (None, 0):
                z_score = (float(value) - float(stat["mean"])) / float(stddev)
            z_scores[metric] = _rounded_metric(z_score, 4)
            if abs(float(z_scores[metric])) >= threshold:
                flags.append(metric)
        if flags:
            anomalous.append(
                {
                    "draw_date": draw["draw_date"],
                    "numbers": draw["numbers"],
                    "bonus": draw["bonus"],
                    "metrics": {key: _rounded_metric(val, 4) for key, val in metrics.items()},
                    "z_scores": z_scores,
                    "flags": flags,
                }
            )

    return {
        "game": loaded["game"],
        "draw_count": len(draws),
        "z_threshold": threshold,
        "anomalous_count": len(anomalous),
        "anomalous_draws": anomalous,
        "metric_stats": metric_stats,
    }


def compute_bonus_intelligence(
    game: str,
    window: int = 50,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    game_key = _normalize_game(game)
    rules = LOTTERY_GAME_RULES[game_key]
    if not rules["has_bonus"]:
        return {"game": game_key, "has_bonus": False}

    loaded = load_lottery_draws(
        game_key,
        start_date=start_date,
        end_date=end_date,
        db_path=db_path,
    )
    draws = loaded["draws"]
    bonus_draws = [d for d in draws if d.get("bonus") is not None]
    total = len(bonus_draws)
    if not bonus_draws:
        return {"game": game_key, "has_bonus": True, "draw_count": 0}

    bonus_universe = list(range(rules["bonus_min"], rules["bonus_max"] + 1))
    freq_counter = Counter()
    for draw in bonus_draws:
        freq_counter[int(draw["bonus"])] += 1

    last_seen = {n: None for n in bonus_universe}
    for idx, draw in enumerate(bonus_draws):
        last_seen[int(draw["bonus"])] = idx

    overdue = []
    overdue_map = {}
    for number in bonus_universe:
        idx = last_seen[number]
        since = total if idx is None else (total - 1 - idx)
        seen_date = bonus_draws[idx]["draw_date"] if idx is not None else None
        row = {
            "number": number,
            "draws_since_seen": since,
            "last_seen_date": seen_date,
            "count": int(freq_counter.get(number, 0)),
        }
        overdue.append(row)
        overdue_map[number] = row
    overdue_sorted = sorted(overdue, key=lambda x: (-x["draws_since_seen"], x["number"]))

    window_draws = bonus_draws[-max(1, min(int(window or 50), total)):]
    window_counter = Counter()
    for draw in window_draws:
        window_counter[int(draw["bonus"])] += 1

    rolling = []
    for number in bonus_universe:
        alltime_count = int(freq_counter.get(number, 0))
        recent_count = int(window_counter.get(number, 0))
        expected = (alltime_count / total) * len(window_draws) if total > 0 else 0
        p_hat = alltime_count / total if total > 0 else 0
        binom_std = math.sqrt(len(window_draws) * p_hat * (1.0 - p_hat)) if len(window_draws) > 0 and 0 < p_hat < 1 else (expected ** 0.5 if expected > 0 else 0)
        z_score = round((recent_count - expected) / binom_std, 3) if binom_std > 0 else None
        trend = "heating" if z_score is not None and z_score > 1.5 else "cooling" if z_score is not None and z_score < -1.5 else "neutral"
        rolling.append(
            {
                "number": number,
                "recent_count": recent_count,
                "alltime_count": alltime_count,
                "expected_recent": round(expected, 3),
                "z_score": z_score,
                "trend": trend,
                "draws_since_seen": overdue_map[number]["draws_since_seen"],
            }
        )
    rolling_sorted = sorted(rolling, key=lambda x: (-(x["z_score"] or 0), x["number"]))

    max_overdue = max(x["draws_since_seen"] for x in overdue) or 1
    recommendations = []
    for item in rolling:
        number = item["number"]
        overdue_score = overdue_map[number]["draws_since_seen"] / max_overdue
        z_score = item["z_score"] or 0
        z_score_bonus = 0.3 if 0.5 <= z_score <= 2.0 else 0.1 if 0 < z_score <= 0.5 else 0
        penalty = 0.2 if z_score > 3.0 else 0
        score = round((overdue_score * 0.7) + z_score_bonus - penalty, 4)
        recommendations.append(
            {
                "number": number,
                "score": score,
                "z_score": z_score,
                "draws_since_seen": overdue_map[number]["draws_since_seen"],
            }
        )
    top_picks = sorted(recommendations, key=lambda x: (-x["score"], x["number"]))[:5]

    return {
        "game": game_key,
        "has_bonus": True,
        "draw_count": total,
        "window_used": len(window_draws),
        "bonus_min": rules["bonus_min"],
        "bonus_max": rules["bonus_max"],
        "frequency": [
            {"number": number, "count": int(freq_counter.get(number, 0))}
            for number in bonus_universe
        ],
        "overdue": overdue_sorted,
        "rolling": rolling_sorted,
        "top_picks": top_picks,
        "most_recent_bonus": int(bonus_draws[-1]["bonus"]) if bonus_draws else None,
        "last_draw_date": bonus_draws[-1]["draw_date"] if bonus_draws else None,
    }


def _safe_hot_cold(game: str, start_date: str | None, end_date: str | None, include_bonus: bool, db_path: str | None) -> dict:
    try:
        return compute_hot_cold_numbers(
            game,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            db_path=db_path,
        )
    except Exception:
        return {"hot_numbers": [], "cold_numbers": []}


def build_lottery_dashboard(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    include_bonus: bool = False,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    rules = LOTTERY_GAME_RULES[loaded["game"]]
    freq = compute_number_frequency(
        loaded["game"],
        start_date=start_date,
        end_date=end_date,
        include_bonus=include_bonus,
        db_path=db_path,
    )
    hot_cold = _safe_hot_cold(loaded["game"], start_date, end_date, include_bonus, db_path)
    overdue = compute_overdue_numbers(
        loaded["game"],
        start_date=start_date,
        end_date=end_date,
        include_bonus=include_bonus,
        db_path=db_path,
    )
    odd_even = compute_odd_even_distribution(loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path)
    low_high = compute_low_high_distribution(loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path)
    sums = compute_sum_distribution(loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path)
    recommended_sum_range = compute_recommended_sum_range(
        loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path
    )
    positional_distribution = compute_positional_distribution(
        loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path
    )
    rolling_frequency = compute_rolling_frequency(
        loaded["game"], window=50, start_date=start_date, end_date=end_date, db_path=db_path
    )
    consecutive = compute_consecutive_stats(loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path)
    repeat_stats = compute_repeat_from_last_draw_stats(loaded["game"], start_date=start_date, end_date=end_date, db_path=db_path)

    dashboard = {
        "game": loaded["game"],
        "filters": {
            "start_date": loaded["start_date"],
            "end_date": loaded["end_date"],
            "include_bonus": bool(include_bonus),
        },
        "rules": {
            "main_count": rules["main_count"],
            "has_bonus": rules["has_bonus"],
            "main_min": rules["main_min"],
            "main_max": rules["main_max"],
            "bonus_min": rules["bonus_min"],
            "bonus_max": rules["bonus_max"],
        },
        "total_draws": len(draws),
        "first_draw_date": draws[0]["draw_date"] if draws else None,
        "last_draw_date": draws[-1]["draw_date"] if draws else None,
        "hot_numbers": hot_cold.get("hot_numbers", []),
        "cold_numbers": hot_cold.get("cold_numbers", []),
        "overdue_numbers": overdue.get("overdue", [])[:15],
        "number_frequency": freq["main_number_frequency"],
        "odd_even_distribution": odd_even["patterns"],
        "low_high_distribution": low_high["patterns"],
        "sum_distribution_summary": sums["summary"],
        "sum_distribution": sums["distribution"],
        "recommended_sum_range": {
            "min": recommended_sum_range["recommended_min"],
            "max": recommended_sum_range["recommended_max"],
            "coverage_pct": 70,
        },
        "positional_distribution": positional_distribution["positions"],
        "rolling_frequency": rolling_frequency["numbers"],
        "consecutive_summary": {
            "draws_with_consecutive": consecutive["draws_with_consecutive"],
            "draws_with_consecutive_pct": consecutive["draws_with_consecutive_pct"],
            "average_consecutive_pairs": consecutive["average_consecutive_pairs"],
            "max_consecutive_streak": consecutive["max_consecutive_streak"],
            "pair_breakdown": consecutive["pair_breakdown"],
        },
        "repeat_from_last_draw_summary": {
            "comparisons": repeat_stats["comparisons"],
            "average_repeat_count": repeat_stats["average_repeat_count"],
            "distribution": repeat_stats["distribution"],
            "recent_overlap": repeat_stats["recent_overlap"],
        },
        "decade_groups": freq["decade_groups"],
        "invalid_rows_skipped": loaded["invalid_rows_skipped"],
    }

    if include_bonus:
        dashboard["bonus_frequency"] = freq.get("bonus_frequency", [])
        dashboard["bonus_overdue"] = overdue.get("bonus_overdue", [])
    return dashboard


def history_rows(
    game: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 300,
    db_path: str | None = None,
) -> dict:
    loaded = load_lottery_draws(game, start_date=start_date, end_date=end_date, db_path=db_path)
    rows = []
    for draw in loaded["draws"][-max(1, min(int(limit or 300), 3000)):]:
        row = {
            "draw_date": draw["draw_date"],
            "numbers": draw["numbers"],
            "bonus": draw["bonus"],
            "source_file": draw["source_file"],
            "imported_at": draw["imported_at"],
        }
        rows.append(row)
    return {
        "game": loaded["game"],
        "draw_count": len(loaded["draws"]),
        "history": list(reversed(rows)),
    }


def _parse_target_pair(value) -> tuple[int, int] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None
    if isinstance(value, dict):
        odd = value.get("odd")
        even = value.get("even")
        if odd is None or even is None:
            return None
        try:
            return int(odd), int(even)
        except (TypeError, ValueError):
            return None
    return None


def _consecutive_pairs(numbers: list[int]) -> int:
    pairs = 0
    for idx in range(1, len(numbers)):
        if numbers[idx] == numbers[idx - 1] + 1:
            pairs += 1
    return pairs


def _max_consecutive_run(numbers: list[int]) -> int:
    if not numbers:
        return 0
    best = 1
    cur = 1
    for idx in range(1, len(numbers)):
        if numbers[idx] == numbers[idx - 1] + 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def _weighted_sample_without_replacement(population: list[int], weights: list[float], k: int) -> list[int]:
    pool = list(population)
    w = list(weights)
    picks = []
    for _ in range(min(k, len(pool))):
        total = sum(max(0.0, x) for x in w)
        if total <= 0:
            idx = random.randrange(len(pool))
        else:
            r = random.uniform(0.0, total)
            acc = 0.0
            idx = len(pool) - 1
            for i, val in enumerate(w):
                acc += max(0.0, val)
                if acc > r:
                    idx = i
                    break
        picks.append(pool.pop(idx))
        w.pop(idx)
    return picks


def _pair_strength(ticket_numbers: list[int], pair_counts: dict[tuple[int, int], int]) -> float:
    pairs = list(itertools.combinations(sorted(ticket_numbers), 2))
    if not pairs:
        return 0.0
    score = sum(pair_counts.get(tuple(pair), 0) for pair in pairs)
    return round(score / len(pairs), 4)


def _ticket_matches_constraints(ticket: dict, rules: dict, filters: dict | None, recent_draw_numbers: list[int]) -> bool:
    if not filters:
        return True
    numbers = sorted(ticket.get("numbers", []))
    if len(numbers) != rules["main_count"]:
        return False

    odd_target = _parse_target_pair(filters.get("odd_even_target"))
    if odd_target:
        odd = sum(1 for x in numbers if x % 2 == 1)
        even = len(numbers) - odd
        if (odd, even) != odd_target:
            return False

    low_high_target = _parse_target_pair(filters.get("low_high_target"))
    if low_high_target:
        threshold = rules["main_max"] // 2
        low = sum(1 for x in numbers if x <= threshold)
        high = len(numbers) - low
        if (low, high) != low_high_target:
            return False

    min_sum = filters.get("min_sum")
    max_sum = filters.get("max_sum")
    total = sum(numbers)
    if min_sum is not None:
        try:
            if total < int(min_sum):
                return False
        except (TypeError, ValueError):
            pass
    if max_sum is not None:
        try:
            if total > int(max_sum):
                return False
        except (TypeError, ValueError):
            pass

    max_run = filters.get("max_consecutive_run")
    if max_run is not None:
        try:
            if _max_consecutive_run(numbers) > int(max_run):
                return False
        except (TypeError, ValueError):
            pass

    overlap_cap = filters.get("avoid_recent_overlap_above")
    if overlap_cap is not None and recent_draw_numbers:
        try:
            if len(set(numbers).intersection(recent_draw_numbers)) > int(overlap_cap):
                return False
        except (TypeError, ValueError):
            pass

    if bool(filters.get("enforce_positional_bands")):
        positional_bands = filters.get("positional_bands")
        if isinstance(positional_bands, list):
            for idx, number in enumerate(numbers):
                if idx >= len(positional_bands):
                    break
                band = positional_bands[idx]
                if not isinstance(band, dict):
                    continue
                band_min = band.get("min")
                band_max = band.get("max")
                try:
                    if band_min is not None and number < int(band_min):
                        return False
                    if band_max is not None and number > int(band_max):
                        return False
                except (TypeError, ValueError):
                    continue
    return True


def _mode_weights(
    game: str,
    numbers: list[int],
    mode: str,
    hot_counts: dict[int, int],
    overdue_counts: dict[int, int],
) -> list[float]:
    if mode == "pure_random":
        return [1.0 for _ in numbers]
    if mode == "hot_bias":
        return [1.0 + float(hot_counts.get(n, 0)) for n in numbers]
    if mode == "cold_bias":
        max_count = max((hot_counts.get(n, 0) for n in numbers), default=0)
        return [1.0 + float(max_count - hot_counts.get(n, 0)) for n in numbers]
    if mode == "overdue_bias":
        return [1.0 + float(overdue_counts.get(n, 0)) for n in numbers]
    if mode == "anti_crowd":
        max_count = max((hot_counts.get(n, 0) for n in numbers), default=0)
        return [1.0 + float(max_count - hot_counts.get(n, 0)) + float(overdue_counts.get(n, 0) * 0.2) for n in numbers]
    return [1.0 for _ in numbers]


def _choose_ticket_numbers(
    game: str,
    mode: str,
    universe: list[int],
    count: int,
    hot_counts: dict[int, int],
    overdue_counts: dict[int, int],
    top_pairs: list[tuple[int, int]],
) -> list[int]:
    if mode == "pair_bias" and top_pairs:
        pair = random.choice(top_pairs[: min(20, len(top_pairs))])
        remaining = [n for n in universe if n not in pair]
        need = max(0, count - 2)
        picks = list(pair) + random.sample(remaining, need)
        return sorted(picks)

    if mode == "balanced_mix":
        sorted_hot = sorted(universe, key=lambda n: (-hot_counts.get(n, 0), n))
        sorted_cold = sorted(universe, key=lambda n: (hot_counts.get(n, 0), n))
        half = max(1, count // 2)
        hot_pool = sorted_hot[:half * 2]
        cold_pool = [n for n in sorted_cold if n not in set(hot_pool)][:half * 2]
        hot_pick = random.sample(hot_pool, min(half, len(hot_pool)))
        need_cold = count - len(hot_pick)
        cold_pick = random.sample(cold_pool, min(need_cold, len(cold_pool))) if cold_pool else []
        base = list(dict.fromkeys(hot_pick + cold_pick))
        remaining = [n for n in universe if n not in set(base)]
        while len(base) < count and remaining:
            pick = random.choice(remaining)
            base.append(pick)
            remaining.remove(pick)
        return sorted(base[:count])

    weights = _mode_weights(game, universe, mode, hot_counts, overdue_counts)
    return sorted(_weighted_sample_without_replacement(universe, weights, count))


def _bonus_value_for_mode(game: str, mode: str, include_bonus: bool, draw_data: dict) -> int | None:
    rules = LOTTERY_GAME_RULES[game]
    if not include_bonus or not rules["has_bonus"]:
        return None
    bonus_numbers = _number_universe(game, include_bonus=True)["bonus_numbers"]
    if not bonus_numbers:
        return None

    bonus_freq = draw_data.get("bonus_frequency", [])
    bonus_overdue = draw_data.get("bonus_overdue", [])
    freq_map = {int(x["number"]): int(x["count"]) for x in bonus_freq}
    over_map = {int(x["number"]): int(x["draws_since_seen"]) for x in bonus_overdue}
    if mode == "hot_bias":
        weights = [1.0 + freq_map.get(n, 0) for n in bonus_numbers]
    elif mode in ("cold_bias", "anti_crowd"):
        max_count = max((freq_map.get(n, 0) for n in bonus_numbers), default=0)
        weights = [1.0 + (max_count - freq_map.get(n, 0)) for n in bonus_numbers]
    elif mode == "overdue_bias":
        weights = [1.0 + over_map.get(n, 0) for n in bonus_numbers]
    else:
        weights = [1.0 for _ in bonus_numbers]
    pick = _weighted_sample_without_replacement(bonus_numbers, weights, 1)
    return int(pick[0]) if pick else None


def _label_ticket(
    ticket_stats: dict,
    main_count: int,
) -> list[str]:
    labels = []
    odd = ticket_stats["odd_count"]
    low = ticket_stats["low_count"]
    hot = ticket_stats["hot_count"]
    cold = ticket_stats["cold_count"]
    overdue = ticket_stats["overdue_count"]
    pair_strength = ticket_stats["pair_strength"]
    overlap = ticket_stats["recent_overlap"]
    anti_crowd_score = ticket_stats["anti_crowd_score"]

    if abs(odd - (main_count - odd)) <= 1 and abs(low - (main_count - low)) <= 1:
        labels.append("Balanced")
    if hot >= max(2, (main_count // 2) + 1):
        labels.append("Hot-heavy")
    if cold >= max(2, (main_count // 2) + 1):
        labels.append("Cold-heavy")
    if overdue >= max(2, (main_count // 2) + 1):
        labels.append("Overdue-heavy")
    if pair_strength >= 1.5:
        labels.append("Pair-clustered")
    if anti_crowd_score >= 70:
        labels.append("Anti-crowd")
    if overlap <= 1:
        labels.append("Recent-overlap-low")
    if overlap >= max(2, main_count // 2):
        labels.append("Recent-overlap-high")
    return labels or ["Balanced"]


def _build_ticket_scoring_context(
    game: str,
    include_bonus: bool,
    freq_payload: dict,
    overdue_payload: dict,
    pair_payload: dict,
    recent_draw_numbers: set[int] | list[int] | None,
) -> dict:
    rules = LOTTERY_GAME_RULES[game]
    main_freq = list(freq_payload.get("main_number_frequency") or [])
    overdue = list(overdue_payload.get("overdue") or [])
    pairs = list(pair_payload.get("pairs") or [])

    main_freq_map = {int(x["number"]): int(x["count"]) for x in main_freq}
    sorted_main = sorted(main_freq, key=lambda x: (-int(x["count"]), int(x["number"])))
    hot_set = {int(x["number"]) for x in sorted_main[: max(6, rules["main_count"] * 2)]}
    cold_set = {int(x["number"]) for x in sorted_main[-max(6, rules["main_count"] * 2):]}
    overdue_map = {int(x["number"]): int(x["draws_since_seen"]) for x in overdue}
    sorted_overdue = sorted(overdue, key=lambda x: (-int(x["draws_since_seen"]), int(x["number"])))
    overdue_set = {int(x["number"]) for x in sorted_overdue[: max(6, rules["main_count"] * 2)]}
    pair_map = {tuple(int(v) for v in x["pair"]): int(x["count"]) for x in pairs}

    return {
        "game": game,
        "include_bonus": bool(include_bonus and rules["has_bonus"]),
        "main_freq_map": main_freq_map,
        "hot_set": hot_set,
        "cold_set": cold_set,
        "overdue_map": overdue_map,
        "overdue_set": overdue_set,
        "pair_map": pair_map,
        "recent_draw_numbers": set(int(x) for x in (recent_draw_numbers or [])),
    }


def _score_ticket_from_context(
    game: str,
    ticket: dict | list[int],
    scoring_context: dict,
) -> dict:
    rules = LOTTERY_GAME_RULES[game]
    if isinstance(ticket, list):
        ticket_obj = {"numbers": ticket}
    else:
        ticket_obj = ticket or {}

    numbers = sorted([int(x) for x in ticket_obj.get("numbers", [])])
    if len(numbers) != rules["main_count"]:
        raise ValueError(f"Ticket must contain exactly {rules['main_count']} main numbers")

    main_freq_map = scoring_context["main_freq_map"]
    hot_set = scoring_context["hot_set"]
    cold_set = scoring_context["cold_set"]
    overdue_set = scoring_context["overdue_set"]
    pair_map = scoring_context["pair_map"]
    last_draw_numbers = scoring_context["recent_draw_numbers"]

    odd = sum(1 for x in numbers if x % 2 == 1)
    even = len(numbers) - odd
    threshold = rules["main_max"] // 2
    low = sum(1 for x in numbers if x <= threshold)
    high = len(numbers) - low
    consecutive = _consecutive_pairs(numbers)
    hot_count = sum(1 for x in numbers if x in hot_set)
    cold_count = sum(1 for x in numbers if x in cold_set)
    overdue_count = sum(1 for x in numbers if x in overdue_set)
    recent_overlap = len(set(numbers).intersection(last_draw_numbers))
    pair_strength = _pair_strength(numbers, pair_map)

    rarity_score = 0.0
    if numbers:
        max_freq = max((main_freq_map.get(n, 0) for n in main_freq_map), default=1)
        for n in numbers:
            rarity_score += max_freq - main_freq_map.get(n, 0)
        rarity_score = rarity_score / len(numbers)
    anti_crowd_score = round(min(100.0, max(0.0, rarity_score * 5 + (2 - recent_overlap) * 12 + overdue_count * 3)), 2)

    # Coverage: how much of the number range the ticket spans (spread / max possible spread)
    spread = max(numbers) - min(numbers) if len(numbers) >= 2 else 0
    max_possible_spread = rules["main_max"] - rules["main_min"]
    spread_ratio = round(spread / max_possible_spread, 4) if max_possible_spread > 0 else 0.0

    score = {
        "game": game,
        "numbers": numbers,
        "bonus": ticket_obj.get("bonus"),
        "sum": sum(numbers),
        "odd_count": odd,
        "even_count": even,
        "low_count": low,
        "high_count": high,
        "consecutive_count": consecutive,
        "hot_count": hot_count,
        "cold_count": cold_count,
        "overdue_count": overdue_count,
        "pair_strength": pair_strength,
        "recent_overlap": recent_overlap,
        "anti_crowd_score": anti_crowd_score,
        "spread": spread,
        "spread_ratio": spread_ratio,
    }
    score["labels"] = _label_ticket(score, rules["main_count"])
    return score


def _generate_tickets_with_signals(
    game_key: str,
    mode_key: str,
    count: int,
    include_bonus: bool,
    filters: dict | None,
    freq_payload: dict,
    overdue_payload: dict,
    pair_payload: dict,
    recent_draw_numbers: set[int] | list[int] | None,
) -> dict:
    rules = LOTTERY_GAME_RULES[game_key]
    universe = _number_universe(game_key, include_bonus=include_bonus)
    main_numbers = universe["main_numbers"]
    hot_counts = {int(x["number"]): int(x["count"]) for x in freq_payload["main_number_frequency"]}
    overdue_counts = {int(x["number"]): int(x["draws_since_seen"]) for x in overdue_payload["overdue"]}
    top_pairs = [tuple(int(v) for v in x["pair"]) for x in pair_payload["pairs"]]
    scoring_context = _build_ticket_scoring_context(
        game_key,
        include_bonus=include_bonus,
        freq_payload=freq_payload,
        overdue_payload=overdue_payload,
        pair_payload=pair_payload,
        recent_draw_numbers=recent_draw_numbers,
    )

    tickets = []
    existing_keys = set()
    per_ticket_retry_cap = max(25, min(400, len(main_numbers) * 4))
    fallback_used = 0
    recent_numbers_list = list(recent_draw_numbers or [])
    bonus_draw_data = {
        "bonus_frequency": freq_payload.get("bonus_frequency", []),
        "bonus_overdue": overdue_payload.get("bonus_overdue", []),
    }

    def _unique_ticket_for_mode(mode_name: str) -> dict:
        nums = _choose_ticket_numbers(
            game_key,
            mode_name,
            main_numbers,
            rules["main_count"],
            hot_counts,
            overdue_counts,
            top_pairs,
        )
        return {
            "numbers": nums,
            "bonus": _bonus_value_for_mode(
                game_key,
                mode_name,
                include_bonus=include_bonus,
                draw_data=bonus_draw_data,
            ),
        }

    def _fallback_ticket() -> dict:
        for _ in range(max(20, len(main_numbers) * 2)):
            ticket = {
                "numbers": sorted(random.sample(main_numbers, rules["main_count"])),
                "bonus": _bonus_value_for_mode(game_key, "pure_random", include_bonus, {}),
            }
            key = (tuple(ticket["numbers"]), ticket.get("bonus"))
            if key not in existing_keys:
                return ticket
        return {
            "numbers": sorted(random.sample(main_numbers, rules["main_count"])),
            "bonus": _bonus_value_for_mode(game_key, "pure_random", include_bonus, {}),
        }

    while len(tickets) < count:
        ticket = None
        for attempt in range(1, per_ticket_retry_cap + 1):
            candidate = _unique_ticket_for_mode(mode_key)
            key = (tuple(candidate["numbers"]), candidate.get("bonus"))
            if key in existing_keys:
                continue
            if _ticket_matches_constraints(candidate, rules, filters, recent_numbers_list):
                ticket = candidate
                break
        if ticket is None:
            fallback_used += 1
            ticket = _fallback_ticket()
            log.warning(
                "[LotteryGen] Retry cap hit; using fallback ticket game=%s mode=%s index=%s filters=%s",
                game_key,
                mode_key,
                len(tickets) + 1,
                filters or {},
            )
        key = (tuple(ticket["numbers"]), ticket.get("bonus"))
        existing_keys.add(key)
        tickets.append(ticket)

    scored = []
    for idx, ticket in enumerate(tickets, start=1):
        scored_ticket = _score_ticket_from_context(game_key, ticket, scoring_context)
        scored_ticket["ticket_id"] = idx
        scored.append(scored_ticket)

    return {
        "game": game_key,
        "mode": mode_key,
        "ticket_count": len(scored),
        "include_bonus": bool(include_bonus and rules["has_bonus"]),
        "tickets": scored,
        "filters": filters or {},
        "fallback_tickets_used": fallback_used,
        "per_ticket_retry_cap": per_ticket_retry_cap,
    }


def score_ticket(
    game: str,
    ticket: dict | list[int],
    include_bonus: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: str | None = None,
) -> dict:
    game_key = _normalize_game(game)
    rules = LOTTERY_GAME_RULES[game_key]
    if isinstance(ticket, list):
        ticket_obj = {"numbers": ticket}
    else:
        ticket_obj = ticket or {}
    if len(sorted([int(x) for x in ticket_obj.get("numbers", [])])) != rules["main_count"]:
        raise ValueError(f"Ticket must contain exactly {rules['main_count']} main numbers")

    freq_payload = compute_number_frequency(
        game_key,
        start_date=start_date,
        end_date=end_date,
        include_bonus=include_bonus,
        db_path=db_path,
    )
    overdue_payload = compute_overdue_numbers(
        game_key,
        start_date=start_date,
        end_date=end_date,
        include_bonus=include_bonus,
        db_path=db_path,
    )
    pair_payload = compute_pair_frequency(
        game_key,
        start_date=start_date,
        end_date=end_date,
        limit=500,
        db_path=db_path,
    )
    history_payload = history_rows(
        game_key,
        start_date=start_date,
        end_date=end_date,
        limit=1,
        db_path=db_path,
    )
    scoring_context = _build_ticket_scoring_context(
        game_key,
        include_bonus=include_bonus,
        freq_payload=freq_payload,
        overdue_payload=overdue_payload,
        pair_payload=pair_payload,
        recent_draw_numbers=(history_payload["history"][0]["numbers"] if history_payload.get("history") else []),
    )
    return _score_ticket_from_context(game_key, ticket_obj, scoring_context)


def generate_tickets(
    game: str,
    mode: str,
    ticket_count: int,
    include_bonus: bool = False,
    filters: dict | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    draw_context: list[dict] | None = None,
    db_path: str | None = None,
) -> dict:
    game_key = _normalize_game(game)
    mode_key = str(mode or "pure_random").strip().lower()
    valid_modes = {
        "pure_random",
        "hot_bias",
        "cold_bias",
        "overdue_bias",
        "balanced_mix",
        "pair_bias",
        "anti_crowd",
    }
    if mode_key not in valid_modes:
        raise ValueError(f"Unsupported generator mode: {mode}")
    count = max(1, min(int(ticket_count or 1), 200))
    rules = LOTTERY_GAME_RULES[game_key]
    universe = _number_universe(game_key, include_bonus=include_bonus)
    main_numbers = universe["main_numbers"]

    if draw_context is None:
        loaded = load_lottery_draws(game_key, start_date=start_date, end_date=end_date, db_path=db_path)
        context_draws = loaded["draws"]
    else:
        context_draws = draw_context
    recent_draw_numbers = set(context_draws[-1]["numbers"]) if context_draws else set()

    # Use phase-2 analytics as source signals for weighted generation.
    # In simulation context, derive signals from past draws only to avoid look-ahead.
    if draw_context is not None:
        main_counter = _main_counter(context_draws)
        freq_payload = {
            "main_number_frequency": [
                {"number": n, "count": int(main_counter.get(n, 0))}
                for n in main_numbers
            ],
            "bonus_frequency": [],
        }
        if include_bonus and rules["has_bonus"]:
            b_counter = _bonus_counter(context_draws)
            freq_payload["bonus_frequency"] = [
                {"number": n, "count": int(b_counter.get(n, 0))}
                for n in universe["bonus_numbers"]
            ]

        last_seen = {n: None for n in main_numbers}
        for idx, dr in enumerate(context_draws):
            for n in dr["numbers"]:
                last_seen[n] = idx
        overdue_payload = {
            "overdue": [
                {
                    "number": n,
                    "draws_since_seen": (len(context_draws) if last_seen[n] is None else (len(context_draws) - 1 - last_seen[n])),
                }
                for n in main_numbers
            ],
            "bonus_overdue": [],
        }
        if include_bonus and rules["has_bonus"]:
            b_last = {n: None for n in universe["bonus_numbers"]}
            for idx, dr in enumerate(context_draws):
                if dr.get("bonus") is not None:
                    b_last[int(dr["bonus"])] = idx
            overdue_payload["bonus_overdue"] = [
                {
                    "number": n,
                    "draws_since_seen": (len(context_draws) if b_last[n] is None else (len(context_draws) - 1 - b_last[n])),
                }
                for n in universe["bonus_numbers"]
            ]

        p_counter = Counter()
        for dr in context_draws:
            for p in itertools.combinations(dr["numbers"], 2):
                p_counter[p] += 1
        pair_payload = {
            "pairs": [
                {"pair": [int(a), int(b)], "count": int(cnt)}
                for (a, b), cnt in sorted(p_counter.items(), key=lambda item: (-item[1], item[0]))[:200]
            ]
        }
    else:
        freq_payload = compute_number_frequency(
            game_key,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            db_path=db_path,
        )
        overdue_payload = compute_overdue_numbers(
            game_key,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            db_path=db_path,
        )
        pair_payload = compute_pair_frequency(
            game_key,
            start_date=start_date,
            end_date=end_date,
            limit=200,
            db_path=db_path,
        )
    return _generate_tickets_with_signals(
        game_key,
        mode_key=mode_key,
        count=count,
        include_bonus=include_bonus,
        filters=filters,
        freq_payload=freq_payload,
        overdue_payload=overdue_payload,
        pair_payload=pair_payload,
        recent_draw_numbers=recent_draw_numbers,
    )


def _default_hit_key(main_hits: int, bonus_hit: bool) -> str:
    return f"{main_hits}+B" if bonus_hit else str(main_hits)


def simulate_generator(
    game: str,
    mode: str,
    tickets_per_draw: int,
    start_date: str | None = None,
    end_date: str | None = None,
    include_bonus: bool = False,
    ticket_cost: float | None = None,
    payout_table: dict | None = None,
    filters: dict | None = None,
    db_path: str | None = None,
    seed: int | None = None,
) -> dict:
    started_at = time.perf_counter()
    if seed is not None:
        random.seed(seed)
    game_key = _normalize_game(game)
    log.info("[LotterySim] request received game=%s mode=%s tickets_per_draw=%s start=%s end=%s include_bonus=%s",
             game_key, mode, tickets_per_draw, start_date, end_date, include_bonus)
    load_started = time.perf_counter()
    loaded = load_lottery_draws(game_key, start_date=start_date, end_date=end_date, db_path=db_path)
    draws = loaded["draws"]
    log.info(
        "[LotterySim] draw history loaded game=%s draws=%s elapsed_ms=%.1f",
        game_key,
        len(draws),
        (time.perf_counter() - load_started) * 1000.0,
    )
    if not draws:
        return {
            "game": game_key,
            "mode": mode,
            "draws_simulated": 0,
            "tickets_generated": 0,
            "total_cost": 0.0,
            "total_return": 0.0,
            "roi": None,
            "hit_distribution": {},
            "best_hit": {"main_hits": 0, "bonus_hit": False, "ticket": None, "draw_date": None},
            "average_hits_per_draw": 0.0,
            "mode_summary": {},
        }

    ticket_n = max(1, min(int(tickets_per_draw or 1), 100))
    parsed_cost = float(ticket_cost) if ticket_cost is not None else 0.0
    payout = payout_table or {}
    rules = LOTTERY_GAME_RULES[game_key]
    universe = _number_universe(game_key, include_bonus=include_bonus)
    main_numbers = universe["main_numbers"]
    bonus_numbers = universe["bonus_numbers"]
    hit_distribution = Counter()
    label_distribution = Counter()
    total_return = 0.0
    total_main_hits = 0
    draw_best_hits = []
    best = {"main_hits": -1, "bonus_hit": False, "ticket": None, "draw_date": None}
    generation_ms = 0.0
    loop_started = time.perf_counter()
    main_counter = Counter()
    pair_counter = Counter()
    last_seen = {n: None for n in main_numbers}
    bonus_counter = Counter()
    bonus_last_seen = {n: None for n in bonus_numbers}
    recent_draw_numbers = set()

    for idx, draw in enumerate(draws):
        # Seed counters from draw 0 before generating any tickets.
        # Generating tickets on draw 0 with empty counters forces pure_random
        # regardless of mode — skip generation until draw 1 (F12 fix).
        if idx == 0:
            recent_draw_numbers = set(draw["numbers"])
            for n in draw["numbers"]:
                main_counter[n] += 1
                last_seen[n] = idx
            for p in itertools.combinations(draw["numbers"], 2):
                pair_counter[p] += 1
            if include_bonus and rules["has_bonus"] and draw.get("bonus") is not None:
                _bv = int(draw["bonus"])
                bonus_counter[_bv] += 1
                bonus_last_seen[_bv] = idx
            continue

        gen_started = time.perf_counter()
        # draws_since_seen = (current_idx - 1) - last_seen_idx, matching the
        # formula used in compute_overdue_numbers: (total-1) - last_seen_idx (F1 fix)
        _prior_idx = idx - 1
        freq_payload = {
            "main_number_frequency": [
                {"number": n, "count": int(main_counter.get(n, 0))}
                for n in main_numbers
            ],
            "bonus_frequency": [],
        }
        overdue_payload = {
            "overdue": [
                {
                    "number": n,
                    "draws_since_seen": (_prior_idx + 1 if last_seen[n] is None else max(0, _prior_idx - last_seen[n])),
                }
                for n in main_numbers
            ],
            "bonus_overdue": [],
        }
        if include_bonus and rules["has_bonus"]:
            freq_payload["bonus_frequency"] = [
                {"number": n, "count": int(bonus_counter.get(n, 0))}
                for n in bonus_numbers
            ]
            overdue_payload["bonus_overdue"] = [
                {
                    "number": n,
                    "draws_since_seen": (_prior_idx + 1 if bonus_last_seen[n] is None else max(0, _prior_idx - bonus_last_seen[n])),
                }
                for n in bonus_numbers
            ]
        pair_payload = {
            "pairs": [
                {"pair": [int(a), int(b)], "count": int(cnt)}
                for (a, b), cnt in sorted(pair_counter.items(), key=lambda item: (-item[1], item[0]))[:200]
            ]
        }
        gen = _generate_tickets_with_signals(
            game_key,
            mode_key=mode,
            count=ticket_n,
            include_bonus=include_bonus,
            filters=filters,
            freq_payload=freq_payload,
            overdue_payload=overdue_payload,
            pair_payload=pair_payload,
            recent_draw_numbers=recent_draw_numbers,
        )
        generation_ms += (time.perf_counter() - gen_started) * 1000.0
        best_for_draw = 0
        for ticket in gen["tickets"]:
            main_hits = len(set(ticket["numbers"]).intersection(draw["numbers"]))
            bonus_hit = bool(include_bonus and draw.get("bonus") is not None and ticket.get("bonus") == draw.get("bonus"))
            hit_key = _default_hit_key(main_hits, bonus_hit)
            hit_distribution[hit_key] += 1
            total_main_hits += main_hits
            best_for_draw = max(best_for_draw, main_hits)
            for lbl in ticket.get("labels", []):
                label_distribution[lbl] += 1

            payout_value = payout.get(hit_key)
            if payout_value is None:
                payout_value = payout.get(str(main_hits))
            if payout_value is not None:
                try:
                    total_return += float(payout_value)
                except (TypeError, ValueError):
                    pass

            if (main_hits > best["main_hits"]) or (
                main_hits == best["main_hits"] and bonus_hit and not best["bonus_hit"]
            ):
                best = {
                    "main_hits": main_hits,
                    "bonus_hit": bonus_hit,
                    "ticket": ticket["numbers"],
                    "bonus": ticket.get("bonus"),
                    "draw_date": draw["draw_date"],
                }
        draw_best_hits.append(best_for_draw)
        if idx in (1, len(draws) - 1) or (idx % 250 == 0):
            log.info(
                "[LotterySim] simulation loop progress game=%s mode=%s draw=%s/%s ticket_gen_ms=%.1f",
                game_key,
                mode,
                idx,
                len(draws),
                generation_ms,
            )
        recent_draw_numbers = set(draw["numbers"])
        for n in draw["numbers"]:
            main_counter[n] += 1
            last_seen[n] = idx
        for p in itertools.combinations(draw["numbers"], 2):
            pair_counter[p] += 1
        if include_bonus and rules["has_bonus"] and draw.get("bonus") is not None:
            bonus_value = int(draw["bonus"])
            bonus_counter[bonus_value] += 1
            bonus_last_seen[bonus_value] = idx

    draws_with_tickets = max(0, len(draws) - 1)
    tickets_generated = draws_with_tickets * ticket_n
    total_cost = round(tickets_generated * parsed_cost, 4)
    total_return = round(total_return, 4)
    roi = None
    if total_cost > 0:
        roi = round((total_return - total_cost) / total_cost, 6)

    log.info(
        "[LotterySim] simulation loop complete game=%s mode=%s draws=%s tickets=%s loop_ms=%.1f ticket_gen_ms=%.1f total_ms=%.1f",
        game_key,
        mode,
        len(draws),
        tickets_generated,
        (time.perf_counter() - loop_started) * 1000.0,
        generation_ms,
        (time.perf_counter() - started_at) * 1000.0,
    )

    # Expected value per ticket: average return per ticket across the simulation
    ev_per_ticket = round(total_return / tickets_generated, 4) if tickets_generated > 0 else 0.0
    ev_net = round(ev_per_ticket - parsed_cost, 4) if parsed_cost > 0 else None
    # Hit rate per tier: proportion of tickets that hit each tier
    hit_rates = {}
    for hit_key, hit_count in hit_distribution.items():
        hit_rates[hit_key] = round(hit_count / tickets_generated, 6) if tickets_generated > 0 else 0.0

    return {
        "game": game_key,
        "mode": mode,
        "draws_simulated": len(draws),
        "tickets_generated": tickets_generated,
        "total_cost": total_cost,
        "total_return": total_return,
        "roi": roi,
        "ev_per_ticket": ev_per_ticket,
        "ev_net_per_ticket": ev_net,
        "hit_distribution": dict(sorted(hit_distribution.items(), key=lambda item: item[0])),
        "hit_rates": dict(sorted(hit_rates.items(), key=lambda item: item[0])),
        "best_hit": best if best["main_hits"] >= 0 else {"main_hits": 0, "bonus_hit": False, "ticket": None, "draw_date": None},
        "average_hits_per_draw": round(mean(draw_best_hits), 4) if draw_best_hits else 0.0,
        "mode_summary": {
            "label_distribution": dict(sorted(label_distribution.items(), key=lambda item: (-item[1], item[0]))),
            "filters": filters or {},
            "ticket_cost": parsed_cost,
            "payout_table_used": bool(payout),
        },
    }


def compare_generator_modes(
    game: str,
    modes: list[str],
    tickets_per_draw: int,
    start_date: str | None = None,
    end_date: str | None = None,
    include_bonus: bool = False,
    ticket_cost: float | None = None,
    payout_table: dict | None = None,
    filters: dict | None = None,
    db_path: str | None = None,
    seed: int | None = None,
) -> dict:
    game_key = _normalize_game(game)
    mode_list = list(dict.fromkeys([str(m).strip().lower() for m in (modes or []) if str(m).strip()]))
    if not mode_list:
        mode_list = [
            "pure_random",
            "hot_bias",
            "cold_bias",
            "overdue_bias",
            "balanced_mix",
            "pair_bias",
            "anti_crowd",
        ]
    summaries = []
    for mode in mode_list:
        sim = simulate_generator(
            game_key,
            mode=mode,
            tickets_per_draw=tickets_per_draw,
            start_date=start_date,
            end_date=end_date,
            include_bonus=include_bonus,
            ticket_cost=ticket_cost,
            payout_table=payout_table,
            filters=filters,
            db_path=db_path,
            seed=seed,
        )
        summaries.append(
            {
                "mode": mode,
                "draws_simulated": sim["draws_simulated"],
                "tickets_generated": sim["tickets_generated"],
                "total_cost": sim["total_cost"],
                "total_return": sim["total_return"],
                "roi": sim["roi"],
                "best_hit": sim["best_hit"],
                "average_hits_per_draw": sim["average_hits_per_draw"],
            }
        )
    return {
        "game": game_key,
        "modes": summaries,
        "tickets_per_draw": max(1, int(tickets_per_draw or 1)),
        "include_bonus": bool(include_bonus),
    }


def generate_wheel(
    game: str,
    chosen_numbers: list[int],
    guarantee_if: int,
    db_path: str | None = None,
) -> dict:
    game_key = _normalize_game(game)
    rules = LOTTERY_GAME_RULES[game_key]
    main_count = rules["main_count"]
    universe = set(_number_universe(game_key)["main_numbers"])

    if not isinstance(chosen_numbers, list):
        raise ValueError("chosen_numbers must be a list of integers")
    try:
        pool = sorted({int(x) for x in chosen_numbers})
    except (TypeError, ValueError):
        raise ValueError("chosen_numbers must contain only integers")

    if len(pool) != len(chosen_numbers):
        raise ValueError("chosen_numbers must not contain duplicates")
    if len(pool) < main_count:
        raise ValueError(f"chosen_numbers must contain at least {main_count} numbers")
    if len(pool) > 20:
        raise ValueError("chosen_numbers must contain no more than 20 numbers")
    if any(number not in universe for number in pool):
        raise ValueError("chosen_numbers contains number(s) outside the valid game range")

    try:
        guarantee = int(guarantee_if)
    except (TypeError, ValueError):
        raise ValueError("guarantee_if must be an integer")
    if guarantee < 2 or guarantee > main_count:
        raise ValueError(f"guarantee_if must be between 2 and {main_count}")

    targets = {tuple(target) for target in itertools.combinations(pool, guarantee)}
    candidates = [tuple(ticket) for ticket in itertools.combinations(pool, main_count)]
    if not targets or not candidates:
        raise ValueError("Unable to generate wheel candidates from the supplied inputs")

    candidate_coverages = [
        {tuple(subset) for subset in itertools.combinations(candidate, guarantee)}
        for candidate in candidates
    ]

    uncovered = set(targets)
    selected: list[tuple[int, ...]] = []
    remaining_candidates = list(range(len(candidates)))

    while uncovered:
        best_idx = None
        best_score = -1
        for idx in remaining_candidates:
            score = sum(1 for subset in candidate_coverages[idx] if subset in uncovered)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is None or best_score <= 0:
            raise ValueError("Unable to complete valid wheel coverage with supplied inputs")
        selected.append(candidates[best_idx])
        uncovered.difference_update(candidate_coverages[best_idx])
        remaining_candidates.remove(best_idx)

    selected_sets = [set(ticket) for ticket in selected]
    coverage_verified = all(
        any(set(target).issubset(ticket_set) for ticket_set in selected_sets)
        for target in targets
    )

    return {
        "game": game_key,
        "chosen_numbers": pool,
        "guarantee_if": guarantee,
        "main_count": main_count,
        "ticket_count": len(selected),
        "tickets": [list(ticket) for ticket in selected],
        "coverage_verified": coverage_verified,
        "note": "Abbreviated wheel — greedy set cover. Not mathematically minimal but guaranteed valid.",
        "is_minimal": False,
    }


def to_csv_text(rows: list[dict], headers: list[str]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in headers})
    return stream.getvalue()
