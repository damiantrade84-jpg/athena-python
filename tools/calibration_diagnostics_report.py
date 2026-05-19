"""Build a markdown calibration diagnostics report from calibration_events.jsonl."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_diagnostics import DEFAULT_DIAGNOSTICS_PATH, write_calibration_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_DIAGNOSTICS_PATH))
    parser.add_argument(
        "--output",
        default=str(Path("docs") / "diagnostics" / "calibration_diagnostics_report.md"),
    )
    args = parser.parse_args()
    out = write_calibration_report(args.input, args.output)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
