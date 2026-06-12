"""Per-family SHADOW/DEMO promotion state (manual promotion only)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from athena_ase.data.costs import ModelFamily
from athena_ase.paths import ase_audit_log_path, promotion_state_path

log = logging.getLogger("ase.registry.promotion")

DeploymentState = Literal["SHADOW", "DEMO"]

_DEFAULT_FAMILIES: tuple[ModelFamily, ...] = (
    "forex",
    "crypto",
    "commodity",
    "equity",
    "index_etf",
)


def _default_state() -> dict[str, str]:
    return {family: "SHADOW" for family in _DEFAULT_FAMILIES}


def _load_raw() -> dict[str, object]:
    path = promotion_state_path()
    if not path.exists():
        return {"families": _default_state(), "watchMax": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("Corrupt promotion state at %s — resetting", path)
        return {"families": _default_state(), "watchMax": {}}
    if "families" not in data:
        data["families"] = _default_state()
    if "watchMax" not in data:
        data["watchMax"] = {}
    return data


def _save_raw(data: dict[str, object]) -> None:
    path = promotion_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _append_audit(action: str, family: str, *, operator: str = "system", detail: dict | None = None) -> None:
    path = ase_audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "family": family,
        "operator": operator,
        "detail": detail or {},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def get_family_state(family: str) -> DeploymentState:
    data = _load_raw()
    families = data.get("families") or {}
    state = str(families.get(family, "SHADOW")).upper()
    return "DEMO" if state == "DEMO" else "SHADOW"


def list_family_states() -> dict[str, DeploymentState]:
    data = _load_raw()
    families = dict(_default_state())
    families.update({str(k): ("DEMO" if str(v).upper() == "DEMO" else "SHADOW") for k, v in (data.get("families") or {}).items()})
    return families  # type: ignore[return-value]


def promote_family(family: str, *, operator: str = "manual") -> DeploymentState:
    data = _load_raw()
    families = dict(data.get("families") or _default_state())
    families[family] = "DEMO"
    data["families"] = families
    _save_raw(data)
    _append_audit("promote", family, operator=operator)
    return "DEMO"


def demote_family(family: str, *, operator: str = "manual") -> DeploymentState:
    data = _load_raw()
    families = dict(data.get("families") or _default_state())
    families[family] = "SHADOW"
    data["families"] = families
    _save_raw(data)
    _append_audit("demote", family, operator=operator)
    return "SHADOW"


def set_watch_max(family: str, active: bool) -> None:
    data = _load_raw()
    watch = dict(data.get("watchMax") or {})
    if active:
        watch[family] = True
    else:
        watch.pop(family, None)
    data["watchMax"] = watch
    _save_raw(data)
    if active:
        _append_audit("watch_max_on", family)


def is_watch_max(family: str) -> bool:
    data = _load_raw()
    watch = data.get("watchMax") or {}
    return bool(watch.get(family))


def summary() -> dict[str, object]:
    return {
        "families": list_family_states(),
        "watchMax": {k: True for k, v in (_load_raw().get("watchMax") or {}).items() if v},
    }
