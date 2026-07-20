"""Parquet-backed historical OHLCV store.

One parquet file per (provider, source, symbol, timeframe) series:
    {root}/{provider}/{source}/{symbol}/{tf}.parquet

Columns: time (int64 epoch seconds, UTC bar open), open/high/low/close/volume
(float64), sorted ascending, unique on time. Reads return the same
engine-shaped candle dicts the live scan consumes:
    {"time": <canonical str>, "open": f, "high": f, "low": f, "close": f, "vol": f}

The canonical time string matches the legacy backtest_candle_cache format
(D1 midnight bars render as "YYYY-MM-DD", everything else ISO-8601 UTC), so
series migrated from the SQLite cache read back byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

_SCHEMA = pa.schema(
    [
        pa.field("time", pa.int64()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
    ]
)

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]")


def default_store_root() -> Path:
    env = os.environ.get("BT_STORE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "backtest_store"


def parse_epoch(value) -> float | None:
    """Parse a bar-time value (epoch s/ms or ISO text) to epoch seconds UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000.0 if raw > 1e12 else raw
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
    return dt.timestamp()


def canonical_time(epoch: float, tf: str) -> str:
    dt = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    if tf == "D1" and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        return dt.strftime("%Y-%m-%d") if dt.microsecond == 0 else dt.isoformat()
    return dt.isoformat()


def _provider_key(provider: str | None) -> str:
    return str(provider or "").strip().lower() or "unknown"


def _source_key(pair: dict) -> str:
    return str(pair.get("source") or "").strip().lower() or "unknown"


def _symbol_key(pair: dict) -> str:
    return str(pair.get("symbol") or pair.get("display") or "").strip()


def _safe(component: str) -> str:
    return _SAFE_COMPONENT.sub("_", component) or "_"


class ParquetCandleStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_store_root()

    # ---------- paths ----------

    def series_dir(self, pair: dict, *, provider: str) -> Path:
        return (
            self.root
            / _safe(_provider_key(provider))
            / _safe(_source_key(pair))
            / _safe(_symbol_key(pair))
        )

    def series_path(self, pair: dict, tf: str, *, provider: str) -> Path:
        return self.series_dir(pair, provider=provider) / f"{str(tf).upper()}.parquet"

    # ---------- io ----------

    def _read_table(self, path: Path) -> pa.Table | None:
        if not path.exists():
            return None
        return pq.read_table(path)

    def read(
        self,
        pair: dict,
        tf: str,
        *,
        provider: str,
        start: float | None = None,
        end: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return engine-shaped candle dicts, ascending. `limit` keeps the most recent rows."""
        tf = str(tf).upper()
        table = self._read_table(self.series_path(pair, tf, provider=provider))
        if table is None or table.num_rows == 0:
            return []
        times = table.column("time").to_numpy()
        mask = np.ones(len(times), dtype=bool)
        if start is not None:
            mask &= times >= float(start)
        if end is not None:
            mask &= times <= float(end)
        idx = np.nonzero(mask)[0]
        if limit is not None and len(idx) > int(limit):
            idx = idx[-int(limit) :]
        if len(idx) == 0:
            return []
        cols = {
            name: table.column(name).to_numpy()[idx]
            for name in ("time", "open", "high", "low", "close", "volume")
        }
        return [
            {
                "time": canonical_time(cols["time"][i], tf),
                "open": float(cols["open"][i]),
                "high": float(cols["high"][i]),
                "low": float(cols["low"][i]),
                "close": float(cols["close"][i]),
                "vol": float(cols["volume"][i]),
            }
            for i in range(len(idx))
        ]

    def write(self, pair: dict, tf: str, rows: list[dict] | None, *, provider: str) -> int:
        """Upsert rows into the series (new rows win on duplicate bar time).

        Returns the number of bars that were new or changed the series length.
        """
        if not rows:
            return 0
        tf = str(tf).upper()
        incoming: dict[int, tuple[float, float, float, float, float]] = {}
        for candle in rows:
            epoch = parse_epoch(candle.get("time") if "time" in candle else candle.get("datetime"))
            if epoch is None:
                epoch = parse_epoch(candle.get("datetime"))
            if epoch is None:
                continue
            try:
                incoming[int(epoch)] = (
                    float(candle["open"]),
                    float(candle["high"]),
                    float(candle["low"]),
                    float(candle["close"]),
                    float(candle.get("vol", candle.get("volume", 0)) or 0),
                )
            except (TypeError, ValueError, KeyError):
                continue
        if not incoming:
            return 0

        path = self.series_path(pair, tf, provider=provider)
        existing = self._read_table(path)
        merged: dict[int, tuple[float, float, float, float, float]] = {}
        before = 0
        if existing is not None and existing.num_rows:
            before = existing.num_rows
            t = existing.column("time").to_numpy()
            o = existing.column("open").to_numpy()
            h = existing.column("high").to_numpy()
            lo = existing.column("low").to_numpy()
            c = existing.column("close").to_numpy()
            v = existing.column("volume").to_numpy()
            for i in range(before):
                merged[int(t[i])] = (float(o[i]), float(h[i]), float(lo[i]), float(c[i]), float(v[i]))
        merged.update(incoming)

        times = sorted(merged)
        arrays = [
            pa.array(times, type=pa.int64()),
            pa.array([merged[t][0] for t in times], type=pa.float64()),
            pa.array([merged[t][1] for t in times], type=pa.float64()),
            pa.array([merged[t][2] for t in times], type=pa.float64()),
            pa.array([merged[t][3] for t in times], type=pa.float64()),
            pa.array([merged[t][4] for t in times], type=pa.float64()),
        ]
        table = pa.Table.from_arrays(arrays, schema=_SCHEMA)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
        self._write_meta(pair, provider=provider)
        return len(times) - before

    def _write_meta(self, pair: dict, *, provider: str) -> None:
        meta_path = self.series_dir(pair, provider=provider) / "_meta.json"
        meta = {
            "symbol": _symbol_key(pair),
            "source": _source_key(pair),
            "provider": _provider_key(provider),
            "display": pair.get("display"),
            "asset_type": pair.get("type") or pair.get("asset_type"),
        }
        try:
            if meta_path.exists():
                current = json.loads(meta_path.read_text(encoding="utf-8"))
                if all(current.get(k) == v for k, v in meta.items() if v is not None):
                    return
                current.update({k: v for k, v in meta.items() if v is not None})
                meta = current
        except (OSError, ValueError):
            pass
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def read_meta(self, series_dir: Path) -> dict:
        meta_path = series_dir / "_meta.json"
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ---------- introspection ----------

    def coverage(self, pair: dict, tf: str, *, provider: str) -> tuple[float | None, float | None, int]:
        table = self._read_table(self.series_path(pair, str(tf).upper(), provider=provider))
        if table is None or table.num_rows == 0:
            return None, None, 0
        times = table.column("time").to_numpy()
        return float(times[0]), float(times[-1]), int(len(times))

    def content_hash(self, pair: dict, tf: str, *, provider: str) -> str | None:
        table = self._read_table(self.series_path(pair, str(tf).upper(), provider=provider))
        if table is None or table.num_rows == 0:
            return None
        digest = hashlib.sha256()
        for name in ("time", "open", "high", "low", "close", "volume"):
            digest.update(np.ascontiguousarray(table.column(name).to_numpy()).tobytes())
        return digest.hexdigest()

    def iter_series(self):
        """Yield (provider, source, symbol_dir_name, tf, path) for every stored series."""
        if not self.root.exists():
            return
        for provider_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for source_dir in sorted(p for p in provider_dir.iterdir() if p.is_dir()):
                for symbol_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
                    for path in sorted(symbol_dir.glob("*.parquet")):
                        yield (
                            provider_dir.name,
                            source_dir.name,
                            symbol_dir,
                            path.stem,
                            path,
                        )
