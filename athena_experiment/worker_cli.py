"""Standalone CLI entrypoint for the Tuning Lab worker: `python -m
athena_experiment.worker_cli --input <path> --output <path>`.

Why this exists instead of ``ProcessPoolExecutor``: ``athena.py`` (the real
server entrypoint) is a monolithic script with no ``if __name__ ==
"__main__":`` guard around its module-level Flask app / service startup. On
Windows, ``multiprocessing``'s spawn start method refuses to start a child
process from inside an unguarded ``__main__`` module — every
``ProcessPoolExecutor.submit()`` call from a request handler running inside
that process raised::

    RuntimeError: An attempt has been made to start a new process before the
    current process has finished its bootstrapping phase...

so every single ``/api/experiment/run`` call failed, which is exactly why
tuning appeared to have no effect no matter what was changed — the run never
executed. Invoking this module as a real OS subprocess via
``subprocess.run([sys.executable, "-m", "athena_experiment.worker_cli", ...])``
sidesteps the problem entirely: the child process imports *this* module as
its own ``__main__`` and never re-executes athena.py's module-level code.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to a JSON file: {symbol, engine, style, overlay}")
    parser.add_argument("--output", required=True, help="Path to write the JSON result to")
    args = parser.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    try:
        from athena_experiment.worker import run_experiment_worker

        result = run_experiment_worker(
            payload["symbol"],
            payload["engine"],
            payload["style"],
            payload.get("overlay") or {},
            n_trials_hint=int(payload.get("n_trials_hint", 1)),
        )
    except Exception as exc:  # surface as a normal error result, not a crash
        result = {"error": f"{type(exc).__name__}: {exc}"}

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh)

    return 0


if __name__ == "__main__":
    sys.exit(main())
