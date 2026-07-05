"""Single EdgeLab research cycle orchestration."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from research.edgelab.audit import append_jsonl, cycle_ledger_path
from research.edgelab.candidate_generator import acquire_candidates
from research.edgelab.config_loader import load_config
from research.edgelab.data_freshness import run_freshness_checks
from research.edgelab.database import (
    connect,
    init_db,
    insert_data_freshness,
    insert_engine_finding,
    insert_research_run,
    insert_suggestion,
)
from research.edgelab.engine_loops.engine_a_loop import run_engine_a_loop
from research.edgelab.engine_loops.engine_b_loop import run_engine_b_loop
from research.edgelab.engine_loops.cascade_loop import run_cascade_loop
from research.edgelab.engine_loops.scalp_loop import run_scalp_loop
from research.edgelab.engine_loops.ase_loop import run_ase_loop
from research.edgelab.export_suggestions import export_suggestions
from research.edgelab.patch_evaluator import evaluate_patch
from research.edgelab.paths import (
    CANDIDATES_DIR,
    DB_PATH,
    EDGELAB_DIR,
    LEDGERS_DIR,
    REPO_ROOT,
    STUB_CONFIG,
    ensure_dirs,
)


ENGINE_RUNNERS = {
    "engine_b": run_engine_b_loop,
    "cascade": run_cascade_loop,
    "engine_a": run_engine_a_loop,
    "scalp": run_scalp_loop,
    "ase": run_ase_loop,
}


def run_cycle(
    *,
    cycle_number: int = 1,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
    repo: Path | None = None,
    seen_patches: set[str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    repo = repo or REPO_ROOT
    config = config or load_config()
    ensure_dirs()
    init_db(DB_PATH)
    seen_patches = seen_patches if seen_patches is not None else set()

    symbols = list(config.get("symbols") or ["BTCUSDT"])
    timeframes = list(config.get("timeframes") or ["H4"])
    enabled = list(config.get("enabled_engines") or ["engine_b"])
    optional = list(config.get("optional_engines") or [])

    freshness_rows = run_freshness_checks(
        repo,
        symbols=symbols,
        timeframes=timeframes,
        max_age_hours=float(config.get("freshness_max_age_hours", 48)),
        min_bars=int(config.get("freshness_min_bars", 200)),
        dry_run=dry_run,
    )

    engines_run: list[str] = []
    findings: list[dict[str, Any]] = []
    commands_run: list[str] = []
    candidates_tested: list[dict[str, Any]] = []
    errors: list[str] = []

    with connect(DB_PATH) as conn:
        for row in freshness_rows:
            insert_data_freshness(conn, row)
            if not row.get("can_run_engine_tests"):
                insert_suggestion(
                    conn,
                    {
                        "engine": "edgelab",
                        "symbol": row.get("symbol"),
                        "timeframe": row.get("timeframe"),
                        "suggestion_type": "data_freshness",
                        "title": f"Data issue: {row.get('symbol')} {row.get('timeframe')}",
                        "summary": "; ".join(row.get("blocking_issues") or row.get("warnings") or []),
                        "evidence": str(row.get("details")),
                        "status": "needs_review",
                        "created_by": "daemon",
                        "risk_flags": row.get("blocking_issues") or row.get("warnings") or [],
                        "baseline_metrics": {"freshness_score": row.get("freshness_score")},
                    },
                )

        if dry_run and not any(r.get("can_run_engine_tests") for r in freshness_rows):
            insert_suggestion(
                conn,
                {
                    "engine": "edgelab",
                    "suggestion_type": "diagnostic",
                    "title": "EdgeLab dry-run heartbeat",
                    "summary": "EdgeLab completed a safe dry-run cycle without touching live execution paths.",
                    "status": "new",
                    "created_by": "daemon",
                    "risk_flags": [],
                },
            )

        freshness_map = {
            (r["symbol"], r["timeframe"]): r for r in freshness_rows
        }

        for engine in enabled + [e for e in optional if e not in enabled]:
            runner = ENGINE_RUNNERS.get(engine)
            if runner is None:
                continue
            for symbol in symbols:
                for tf in timeframes:
                    fr = freshness_map.get((symbol, tf), {})
                    finding = runner(
                        repo=repo,
                        config=config,
                        symbol=symbol,
                        timeframe=tf,
                        dry_run=dry_run,
                        can_run=bool(fr.get("can_run_engine_tests", dry_run)),
                    )
                    engines_run.append(engine)
                    if finding.command:
                        commands_run.append(finding.command)
                    insert_engine_finding(
                        conn,
                        {
                            "engine": finding.engine,
                            "symbol": finding.symbol,
                            "timeframe": finding.timeframe,
                            "finding_type": finding.finding_type,
                            "title": finding.title,
                            "details": finding.details,
                        },
                    )
                    fd = {
                        "engine": finding.engine,
                        "symbol": finding.symbol,
                        "timeframe": finding.timeframe,
                        "title": finding.title,
                        "skipped": finding.skipped,
                        "skip_reason": finding.skip_reason,
                        "score": finding.score,
                        "metrics": finding.metrics,
                    }
                    findings.append(fd)

                    if finding.skipped and finding.skip_reason == "data_freshness_blocked":
                        continue
                    if finding.metrics:
                        insert_suggestion(
                            conn,
                            {
                                "engine": finding.engine,
                                "symbol": finding.symbol,
                                "timeframe": finding.timeframe,
                                "suggestion_type": "engine_finding",
                                "title": finding.title,
                                "summary": f"Research score {finding.score:.2f}",
                                "baseline_metrics": finding.metrics,
                                "status": "new",
                                "created_by": "daemon",
                                "risk_flags": [],
                            },
                        )

        mode = str(config.get("candidate_generation_mode") or "none")
        max_c = int(config.get("max_candidates_per_cycle") or 0)
        patches = acquire_candidates(
            mode,
            config=config,
            repo=repo,
            candidates_dir=CANDIDATES_DIR,
            stub_config=STUB_CONFIG,
            seen=seen_patches,
            cycle=cycle_number,
            limit=max_c,
        )

        for patch in patches:
            seen_patches.add(str(patch.resolve()))
            try:
                fr_ok = all(r.get("can_run_engine_tests", dry_run) for r in freshness_rows) if freshness_rows else True
                ev = evaluate_patch(
                    patch,
                    config=config,
                    repo=repo,
                    dry_run=dry_run,
                    data_freshness_ok=fr_ok or dry_run,
                    created_by="daemon",
                )
                candidates_tested.append(ev)
            except Exception as exc:
                errors.append(str(exc))

        duration = round(time.monotonic() - started, 3)
        summary = {
            "freshness_count": len(freshness_rows),
            "findings_count": len(findings),
            "candidates_count": len(candidates_tested),
            "accepted": sum(1 for c in candidates_tested if c.get("accepted")),
            "rejected": sum(1 for c in candidates_tested if not c.get("accepted")),
        }
        insert_research_run(
            conn,
            {
                "cycle_number": cycle_number,
                "dry_run": dry_run,
                "engines_run": sorted(set(engines_run)),
                "duration_seconds": duration,
                "summary": summary,
            },
        )
        conn.commit()

    export_path = export_suggestions(db_path=DB_PATH)
    audit_entry = {
        "cycle_number": cycle_number,
        "dry_run": dry_run,
        "data_freshness": freshness_rows,
        "engines_run": sorted(set(engines_run)),
        "commands_run": commands_run,
        "findings": findings,
        "candidates_tested": candidates_tested,
        "accepted": summary["accepted"],
        "rejected": summary["rejected"],
        "errors": errors,
        "duration_seconds": duration,
        "export_path": str(export_path),
    }
    append_jsonl(cycle_ledger_path(LEDGERS_DIR, cycle_number), audit_entry)
    append_jsonl(EDGELAB_DIR / "logs" / "edgelab_audit.jsonl", audit_entry)

    print(f"[EdgeLab] cycle={cycle_number} dry_run={dry_run} duration={duration}s export={export_path}")
    return audit_entry
