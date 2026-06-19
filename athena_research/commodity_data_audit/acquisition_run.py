from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena_research.commodity_data_audit.capture_state import (
    annotate_bars_at_capture,
    confirmed_research_bars_for_hash,
    provisional_bars,
    research_eligible_bars,
)
from athena_research.commodity_data_audit.freeze_store import (
    FreezeStoreError,
    hash_stable_json,
    inventory_chunk_set,
    list_chunk_files,
    read_json,
    rebuild_merged_from_chunks,
    repo_root,
    write_json_immutable,
)


def infer_legacy_pinned_as_of(raw_root: Path, slug: str, timeframe: str) -> datetime:
    mtimes = [path.stat().st_mtime for path in list_chunk_files(raw_root, slug, timeframe)]
    if not mtimes:
        raise FreezeStoreError(f"cannot infer pinned as_of without chunks for {slug}/{timeframe}")
    return datetime.fromtimestamp(max(mtimes), tz=timezone.utc)


def init_acquisition_run_state(
    state: dict[str, Any],
    *,
    pinned_as_of: datetime,
    new_run: bool,
) -> dict[str, Any]:
    pinned_iso = pinned_as_of.astimezone(timezone.utc).isoformat()
    if new_run or not state.get("pinned_as_of"):
        state = dict(state)
        state["pinned_as_of"] = pinned_iso
        if new_run or not state.get("acquisition_run_id"):
            state["acquisition_run_id"] = hash_stable_json(
                {"pinned_as_of": pinned_iso, "started_at": pinned_iso}
            )[:16]
        state.setdefault("chunk_revisions", {})
        return state
    return state


def resolve_pinned_as_of_for_resume(
    state: dict[str, Any],
    *,
    requested_as_of: datetime | None,
    raw_root: Path,
    slug: str,
    timeframe: str,
    new_run: bool,
) -> tuple[datetime, dict[str, Any]]:
    if new_run:
        pinned = (requested_as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        updated = init_acquisition_run_state(state, pinned_as_of=pinned, new_run=True)
        return pinned, updated

    if state.get("pinned_as_of"):
        pinned = datetime.fromisoformat(str(state["pinned_as_of"]))
        if pinned.tzinfo is None:
            pinned = pinned.replace(tzinfo=timezone.utc)
        if requested_as_of is not None and requested_as_of.astimezone(timezone.utc) != pinned:
            raise FreezeStoreError(
                "resume refused: requested as_of differs from pinned acquisition as_of; "
                "start a new acquisition run/revision instead"
            )
        return pinned.astimezone(timezone.utc), state

    if list_chunk_files(raw_root, slug, timeframe):
        pinned = infer_legacy_pinned_as_of(raw_root, slug, timeframe)
        updated = init_acquisition_run_state(state, pinned_as_of=pinned, new_run=False)
        return pinned, updated

    pinned = (requested_as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    updated = init_acquisition_run_state(state, pinned_as_of=pinned, new_run=True)
    return pinned, updated


def chunk_revision_path(
    raw_root: Path,
    slug: str,
    timeframe: str,
    interval_key: str,
    revision_id: str,
) -> Path:
    safe_key = interval_key_dirname(interval_key)
    return raw_root / slug / timeframe / "chunks" / "revisions" / safe_key / f"{revision_id}.json"


def write_interval_revision(
    raw_root: Path,
    slug: str,
    timeframe: str,
    *,
    interval_key: str,
    revision_id: str,
    bars: list[dict[str, Any]],
    supersession_of: str,
    pinned_as_of: datetime,
    revision_kind: str,
) -> str:
    path = chunk_revision_path(raw_root, slug, timeframe, interval_key, revision_id)
    write_json_immutable(path, bars)
    rel = str(path.relative_to(repo_root())).replace("\\", "/")
    return rel


def record_interval_revision(
    state: dict[str, Any],
    *,
    interval_key: str,
    primary_chunk: str,
    revision_path: str,
    revision_id: str,
    supersession_of: str,
    pinned_as_of: datetime,
    revision_kind: str,
) -> dict[str, Any]:
    state = dict(state)
    revisions = dict(state.get("chunk_revisions") or {})
    entry = dict(revisions.get(interval_key) or {})
    entry["primary_chunk"] = primary_chunk
    history = list(entry.get("revisions") or [])
    history.append(
        {
            "revision_id": revision_id,
            "revision_path": revision_path,
            "revision_kind": revision_kind,
            "supersession_of": supersession_of,
            "pinned_as_of": pinned_as_of.astimezone(timezone.utc).isoformat(),
        }
    )
    entry["revisions"] = history
    entry["selected_revision"] = revision_path
    revisions[interval_key] = entry
    state["chunk_revisions"] = revisions
    return state


def compute_confirmed_research_content_hash(
    raw_root: Path,
    slug: str,
    timeframe: str,
    *,
    pinned_as_of: datetime,
) -> str:
    bars = rebuild_merged_from_chunks(raw_root, slug, timeframe)
    confirmed = confirmed_research_bars_for_hash(bars, timeframe=timeframe, pinned_as_of=pinned_as_of)
    return hash_stable_json(confirmed)


def compute_confirmed_chunk_set_hash(
    raw_root: Path,
    slug: str,
    timeframe: str,
    *,
    pinned_as_of: datetime,
) -> str:
    inventory = inventory_chunk_set(raw_root, slug, timeframe)
    bars = rebuild_merged_from_chunks(raw_root, slug, timeframe)
    confirmed = research_eligible_bars(bars, timeframe=timeframe, pinned_as_of=pinned_as_of)
    provisional = provisional_bars(bars, timeframe=timeframe, pinned_as_of=pinned_as_of)
    return hash_stable_json(
        {
            "pinned_as_of": pinned_as_of.astimezone(timezone.utc).isoformat(),
            "chunk_inventory": inventory,
            "confirmed_row_count": len(confirmed),
            "provisional_row_count": len(provisional),
            "confirmed_content_hash": hash_stable_json(
                confirmed_research_bars_for_hash(bars, timeframe=timeframe, pinned_as_of=pinned_as_of)
            ),
        }
    )


def prepare_bars_for_immutable_chunk_write(
    bars: list[dict[str, Any]],
    *,
    timeframe: str,
    pinned_as_of: datetime,
) -> list[dict[str, Any]]:
    return annotate_bars_at_capture(bars, timeframe=timeframe, as_of=pinned_as_of)


def _interval_key_to_bounds(interval_key: str) -> tuple[datetime, datetime]:
    start_raw, end_raw = interval_key.split("__", 1)
    start = datetime.fromisoformat(start_raw)
    end = datetime.fromisoformat(end_raw)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def interval_key_dirname(interval_key: str) -> str:
    start, end = _interval_key_to_bounds(interval_key)
    return (
        f"{start.strftime('%Y%m%dT%H%M%SZ')}__{end.strftime('%Y%m%dT%H%M%SZ')}"
    )


def apply_selected_revisions(
    bars: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    raw_root: Path,
) -> list[dict[str, Any]]:
    revisions = state.get("chunk_revisions") or {}
    if not revisions:
        return bars
    merged = {int(bar["time"]): bar for bar in bars}
    for interval_key, entry in revisions.items():
        selected = entry.get("selected_revision")
        if not selected:
            continue
        rev_path = repo_root() / Path(str(selected).replace("\\", "/"))
        if not rev_path.exists():
            raise FreezeStoreError(f"selected revision not found: {selected}")
        rev_bars = read_json(rev_path)
        start, end = _interval_key_to_bounds(interval_key)
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        for ts in list(merged):
            if start_ts <= ts < end_ts:
                del merged[ts]
        for bar in rev_bars:
            merged[int(bar["time"])] = bar
    return [merged[key] for key in sorted(merged)]


def rebuild_research_merge(
    raw_root: Path,
    slug: str,
    timeframe: str,
    *,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    bars = rebuild_merged_from_chunks(raw_root, slug, timeframe)
    return apply_selected_revisions(bars, state=state, raw_root=raw_root)
