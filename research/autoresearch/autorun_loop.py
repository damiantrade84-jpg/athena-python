#!/usr/bin/env python
"""AutoResearch autorun loop: file_queue, command, manual, or safe_stub modes."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.autoresearch.config_loader import load_config
from research.autoresearch.core import repo_root


AUTORESEARCH_DIR = ROOT / "research" / "autoresearch"
CANDIDATES_DIR = AUTORESEARCH_DIR / "candidates"
ACCEPTED_DIR = AUTORESEARCH_DIR / "accepted"
REJECTED_DIR = AUTORESEARCH_DIR / "rejected"
LOGS_DIR = AUTORESEARCH_DIR / "logs"
STUB_CONFIG = AUTORESEARCH_DIR / "stub" / "stub_config.yaml"


def ensure_directories() -> None:
    for d in (CANDIDATES_DIR, ACCEPTED_DIR, REJECTED_DIR, LOGS_DIR, STUB_CONFIG.parent):
        d.mkdir(parents=True, exist_ok=True)


def _run_single(patch: Path, *, config_path: Path, ledger_path: Path, iteration: int, mode: str, dry_run: bool) -> int:
    cmd = [
        sys.executable,
        str(AUTORESEARCH_DIR / "run_autoresearch.py"),
        "--candidate-patch",
        str(patch),
        "--config",
        str(config_path),
        "--ledger",
        str(ledger_path),
        "--iteration",
        str(iteration),
        "--mode",
        mode,
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def _list_candidate_patches() -> list[Path]:
    return sorted(CANDIDATES_DIR.glob("*.patch"))


def _move_patch(patch: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / patch.name
    if target.exists():
        target = dest_dir / f"{patch.stem}_{int(time.time())}{patch.suffix}"
    patch.replace(target)
    return target


def _write_stub_config(scenario: str) -> None:
    STUB_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    STUB_CONFIG.write_text(
        "# Research-only stub config for AutoResearch safe_stub mode.\n"
        f"evaluator_scenario: {scenario}\n",
        encoding="utf-8",
    )


def _create_stub_patch(name: str, scenario: str) -> Path:
    _write_stub_config("baseline")
    baseline_text = STUB_CONFIG.read_text(encoding="utf-8")
    new_text = baseline_text.replace("evaluator_scenario: baseline", f"evaluator_scenario: {scenario}")
    rel = STUB_CONFIG.relative_to(ROOT).as_posix()
    patch_lines = [
        f"diff --git a/{rel} b/{rel}",
        "--- a/" + rel,
        "+++ b/" + rel,
        "@@ -1,2 +1,2 @@",
        " # Research-only stub config for AutoResearch safe_stub mode.",
        "-evaluator_scenario: baseline",
        f"+evaluator_scenario: {scenario}",
        "",
    ]
    patch_path = CANDIDATES_DIR / name
    patch_path.write_text("\n".join(patch_lines), encoding="utf-8")
    return patch_path


def get_candidate_patch_file_queue(existing: set[str]) -> Path | None:
    for patch in _list_candidate_patches():
        if str(patch) not in existing:
            return patch
    return None


def get_candidate_patch_command(config: dict) -> Path | None:
    cmd = str(config.get("candidate_command") or "").strip()
    if not cmd:
        print("ERROR: candidate_command not configured for command mode.", file=sys.stderr)
        return None
    proc = subprocess.run(cmd, shell=True, cwd=str(ROOT), capture_output=True, text=True)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        print(f"ERROR: candidate_command failed:\n{output}", file=sys.stderr)
        return None
    for line in output.splitlines():
        line = line.strip()
        if line.endswith(".patch") and Path(line).exists():
            return Path(line)
    patches = _list_candidate_patches()
    return patches[-1] if patches else None


def get_candidate_patch_manual(config: dict, seen: set[str]) -> Path | None:
    poll = float(config.get("manual_poll_seconds", 5))
    timeout = float(config.get("manual_timeout_seconds", 3600))
    deadline = time.monotonic() + timeout
    print(f"Manual mode: waiting for .patch in {CANDIDATES_DIR} (timeout {timeout}s)...")
    while time.monotonic() < deadline:
        patch = get_candidate_patch_file_queue(seen)
        if patch:
            return patch
        time.sleep(poll)
    print("Manual mode: timeout waiting for candidate patch.")
    return None


def get_candidate_patch_safe_stub(iteration: int) -> Path:
    scenarios = ["candidate_good", "candidate_bad_trades", "candidate_good"]
    scenario = scenarios[(iteration - 1) % len(scenarios)]
    name = f"safe_stub_{iteration:03d}_{scenario}.patch"
    return _create_stub_patch(name, scenario)


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoResearch autorun loop (research-only)")
    ap.add_argument("--mode", choices=["file_queue", "command", "manual", "safe_stub"], required=True)
    ap.add_argument("--max-iterations", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", type=str, default=str(AUTORESEARCH_DIR / "autoresearch_config.yaml"))
    ap.add_argument("--ledger", type=str, default=str(AUTORESEARCH_DIR / "results_ledger.jsonl"))
    args = ap.parse_args()

    ensure_directories()
    config_path = Path(args.config)
    ledger_path = Path(args.ledger)
    config = load_config(config_path)
    dry_run = args.dry_run or bool(config.get("dry_run_default"))

    print("=== AutoResearch autorun loop (research-only) ===")
    print(f"Mode: {args.mode} | max_iterations={args.max_iterations} | dry_run={dry_run}")

    baseline_cmd = [
        sys.executable,
        str(AUTORESEARCH_DIR / "run_autoresearch.py"),
        "--baseline-only",
        "--config",
        str(config_path),
        "--ledger",
        str(ledger_path),
    ]
    if dry_run:
        baseline_cmd.append("--dry-run")
    baseline_rc = subprocess.run(baseline_cmd, cwd=str(ROOT)).returncode
    if baseline_rc != 0:
        print("STOP: baseline evaluation failed.", file=sys.stderr)
        return baseline_rc

    seen: set[str] = set()
    stop_reason = "max_iterations_reached"
    iteration = 0

    while iteration < args.max_iterations:
        iteration += 1
        print(f"\n--- Iteration {iteration}/{args.max_iterations} ---")

        if args.mode == "file_queue":
            patch = get_candidate_patch_file_queue(seen)
        elif args.mode == "command":
            patch = get_candidate_patch_command(config)
        elif args.mode == "manual":
            patch = get_candidate_patch_manual(config, seen)
        elif args.mode == "safe_stub":
            patch = get_candidate_patch_safe_stub(iteration)
        else:
            patch = None

        if patch is None:
            stop_reason = "no_candidates_remaining"
            break

        seen.add(str(patch))
        rc = _run_single(
            patch,
            config_path=config_path,
            ledger_path=ledger_path,
            iteration=iteration,
            mode=args.mode,
            dry_run=dry_run,
        )

        if rc == 0:
            _move_patch(patch, ACCEPTED_DIR)
        else:
            _move_patch(patch, REJECTED_DIR)

    print(f"\n=== Loop finished: {stop_reason} after {iteration} iteration(s) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
