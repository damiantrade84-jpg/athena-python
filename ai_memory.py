"""ai_memory.py — local semantic memory for trade setups (Phase 1).

Dependency-free embedding + retrieval over trades, journal entries, and
playbooks. Uses the hashing trick for embeddings (no neural model, no
external API) and pure-Python cosine similarity over vectors stored in
SQLite. Config-gated by `AI_MEMORY_ENABLED` (default False).

Safety:
- LOCAL EMBEDDINGS ONLY. Never calls OpenAI, Anthropic, or any external API
  to embed trade data. This is a hard safety rule for this module.
- Read-only retrieval. This module never mutates trades, gates, scoring, or
  execution. Retrieved analogues are advisory context only.
- Fail-closed: any storage/embedding error returns an empty result set.
- Per-pair memory: embeddings are tagged with `pair` so retrieval can be
  scoped to the same instrument.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any

try:
    from config import CONFIG
except Exception:  # pragma: no cover
    CONFIG = {}

log = logging.getLogger("athena.ai_memory")

_DIMENSIONS = 256
_DB_LOCK = threading.Lock()
_DEFAULT_DB_NAME = "ai_memory.db"


def is_memory_enabled() -> bool:
    try:
        return bool(CONFIG.get("AI_MEMORY_ENABLED", False))
    except Exception:
        return False


def _db_path() -> str:
    cfg_dir = CONFIG.get("AI_MEMORY_DB_PATH", "")
    if cfg_dir and isinstance(cfg_dir, str):
        return cfg_dir
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _DEFAULT_DB_NAME)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def embed_text(text: str, *, dimensions: int = _DIMENSIONS) -> list[float]:
    """Hashing-trick embedding: bag-of-words hashed into a fixed-size vector.

    Returns a unit-length vector. Dependency-free, deterministic, local.
    Never calls an external API.
    """
    tokens = _tokenize(text)
    vec = [0.0] * dimensions
    if not tokens:
        return vec
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dimensions
        sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
        vec[idx] += sign
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot  # vectors are already unit-normalized


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            pair TEXT,
            text TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            metadata_json TEXT,
            created_ts REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ai_memory_kind ON ai_memory_entries(kind)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ai_memory_pair ON ai_memory_entries(pair)")
    con.commit()


def _connect() -> sqlite3.Connection | None:
    try:
        path = _db_path()
        con = sqlite3.connect(path, timeout=15.0)
        con.row_factory = sqlite3.Row
        _ensure_schema(con)
        return con
    except Exception as exc:
        log.warning("[AI_MEMORY] failed to open db at %s: %s", _db_path(), exc)
        return None


def store_entry(
    *,
    kind: str,
    text: str,
    pair: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """Store an entry with its embedding. Returns the row id, or None on failure."""
    if not is_memory_enabled():
        return None
    if not text or not text.strip():
        return None
    try:
        vec = embed_text(text)
        with _DB_LOCK:
            con = _connect()
            if con is None:
                return None
            try:
                cur = con.execute(
                    """
                    INSERT INTO ai_memory_entries (kind, pair, text, vector_json, metadata_json, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(kind or "generic"),
                        str(pair or "") or None,
                        text,
                        json.dumps(vec),
                        json.dumps(metadata) if metadata else None,
                        time.time(),
                    ),
                )
                con.commit()
                return int(cur.lastrowid)
            finally:
                con.close()
    except Exception as exc:
        log.warning("[AI_MEMORY] store_entry failed: %s", exc)
        return None


def retrieve_similar(
    *,
    query_text: str,
    kind: str | None = None,
    pair: str | None = None,
    top_k: int = 5,
    min_similarity: float = 0.1,
) -> list[dict[str, Any]]:
    """Retrieve the top-k most similar entries. Fail-closed: returns [] on any error."""
    if not is_memory_enabled():
        return []
    if not query_text or not query_text.strip():
        return []
    try:
        query_vec = embed_text(query_text)
        with _DB_LOCK:
            con = _connect()
            if con is None:
                return []
            try:
                sql = "SELECT id, kind, pair, text, vector_json, metadata_json, created_ts FROM ai_memory_entries"
                clauses: list[str] = []
                params: list[Any] = []
                if kind:
                    clauses.append("kind = ?")
                    params.append(str(kind))
                if pair:
                    clauses.append("pair = ?")
                    params.append(str(pair))
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
                sql += " ORDER BY created_ts DESC LIMIT 1000"
                rows = con.execute(sql, params).fetchall()
            finally:
                con.close()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            try:
                vec = json.loads(row["vector_json"])
            except Exception:
                continue
            sim = _cosine(query_vec, vec)
            if sim < min_similarity:
                continue
            try:
                meta = json.loads(row["metadata_json"]) if row["metadata_json"] else None
            except Exception:
                meta = None
            entry = {
                "id": int(row["id"]),
                "kind": row["kind"],
                "pair": row["pair"],
                "text": row["text"],
                "similarity": round(float(sim), 4),
                "metadata": meta,
                "created_ts": float(row["created_ts"]),
            }
            scored.append((sim, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[: max(0, int(top_k))]]
    except Exception as exc:
        log.warning("[AI_MEMORY] retrieve_similar failed: %s", exc)
        return []


def build_context_block(
    *,
    query_text: str,
    kind: str | None = None,
    pair: str | None = None,
    top_k: int = 3,
    header: str = "=== SIMILAR HISTORICAL SETUP ANALOGUES (advisory, semantic retrieval) ===",
) -> str:
    """Return a prompt-ready context block of similar analogues.

    Empty string when disabled or no matches. Never raises.
    """
    if not is_memory_enabled():
        return ""
    try:
        entries = retrieve_similar(
            query_text=query_text,
            kind=kind,
            pair=pair,
            top_k=top_k,
        )
        if not entries:
            return ""
        lines = [header, ""]
        for i, e in enumerate(entries, 1):
            lines.append(f"[{i}] similarity={e['similarity']:.2f} pair={e.get('pair') or 'n/a'} kind={e.get('kind') or 'generic'}")
            text = (e.get("text") or "").strip().replace("\n", " ")
            if len(text) > 240:
                text = text[:240] + "..."
            lines.append(f"    {text}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


def reset_store_for_tests(db_path: str | None = None) -> None:
    """Test hook: drop and recreate the schema at the given path."""
    try:
        path = db_path or _db_path()
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
