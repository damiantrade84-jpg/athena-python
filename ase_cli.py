#!/usr/bin/env python3
"""ASE v2.1 CLI — training, validation, ingest, promotion, and ops (demo/paper only)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from athena_ase.data.ingest.runner import ALL_SOURCES, run_ingest
from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.exceptions import HoldoutAlreadyEvaluated
from athena_ase.inference.monitor import drift_score, should_auto_demote
from athena_ase.inference.parity import check_parity, parity_hash
from athena_ase.registry.artifacts import (
    artifact_content_hash,
    artifact_dir,
    load_manifest,
    verify_manifest,
)
from athena_ase.registry.holdout import HoldoutRegistry
from athena_ase.registry.promotion import demote_family, promote_family, set_watch_max
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


def _cmd_export_holdout_metrics(args: argparse.Namespace) -> int:
    from athena_research.ase.train import train_family_horizon

    version = str(args.version or "").strip()
    if not version:
        version = "cand-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = train_family_horizon(args.family, args.horizon, version=version)
    gate_metrics = dict(result["metrics"]["holdout_gate_metrics"])
    manifest = load_manifest(Path(result["artifact_path"]) / "manifest.json")
    gate_metrics["family"] = args.family
    gate_metrics["horizon"] = args.horizon
    gate_metrics["artifact_version"] = manifest.version
    gate_metrics["artifact_hash"] = artifact_content_hash(manifest)
    out_path = (
        Path(args.out)
        if args.out
        else Path("athena_research/ase/output")
        / f"holdout_metrics_{args.family}_{args.horizon}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(gate_metrics, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "written": str(out_path),
                "oos_trades": gate_metrics["oos_trades"],
            },
            indent=2,
        )
    )
    return 0


def _cmd_holdout_eval(args: argparse.Namespace) -> int:
    payload = _load_metrics_file(args.metrics_file)
    file_family = str(payload.get("family") or args.family)
    file_horizon = str(payload.get("horizon") or args.horizon)
    if file_family != args.family or file_horizon != args.horizon:
        print(json.dumps({"error": "metrics_identity_mismatch"}, indent=2))
        return 1
    artifact_version = str(args.artifact_version or payload.get("artifact_version") or "")
    artifact_hash = str(args.artifact_hash or payload.get("artifact_hash") or "").lower()
    if not artifact_version or not artifact_hash:
        print(json.dumps({"error": "artifact_identity_required"}, indent=2))
        return 1
    metrics = {
        key: value
        for key, value in payload.items()
        if key not in {"family", "horizon", "artifact_version", "artifact_hash"}
    }
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
            artifact_version=artifact_version,
            artifact_hash=artifact_hash,
        )
    except HoldoutAlreadyEvaluated as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 2


def _cmd_promote(args: argparse.Namespace) -> int:
    supplied_metrics = _load_metrics_file(args.metrics_file)
    root = artifact_dir(args.family, args.horizon, args.artifact_version)
    try:
        manifest = load_manifest(root / "manifest.json")
        integrity_failures = verify_manifest(manifest, root)
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"promoted": False, "failures": [f"artifact_invalid:{exc}"]}, indent=2))
        return 1
    if integrity_failures:
        print(json.dumps({"promoted": False, "failures": integrity_failures}, indent=2))
        return 1
    if (
        manifest.family != args.family
        or manifest.horizon != args.horizon
        or manifest.version != args.artifact_version
    ):
        print(json.dumps({"promoted": False, "failures": ["artifact_identity_mismatch"]}, indent=2))
        return 1

    digest = artifact_content_hash(manifest)
    claimed_hash = str(args.artifact_hash or "").strip().lower()
    if claimed_hash and claimed_hash != digest:
        print(json.dumps({"promoted": False, "failures": ["artifact_hash_mismatch"]}, indent=2))
        return 1

    manifest_metrics = (
        ((manifest.validation_report or {}).get("metrics") or {}).get("holdout_gate_metrics")
        or {}
    )
    comparable_supplied = {
        key: value
        for key, value in supplied_metrics.items()
        if key not in {"family", "horizon", "artifact_version", "artifact_hash"}
    }
    if comparable_supplied != manifest_metrics:
        print(json.dumps({"promoted": False, "failures": ["metrics_artifact_mismatch"]}, indent=2))
        return 1

    reg_path = Path(args.registry) if args.registry else None
    holdout = HoldoutRegistry.load(reg_path)
    ok, failures = promotion_requirements(
        family=args.family,
        horizon=args.horizon,
        metrics=manifest_metrics,
        holdout=holdout,
        artifact_version=manifest.version,
        artifact_hash=digest,
    )
    if not ok:
        print(json.dumps({"promoted": False, "failures": failures}, indent=2))
        return 1
    if args.dry_run:
        print(json.dumps({"promoted": True, "dry_run": True, "artifact_hash": digest}, indent=2))
        return 0
    state = promote_family(
        args.family,
        horizon=args.horizon,
        version=args.artifact_version,
        artifact_hash=digest,
        operator=args.operator,
    )
    print(
        json.dumps(
            {
                "promoted": True,
                "state": state,
                "artifact_version": args.artifact_version,
                "artifact_hash": digest,
            },
            indent=2,
        )
    )
    return 0


def _cmd_demote(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(json.dumps({"demoted": True, "dry_run": True}, indent=2))
        return 0
    state = demote_family(args.family, operator=args.operator)
    print(json.dumps({"demoted": True, "state": state}, indent=2))
    return 0


def _cmd_clear_watch_max(args: argparse.Namespace) -> int:
    """Clear the drift watch-max latch for a family (manual reset after review)."""
    if args.dry_run:
        print(json.dumps({"cleared": True, "dry_run": True, "family": args.family}, indent=2))
        return 0
    set_watch_max(args.family, False)
    print(json.dumps({"cleared": True, "family": args.family}, indent=2))
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
    holdout.add_argument("--artifact-version", default="")
    holdout.add_argument("--artifact-hash", default="")
    holdout.add_argument("--operator", default="cli")
    holdout.add_argument("--registry", default="")
    holdout.add_argument("--dry-run", action="store_true")
    holdout.set_defaults(func=_cmd_holdout_eval)

    export_metrics = sub.add_parser(
        "export-holdout-metrics",
        help="Run train_family_horizon and write its holdout_gate_metrics to JSON "
        "for use as ase_cli holdout-eval/promote --metrics-file",
    )
    export_metrics.add_argument("--family", required=True)
    export_metrics.add_argument(
        "--horizon",
        required=True,
        choices=["intraday", "swing"],
    )
    export_metrics.add_argument("--out", default="")
    export_metrics.add_argument(
        "--version",
        default="",
        help="Unique artifact version for this candidate (default: cand-<UTC timestamp>)",
    )
    export_metrics.set_defaults(func=_cmd_export_holdout_metrics)

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

    clear_wm = sub.add_parser(
        "clear-watch-max",
        help="Clear the drift watch-max latch for a family after manual review",
    )
    clear_wm.add_argument("--family", required=True)
    clear_wm.add_argument("--dry-run", action="store_true")
    clear_wm.set_defaults(func=_cmd_clear_watch_max)

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
