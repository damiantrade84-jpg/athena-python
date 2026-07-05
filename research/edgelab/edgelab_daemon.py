#!/usr/bin/env python
"""Athena EdgeLab daemon — research-only edge discovery loop."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.edgelab.config_loader import load_config
from research.edgelab.cycle import run_cycle
from research.edgelab.paths import CONFIG_PATH, ensure_dirs


def main() -> int:
    ap = argparse.ArgumentParser(description="Athena EdgeLab daemon (research-only)")
    ap.add_argument("--once", action="store_true", help="Run one cycle and exit")
    ap.add_argument("--interval-seconds", type=int, default=0)
    ap.add_argument("--max-cycles", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    args = ap.parse_args()

    config = load_config(Path(args.config))
    dry_run = args.dry_run or bool(config.get("dry_run_default"))
    interval = args.interval_seconds or int(config.get("loop_interval_seconds") or 900)
    max_cycles = args.max_cycles or (1 if args.once else 0)

    ensure_dirs()
    print("=== Athena EdgeLab (research-only) ===")
    print(f"dry_run={dry_run} interval={interval}s max_cycles={max_cycles or 'unlimited'}")

    seen: set[str] = set()
    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle(cycle_number=cycle, config=config, dry_run=dry_run, seen_patches=seen)
        except Exception as exc:
            print(f"[EdgeLab] cycle {cycle} error: {exc}", file=sys.stderr)

        if max_cycles and cycle >= max_cycles:
            print(f"[EdgeLab] stop: max_cycles={max_cycles}")
            break
        if args.once:
            break
        print(f"[EdgeLab] sleeping {interval}s...")
        time.sleep(interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
