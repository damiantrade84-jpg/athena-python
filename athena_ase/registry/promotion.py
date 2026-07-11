"""Per-family/horizon SHADOW/DEMO deployment bindings."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class DeploymentBinding:
    family: str
    horizon: str
    state: DeploymentState
    version: str
    artifact_hash: str
    promoted_at: str
    operator: str


def _default_state() -> dict[str, str]:
    return {family: "SHADOW" for family in _DEFAULT_FAMILIES}


def _binding_key(family: str, horizon: str) -> str:
    return f"{family}:{horizon}"


def _empty_state() -> dict[str, object]:
    return {"families": _default_state(), "bindings": {}, "watchMax": {}}


def _load_raw() -> dict[str, object]:
    path = promotion_state_path()
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        log.warning("Unreadable promotion state at %s; failing closed", path)
        return _empty_state()
    if not isinstance(data, dict):
        log.warning("Invalid promotion state at %s; failing closed", path)
        return _empty_state()
    data.setdefault("families", _default_state())
    data.setdefault("bindings", {})
    data.setdefault("watchMax", {})
    return data


def _save_raw(data: dict[str, object]) -> None:
    path = promotion_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def _append_audit(
    action: str,
    family: str,
    *,
    operator: str = "system",
    detail: dict | None = None,
) -> None:
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


def get_deployment_binding(family: str, horizon: str) -> DeploymentBinding | None:
    bindings = _load_raw().get("bindings") or {}
    raw = bindings.get(_binding_key(family, horizon)) if isinstance(bindings, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        binding = DeploymentBinding(**raw)
    except (TypeError, ValueError):
        return None
    if binding.state != "DEMO" or binding.family != family or binding.horizon != horizon:
        return None
    return binding


def get_family_state(family: str) -> DeploymentState:
    bindings = _load_raw().get("bindings") or {}
    if isinstance(bindings, dict):
        for raw in bindings.values():
            if (
                isinstance(raw, dict)
                and raw.get("family") == family
                and raw.get("state") == "DEMO"
            ):
                return "DEMO"
    return "SHADOW"


def get_deployment_state(family: str, horizon: str) -> DeploymentState:
    return "DEMO" if get_deployment_binding(family, horizon) is not None else "SHADOW"


def execution_binding_check(
    *, family: str, horizon: str, version: str, artifact_hash: str
) -> tuple[bool, str]:
    binding = get_deployment_binding(family, horizon)
    if binding is None:
        bindings = _load_raw().get("bindings") or {}
        family_bound = any(
            isinstance(raw, dict)
            and raw.get("family") == family
            and raw.get("state") == "DEMO"
            for raw in (bindings.values() if isinstance(bindings, dict) else ())
        )
        return False, "horizon_not_promoted" if family_bound else "shadow_not_promoted"
    if binding.version != version:
        return False, "artifact_version_mismatch"
    if not artifact_hash or binding.artifact_hash != artifact_hash:
        return False, "artifact_hash_mismatch"
    return True, "ok"


def list_family_states() -> dict[str, DeploymentState]:
    return {family: get_family_state(family) for family in _DEFAULT_FAMILIES}


def promote_family(
    family: str,
    *,
    horizon: str,
    version: str,
    artifact_hash: str,
    operator: str = "manual",
) -> DeploymentState:
    if family not in _DEFAULT_FAMILIES:
        raise ValueError(f"unsupported ASE family: {family!r}")
    if horizon not in ("intraday", "swing"):
        raise ValueError(f"unsupported ASE horizon: {horizon!r}")
    clean_version = str(version or "").strip()
    clean_hash = str(artifact_hash or "").strip().lower()
    if not clean_version or Path(clean_version).name != clean_version:
        raise ValueError("artifact version is required")
    if not clean_hash:
        raise ValueError("artifact hash is required")

    data = _load_raw()
    binding = DeploymentBinding(
        family=family,
        horizon=horizon,
        state="DEMO",
        version=clean_version,
        artifact_hash=clean_hash,
        promoted_at=datetime.now(timezone.utc).isoformat(),
        operator=operator,
    )
    bindings = dict(data.get("bindings") or {})
    bindings[_binding_key(family, horizon)] = asdict(binding)
    data["bindings"] = bindings
    _save_raw(data)
    _append_audit("promote", family, operator=operator, detail=asdict(binding))
    return "DEMO"


def demote_family(
    family: str,
    *,
    horizon: str | None = None,
    operator: str = "manual",
) -> DeploymentState:
    data = _load_raw()
    bindings = dict(data.get("bindings") or {})
    for key, raw in list(bindings.items()):
        if not isinstance(raw, dict) or raw.get("family") != family:
            continue
        if horizon is not None and raw.get("horizon") != horizon:
            continue
        updated = dict(raw)
        updated["state"] = "SHADOW"
        bindings[key] = updated
    data["bindings"] = bindings
    _save_raw(data)
    _append_audit("demote", family, operator=operator, detail={"horizon": horizon})
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
    return bool(watch.get(family)) if isinstance(watch, dict) else False


def summary() -> dict[str, object]:
    data = _load_raw()
    return {
        "families": list_family_states(),
        "bindings": data.get("bindings") or {},
        "watchMax": {
            k: True
            for k, v in (data.get("watchMax") or {}).items()
            if v
        },
    }
