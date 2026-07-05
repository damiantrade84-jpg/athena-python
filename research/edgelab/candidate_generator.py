"""Candidate patch acquisition modes for EdgeLab."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any


def list_patches(candidates_dir: Path) -> list[Path]:
    return sorted(candidates_dir.glob("*.patch"))


def acquire_none() -> list[Path]:
    return []


def acquire_file_queue(candidates_dir: Path, seen: set[str], limit: int) -> list[Path]:
    out: list[Path] = []
    for patch in list_patches(candidates_dir):
        key = str(patch.resolve())
        if key in seen:
            continue
        out.append(patch)
        seen.add(key)
        if len(out) >= limit:
            break
    return out


def acquire_command(config: dict[str, Any], repo: Path, limit: int) -> list[Path]:
    cmd = str(config.get("candidate_command") or "").strip()
    if not cmd:
        return []
    proc = subprocess.run(cmd, shell=True, cwd=str(repo), capture_output=True, text=True)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return []
    found: list[Path] = []
    for line in output.splitlines():
        line = line.strip()
        if line.endswith(".patch"):
            p = Path(line)
            if not p.is_absolute():
                p = repo / p
            if p.exists():
                found.append(p)
    if not found:
        patches = list_patches(repo / "research" / "edgelab" / "candidates")
        found = patches[-limit:]
    return found[:limit]


def acquire_manual(
    candidates_dir: Path,
    seen: set[str],
    *,
    poll_seconds: float,
    timeout_seconds: float,
    limit: int,
) -> list[Path]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        batch = acquire_file_queue(candidates_dir, seen, limit)
        if batch:
            return batch
        time.sleep(poll_seconds)
    return []


def _write_stub(stub_config: Path, scenario: str) -> None:
    stub_config.parent.mkdir(parents=True, exist_ok=True)
    stub_config.write_text(
        "# EdgeLab safe stub config (research-only).\n"
        f"evaluator_scenario: {scenario}\n",
        encoding="utf-8",
    )


def create_safe_stub_patch(
    *,
    candidates_dir: Path,
    stub_config: Path,
    repo: Path,
    name: str,
    scenario: str,
) -> Path:
    _write_stub(stub_config, "baseline")
    rel = stub_config.relative_to(repo).as_posix()
    patch_lines = [
        f"diff --git a/{rel} b/{rel}",
        f"--- a/{rel}",
        f"+++ b/{rel}",
        "@@ -1,2 +1,2 @@",
        " # EdgeLab safe stub config (research-only).",
        "-evaluator_scenario: baseline",
        f"+evaluator_scenario: {scenario}",
        "",
    ]
    candidates_dir.mkdir(parents=True, exist_ok=True)
    patch_path = candidates_dir / name
    patch_path.write_text("\n".join(patch_lines), encoding="utf-8")
    return patch_path


def acquire_safe_stub(
    *,
    candidates_dir: Path,
    stub_config: Path,
    repo: Path,
    cycle: int,
    limit: int,
) -> list[Path]:
    scenarios = ["candidate_good", "candidate_bad_trades", "candidate_good"]
    out: list[Path] = []
    for i in range(limit):
        scenario = scenarios[(cycle + i - 1) % len(scenarios)]
        name = f"edgelab_stub_{cycle:03d}_{i+1}_{scenario}.patch"
        out.append(
            create_safe_stub_patch(
                candidates_dir=candidates_dir,
                stub_config=stub_config,
                repo=repo,
                name=name,
                scenario=scenario,
            )
        )
    return out


def acquire_candidates(
    mode: str,
    *,
    config: dict[str, Any],
    repo: Path,
    candidates_dir: Path,
    stub_config: Path,
    seen: set[str],
    cycle: int,
    limit: int,
) -> list[Path]:
    mode = (mode or "none").lower()
    if mode == "none":
        return acquire_none()
    if mode == "file_queue":
        return acquire_file_queue(candidates_dir, seen, limit)
    if mode == "command":
        return acquire_command(config, repo, limit)
    if mode == "manual":
        return acquire_manual(
            candidates_dir,
            seen,
            poll_seconds=float(config.get("manual_poll_seconds", 5)),
            timeout_seconds=float(config.get("manual_timeout_seconds", 60)),
            limit=limit,
        )
    if mode == "safe_stub":
        return acquire_safe_stub(
            candidates_dir=candidates_dir,
            stub_config=stub_config,
            repo=repo,
            cycle=cycle,
            limit=min(1, limit),
        )
    return []
