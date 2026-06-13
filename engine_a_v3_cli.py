from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine_a_v3.workflow import (
    DEFAULT_MANIFEST,
    promote_manifest_candidate,
    validate_manifest_group,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Engine A V3 validation and promotion")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--group", required=True)
    validate.add_argument("--horizon", required=True, choices=("intraday", "swing"))
    validate.add_argument("--force-refresh", action="store_true")
    validate.add_argument("--candidate-root")

    promote = sub.add_parser("promote")
    promote.add_argument("--candidate", required=True)
    promote.add_argument("--confirm-artifact-id", required=True)
    promote.add_argument("--registry-root")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--candidate", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "validate":
        path = validate_manifest_group(
            args.group,
            args.horizon,
            manifest_path=args.manifest,
            candidate_root=args.candidate_root,
            force_refresh=args.force_refresh,
        )
        artifact = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps({"path": str(path), "artifact": artifact}, indent=2))
        return 0 if artifact.get("status") == "PROMOTED" else 2
    if args.command == "promote":
        path = promote_manifest_candidate(
            args.candidate,
            expected_artifact_id=args.confirm_artifact_id,
            manifest_path=args.manifest,
            registry_root=args.registry_root,
        )
        print(json.dumps({"promotedPath": str(path)}, indent=2))
        return 0
    artifact = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    print(json.dumps(artifact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
