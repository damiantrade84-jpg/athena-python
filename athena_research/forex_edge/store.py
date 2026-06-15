from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import numpy as np
import pandas as pd

from athena_research.forex_edge.config import default_store_root
from athena_research.forex_edge.models import (
    DatasetManifest,
    InvalidResearchInputError,
)
from athena_research.reproducibility import hash_stable_json


@dataclass(frozen=True)
class RawArtifact:
    path: Path
    sha256: str
    retrieval_id: str


def _validate_path_segment(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidResearchInputError(
            f"{name} must be a nonempty safe path segment"
        )
    path = Path(value)
    unsafe = (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or path.name != value
        or path.is_absolute()
        or bool(path.drive)
        or bool(path.anchor)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    )
    if unsafe:
        raise InvalidResearchInputError(
            f"{name} must be a nonempty safe path segment"
        )
    return value


def _publish_no_overwrite(temp: Path, target: Path) -> None:
    os.link(temp, target)


def _json_safe_scalar(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Enum):
        return _json_safe_scalar(value.value)
    if isinstance(value, (pd.Timestamp, datetime, date, np.datetime64)):
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise InvalidResearchInputError(
                "unsupported datetime value"
            ) from exc
        if pd.isna(timestamp):
            return None
        return timestamp.tz_localize("UTC").isoformat() if (
            timestamp.tzinfo is None
        ) else timestamp.tz_convert("UTC").isoformat()
    if isinstance(value, np.generic):
        return _json_safe_scalar(value.item())
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidResearchInputError(
                "JSON-safe values must contain only finite floats"
            )
        return value
    if isinstance(value, (str, int, bool)):
        return value
    raise InvalidResearchInputError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidResearchInputError(
                    "JSON-safe mapping keys must be strings"
                )
            result[key] = _json_safe_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_safe_value(item) for item in value]
        return sorted(
            converted,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return _json_safe_scalar(value)


def canonical_frame_hash(frame: pd.DataFrame) -> str:
    if any(not isinstance(column, str) for column in frame.columns):
        raise InvalidResearchInputError(
            "JSON-safe frame column names must be strings"
        )
    columns = sorted(frame.columns)
    records = [
        {
            column: _json_safe_scalar(value)
            for column, value in zip(columns, row, strict=True)
        }
        for row in frame.reindex(columns=columns).itertuples(
            index=False,
            name=None,
        )
    ]
    return hash_stable_json(records)


class ResearchStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_store_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_raw(
        self,
        source: str,
        dataset: str,
        content: bytes,
    ) -> RawArtifact:
        source = _validate_path_segment("source", source)
        dataset = _validate_path_segment("dataset", dataset)
        digest = hashlib.sha256(content).hexdigest()
        retrieval_id = digest[:16]
        path = (
            self.root
            / "raw"
            / source
            / dataset
            / retrieval_id
            / "response.bin"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._verify_raw(path, content)
        else:
            temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temp.write_bytes(content)
                try:
                    _publish_no_overwrite(temp, path)
                except FileExistsError:
                    self._verify_raw(path, content)
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
        return RawArtifact(
            path=path,
            sha256=digest,
            retrieval_id=retrieval_id,
        )

    def _verify_raw(self, path: Path, content: bytes) -> None:
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"immutable raw conflict: {path}") from exc
        if existing != content:
            raise RuntimeError(f"immutable raw conflict: {path}")

    def _write_partition(self, path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._verify_partition(path, frame)
            return

        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            frame.to_parquet(temp, index=False)
            try:
                _publish_no_overwrite(temp, path)
            except FileExistsError:
                self._verify_partition(path, frame)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _verify_partition(self, path: Path, frame: pd.DataFrame) -> None:
        try:
            existing = pd.read_parquet(path)
            matches = (
                canonical_frame_hash(existing)
                == canonical_frame_hash(frame)
            )
        except Exception as exc:
            raise RuntimeError(
                f"immutable partition conflict: {path}"
            ) from exc
        if not matches:
            raise RuntimeError(f"immutable partition conflict: {path}")

    def write_normalized(
        self,
        *,
        dataset: str,
        key: str,
        frame: pd.DataFrame,
        source: str,
        source_url: str,
        raw_hashes: tuple[str, ...],
        metadata: dict[str, Any],
    ) -> DatasetManifest:
        source = _validate_path_segment("source", source)
        dataset = _validate_path_segment("dataset", dataset)
        key = _validate_path_segment("key", key)
        if not frame.empty and "timestamp" not in frame.columns:
            raise ValueError("nonempty normalized frame requires timestamp")
        if "config_hash" not in metadata:
            raise InvalidResearchInputError(
                "normalized metadata requires config_hash"
            )

        safe_metadata = _json_safe_value(metadata)
        safe_raw_hashes = tuple(_json_safe_value(raw_hashes))
        work = frame.copy()
        if not work.empty:
            try:
                work["timestamp"] = pd.to_datetime(
                    work["timestamp"],
                    utc=True,
                    errors="raise",
                )
            except (TypeError, ValueError) as exc:
                raise InvalidResearchInputError(
                    "timestamp values must be valid UTC datetimes"
                ) from exc
            if work["timestamp"].isna().any():
                raise InvalidResearchInputError(
                    "timestamp values must not be missing"
                )

        frame_hash = canonical_frame_hash(work)
        version = hash_stable_json(
            {
                "dataset": dataset,
                "key": key,
                "frame_hash": frame_hash,
                "raw_hashes": safe_raw_hashes,
                "config_hash": safe_metadata["config_hash"],
            }
        )[:16]
        base = self.root / "normalized" / dataset / key / version
        partition_hashes: dict[str, str] = {}
        expected_paths: set[Path] = set()

        if work.empty:
            path = base / "empty" / "data.parquet"
            self._write_partition(path, work)
            expected_paths.add(path)
            partition_hashes["empty"] = frame_hash
            actual_start = None
            actual_end = None
        else:
            actual_start = work["timestamp"].min().isoformat()
            actual_end = work["timestamp"].max().isoformat()
            partitioned = work.assign(_year=work["timestamp"].dt.year)
            for year, chunk in partitioned.groupby("_year", sort=True):
                clean = chunk.drop(columns="_year").reset_index(drop=True)
                label = str(int(year))
                path = base / label / "data.parquet"
                self._write_partition(path, clean)
                expected_paths.add(path)
                partition_hashes[label] = canonical_frame_hash(clean)

        actual_paths = set(base.glob("*/data.parquet"))
        if actual_paths != expected_paths:
            raise RuntimeError(f"immutable partition conflict: {base}")

        manifest_path = (
            self.root
            / "manifests"
            / dataset
            / key
            / f"{version}.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        existing_manifest = self._read_manifest(manifest_path)
        retrieved_at = safe_metadata.get("retrieved_at", "")
        if not isinstance(retrieved_at, str):
            raise InvalidResearchInputError(
                "metadata retrieved_at must normalize to a string"
            )
        manifest = DatasetManifest(
            schema_version=1,
            dataset=dataset,
            key=key,
            version=version,
            source=source,
            source_url=source_url,
            retrieved_at=retrieved_at,
            actual_start=actual_start,
            actual_end=actual_end,
            row_count=len(work),
            raw_hashes=safe_raw_hashes,
            partition_hashes=partition_hashes,
            metadata=safe_metadata,
        )
        payload = manifest.to_dict()

        if existing_manifest is not None:
            if existing_manifest.to_dict() != payload:
                raise RuntimeError(
                    f"immutable manifest conflict: {manifest_path}"
                )
            return existing_manifest

        temp = manifest_path.with_name(
            f".{manifest_path.name}.{uuid4().hex}.tmp"
        )
        try:
            temp.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=True,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                _publish_no_overwrite(temp, manifest_path)
            except FileExistsError:
                concurrent_manifest = self._read_manifest(manifest_path)
                if (
                    concurrent_manifest is None
                    or concurrent_manifest.to_dict() != payload
                ):
                    raise RuntimeError(
                        f"immutable manifest conflict: {manifest_path}"
                    )
                return concurrent_manifest
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
        return manifest

    def _read_manifest(self, path: Path) -> DatasetManifest | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("manifest payload must be an object")
            payload["raw_hashes"] = tuple(payload["raw_hashes"])
            return DatasetManifest(**payload)
        except Exception as exc:
            raise RuntimeError(
                f"immutable manifest conflict: {path}"
            ) from exc

    def load_normalized(
        self,
        dataset: str,
        key: str,
        version: str,
    ) -> pd.DataFrame:
        dataset = _validate_path_segment("dataset", dataset)
        key = _validate_path_segment("key", key)
        version = _validate_path_segment("version", version)
        base = self.root / "normalized" / dataset / key / version
        paths = sorted(base.glob("*/data.parquet"))
        if not paths:
            raise FileNotFoundError(f"{dataset}/{key}/{version}")
        try:
            frames = [pd.read_parquet(path) for path in paths]
        except Exception as exc:
            raise RuntimeError(
                f"immutable partition conflict: {base}"
            ) from exc
        return pd.concat(frames, ignore_index=True)

    def run_dir(self, run_id: str) -> Path:
        run_id = _validate_path_segment("run_id", run_id)
        path = self.root / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path
