"""Frozen backtest dataset manifest helpers.

Frozen mode is opt-in via BACKTEST_DATA_AS_OF or CONFIG["BACKTEST_DATA_AS_OF"].
When active, callers must read only manifest-backed data and must raise on
missing or changed series instead of falling back to live providers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class FrozenDataError(RuntimeError):
    """Raised when frozen backtest data is missing or fails verification."""


def active_data_as_of() -> str | None:
    if str(os.environ.get("ATHENA_FREEZE_BUILDING") or "").strip() == "1":
        return None
    value = os.environ.get("BACKTEST_DATA_AS_OF")
    if value is None:
        try:
            from config import CONFIG

            value = CONFIG.get("BACKTEST_DATA_AS_OF")
        except Exception:
            value = None
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "false", "0"}:
        return None
    return text[:10]


def frozen_root() -> Path:
    root = os.environ.get("ATHENA_FROZEN_DATA_ROOT")
    if root:
        return Path(root).resolve()
    return Path(__file__).resolve().parent


def manifest_path(data_as_of: str) -> Path:
    return frozen_root() / "data" / "manifests" / f"{str(data_as_of)[:10]}.json"


def _load_manifest(data_as_of: str) -> dict[str, Any]:
    path = manifest_path(data_as_of)
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "data_as_of": str(data_as_of)[:10],
            "series": {},
        }
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if int(data.get("schema_version", 0) or 0) != SCHEMA_VERSION:
        raise FrozenDataError(f"unsupported frozen manifest schema: {path}")
    return data


def _save_manifest(data_as_of: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(data_as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["data_as_of"] = str(data_as_of)[:10]
    manifest.setdefault("series", {})
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("_") or "unknown"


def _series_key(kind: str, display: str, tf: str | None, provider: str | None) -> str:
    return "|".join(
        [
            str(kind or "").strip().lower(),
            str(display or "").strip(),
            str(tf or "").strip().upper(),
            str(provider or "").strip().lower(),
        ]
    )


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw /= 1000.0
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cutoff_dt(data_as_of: str) -> datetime:
    base = datetime.strptime(str(data_as_of)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return base + timedelta(days=1)


def _norm_float(value: Any) -> float:
    return float(value or 0.0)


def _normalise_candle(candle: dict[str, Any]) -> dict[str, Any] | None:
    dt = _parse_time(candle.get("time") or candle.get("datetime") or candle.get("bar_time"))
    if dt is None:
        return None
    return {
        "time": dt.isoformat(),
        "open": _norm_float(candle.get("open")),
        "high": _norm_float(candle.get("high")),
        "low": _norm_float(candle.get("low")),
        "close": _norm_float(candle.get("close")),
        "vol": _norm_float(candle.get("vol", candle.get("volume", 0))),
    }


def _filter_candles(data_as_of: str, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = _cutoff_dt(data_as_of)
    out: list[dict[str, Any]] = []
    for candle in candles or []:
        norm = _normalise_candle(candle)
        if not norm:
            continue
        dt = _parse_time(norm["time"])
        if dt is not None and dt < cutoff:
            out.append(norm)
    out.sort(key=lambda row: row["time"])
    return out


def _stable_hash(rows: Any) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rel_path_for(kind: str, data_as_of: str, display: str, tf: str | None, provider: str | None) -> Path:
    parts = [
        "data",
        "frozen",
        str(data_as_of)[:10],
        _slug(kind),
        f"{_slug(display)}__{_slug(tf or 'series')}__{_slug(provider or 'provider')}.json",
    ]
    return Path(*parts)


def write_frozen_candles(
    data_as_of: str,
    pair: dict[str, Any],
    tf: str,
    provider: str,
    candles: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    rows = _filter_candles(data_as_of, list(candles or []))
    if not rows:
        raise FrozenDataError(
            f"cannot freeze empty candle series: {pair.get('display')} {tf} {provider}"
        )
    display = str(pair.get("display") or pair.get("symbol") or "")
    tf_u = str(tf or "").upper()
    provider_key = str(provider or "").strip().lower()
    rel_path = _rel_path_for("candles", data_as_of, display, tf_u, provider_key)
    abs_path = frozen_root() / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = _stable_hash(rows)
    rec = {
        "kind": "candles",
        "display": display,
        "symbol": pair.get("symbol"),
        "source": pair.get("source"),
        "asset_type": pair.get("type"),
        "timeframe": tf_u,
        "provider": provider_key,
        "row_count": len(rows),
        "first_bar_time": rows[0]["time"],
        "last_bar_time": rows[-1]["time"],
        "sha256": digest,
        "path": str(rel_path).replace("\\", "/"),
    }
    manifest = _load_manifest(data_as_of)
    manifest.setdefault("series", {})[_series_key("candles", display, tf_u, provider_key)] = rec
    _save_manifest(data_as_of, manifest)
    return rec


def _manifest_record(data_as_of: str, kind: str, display: str, tf: str | None, provider: str | None) -> dict[str, Any]:
    manifest = _load_manifest(data_as_of)
    key = _series_key(kind, display, tf, provider)
    rec = (manifest.get("series") or {}).get(key)
    if not rec:
        label = "candle" if kind == "candles" else kind
        raise FrozenDataError(
            f"missing frozen {label} series: data_as_of={data_as_of} display={display} tf={tf} provider={provider}"
        )
    return rec


def read_frozen_candles(
    data_as_of: str,
    pair: dict[str, Any],
    tf: str,
    provider: str,
    *,
    limit: int | None = None,
    min_bars: int | None = None,
) -> list[dict[str, Any]]:
    display = str(pair.get("display") or pair.get("symbol") or "")
    tf_u = str(tf or "").upper()
    provider_key = str(provider or "").strip().lower()
    rec = _manifest_record(data_as_of, "candles", display, tf_u, provider_key)
    path = frozen_root() / str(rec.get("path") or "")
    if not path.exists():
        raise FrozenDataError(f"missing frozen candle file: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    digest = _stable_hash(rows)
    if digest != rec.get("sha256"):
        raise FrozenDataError(
            f"hash mismatch for frozen candle series: {display} {tf_u} {provider_key}"
        )
    if int(rec.get("row_count") or 0) != len(rows):
        raise FrozenDataError(
            f"row count mismatch for frozen candle series: {display} {tf_u} {provider_key}"
        )
    need = int(min_bars or 0)
    if need and len(rows) < need:
        raise FrozenDataError(
            f"frozen candle series too short: {display} {tf_u} {provider_key} rows={len(rows)} need={need}"
        )
    limit_i = int(limit or len(rows))
    return rows[-limit_i:] if limit_i > 0 else list(rows)


def write_frozen_rows(
    data_as_of: str,
    pair: dict[str, Any],
    kind: str,
    provider: str,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    display = str(pair.get("display") or pair.get("symbol") or "")
    provider_key = str(provider or "").strip().lower()
    norm_rows = list(rows or [])
    norm_rows.sort(key=lambda row: str(row.get("ts_ms") or row.get("time") or ""))
    rel_path = _rel_path_for(kind, data_as_of, display, None, provider_key)
    abs_path = frozen_root() / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(json.dumps(norm_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = _stable_hash(norm_rows)
    rec = {
        "kind": kind,
        "display": display,
        "symbol": pair.get("symbol"),
        "source": pair.get("source"),
        "asset_type": pair.get("type"),
        "provider": provider_key,
        "row_count": len(norm_rows),
        "sha256": digest,
        "path": str(rel_path).replace("\\", "/"),
    }
    if norm_rows:
        first = norm_rows[0].get("ts_ms") or norm_rows[0].get("time")
        last = norm_rows[-1].get("ts_ms") or norm_rows[-1].get("time")
        rec["first"] = first
        rec["last"] = last
    manifest = _load_manifest(data_as_of)
    manifest.setdefault("series", {})[_series_key(kind, display, None, provider_key)] = rec
    _save_manifest(data_as_of, manifest)
    return rec


def read_frozen_rows(data_as_of: str, pair: dict[str, Any], kind: str, provider: str) -> list[dict[str, Any]]:
    display = str(pair.get("display") or pair.get("symbol") or "")
    provider_key = str(provider or "").strip().lower()
    rec = _manifest_record(data_as_of, kind, display, None, provider_key)
    path = frozen_root() / str(rec.get("path") or "")
    if not path.exists():
        raise FrozenDataError(f"missing frozen {kind} file: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    digest = _stable_hash(rows)
    if digest != rec.get("sha256"):
        raise FrozenDataError(f"hash mismatch for frozen {kind} series: {display} {provider_key}")
    if int(rec.get("row_count") or 0) != len(rows):
        raise FrozenDataError(f"row count mismatch for frozen {kind} series: {display}")
    return rows


def write_frozen_factor_values(
    data_as_of: str,
    display: str,
    factor_name: str,
    values_by_date: dict[str, Any],
) -> dict[str, Any]:
    rel_path = _rel_path_for(f"factor_{factor_name}", data_as_of, display, None, "snapshot")
    abs_path = frozen_root() / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    rows = {str(k)[:10]: v for k, v in sorted((values_by_date or {}).items())}
    abs_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = _stable_hash(rows)
    rec = {
        "kind": f"factor_{factor_name}",
        "display": display,
        "provider": "snapshot",
        "row_count": len(rows),
        "sha256": digest,
        "path": str(rel_path).replace("\\", "/"),
    }
    if rows:
        rec["first"] = min(rows)
        rec["last"] = max(rows)
    manifest = _load_manifest(data_as_of)
    manifest.setdefault("series", {})[
        _series_key(f"factor_{factor_name}", display, None, "snapshot")
    ] = rec
    _save_manifest(data_as_of, manifest)
    return rec


def read_frozen_factor_value(
    data_as_of: str,
    display: str,
    factor_name: str,
    as_of_date: str | None,
) -> Any:
    date_key = str(as_of_date or data_as_of)[:10]
    rec = _manifest_record(data_as_of, f"factor_{factor_name}", display, None, "snapshot")
    path = frozen_root() / str(rec.get("path") or "")
    if not path.exists():
        raise FrozenDataError(f"missing frozen factor file: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    digest = _stable_hash(rows)
    if digest != rec.get("sha256"):
        raise FrozenDataError(f"hash mismatch for frozen factor series: {display} {factor_name}")
    if date_key not in rows:
        raise FrozenDataError(
            f"missing frozen factor value: data_as_of={data_as_of} display={display} factor={factor_name} date={date_key}"
        )
    return rows[date_key]
