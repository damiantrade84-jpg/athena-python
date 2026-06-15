from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import requests

from athena_research.forex_edge.config import load_config, redact_secrets
from athena_research.forex_edge.models import BlockedDataError
from athena_research.forex_edge.runner import RunRequest, run_research


EXIT_COMPLETED = 0
EXIT_NO_EDGE = 2
EXIT_BLOCKED = 3
EXIT_INVALID = 4
EXIT_PROVIDER = 5


def redact_text(text: str) -> str:
    return str(redact_secrets(text))


def _manifest_map(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError("manifest must be source=id")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forex_edge_cli",
        description="Standalone research-only forex edge studies",
    )
    parser.add_argument("--config", default="configs/forex_edge_research.yaml")
    parser.add_argument("--store-root", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_bis = sub.add_parser("ingest-bis")
    ingest_bis.add_argument("--start", default="")
    ingest_bis.add_argument("--end", default="")
    ingest_bis.add_argument("--series-key", action="append")

    ingest_cftc = sub.add_parser("ingest-cftc")
    ingest_cftc.add_argument("--year", action="append", type=int)

    ingest_fred = sub.add_parser("ingest-fred")
    ingest_fred.add_argument("--dataset", choices=["spot", "rates"], default="spot")
    ingest_fred.add_argument("--start", default="")
    ingest_fred.add_argument("--end", default="")
    ingest_fred.add_argument("--series-id", action="append")

    dukascopy = sub.add_parser("import-dukascopy")
    dukascopy.add_argument("--file", required=True)
    dukascopy.add_argument("--symbol", default="")
    dukascopy.add_argument("--timezone", default="")
    dukascopy.add_argument("--schema", default="")
    dukascopy.add_argument("--delimiter", default=",")

    for command in ("quality-report", "run-portfolio", "run-fixing", "run-both"):
        child = sub.add_parser(command)
        child.add_argument("--manifest", action="append")
        child.add_argument("--out", default="")
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, object]:
    if args.command.startswith("ingest") or args.command == "import-dukascopy":
        raise BlockedDataError("PINNED_MANIFEST_REQUIRED")
    config = load_config(args.config)
    lane = {
        "quality-report": "both",
        "run-portfolio": "portfolio",
        "run-fixing": "fixing",
        "run-both": "both",
    }[args.command]
    out = Path(args.out) if args.out else Path("athena_research/forex_edge/output")
    paths = run_research(
        RunRequest(
            lane=lane,
            dataset_manifests=_manifest_map(args.manifest),
            output_root=out,
        ),
        config,
    )
    return {"status": "COMPLETED", "artifacts": [str(path) for path in paths]}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = _dispatch(args)
        print(json.dumps(redact_secrets(result), indent=2, sort_keys=True))
        statuses = {
            item.get("study_status")
            for item in result.get("results", [])
            if isinstance(item, dict)
        }
        return EXIT_NO_EDGE if statuses == {"COMPLETED_NO_EDGE"} else EXIT_COMPLETED
    except BlockedDataError as exc:
        print(json.dumps({"status": "BLOCKED_DATA", "reason": str(exc)}))
        return EXIT_BLOCKED
    except (ValueError, KeyError, pd.errors.ParserError) as exc:
        print(
            json.dumps(
                {"status": "INVALID_INPUT", "reason": redact_text(str(exc))}
            )
        )
        return EXIT_INVALID
    except requests.RequestException as exc:
        print(
            json.dumps(
                {"status": "PROVIDER_FAILURE", "reason": redact_text(str(exc))}
            )
        )
        return EXIT_PROVIDER


if __name__ == "__main__":
    sys.exit(main())
