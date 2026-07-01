"""prompt_store.py — external prompt loader with hot-reload, hashing, and fail-closed fallback.

Config-gated by `AI_PROMPT_STORE_ENABLED` (default False). When disabled, every
call returns the supplied fallback text — zero behavior change.

When enabled, the loader looks for `<store_dir>/<surface>.md` (or `.txt`) and
returns its contents with optional YAML frontmatter stripped. Hot-reload is
mtime-based; the cache is checked on every call. File I/O errors fall back to
the supplied fallback text and log a warning — never raises.

Audit: every call returns the sha256 of the rendered text so callers can stamp
it into the existing ai_review_logger audit trail via the optional
`prompt_source` / `prompt_text_hash` fields.

Safety: this module is read-only. It never writes, executes, or imports app
code. It is safe to call from any advisory AI surface.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Tuple

log = logging.getLogger("athena.prompt_store")

# Cache: surface -> (text, mtime, sha256_hex, source)
_CACHE: dict[str, Tuple[str, float, str, str]] = {}
_CACHE_LOCK = threading.Lock()

_DEFAULT_STORE_DIR_NAME = "prompts"


def _resolve_store_dir() -> str:
    """Resolve the prompt store directory. Never raises."""
    try:
        from config import CONFIG  # local import to avoid cycles

        cfg_dir = CONFIG.get("AI_PROMPT_STORE_DIR", _DEFAULT_STORE_DIR_NAME)
    except Exception:
        cfg_dir = _DEFAULT_STORE_DIR_NAME
    if not cfg_dir:
        cfg_dir = _DEFAULT_STORE_DIR_NAME
    if os.path.isabs(cfg_dir):
        return cfg_dir
    # Default to a sibling directory next to this module.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg_dir)


def _is_enabled() -> bool:
    """Return True only if the feature is explicitly enabled in CONFIG."""
    try:
        from config import CONFIG

        return bool(CONFIG.get("AI_PROMPT_STORE_ENABLED", False))
    except Exception:
        return False


def _strip_frontmatter(text: str) -> str:
    """Strip a leading YAML frontmatter block (--- ... ---) from a prompt file.

    Returns the text unchanged if no frontmatter is present. Only strips a
    frontmatter that starts on the first byte of the file.
    """
    if not text or not text.startswith("---"):
        return text
    # Find the closing --- on its own line.
    rest = text[3:]
    if rest.startswith("\r\n"):
        rest = rest[2:]
    elif rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return text
    body = rest[end + 4 :]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return body


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _candidate_paths(store_dir: str, surface: str) -> list[str]:
    safe = os.path.basename(surface or "")
    if not safe or safe != surface:
        # Reject path traversal / absolute surfaces; only bare names allowed.
        return []
    return [
        os.path.join(store_dir, f"{safe}.md"),
        os.path.join(store_dir, f"{safe}.txt"),
    ]


def load_prompt(
    surface: str,
    *,
    fallback: str,
    version: str | None = None,
) -> Tuple[str, str, str]:
    """Return (prompt_text, source, sha256_hex).

    source is one of:
      - "disabled"  — feature off, returned fallback text
      - "file"      — loaded from store_dir/<surface>.md
      - "fallback"  — feature on but file missing/unreadable, returned fallback text

    Never raises. Hot-reloads on file mtime change.

    `version` is accepted for future audit stamping but does not affect loading;
    the on-disk file is the source of truth when enabled.
    """
    text = fallback or ""
    if not _is_enabled():
        return text, "disabled", _hash_text(text)

    store_dir = _resolve_store_dir()
    path = None
    for cand in _candidate_paths(store_dir, surface):
        try:
            if os.path.isfile(cand):
                path = cand
                break
        except Exception:
            continue

    if path is None:
        return text, "fallback", _hash_text(text)

    try:
        mtime = float(os.path.getmtime(path))
    except Exception:
        mtime = 0.0

    with _CACHE_LOCK:
        cached = _CACHE.get(surface)
        if cached and cached[1] == mtime:
            # cache tuple is (text, mtime, hash, source) — return (text, source, hash)
            return cached[0], cached[3], cached[2]

    try:
        with open(path, "r", encoding="utf-8") as rf:
            raw = rf.read()
        body = _strip_frontmatter(raw)
        h = _hash_text(body)
        with _CACHE_LOCK:
            _CACHE[surface] = (body, mtime, h, "file")
        return body, "file", h
    except Exception as exc:
        log.warning("[PROMPT_STORE] failed to load %s: %s", path, exc)
        return text, "fallback", _hash_text(text)


def get_prompt_hash(text: str) -> str:
    """Return sha256 hex of prompt text (helper for audit callers)."""
    return _hash_text(text)


def clear_cache() -> None:
    """Test hook — drop the in-memory cache."""
    with _CACHE_LOCK:
        _CACHE.clear()


def cached_surfaces() -> list[str]:
    """Test hook — return sorted list of currently cached surfaces."""
    with _CACHE_LOCK:
        return sorted(_CACHE.keys())
