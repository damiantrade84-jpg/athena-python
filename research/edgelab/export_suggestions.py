#!/usr/bin/env python
"""Export EdgeLab suggestions to JSON for UI consumption."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.edgelab.database import connect, init_db, list_suggestions
from research.edgelab.paths import DB_PATH, OUT_DIR, ensure_dirs


def export_suggestions(
    *,
    db_path: Path | None = None,
    out_path: Path | None = None,
    limit: int = 200,
) -> Path:
    ensure_dirs()
    db_path = db_path or DB_PATH
    out_path = out_path or OUT_DIR / "suggestions.json"
    init_db(db_path)

    with connect(db_path) as conn:
        suggestions = list_suggestions(conn, limit=limit)
        freshness = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM data_freshness ORDER BY timestamp DESC LIMIT ?",
                (50,),
            ).fetchall()
        ]
        runs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM research_runs ORDER BY timestamp DESC LIMIT ?",
                (20,),
            ).fetchall()
        ]

    payload = {
        "research_only": True,
        "disclaimer": "EdgeLab suggestions are research-only. Manual promotion required for production.",
        "suggestions": suggestions,
        "data_freshness": freshness,
        "recent_runs": runs,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Export EdgeLab suggestions JSON")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    out = Path(args.out) if args.out else None
    path = export_suggestions(out_path=out, limit=args.limit)
    print(f"Exported to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
