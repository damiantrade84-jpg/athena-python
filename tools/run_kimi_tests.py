#!/usr/bin/env python3
"""Test runner optimized for Kimi Code agent consumption.

Usage:
    python tools/run_kimi_tests.py --engine A --coverage --json
    python tools/run_kimi_tests.py --pattern tests/test_risk_engine.py
    python tools/run_kimi_tests.py --all --parallel
    python tools/run_kimi_tests.py --list-engines
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"

ENGINE_MAP = {
    "A": ["test_factor_scoring.py", "test_engine_a*.py", "test_scoring*.py"],
    "B": ["test_engine_b*.py", "test_market_structure.py", "test_naked*.py"],
    "C": ["test_engine_c*.py", "test_meta_learner.py", "test_signal_debate.py"],
    "D": ["test_scalp_engine.py", "test_engine_d*.py", "test_volume_profile*.py"],
    "risk": ["test_risk_engine.py", "test_risk*.py", "test_guardian.py"],
    "exec": ["test_execution.py", "test_bybit*.py", "test_mt5*.py"],
    "data": ["test_data_feeds.py", "test_candle*.py", "test_eodhd*.py"],
    "ai": ["test_ai_learning.py", "test_meta_learner.py", "test_confidence*.py"],
    "backtest": ["test_backtest*.py", "test_bt*.py"],
}


def resolve_patterns(patterns: list[str]) -> list[Path]:
    """Resolve glob patterns to actual test files."""
    files = set()
    for p in patterns:
        matched = list(TESTS_DIR.glob(p))
        if matched:
            files.update(matched)
        else:
            # Try as exact path
            exact = TESTS_DIR / p
            if exact.exists():
                files.add(exact)
    return sorted(files)


def run_tests(
    test_files: list[Path],
    coverage: bool = False,
    parallel: bool = False,
    verbose: bool = True,
    timeout: int = 600
) -> dict:
    cmd = [sys.executable, "-m", "pytest"]
    if verbose:
        cmd.append("-v")
    if coverage:
        cmd += [
            "--cov=.",
            "--cov-report=json:tests/coverage.json",
            "--cov-report=term-missing",
        ]
    if parallel:
        cmd += ["-n", "auto"]

    for f in test_files:
        cmd.append(str(f))

    result = subprocess.run(
        cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout
    )

    output = {
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "summary": extract_summary(result.stdout),
        "files_tested": [str(f.name) for f in test_files],
    }

    cov_path = PROJECT_ROOT / "tests" / "coverage.json"
    if coverage and cov_path.exists():
        try:
            with open(cov_path) as f:
                cov_data = json.load(f)
            output["coverage_percent"] = cov_data.get("totals", {}).get(
                "percent_covered", 0
            )
            output["coverage_files"] = len(cov_data.get("files", {}))
        except Exception:
            pass

    return output


def extract_summary(stdout: str) -> str:
    lines = stdout.strip().split("\n")
    for line in reversed(lines):
        line_stripped = line.strip()
        if any(
            word in line_stripped
            for word in ["passed", "failed", "error", "skipped"]
        ):
            return line_stripped
    return "No summary found"


def main():
    parser = argparse.ArgumentParser(description="Athena test runner for Kimi Code")
    parser.add_argument(
        "--engine", choices=list(ENGINE_MAP.keys()), help="Test engine-specific files"
    )
    parser.add_argument("--pattern", help="Glob pattern for test files")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--coverage", action="store_true", help="Generate coverage report")
    parser.add_argument("--parallel", action="store_true", help="Run in parallel (-n auto)")
    parser.add_argument("--json", action="store_true", help="Output as JSON for agent parsing")
    parser.add_argument("--list-engines", action="store_true", help="Show available engines")
    parser.add_argument("--timeout", type=int, default=600, help="Test timeout in seconds")
    args = parser.parse_args()

    if args.list_engines:
        print("Available engine test suites:")
        for k, v in ENGINE_MAP.items():
            print(f"  {k}: {', '.join(v)}")
        return

    if args.all:
        test_files = resolve_patterns(["test_*.py"])
    elif args.engine:
        test_files = resolve_patterns(ENGINE_MAP[args.engine])
    elif args.pattern:
        test_files = resolve_patterns([args.pattern])
    else:
        test_files = resolve_patterns(["test_*.py"])

    if not test_files:
        print("ERROR: No test files matched the criteria", file=sys.stderr)
        sys.exit(1)

    result = run_tests(
        test_files,
        coverage=args.coverage,
        parallel=args.parallel,
        timeout=args.timeout,
    )

    if args.json:
        # Truncate long outputs for JSON
        json_result = {
            **result,
            "stdout": result["stdout"][-8000:] if len(result["stdout"]) > 8000 else result["stdout"],
            "stderr": result["stderr"][-4000:] if len(result["stderr"]) > 4000 else result["stderr"],
        }
        print(json.dumps(json_result, indent=2))
    else:
        print(result["stdout"])
        if result["stderr"]:
            print("--- STDERR ---", file=sys.stderr)
            print(result["stderr"], file=sys.stderr)
        print(f"\n=== SUMMARY: {result['summary']} ===")
        if "coverage_percent" in result:
            print(f"Coverage: {result['coverage_percent']:.1f}% ({result.get('coverage_files', 0)} files)")

    sys.exit(result["returncode"])


if __name__ == "__main__":
    main()
