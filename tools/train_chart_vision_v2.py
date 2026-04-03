"""Train the advisory chart_vision_v2 hybrid model from vision_* tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vision_hybrid import train_hybrid_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="audit.db", help="Path to audit SQLite DB")
    parser.add_argument(
        "--model-dir",
        default="models/chart_vision_v2",
        help="Directory where model artifacts are written",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=40,
        help="Minimum labeled rows required before training runs",
    )
    args = parser.parse_args()
    result = train_hybrid_model(
        db_path=str(Path(args.db)),
        model_dir=str(Path(args.model_dir)),
        min_samples=int(args.min_samples),
    )
    print(
        json.dumps(
            {
                "model_path": result.model_path,
                "rows_total": result.rows_total,
                "rows_train": result.rows_train,
                "rows_val": result.rows_val,
                "rows_test": result.rows_test,
                "metrics": result.metrics,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

