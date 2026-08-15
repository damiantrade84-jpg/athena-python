"""Holdout single-shot registry (ASE Phase 4 T4.1)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena_ase.exceptions import HoldoutAlreadyEvaluated
from athena_ase.paths import default_ase_state_root

log = logging.getLogger("ase.registry.holdout")


def default_holdout_path() -> Path:
    return default_ase_state_root() / "holdout_registry.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HoldoutRecord:
    family: str
    horizon: str
    evaluated_at: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    operator: str = "system"
    artifact_version: str = ""
    artifact_hash: str = ""


class HoldoutRegistry:
    def __init__(self, holdout: dict[str, HoldoutRecord] | None = None):
        self.holdout = holdout or {}

    @staticmethod
    def _key(family: str, horizon: str) -> str:
        return f"{family}:{horizon}"

    @classmethod
    def load(cls, path: Path | None = None) -> HoldoutRegistry:
        p = path or default_holdout_path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            holdout = {k: HoldoutRecord(**v) for k, v in (raw.get("holdout") or {}).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            # Fail closed like the promotion state: an unreadable registry is
            # an empty one, so promotion blocks on holdout_not_evaluated
            # instead of crashing the check.
            log.warning("Unreadable holdout registry at %s; failing closed: %s", p, exc)
            return cls()
        return cls(holdout=holdout)

    def save(self, path: Path | None = None) -> Path:
        p = path or default_holdout_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"holdout": {k: asdict(v) for k, v in self.holdout.items()}}
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return p

    def holdout_done(self, family: str, horizon: str) -> bool:
        return self._key(family, horizon) in self.holdout

    def record_holdout_eval(
        self,
        *,
        family: str,
        horizon: str,
        passed: bool,
        metrics: dict[str, Any] | None = None,
        operator: str = "system",
        artifact_version: str = "",
        artifact_hash: str = "",
    ) -> HoldoutRecord:
        key = self._key(family, horizon)
        if key in self.holdout:
            raise HoldoutAlreadyEvaluated(family, horizon)
        rec = HoldoutRecord(
            family=family,
            horizon=horizon,
            evaluated_at=_now_iso(),
            passed=passed,
            metrics=metrics or {},
            operator=operator,
            artifact_version=str(artifact_version or ""),
            artifact_hash=str(artifact_hash or "").lower(),
        )
        self.holdout[key] = rec
        return rec
