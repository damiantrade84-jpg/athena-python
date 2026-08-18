"""OPUS configuration loading and access.

Loads opus.yaml, deep-merges an optional git-ignored opus.local.yaml over it,
and exposes typed accessors. The engine never reads a raw dict key inline: a
missing key here raises loudly at load time rather than silently defaulting a
risk threshold to zero somewhere deep in a scan.
"""

from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
_BASE_PATH = _HERE / "opus.yaml"
_LOCAL_PATH = _HERE / "opus.local.yaml"

_lock = threading.Lock()
_cache: dict | None = None
_cache_stamp: tuple | None = None


class OpusConfigError(RuntimeError):
    """Raised when configuration is missing or structurally invalid."""


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _file_stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, 0)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise OpusConfigError(f"{path.name}: top level must be a mapping")
    return data


def load(force: bool = False) -> dict:
    """Return the merged OPUS config block, reloading if a file changed."""
    global _cache, _cache_stamp
    stamp = (_file_stamp(_BASE_PATH), _file_stamp(_LOCAL_PATH))
    with _lock:
        if not force and _cache is not None and _cache_stamp == stamp:
            return _cache

        base = _load_yaml(_BASE_PATH)
        local = _load_yaml(_LOCAL_PATH)
        merged = _deep_merge(base, local)

        block = merged.get("OPUS")
        if not isinstance(block, dict):
            raise OpusConfigError("opus.yaml: missing top-level 'OPUS' mapping")

        _validate(block)
        _cache = block
        _cache_stamp = stamp
        return block


def _validate(cfg: dict) -> None:
    required = [
        "UNIVERSE", "TIMEFRAMES", "BARS", "SESSIONS", "INDICATORS",
        "WEIGHTS", "COHERENCE", "REGIME_ROUTING", "GEOMETRY",
        "CALIBRATION", "COSTS", "GATES", "RISK", "EXECUTION", "STORE",
    ]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise OpusConfigError(f"opus.yaml: missing sections {missing}")

    if not isinstance(cfg["UNIVERSE"], list) or not cfg["UNIVERSE"]:
        raise OpusConfigError("opus.yaml: UNIVERSE must be a non-empty list")
    for entry in cfg["UNIVERSE"]:
        for key in ("symbol", "venue", "class"):
            if not entry.get(key):
                raise OpusConfigError(
                    f"opus.yaml: universe entry {entry!r} missing '{key}'"
                )

    geom = cfg["GEOMETRY"]
    if geom["min_stop_sigma"] >= geom["max_stop_sigma"]:
        raise OpusConfigError("GEOMETRY: min_stop_sigma must be < max_stop_sigma")
    if geom["min_rr"] <= 0:
        raise OpusConfigError("GEOMETRY: min_rr must be positive")

    gates = cfg["GATES"]
    for key in ("min_probability", "max_cost_r"):
        value = float(gates[key])
        if not 0.0 <= value <= 1.0:
            raise OpusConfigError(f"GATES.{key} must be within [0, 1]")

    risk = cfg["RISK"]
    if not 0.0 < float(risk["kelly_fraction"]) <= 1.0:
        raise OpusConfigError("RISK.kelly_fraction must be in (0, 1]")
    if float(risk["risk_pct_max"]) <= 0:
        raise OpusConfigError("RISK.risk_pct_max must be positive")


def get(path: str, default: Any = None) -> Any:
    """Dotted-path accessor, e.g. get('GATES.min_expectancy_r')."""
    node: Any = load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def require(path: str) -> Any:
    """Like `get`, but a missing key is a hard error.

    Used for every safety-relevant threshold so a typo in opus.local.yaml
    surfaces immediately instead of disabling a gate silently.
    """
    sentinel = object()
    value = get(path, sentinel)
    if value is sentinel:
        raise OpusConfigError(f"opus.yaml: required key '{path}' is missing")
    return value


def universe() -> list[dict]:
    return [dict(entry) for entry in load()["UNIVERSE"]]


def universe_map() -> dict[str, dict]:
    return {entry["symbol"]: entry for entry in universe()}


def timeframes_for(asset_class: str) -> dict:
    table = load()["TIMEFRAMES"]
    return dict(table.get(asset_class) or table["default"])


def cost_model(asset_class: str) -> dict:
    table = load()["COSTS"]
    return dict(table.get(asset_class) or table["default"])


def weights_for(archetype: str) -> dict:
    table = load()["WEIGHTS"]
    if archetype not in table:
        raise OpusConfigError(f"WEIGHTS: no weight set for archetype {archetype}")
    return dict(table[archetype])


def regime_multiplier(regime: str, archetype: str) -> float:
    table = load()["REGIME_ROUTING"]
    row = table.get(regime) or {}
    # An archetype absent from a regime row is disabled in that regime, not
    # neutral. Defaulting to 1.0 would let an unlisted setup trade anywhere.
    return float(row.get(archetype, 0.0))


def indicator(name: str) -> dict:
    block = load()["INDICATORS"]
    if name not in block:
        raise OpusConfigError(f"INDICATORS: missing block for {name}")
    return dict(block[name])


def sessions() -> dict:
    return dict(load()["SESSIONS"])


def is_live_mode() -> bool:
    """Live routing needs BOTH the config flag and an environment token.

    Two independent switches means neither a stray config edit nor a stray
    environment variable can arm live execution on its own.
    """
    if str(load().get("MODE", "paper")).strip().lower() != "live":
        return False
    return bool(os.environ.get("OPUS_LIVE_CONFIRM", "").strip())


def store_path() -> str:
    raw = str(load()["STORE"]["path"])
    path = Path(raw)
    if not path.is_absolute():
        path = _HERE.parent / raw
    return str(path)


__all__ = [
    "OpusConfigError", "load", "get", "require", "universe", "universe_map",
    "timeframes_for", "cost_model", "weights_for", "regime_multiplier",
    "indicator", "sessions", "is_live_mode", "store_path",
]
