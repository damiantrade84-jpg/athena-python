#!/usr/bin/env python3
"""Audit PTIS partition Parquet files for corruption."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from athena_ase.data.parquet_io import is_valid_parquet_file, quarantine_corrupt_parquet
from athena_ase.data.ptis import default_ptis_root


def audit_ptis(root: Path, *, quarantine: bool = False) -> int:
    bad: list[Path] = []
    checked = 0
    for path in sorted(root.rglob("data.parquet")):
        checked += 1
        if not is_valid_parquet_file(path):
            bad.append(path)
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            print(f"INVALID ({size} bytes): {path}")
            if quarantine:
                dest = quarantine_corrupt_parquet(path)
                if dest is not None:
                    print(f"  quarantined -> {dest}")
    print(f"checked={checked} invalid={len(bad)} root={root}")
    return len(bad)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit PTIS data.parquet partition files.")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="PTIS root (default: ATHENA_PTIS_ROOT or %%LOCALAPPDATA%%/Athena/ptis)",
    )
    parser.add_argument(
        "--quarantine",
        action="store_true",
        help="Move invalid files to data.parquet.corrupt.<timestamp> siblings",
    )
    args = parser.parse_args(argv)
    root = args.root or default_ptis_root()
    if not root.exists():
        print(f"PTIS root not found: {root}", file=sys.stderr)
        return 1
    invalid = audit_ptis(root, quarantine=args.quarantine)
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
