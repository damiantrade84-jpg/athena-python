"""Run Phase 1 Layer 1 event backtest and reports."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_research.ase.event_backtest import run_and_persist
from athena_research.ase.reports import phase1_report
from athena_research.ase.trials_registry import append_trial

log = logging.getLogger("ase.phase1")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="ASE Phase 1 Layer 1 backtest")
    p.add_argument("--ptis-root", default="")
    p.add_argument("--start-ms", type=int, default=0)
    p.add_argument("--end-ms", type=int, default=2_000_000_000_000)
    args = p.parse_args(argv)
    store = PTISStore(args.ptis_root or default_ptis_root())
    path = run_and_persist(store, start_ms=args.start_ms, end_ms=args.end_ms)
    log.info("events written: %s", path)
    report = phase1_report(path)
    log.info("report written: %s", report)
    append_trial(
        family="all",
        horizon="all",
        config={"start_ms": args.start_ms, "end_ms": args.end_ms},
        results={"events_path": str(path), "report": str(report)},
        notes="phase1 default run",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
