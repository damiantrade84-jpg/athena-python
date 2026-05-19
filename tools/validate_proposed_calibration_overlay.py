#!/usr/bin/env python3
"""Validate configs/proposed_calibration_overlay.yaml (research-only, not live)."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERLAY = ROOT / "configs" / "proposed_calibration_overlay.yaml"
CONFIG_PY = ROOT / "config.py"
CONFIG_YAML = ROOT / "config.yaml"

# Keys that must never be enabled to true in the overlay without explicit review.
_EXECUTION_DANGER_KEYS = {
    "REAL_ORDERS_ALLOWED",
    "AUTO_EXECUTE",
    "AUTO_TRADE_ENABLED",
}
_EXECUTION_DANGER_PATHS = {
    ("PAPER_SOAK", "REAL_ORDERS_ALLOWED"),
}

# Top-level keys allowed in overlay without being in CONFIG (metadata / research structs).
_RESEARCH_ONLY_TOP_KEYS = {
    "PROPOSED_OVERLAY_META",
    "_proposed_gate_adjustments",
    "_proposed_holds",
    "_proposed_active_settings",
}

_REQUIRED_PROPOSAL_FIELDS = (
    "evidence_files",
    "rollback_rule",
    "experiment_id",
)

_PROTECTED_LIVE_FILES = (
    "config.py",
    "config.yaml",
    "scoring.py",
    "factor_scoring.py",
    "forex_scoring.py",
    "market_structure.py",
    "execution.py",
    "risk_engine.py",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return doc


def _flatten_config_keys(cfg: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in cfg.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        keys.add(path)
        if isinstance(value, dict):
            keys.update(_flatten_config_keys(value, path))
    return keys


def _known_config_keys() -> set[str]:
    try:
        from config import CONFIG

        return _flatten_config_keys(dict(CONFIG))
    except Exception:
        return set()


def _collect_scalar_overrides(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key.startswith("_") or key in _RESEARCH_ONLY_TOP_KEYS:
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_collect_scalar_overrides(value, path))
        else:
            out[path] = value
    return out


def _walk_bool_paths(obj: Any, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], bool]]:
    found: list[tuple[tuple[str, ...], bool]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = prefix + (str(key),)
            if isinstance(value, bool):
                found.append((path, value))
            elif isinstance(value, dict):
                found.extend(_walk_bool_paths(value, path))
    return found


def _check_not_auto_imported(errors: list[str]) -> None:
    for path in (CONFIG_PY, CONFIG_YAML):
        text = path.read_text(encoding="utf-8")
        if "proposed_calibration_overlay" in text:
            errors.append(f"{path} references proposed_calibration_overlay — must not auto-import overlay")


def _check_meta(doc: dict[str, Any], errors: list[str]) -> None:
    meta = doc.get("PROPOSED_OVERLAY_META")
    if not isinstance(meta, dict):
        errors.append("missing PROPOSED_OVERLAY_META mapping")
        return
    if meta.get("import_into_live_config") is not False:
        errors.append("PROPOSED_OVERLAY_META.import_into_live_config must be false")
    if meta.get("status") not in {"proposed_inactive", "proposed"}:
        errors.append(f"unexpected overlay status: {meta.get('status')!r}")
    if meta.get("live_behavior_changed_by_this_file") is not False:
        errors.append("PROPOSED_OVERLAY_META.live_behavior_changed_by_this_file must be false")


def _check_proposals(doc: dict[str, Any], errors: list[str]) -> None:
    proposals = doc.get("_proposed_gate_adjustments")
    if not isinstance(proposals, list):
        errors.append("_proposed_gate_adjustments must be a list")
        return
    for item in proposals:
        if not isinstance(item, dict):
            errors.append("gate adjustment entry must be a mapping")
            continue
        if item.get("apply") is not False:
            errors.append(f"{item.get('proposal_id')}: apply must be false")
        for field in _REQUIRED_PROPOSAL_FIELDS:
            if field not in item:
                errors.append(f"{item.get('proposal_id')}: missing {field}")
        if "baseline_expectancy_r" not in item and "baseline_metric" not in item:
            errors.append(f"{item.get('proposal_id')}: missing baseline metric")
        cohort = item.get("cohort") or {}
        if isinstance(cohort, dict) and cohort.get("accepted_n", 0) < 30:
            errors.append(
                f"{item.get('proposal_id')}: accepted_n<30 — must stay exploratory only (apply:false)"
            )


def _check_active_settings(doc: dict[str, Any], errors: list[str]) -> None:
    active = doc.get("_proposed_active_settings")
    if not isinstance(active, dict):
        errors.append("missing _proposed_active_settings mapping")
        return
    for key, meta in active.items():
        if not isinstance(meta, dict):
            errors.append(f"_proposed_active_settings.{key} must be a mapping")
            continue
        for req in ("evidence_file", "metric", "accepted_sample_size", "rollback_rule", "live_change_allowed"):
            if req not in meta:
                errors.append(f"_proposed_active_settings.{key}: missing {req}")
        if meta.get("live_change_allowed") is not False:
            errors.append(f"_proposed_active_settings.{key}: live_change_allowed must be false")


def _check_overlay_keys(doc: dict[str, Any], known: set[str], errors: list[str]) -> None:
    scalars = _collect_scalar_overrides(doc)
    for path, _value in scalars.items():
        top = path.split(".")[0]
        if top in _RESEARCH_ONLY_TOP_KEYS:
            continue
        if path not in known and top not in known:
            errors.append(f"unknown overlay key (not in CONFIG): {path}")


def _check_execution_safety(doc: dict[str, Any], errors: list[str]) -> None:
    for path, value in _walk_bool_paths(doc):
        joined = ".".join(path)
        if path and path[0] in _EXECUTION_DANGER_KEYS and value is True:
            errors.append(f"execution danger key enabled in overlay: {joined}")
        if path in _EXECUTION_DANGER_PATHS and value is True:
            errors.append(f"execution danger path enabled in overlay: {joined}")
    if doc.get("REAL_ORDERS_ALLOWED") is True:
        errors.append("REAL_ORDERS_ALLOWED must not be true in overlay")
    if isinstance(doc.get("PAPER_SOAK"), dict) and doc["PAPER_SOAK"].get("REAL_ORDERS_ALLOWED") is True:
        errors.append("PAPER_SOAK.REAL_ORDERS_ALLOWED must not be true in overlay")


def _check_inline_evidence_comments(text: str, errors: list[str]) -> None:
    if "Evidence:" not in text and "_proposed_active_settings" not in text:
        errors.append("overlay missing Evidence metadata (comments or _proposed_active_settings)")


def _check_protected_live_files_unchanged(errors: list[str]) -> None:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", *_PROTECTED_LIVE_FILES],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        errors.append(f"could not verify protected live diffs: {exc}")
        return
    changed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if changed:
        errors.append(
            "protected live/config/scoring/execution files have unstaged diffs: "
            + ", ".join(changed)
        )


def validate_overlay(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"overlay not found: {path}"]
    text = path.read_text(encoding="utf-8")
    try:
        doc = _load_yaml(path)
    except Exception as exc:
        return [f"YAML parse error: {exc}"]

    _check_not_auto_imported(errors)
    _check_meta(doc, errors)
    _check_proposals(doc, errors)
    _check_active_settings(doc, errors)
    _check_execution_safety(doc, errors)
    _check_inline_evidence_comments(text, errors)
    _check_protected_live_files_unchanged(errors)

    known = _known_config_keys()
    if known:
        _check_overlay_keys(doc, known, errors)

    # Top-level research keys must match _proposed_active_settings
    active = doc.get("_proposed_active_settings") or {}
    for key in ("CALIBRATION_DIAGNOSTICS_ENABLED", "CALIBRATION_DIAGNOSTICS_PATH"):
        if key in doc and key not in active:
            errors.append(f"{key} present but missing from _proposed_active_settings")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate proposed calibration overlay YAML.")
    parser.add_argument("--overlay", default=str(DEFAULT_OVERLAY))
    args = parser.parse_args(argv)
    path = Path(args.overlay)
    errors = validate_overlay(path)
    if errors:
        print(f"FAIL: {path} ({len(errors)} issue(s))")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"OK: {path} — overlay valid, not auto-imported, research-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
