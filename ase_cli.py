#!/usr/bin/env python3
"""ASE v2.1 CLI — training, validation, ingest, promotion, and ops (demo/paper only)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from athena_ase.data.ingest.runner import ALL_SOURCES, run_ingest
from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.exceptions import HoldoutAlreadyEvaluated
from athena_ase.inference.monitor import drift_score, should_auto_demote
from athena_ase.inference.parity import check_parity, parity_hash
from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.registry.promotion import demote_family, get_family_state, promote_family
from athena_ase.shadow.journal import shadow_summary
from athena_ase.validation.holdout import run_holdout_eval
from athena_ase.validation.promotion import promotion_requirements
from athena_research.ase.reports import phase1_report, phase2_validation_report

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


def _load_metrics_file(path: str) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    return json.loads(p.read_text(encoding="utf-8"))


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        phase1_report()
    except (ImportError, OSError) as exc:
        log.warning("phase1 report skipped: %s", exc)
    path = phase2_validation_report()
    print(str(path))
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    from athena_research.ase.train import train_family_horizon

    events = Path(args.events_path) if args.events_path else None
    result = train_family_horizon(
        args.family,
        args.horizon,
        events_path=events,
        version=args.version,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("trained") else 2


def _cmd_train_all(args: argparse.Namespace) -> int:
    from athena_research.ase.train import train_all

    result = train_all(
        version=args.version,
        events_path=Path(args.events_path) if args.events_path else None,
        report_path=Path(args.report_path),
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("trained") else 2


def _cmd_freeze(args: argparse.Namespace) -> int:
    from athena_research.ase.train import freeze_family_horizon

    path = freeze_family_horizon(args.family, args.horizon, version=args.version)
    print(str(path))
    return 0


def _cmd_holdout_eval(args: argparse.Namespace) -> int:
    metrics = _load_metrics_file(args.metrics_file)
    reg_path = Path(args.registry) if args.registry else None
    reg = HoldoutRegistry.load(reg_path)
    try:
        result = run_holdout_eval(
            family=args.family,
            horizon=args.horizon,
            metrics=metrics,
            operator=args.operator,
            registry=reg,
            registry_path=reg_path,
            save=not args.dry_run,
        )
    except HoldoutAlreadyEvaluated as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 2


def _cmd_promote(args: argparse.Namespace) -> int:
    metrics = _load_metrics_file(args.metrics_file)
    reg_path = Path(args.registry) if args.registry else None
    holdout = HoldoutRegistry.load(reg_path)
    ok, failures = promotion_requirements(
        family=args.family,
        horizon=args.horizon,
        metrics=metrics,
        holdout=holdout,
    )
    if not ok:
        print(json.dumps({"promoted": False, "failures": failures}, indent=2))
        return 1
    if args.dry_run:
        print(json.dumps({"promoted": True, "dry_run": True}, indent=2))
        return 0
    state = promote_family(args.family, operator=args.operator)
    print(json.dumps({"promoted": True, "state": state, "artifact_version": args.artifact_version}, indent=2))
    return 0


def _cmd_demote(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(json.dumps({"demoted": True, "dry_run": True}, indent=2))
        return 0
    state = demote_family(args.family, operator=args.operator)
    print(json.dumps({"demoted": True, "state": state}, indent=2))
    return 0


def _cmd_shadow_report(args: argparse.Namespace) -> int:
    summary = shadow_summary(Path(args.journal) if args.journal else None)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_drift_report(args: argparse.Namespace) -> int:
    payload = _load_metrics_file(args.metrics_file)
    psi = {k: float(v) for k, v in (payload.get("psi") or {}).items()}
    score = drift_score(psi)
    auto = should_auto_demote(psi)
    print(json.dumps({"driftScore": score, "auto_demote": auto, "psi": psi}, indent=2))
    return 0


def _cmd_parity_check(args: argparse.Namespace) -> int:
    payload = _load_metrics_file(args.metrics_file)
    research = payload.get("research") or []
    runtime = payload.get("runtime") or []
    ok = check_parity(research, runtime)
    print(
        json.dumps(
            {
                "parity_ok": ok,
                "research_hash": parity_hash(research),
                "runtime_hash": parity_hash(runtime),
            },
            indent=2,
        )
    )
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ase_cli", description="ASE v2.1 demo/paper tooling")
    parser.add_argument(
        "--ptis-root",
        default="",
        help="Override PTIS root (default: %%LOCALAPPDATA%%/Athena/ptis)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest legacy caches/feeds into PTIS")
    ingest.add_argument("--ptis-root", default="", help="Override PTIS root")
    ingest.add_argument("--sources", default=",".join(ALL_SOURCES))
    ingest.add_argument("--backtest-db", default="")
    ingest.add_argument("--duka-db", default="")
    ingest.add_argument("--cot-db", default="")
    ingest.add_argument("--carry-db", default="")
    ingest.add_argument("--bybit-symbols", default="")
    ingest.add_argument("--bybit-lookback-days", type=int, default=730)
    ingest.add_argument("--no-audit", action="store_true")
    ingest.add_argument("--audit-path", default="")
    ingest.set_defaults(func=_cmd_ingest)

    audit = sub.add_parser("audit", help="Regenerate availability audit from PTIS catalog")
    audit.add_argument("--ptis-root", default="")
    audit.add_argument("--audit-path", default="")
    audit.set_defaults(func=_cmd_audit)

    validate = sub.add_parser("validate", help="Generate phase1 + phase2 validation report stubs")
    validate.set_defaults(func=_cmd_validate)

    holdout = sub.add_parser("holdout-eval", help="Single-shot holdout evaluation per family-horizon")
    holdout.add_argument("--family", required=True)
    holdout.add_argument("--horizon", required=True, choices=["intraday", "swing"])
    holdout.add_argument("--metrics-file", required=True)
    holdout.add_argument("--operator", default="cli")
    holdout.add_argument("--registry", default="")
    holdout.add_argument("--dry-run", action="store_true")
    holdout.set_defaults(func=_cmd_holdout_eval)

    promote = sub.add_parser("promote", help="Manually promote a passing family to demo")
    promote.add_argument("--family", required=True)
    promote.add_argument("--horizon", required=True, choices=["intraday", "swing"])
    promote.add_argument("--metrics-file", required=True)
    promote.add_argument("--artifact-version", required=True)
    promote.add_argument("--artifact-hash", default="")
    promote.add_argument("--operator", default="cli")
    promote.add_argument("--registry", default="")
    promote.add_argument("--dry-run", action="store_true")
    promote.set_defaults(func=_cmd_promote)

    demote = sub.add_parser("demote", help="Demote a promoted family back to shadow")
    demote.add_argument("--family", required=True)
    demote.add_argument("--operator", default="cli")
    demote.add_argument("--registry", default="")
    demote.add_argument("--dry-run", action="store_true")
    demote.set_defaults(func=_cmd_demote)

    shadow = sub.add_parser("shadow-report", help="Summarize ASE shadow journal")
    shadow.add_argument("--journal", default="")
    shadow.set_defaults(func=_cmd_shadow_report)

    drift = sub.add_parser("drift-report", help="Summarize feature drift PSI metrics")
    drift.add_argument("--metrics-file", required=True)
    drift.set_defaults(func=_cmd_drift_report)

    parity = sub.add_parser("parity-check", help="Compare research vs runtime parity hashes")
    parity.add_argument("--metrics-file", required=True)
    parity.set_defaults(func=_cmd_parity_check)

    train = sub.add_parser("train", help="Train ASE meta-model for family-horizon")
    train.add_argument("--family", required=True)
    train.add_argument("--horizon", required=True, choices=["intraday", "swing"])
    train.add_argument("--version", default="v1")
    train.add_argument("--events-path", default="")
    train.set_defaults(func=_cmd_train)

    train_all = sub.add_parser("train-all", help="Train every ASE family-horizon independently")
    train_all.add_argument("--version", default="v1")
    train_all.add_argument("--events-path", default="")
    train_all.add_argument("--report-path", default="reports/ase_training_report.md")
    train_all.set_defaults(func=_cmd_train_all)

    freeze = sub.add_parser("freeze", help="Freeze trained artifacts for family-horizon")
    freeze.add_argument("--family", required=True)
    freeze.add_argument("--horizon", required=True, choices=["intraday", "swing"])
    freeze.add_argument("--version", default="v1")
    freeze.set_defaults(func=_cmd_freeze)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
