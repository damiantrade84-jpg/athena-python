from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class FreezeStoreError(RuntimeError):
    """Immutable store conflict or missing artifact."""


def hash_stable_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(relative: str) -> Path:
    return repo_root() / relative


def write_json_immutable(path: Path, data: Any) -> str:
    import hashlib

    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if path.exists():
        existing = hashlib.sha256(path.read_bytes()).hexdigest()
        if existing != digest:
            raise FreezeStoreError(f"refusing to overwrite conflicting content: {path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.encode("utf-8"))
    return digest


def write_json_mutable(path: Path, data: Any) -> str:
    import hashlib

    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.encode("utf-8"))
    return digest


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def chunk_path(
    raw_root: Path,
    slug: str,
    timeframe: str,
    chunk_start: datetime,
    chunk_end: datetime,
) -> Path:
    start = chunk_start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = chunk_end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return raw_root / slug / timeframe / "chunks" / f"{start}__{end}.json"


def state_path(raw_root: Path, slug: str, timeframe: str) -> Path:
    return raw_root / slug / timeframe / "resume_state.json"


def merged_raw_path(raw_root: Path, slug: str, timeframe: str) -> Path:
    """Legacy fixed-path merge cache. Preserved for provenance only; not authoritative."""
    return raw_root / slug / timeframe / "merged_raw.json"


def merged_snapshots_dir(raw_root: Path, slug: str, timeframe: str) -> Path:
    return raw_root / slug / timeframe / "merged" / "snapshots"


def latest_merge_pointer_path(raw_root: Path, slug: str, timeframe: str) -> Path:
    return raw_root / slug / timeframe / "merged" / "latest.json"


def chunk_dir_path(raw_root: Path, slug: str, timeframe: str) -> Path:
    return raw_root / slug / timeframe / "chunks"


def list_chunk_files(raw_root: Path, slug: str, timeframe: str) -> list[Path]:
    chunk_dir = chunk_dir_path(raw_root, slug, timeframe)
    if not chunk_dir.exists():
        return []
    return sorted(chunk_dir.glob("*.json"))


def inventory_chunk_set(raw_root: Path, slug: str, timeframe: str) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in list_chunk_files(raw_root, slug, timeframe):
        bars = read_json(path)
        times = [int(bar["time"]) for bar in bars] if bars else []
        inventory.append(
            {
                "filename": path.name,
                "sha256": hash_file_bytes(path),
                "row_count": len(bars),
                "start_timestamp": datetime.fromtimestamp(min(times), tz=timezone.utc).isoformat() if times else None,
                "end_timestamp": datetime.fromtimestamp(max(times), tz=timezone.utc).isoformat() if times else None,
            }
        )
    return inventory


def compute_chunk_set_hash(raw_root: Path, slug: str, timeframe: str) -> str:
    inventory = inventory_chunk_set(raw_root, slug, timeframe)
    if not inventory:
        raise FreezeStoreError(f"no immutable chunks found for {slug}/{timeframe}")
    return hash_stable_json(inventory)


def rebuild_merged_from_chunks(raw_root: Path, slug: str, timeframe: str) -> list[dict[str, Any]]:
    chunks = [read_json(path) for path in list_chunk_files(raw_root, slug, timeframe)]
    if not chunks:
        raise FreezeStoreError(f"no immutable chunks found for {slug}/{timeframe}")
    return merge_bars_by_time(chunks)


def compute_merged_bar_content_hash(raw_root: Path, slug: str, timeframe: str) -> str:
    return hash_stable_json(rebuild_merged_from_chunks(raw_root, slug, timeframe))


def merge_snapshot_path(raw_root: Path, slug: str, timeframe: str, chunk_set_hash: str) -> Path:
    return merged_snapshots_dir(raw_root, slug, timeframe) / f"{chunk_set_hash}.json"


def write_merge_snapshot_if_absent(
    raw_root: Path,
    slug: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    *,
    chunk_set_hash: str,
) -> str:
    snapshot_file = merge_snapshot_path(raw_root, slug, timeframe, chunk_set_hash)
    return write_json_immutable(snapshot_file, bars)


def update_latest_merge_pointer(
    raw_root: Path,
    slug: str,
    timeframe: str,
    *,
    chunk_set_hash: str,
    merged_bar_content_hash: str,
    row_count: int,
    first_timestamp: str | None,
    last_timestamp: str | None,
    legacy_merged_raw_path: str | None = None,
) -> None:
    pointer = {
        "chunk_set_hash": chunk_set_hash,
        "merged_bar_content_hash": merged_bar_content_hash,
        "snapshot_path": str(
            merge_snapshot_path(raw_root, slug, timeframe, chunk_set_hash).relative_to(repo_root())
        ).replace("\\", "/"),
        "row_count": row_count,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if legacy_merged_raw_path:
        pointer["legacy_merged_raw_path"] = legacy_merged_raw_path
    write_json_mutable(latest_merge_pointer_path(raw_root, slug, timeframe), pointer)


def publish_chunk_derived_merge(
    raw_root: Path,
    slug: str,
    timeframe: str,
    *,
    pinned_as_of: datetime | None = None,
    confirmed_chunk_set_hash: str | None = None,
    confirmed_research_content_hash: str | None = None,
    confirmed_row_count: int | None = None,
    provisional_row_count: int | None = None,
) -> dict[str, Any]:
    bars = rebuild_merged_from_chunks(raw_root, slug, timeframe)
    chunk_set_hash = compute_chunk_set_hash(raw_root, slug, timeframe)
    merged_bar_content_hash = hash_stable_json(bars)
    write_merge_snapshot_if_absent(
        raw_root,
        slug,
        timeframe,
        bars,
        chunk_set_hash=chunk_set_hash,
    )
    first_ts = datetime.fromtimestamp(int(bars[0]["time"]), tz=timezone.utc).isoformat() if bars else None
    last_ts = datetime.fromtimestamp(int(bars[-1]["time"]), tz=timezone.utc).isoformat() if bars else None
    legacy = merged_raw_path(raw_root, slug, timeframe)
    pointer: dict[str, Any] = {
        "chunk_set_hash": chunk_set_hash,
        "merged_bar_content_hash": merged_bar_content_hash,
        "snapshot_path": str(
            merge_snapshot_path(raw_root, slug, timeframe, chunk_set_hash).relative_to(repo_root())
        ).replace("\\", "/"),
        "row_count": len(bars),
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if pinned_as_of is not None:
        pointer["pinned_as_of"] = pinned_as_of.astimezone(timezone.utc).isoformat()
    if confirmed_chunk_set_hash is not None:
        pointer["confirmed_chunk_set_hash"] = confirmed_chunk_set_hash
    if confirmed_research_content_hash is not None:
        pointer["confirmed_research_content_hash"] = confirmed_research_content_hash
    if confirmed_row_count is not None:
        pointer["confirmed_row_count"] = confirmed_row_count
    if provisional_row_count is not None:
        pointer["provisional_row_count_at_capture"] = provisional_row_count
    if legacy.exists():
        pointer["legacy_merged_raw_path"] = str(legacy.relative_to(repo_root())).replace("\\", "/")
    write_json_mutable(latest_merge_pointer_path(raw_root, slug, timeframe), pointer)
    return {
        "chunk_set_hash": chunk_set_hash,
        "merged_bar_content_hash": merged_bar_content_hash,
        "row_count": len(bars),
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "snapshot_path": pointer["snapshot_path"],
        "confirmed_chunk_set_hash": confirmed_chunk_set_hash,
        "confirmed_research_content_hash": confirmed_research_content_hash,
        "confirmed_row_count": confirmed_row_count,
        "provisional_row_count_at_capture": provisional_row_count,
    }


def resolve_raw_evidence_hashes(raw_root: Path, slug: str, timeframe: str) -> dict[str, str]:
    legacy = merged_raw_path(raw_root, slug, timeframe)
    if list_chunk_files(raw_root, slug, timeframe):
        return {
            "chunk_set_hash": compute_chunk_set_hash(raw_root, slug, timeframe),
            "merged_bar_content_hash": compute_merged_bar_content_hash(raw_root, slug, timeframe),
            "legacy_merged_raw_file_hash": hash_file_bytes(legacy) if legacy.exists() else "",
        }
    if legacy.exists():
        bars = read_json(legacy)
        return {
            "chunk_set_hash": "",
            "merged_bar_content_hash": hash_stable_json(bars),
            "legacy_merged_raw_file_hash": hash_file_bytes(legacy),
        }
    raise FreezeStoreError(f"raw data not found for {slug}/{timeframe}")


def verify_manifest_raw_evidence_unchanged(
    original_manifest: dict[str, Any],
    raw_hashes: dict[str, str],
) -> None:
    stored_content = str(original_manifest.get("raw_content_hash") or "")
    stored_chunk = str(original_manifest.get("chunk_set_hash") or "")
    allowed = {
        raw_hashes.get("merged_bar_content_hash", ""),
        raw_hashes.get("legacy_merged_raw_file_hash", ""),
    }
    if stored_chunk and stored_chunk != raw_hashes.get("chunk_set_hash"):
        raise FreezeStoreError(
            "immutable chunk set hash does not match original manifest chunk_set_hash; refusing re-QA"
        )
    if stored_content and stored_content not in allowed:
        raise FreezeStoreError(
            "raw evidence hash does not match original manifest raw_content_hash; refusing re-QA"
        )


def normalized_path(normalized_root: Path, slug: str, timeframe: str) -> Path:
    return normalized_root / slug / f"{timeframe}.json"


def manifest_path(normalized_root: Path, slug: str, timeframe: str) -> Path:
    return normalized_root / slug / f"{timeframe}.manifest.json"


def qa_manifest_path(
    normalized_root: Path,
    slug: str,
    timeframe: str,
    *,
    qa_algorithm_version: str,
) -> Path:
    return normalized_root / slug / f"{timeframe}.manifest.{qa_algorithm_version}.json"


def hash_file_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_raw_bars_from_store(raw_root: Path, slug: str, timeframe: str) -> list[dict[str, Any]]:
    if list_chunk_files(raw_root, slug, timeframe):
        return rebuild_merged_from_chunks(raw_root, slug, timeframe)
    merged = merged_raw_path(raw_root, slug, timeframe)
    if merged.exists():
        return read_json(merged)
    raise FreezeStoreError(f"raw data not found for {slug}/{timeframe}")


def git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def merge_bars_by_time(chunks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for bars in chunks:
        for bar in bars:
            merged[int(bar["time"])] = bar
    return [merged[key] for key in sorted(merged)]


def iter_utc_chunks(
    start: datetime,
    end: datetime,
    *,
    chunk_days: int,
) -> list[tuple[datetime, datetime]]:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)
    cursor = start
    out: list[tuple[datetime, datetime]] = []
    step = timedelta(days=max(1, chunk_days))
    while cursor < end:
        nxt = min(cursor + step, end)
        out.append((cursor, nxt))
        cursor = nxt
    return out


def load_or_init_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed_chunks": []}
    return read_json(path)


def save_state(path: Path, state: dict[str, Any]) -> None:
    write_json_mutable(path, state)


def normalize_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for bar in bars:
        ts = datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc).isoformat()
        normalized.append(
            {
                "time": ts,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "tick_volume": int(bar["tick_volume"]),
                "spread": int(bar["spread"]),
                "real_volume": int(bar["real_volume"]),
            }
        )
    return normalized


def write_manifest_if_absent(path: Path, manifest: dict[str, Any]) -> str:
    import hashlib

    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if path.exists():
        existing = hashlib.sha256(path.read_bytes()).hexdigest()
        if existing != digest:
            raise FreezeStoreError(f"refusing to overwrite conflicting manifest: {path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.encode("utf-8"))
    return digest
