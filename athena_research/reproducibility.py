"""
Research Lab reproducibility metadata helpers.

This module is metadata-only. It must not import live execution modules or
change strategy/backtest semantics.
"""

from __future__ import annotations

import hashlib
import json
import platform as platform_module
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd


RunCommand = Callable[..., subprocess.CompletedProcess]


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def stable_json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )


def hash_stable_json(value: Any) -> str:
    return hashlib.sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def hash_file_bytes(path: str | Path) -> str | None:
    p = Path(path)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _safe_number(value: Any) -> float | int | None:
    if pd.isna(value):
        return None
    f = float(value)
    if f.is_integer():
        return int(f)
    return f


def hash_ohlcv_frame(df: pd.DataFrame | None) -> str | None:
    """Hash canonical OHLCV candle content, excluding volatile run metadata."""
    if df is None:
        return None
    columns = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    if not columns:
        return hash_stable_json([])

    if isinstance(df.index, pd.DatetimeIndex):
        timestamps = pd.to_datetime(df.index, utc=True)
        ts_values = [ts.isoformat() for ts in timestamps]
    else:
        ts_values = [str(v) for v in df.index]

    records: list[dict[str, Any]] = []
    work = df[columns]
    for pos, (_, row) in enumerate(work.iterrows()):
        item: dict[str, Any] = {"timestamp": ts_values[pos]}
        for col in columns:
            item[col] = _safe_number(row[col])
        records.append(item)
    return hash_stable_json(records)


def _git_run(
    run_cmd: RunCommand,
    repo_root: Path,
    args: list[str],
) -> str:
    proc = run_cmd(
        args,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail or f"git command failed: {' '.join(args)}")
    return str(proc.stdout or "").strip()


def collect_git_metadata(
    repo_root: str | Path = ".",
    *,
    run_cmd: RunCommand = subprocess.run,
) -> dict[str, Any]:
    root = Path(repo_root)
    try:
        commit = _git_run(run_cmd, root, ["git", "rev-parse", "HEAD"]) or None
        branch = _git_run(run_cmd, root, ["git", "branch", "--show-current"]) or None
        status = _git_run(run_cmd, root, ["git", "status", "--porcelain"])
        dirty = bool(status.strip())
        diff_hash = None
        if dirty:
            diff = _git_run(run_cmd, root, ["git", "diff", "--binary", "HEAD"])
            diff_hash = hashlib.sha256((status + "\n" + diff).encode("utf-8")).hexdigest()
        return {
            "git_commit": commit,
            "git_branch": branch,
            "git_dirty": dirty,
            "git_diff_hash": diff_hash,
            "unavailable_reason": None,
        }
    except FileNotFoundError:
        return {
            "git_commit": None,
            "git_branch": None,
            "git_dirty": None,
            "git_diff_hash": None,
            "unavailable_reason": "git_unavailable",
        }
    except Exception as exc:
        return {
            "git_commit": None,
            "git_branch": None,
            "git_dirty": None,
            "git_diff_hash": None,
            "unavailable_reason": f"git_metadata_unavailable:{exc}",
        }


def collect_source_hashes(paths: Iterable[str | Path], *, repo_root: str | Path = ".") -> dict[str, str | None]:
    root = Path(repo_root)
    out: dict[str, str | None] = {}
    for raw in paths:
        p = Path(raw)
        full = p if p.is_absolute() else root / p
        key = str(p).replace("\\", "/")
        out[key] = hash_file_bytes(full)
    return out


def build_config_metadata(config_path: str | Path, effective_config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "config_path": str(config_path),
        "config_hash": hash_file_bytes(config_path),
        "effective_config_hash": hash_stable_json(effective_config),
        "config_version": effective_config.get("version") if isinstance(effective_config, Mapping) else None,
    }


def _package_version(dist_name: str) -> str | None:
    try:
        return importlib_metadata.version(dist_name)
    except importlib_metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def collect_environment_metadata() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform_module.platform(),
        "packages": {
            "pandas": _package_version("pandas"),
            "numpy": _package_version("numpy"),
            "vectorbt": _package_version("vectorbt"),
            "yaml": _package_version("PyYAML"),
            "requests": _package_version("requests"),
            "yfinance": _package_version("yfinance"),
            "MetaTrader5": _package_version("MetaTrader5"),
        },
    }


def build_cost_metadata(fees_cfg: Mapping[str, Any], slippage_cfg: Mapping[str, Any]) -> dict[str, Any]:
    per_side: dict[str, float | None] = {}
    for key, value in dict(fees_cfg or {}).items():
        try:
            per_side[str(key)] = max(0.0, float(value) / 2.0)
        except (TypeError, ValueError):
            per_side[str(key)] = None
    return {
        "fee_model": "config_round_trip_converted_to_per_side",
        "fees_config": dict(fees_cfg or {}),
        "per_side_fee_by_asset_class": per_side,
        "slippage": dict(slippage_cfg or {}),
    }


def _spec_payload(spec: Any) -> dict[str, Any]:
    if isinstance(spec, Mapping):
        return dict(spec)
    return {
        "family": getattr(spec, "family", ""),
        "name": getattr(spec, "name", ""),
        "params": getattr(spec, "params", {}),
        "direction": getattr(spec, "direction", ""),
        "tags": getattr(spec, "tags", []),
    }


def build_strategy_registry_metadata(specs: Iterable[Any] | None = None) -> dict[str, Any]:
    from athena_research import strategies

    registry_payload = {
        name: {
            "family": family,
            "callable": f"{fn.__module__}.{fn.__qualname__}",
        }
        for name, (family, fn) in sorted(strategies.STRATEGY_REGISTRY.items())
    }
    family_payload = {
        name: list(values)
        for name, values in sorted(strategies.FAMILY_STRATEGIES.items())
    }
    specs_payload = [_spec_payload(spec) for spec in (specs or [])]
    return {
        "registry_hash": hash_stable_json(registry_payload),
        "family_map_hash": hash_stable_json(family_payload),
        "strategy_specs_hash": hash_stable_json(specs_payload),
        "formula_source": "athena_research.strategies:pandas_native",
    }


def build_session_calendar_metadata() -> dict[str, str]:
    return {
        "timezone": "UTC",
        "session_policy": "fixed_utc_windows",
        "dst_policy": "not_adjusted_or_not_declared",
    }
