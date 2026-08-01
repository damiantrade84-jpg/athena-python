"""Run ``athena_experiment.worker_cli`` as a real OS subprocess.

See ``worker_cli.py`` for why this exists instead of ``ProcessPoolExecutor``:
that approach silently fails whenever it's called from a request handler
running inside ``athena.py``'s unguarded ``__main__`` module. Plain
``subprocess.run`` has no such restriction.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class WorkerTimeoutError(TimeoutError):
    pass


def run_worker_subprocess(
    symbol: str,
    engine: str,
    style: str,
    overlay: dict[str, Any],
    timeout_sec: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="athena_experiment_") as tmpdir:
        input_path = Path(tmpdir) / "input.json"
        output_path = Path(tmpdir) / "output.json"
        input_path.write_text(
            json.dumps({"symbol": symbol, "engine": engine, "style": style, "overlay": overlay}),
            encoding="utf-8",
        )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "athena_experiment.worker_cli",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                timeout=timeout_sec,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run kills and reaps the child before raising this.
            raise WorkerTimeoutError(f"Experiment run exceeded {timeout_sec}s and was aborted.") from exc

        if not output_path.exists():
            tail = (proc.stderr or "")[-4000:]
            raise RuntimeError(f"worker produced no output (exit {proc.returncode}): {tail}")

        return json.loads(output_path.read_text(encoding="utf-8"))
