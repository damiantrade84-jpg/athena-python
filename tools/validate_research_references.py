from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "docs" / "research" / "indicator_calibration_references.yaml"
REQUIRED_ENTRY_FIELDS = {
    "title",
    "author_source",
    "year",
    "verified_url_or_identifier",
    "citation",
    "used_for",
    "limitation",
    "directly_mapped_config_keys",
}


def _config_py_keys(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CONFIG"
            for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "CONFIG":
            value = node.value
        if not isinstance(value, ast.Dict):
            continue
        for key_node in value.keys:
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                keys.add(key_node.value)
    return keys


def _config_yaml_keys(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return set(data.keys()) if isinstance(data, dict) else set()


def _iter_entries(registry: dict[str, Any]):
    sections = registry.get("sections") or {}
    if not isinstance(sections, dict):
        return
    for section_name, section in sections.items():
        refs = (section or {}).get("references") or []
        for idx, entry in enumerate(refs):
            yield section_name, idx, entry


def validate_registry(path: Path = DEFAULT_REGISTRY) -> list[str]:
    errors: list[str] = []
    registry = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(registry, dict):
        return ["registry root must be a mapping"]

    sections = registry.get("sections")
    if not isinstance(sections, dict) or not sections:
        errors.append("sections must be a non-empty mapping")
        return errors

    known_keys = _config_py_keys(ROOT / "config.py") | _config_yaml_keys(ROOT / "config.yaml")
    entry_count = 0
    for section_name, idx, entry in _iter_entries(registry):
        entry_count += 1
        label = f"{section_name}.references[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be a mapping")
            continue
        missing = sorted(REQUIRED_ENTRY_FIELDS - set(entry))
        if missing:
            errors.append(f"{label} missing required fields: {', '.join(missing)}")
        for field in ("citation", "used_for", "limitation", "verified_url_or_identifier"):
            if not str(entry.get(field, "") or "").strip():
                errors.append(f"{label}.{field} must be non-empty")
        if entry.get("verified") is not True and bool(entry.get("production_supporting", False)):
            errors.append(f"{label} unverified references cannot be production-supporting")
        mapped = entry.get("directly_mapped_config_keys")
        if not isinstance(mapped, list):
            errors.append(f"{label}.directly_mapped_config_keys must be a list")
            continue
        for key in mapped:
            if not isinstance(key, str) or not key.strip():
                errors.append(f"{label}.directly_mapped_config_keys contains a non-string key")
            elif key not in known_keys:
                errors.append(f"{label} maps unknown config key: {key}")
    if entry_count == 0:
        errors.append("registry must contain at least one reference entry")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate research reference registry.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    args = parser.parse_args(argv)

    errors = validate_registry(Path(args.registry))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {args.registry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
