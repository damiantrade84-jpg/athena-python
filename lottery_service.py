import csv
import io
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from itertools import zip_longest


LOTTERY_GAME_SPECS = {
    "lotto": {"main_count": 6, "has_bonus": True},
    "powerball": {"main_count": 5, "has_bonus": True},
    "daily_lotto": {"main_count": 5, "has_bonus": False},
}


def ensure_lottery_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS lottery_draws (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game        TEXT NOT NULL,
            draw_date   TEXT NOT NULL,
            n1          INTEGER NOT NULL,
            n2          INTEGER NOT NULL,
            n3          INTEGER NOT NULL,
            n4          INTEGER NOT NULL,
            n5          INTEGER NOT NULL,
            n6          INTEGER,
            bonus       INTEGER,
            source_file TEXT,
            imported_at TEXT NOT NULL,
            UNIQUE(game, draw_date)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_lottery_draws_game_date ON lottery_draws (game, draw_date DESC)"
    )


def _normalize_header(value: str) -> str:
    return str(value or "").strip().lower()


def _parse_date(raw: str) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_int(raw: str) -> int | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def parse_lottery_csv(csv_text: str, game: str) -> dict:
    spec = LOTTERY_GAME_SPECS.get(game)
    if not spec:
        raise ValueError(f"Unsupported lottery game: {game}")

    stream = io.StringIO(csv_text or "")
    reader = csv.reader(stream)
    header = next(reader, None)
    if not header:
        return {
            "rows": [],
            "preview": [],
            "total_rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "error_counts": {"empty_file": 1},
            "errors": [{"row": 0, "message": "CSV is empty"}],
        }

    headers = [_normalize_header(h) for h in header]
    required = ["draw_date"] + [f"n{i}" for i in range(1, spec["main_count"] + 1)]
    if spec["has_bonus"]:
        required.append("bonus")
    missing_columns = [col for col in required if col not in headers]
    if missing_columns:
        return {
            "rows": [],
            "preview": [],
            "total_rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "error_counts": {"missing_columns": len(missing_columns)},
            "errors": [
                {
                    "row": 0,
                    "message": "Missing required columns: " + ", ".join(missing_columns),
                }
            ],
        }

    parsed_rows = []
    errors = []
    error_counts = Counter()

    for row_idx, values in enumerate(reader, start=2):
        if not values or not any(str(v or "").strip() for v in values):
            continue

        record = {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in zip_longest(headers, values, fillvalue="")
            if key
        }
        draw_date = _parse_date(record.get("draw_date"))
        if not draw_date:
            error_counts["invalid_date"] += 1
            errors.append({"row": row_idx, "message": "Invalid draw_date"})
            continue

        main_numbers = []
        missing_required = False
        invalid_number = False
        for i in range(1, spec["main_count"] + 1):
            num = _parse_int(record.get(f"n{i}"))
            if num is None:
                if not str(record.get(f"n{i}") or "").strip():
                    missing_required = True
                else:
                    invalid_number = True
                break
            main_numbers.append(num)
        if missing_required:
            error_counts["missing_numbers"] += 1
            errors.append({"row": row_idx, "message": "Missing required number(s)"})
            continue
        if invalid_number:
            error_counts["invalid_numbers"] += 1
            errors.append({"row": row_idx, "message": "Invalid number value"})
            continue
        if len(set(main_numbers)) != len(main_numbers):
            error_counts["duplicate_numbers"] += 1
            errors.append({"row": row_idx, "message": "Duplicate main numbers in draw"})
            continue

        bonus = None
        if spec["has_bonus"]:
            bonus = _parse_int(record.get("bonus"))
            if bonus is None:
                error_counts["missing_bonus"] += 1
                errors.append({"row": row_idx, "message": "Missing or invalid bonus value"})
                continue

        sorted_main = sorted(main_numbers)
        parsed = {
            "draw_date": draw_date,
            "n1": sorted_main[0],
            "n2": sorted_main[1],
            "n3": sorted_main[2],
            "n4": sorted_main[3],
            "n5": sorted_main[4],
            "n6": sorted_main[5] if spec["main_count"] == 6 else None,
            "bonus": bonus,
        }
        parsed_rows.append(parsed)

    return {
        "rows": parsed_rows,
        "preview": parsed_rows[:12],
        "total_rows": len(parsed_rows) + len(errors),
        "valid_rows": len(parsed_rows),
        "invalid_rows": len(errors),
        "error_counts": dict(error_counts),
        "errors": errors[:50],
    }


def import_lottery_csv(
    db_path: str,
    game: str,
    csv_text: str,
    source_file: str = "",
    mode: str = "append",
    preview_only: bool = False,
) -> dict:
    parsed = parse_lottery_csv(csv_text, game)
    result = {
        "game": game,
        "mode": mode,
        "preview": parsed["preview"],
        "total_rows": parsed["total_rows"],
        "valid_rows": parsed["valid_rows"],
        "invalid_rows": parsed["invalid_rows"],
        "error_counts": parsed["error_counts"],
        "errors": parsed["errors"],
        "imported_rows": 0,
        "duplicates_skipped": 0,
        "deleted_rows": 0,
    }
    if preview_only:
        return result

    rows = parsed["rows"]
    if mode == "replace" and not rows:
        result["error"] = "Replace mode requires at least one valid row"
        return result

    imported_at = datetime.now(timezone.utc).isoformat()
    source_name = os.path.basename(source_file or "")

    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        ensure_lottery_schema(con)
        if mode == "replace":
            cur = con.execute("DELETE FROM lottery_draws WHERE game = ?", (game,))
            result["deleted_rows"] = max(cur.rowcount, 0)

        for row in rows:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO lottery_draws (
                    game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game,
                    row["draw_date"],
                    row["n1"],
                    row["n2"],
                    row["n3"],
                    row["n4"],
                    row["n5"],
                    row["n6"],
                    row["bonus"],
                    source_name,
                    imported_at,
                ),
            )
            if cur.rowcount:
                result["imported_rows"] += 1
            else:
                result["duplicates_skipped"] += 1
        con.commit()

    return result


def fetch_lottery_draws(db_path: str, game: str | None = None, limit: int = 200) -> dict:
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_lottery_schema(con)
        params = []
        where = ""
        if game:
            where = "WHERE game = ?"
            params.append(game)
        params.append(max(1, min(int(limit or 200), 1000)))
        rows = con.execute(
            f"""
            SELECT id, game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at
            FROM lottery_draws
            {where}
            ORDER BY draw_date DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"draws": [dict(row) for row in rows]}


def add_lottery_draw(
    db_path: str,
    game: str,
    draw_date: str,
    numbers: list[int],
    bonus: int | None = None,
    source_file: str = "manual",
) -> dict:
    """Insert a single draw result manually, without CSV upload.

    Returns a result dict with keys: inserted (bool), duplicate (bool), error (str|None).
    """
    spec = LOTTERY_GAME_SPECS.get(game)
    if not spec:
        raise ValueError(f"Unsupported lottery game: {game}")

    parsed_date = _parse_date(str(draw_date or "").strip())
    if not parsed_date:
        raise ValueError(f"Invalid draw_date: {draw_date!r} — expected YYYY-MM-DD")

    main_count = spec["main_count"]
    clean = []
    for raw in (numbers or []):
        n = _parse_int(str(raw))
        if n is None:
            raise ValueError(f"Invalid number value: {raw!r}")
        clean.append(n)

    if len(clean) != main_count:
        raise ValueError(f"Expected {main_count} main numbers, got {len(clean)}")
    if len(set(clean)) != len(clean):
        raise ValueError("Duplicate main numbers in draw")

    sorted_main = sorted(clean)

    if spec["has_bonus"]:
        if bonus is None:
            raise ValueError("bonus is required for this game")
        bonus = _parse_int(str(bonus))
        if bonus is None:
            raise ValueError("Invalid bonus value")
    else:
        bonus = None

    imported_at = datetime.now(timezone.utc).isoformat()

    row = {
        "n1": sorted_main[0],
        "n2": sorted_main[1],
        "n3": sorted_main[2],
        "n4": sorted_main[3],
        "n5": sorted_main[4],
        "n6": sorted_main[5] if main_count == 6 else None,
        "bonus": bonus,
    }

    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        ensure_lottery_schema(con)
        cur = con.execute(
            """
            INSERT OR IGNORE INTO lottery_draws
                (game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game,
                parsed_date,
                row["n1"], row["n2"], row["n3"], row["n4"], row["n5"], row["n6"],
                row["bonus"],
                source_file,
                imported_at,
            ),
        )
        inserted = cur.rowcount > 0
        con.commit()

    return {
        "game": game,
        "draw_date": parsed_date,
        "numbers": sorted_main,
        "bonus": bonus,
        "inserted": inserted,
        "duplicate": not inserted,
        "error": None,
    }


def delete_lottery_draw(
    db_path: str,
    game: str,
    draw_date: str,
) -> dict:
    """Delete a single draw by game + date. Returns deleted count."""
    parsed_date = _parse_date(str(draw_date or "").strip())
    if not parsed_date:
        raise ValueError(f"Invalid draw_date: {draw_date!r}")

    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        ensure_lottery_schema(con)
        cur = con.execute(
            "DELETE FROM lottery_draws WHERE game = ? AND draw_date = ?",
            (game, parsed_date),
        )
        deleted = max(cur.rowcount, 0)
        con.commit()

    return {"game": game, "draw_date": parsed_date, "deleted_rows": deleted}


def clear_lottery_draws(db_path: str, game: str | None = None) -> dict:
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.execute("PRAGMA journal_mode=WAL")
        ensure_lottery_schema(con)
        if game:
            cur = con.execute("DELETE FROM lottery_draws WHERE game = ?", (game,))
        else:
            cur = con.execute("DELETE FROM lottery_draws")
        deleted_rows = max(cur.rowcount, 0)
        con.commit()
    return {"deleted_rows": deleted_rows, "game": game or "all"}


def lottery_stats(db_path: str, game: str) -> dict:
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_lottery_schema(con)
        rows = con.execute(
            """
            SELECT id, game, draw_date, n1, n2, n3, n4, n5, n6, bonus, source_file, imported_at
            FROM lottery_draws
            WHERE game = ?
            ORDER BY draw_date DESC, id DESC
            """,
            (game,),
        ).fetchall()

    draws = [dict(row) for row in rows]
    if not draws:
        return {
            "game": game,
            "draw_count": 0,
            "main_number_frequency": [],
            "bonus_frequency": [],
            "recent_draws": [],
            "top_main_numbers": [],
        }

    main_counter = Counter()
    bonus_counter = Counter()
    for draw in draws:
        for key in ("n1", "n2", "n3", "n4", "n5", "n6"):
            value = draw.get(key)
            if value is not None:
                main_counter[int(value)] += 1
        if draw.get("bonus") is not None:
            bonus_counter[int(draw["bonus"])] += 1

    main_frequency = [
        {"number": number, "count": count}
        for number, count in sorted(main_counter.items())
    ]
    bonus_frequency = [
        {"number": number, "count": count}
        for number, count in sorted(bonus_counter.items())
    ]
    top_main = sorted(
        main_frequency, key=lambda item: (-item["count"], item["number"])
    )[:10]

    return {
        "game": game,
        "draw_count": len(draws),
        "latest_draw_date": draws[0]["draw_date"],
        "earliest_draw_date": draws[-1]["draw_date"],
        "main_number_frequency": main_frequency,
        "bonus_frequency": bonus_frequency,
        "top_main_numbers": top_main,
        "recent_draws": draws[:20],
    }
