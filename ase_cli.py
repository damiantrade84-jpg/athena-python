#!/usr/bin/env python3
"""ASE v2.1 CLI — training, validation, ingest, and ops (demo/paper only)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from athena_ase.data.ingest.runner import ALL_SOURCES, run_ingest
from athena_ase.data.ptis import PTISStore, default_ptis_root

log = logging.getLogger("ase_cli")


def _cmd_ingest(args: argparse.Namespace) -> int:
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    results = run_ingest(
        PTISStore(args.ptis_root) if args.ptis_root else None,
        sources=sources,
        ptis_root=args.ptis_root,
        backtest_db=args.backtest_db,
        duka_db=args.duka_db,
        cot_db=args.cot_db,
        carry_db=args.carry_db,
        bybit_symbols=args.bybit_symbols.split(",") if args.bybit_symbols else None,
        bybit_lookback_days=args.bybit_lookback_days,
        write_audit=not args.no_audit,
        audit_path=args.audit_path,
    )
    summary = {k: {"series": len(v), "rows": sum(v.values())} for k, v in results.items()}
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    from athena_ase.data.ingest.audit import write_availability_audit

    store = PTISStore(args.ptis_root or default_ptis_root())
    path = write_availability_audit(
        store,
        args.audit_path or Path("reports") / "availability_audit.md",
    )
    print(str(path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ase_cli", description="ASE v2.1 demo/paper tooling")
    parser.add_argument(
        "--ptis-root",
        default="",
        help="Override PTIS root (default: %%LOCALAPPDATA%%/Athena/ptis)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest legacy caches/feeds into PTIS")
    ingest.add_argument(
        "--ptis-root",
        default="",
        help="Override PTIS root (default: %%LOCALAPPDATA%%/Athena/ptis)",
    )
    ingest.add_argument(
        "--sources",
        default=",".join(ALL_SOURCES),
        help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})",
    )
    ingest.add_argument("--backtest-db", default="", help="Override backtest_candles.db path")
    ingest.add_argument("--duka-db", default="", help="Override duka_volume.db path")
    ingest.add_argument("--cot-db", default="", help="Override cot_cache.db path")
    ingest.add_argument("--carry-db", default="", help="Override carry_cache.db path")
    ingest.add_argument("--bybit-symbols", default="", help="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT")
    ingest.add_argument("--bybit-lookback-days", type=int, default=730)
    ingest.add_argument("--no-audit", action="store_true", help="Skip availability_audit.md write")
    ingest.add_argument("--audit-path", default="", help="Override audit report path")
    ingest.set_defaults(func=_cmd_ingest)

    audit = sub.add_parser("audit", help="Regenerate availability audit from PTIS catalog")
    audit.add_argument(
        "--ptis-root",
        default="",
        help="Override PTIS root (default: %%LOCALAPPDATA%%/Athena/ptis)",
    )
    audit.add_argument("--audit-path", default="", help="Output markdown path")
    audit.set_defaults(func=_cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
