"""Pure validation + YAML persistence for the Engine A Exit Strategy tab.

Standalone (imports only exit_policy + stdlib; never athena.py/config.py, whose
import aborts on the real-orders gate) so the validation and the comment-preserving
YAML write are unit-testable. The Flask route in athena.py calls these, then
mutates the in-memory CONFIG.
"""

from __future__ import annotations

import re

import exit_policy

# Single-line flow-style keys in config.yaml this tab owns. ENGINE_A_TIME_EXIT_BARS
# is intentionally excluded (block-style; not edited here).
EXIT_MODE_YAML_KEYS = (
    "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT",
    "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP",
    "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP",
)


def _coerce_pip(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def validate_exit_mode_updates(d: dict, known_groups) -> tuple[dict, list[str]]:
    """Validate a POST body. Returns (updates, errors).

    updates maps CONFIG keys -> sanitized values (only for keys present in d).
    Modes are checked against exit_policy.VALID_EXIT_MODES; groups against
    known_groups; pip bounds must be positive numbers with min <= max. Empty/
    unusable pip entries are dropped (not persisted). Any invalid entry appends
    an error and the caller rejects the whole POST (HTTP 400).
    """
    updates: dict = {}
    errors: list[str] = []
    known = set(known_groups)

    if "globalDefault" in d:
        gd = exit_policy.normalize_mode(d.get("globalDefault"))
        if gd is None:
            errors.append(f"globalDefault is not a valid exit mode: {d.get('globalDefault')!r}")
        else:
            updates["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] = gd

    if "byScoreGroup" in d:
        raw = d.get("byScoreGroup") or {}
        clean: dict = {}
        if not isinstance(raw, dict):
            errors.append("byScoreGroup must be an object")
        else:
            for group, mode in raw.items():
                if group not in known:
                    errors.append(f"byScoreGroup: unknown score group {group!r}")
                    continue
                norm = exit_policy.normalize_mode(mode)
                if norm is None:
                    errors.append(f"byScoreGroup[{group}]: invalid mode {mode!r} ({mode})")
                    continue
                clean[group] = norm
        updates["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] = clean

    if "advisablePipByScoreGroup" in d:
        raw = d.get("advisablePipByScoreGroup") or {}
        clean = {}
        if not isinstance(raw, dict):
            errors.append("advisablePipByScoreGroup must be an object")
        else:
            for group, band in raw.items():
                if group not in known:
                    errors.append(f"advisablePipByScoreGroup: unknown score group {group!r}")
                    continue
                band = band or {}
                lo = _coerce_pip(band.get("min_pip")) if "min_pip" in band else None
                hi = _coerce_pip(band.get("max_pip")) if "max_pip" in band else None
                if lo is None and hi is None:
                    continue  # nothing usable -> drop the entry
                if lo is not None and hi is not None and lo > hi:
                    errors.append(
                        f"advisablePipByScoreGroup[{group}]: min_pip ({lo}) > max_pip ({hi})"
                    )
                    continue
                entry = {}
                if lo is not None:
                    entry["min_pip"] = lo
                if hi is not None:
                    entry["max_pip"] = hi
                clean[group] = entry
        updates["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] = clean

    return updates, errors


def persist_exit_mode_config_yaml(cfg_path: str, current: dict) -> None:
    """Write the owned keys back to config.yaml, preserving inline comments.

    Renders dict values as single-line flow YAML and scalars bare, then does a
    single-line regex replace per key (mirrors athena._persist_scan_settings_yaml).
    """
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    for key in EXIT_MODE_YAML_KEYS:
        if key not in current:
            continue
        value = current[key]
        if isinstance(value, dict):
            rendered = _yaml.safe_dump(value, default_flow_style=True).strip()
        else:
            rendered = str(value)
        content, count = re.subn(
            rf"^({re.escape(key)}\s*:\s*)([^#\n]+?)(\s*(?:#.*)?)$",
            lambda m, v=rendered: f"{m.group(1)}{v}{m.group(3)}",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if count == 0:
            raise ValueError(f"config.yaml: {key} not found")

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)
