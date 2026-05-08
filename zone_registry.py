from __future__ import annotations

import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

_ZONE_DB_PATH = Path(__file__).resolve().parent / "zones.db"
_SINGLETON_LOCK = threading.Lock()
_SINGLETON: "ZoneRegistry | None" = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ZoneRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._zones: dict[tuple[str, str], list[dict]] = {}
        self._persistence_enabled = False
        self._db_path = _ZONE_DB_PATH
        self._refresh_persistence(force_reload=True)

    def upsert_zones(
        self,
        symbol: str,
        timeframe: str,
        obs: list[dict] | None,
        fvgs: list[dict] | None,
        atr: float | None = None,
        asset_type: str | None = None,
    ) -> None:
        key = self._key(symbol, timeframe)
        tolerance = max(float(atr or 0.0) * 0.5, 0.0)
        incoming = self._normalize_many(obs or [], "OB") + self._normalize_many(fvgs or [], "FVG")
        if not incoming:
            return

        with self._lock:
            bucket = self._zones.setdefault(key, [])
            for zone in incoming:
                zone["symbol"] = key[0]
                zone["timeframe"] = key[1]
                zone["asset_type"] = asset_type or "unknown"
                match = self._find_match(bucket, zone, tolerance)
                if match is None:
                    bucket.append(zone)
                    continue
                match["top"] = max(match["top"], zone["top"])
                match["bottom"] = min(match["bottom"], zone["bottom"])
                match["strength"] = max(match["strength"], zone["strength"])
                match["scan_count"] += 1
                if zone.get("mitigated"):
                    match["mitigated"] = True
                    match["mitigated_at"] = match["mitigated_at"] or zone.get("mitigated_at") or _utc_now_iso()
            self._persist_locked()

    def mark_mitigated(
        self,
        symbol: str,
        timeframe: str,
        current_price: float,
        atr: float,
    ) -> None:
        _ = atr
        key = self._key(symbol, timeframe)
        touched = False
        with self._lock:
            for zone in self._zones.get(key, []):
                if zone.get("mitigated"):
                    continue
                # Use zone edge crossing instead of midpoint to detect wick invalidations.
                # Bullish zone: mitigated when price crosses below the bottom edge.
                # Bearish zone: mitigated when price crosses above the top edge.
                direction = zone.get("direction")
                if direction == "bullish" and current_price <= zone["bottom"]:
                    zone["mitigated"] = True
                    zone["mitigated_at"] = _utc_now_iso()
                    touched = True
                elif direction == "bearish" and current_price >= zone["top"]:
                    zone["mitigated"] = True
                    zone["mitigated_at"] = _utc_now_iso()
                    touched = True
            if touched:
                self._persist_locked()

    def get_active_zones(self, symbol: str, timeframe: str) -> list[dict]:
        key = self._key(symbol, timeframe)
        with self._lock:
            return [
                deepcopy(zone)
                for zone in self._zones.get(key, [])
                if not zone.get("mitigated", False)
            ]

    def has_zones(self, symbol: str, timeframe: str) -> bool:
        key = self._key(symbol, timeframe)
        with self._lock:
            return bool(self._zones.get(key))

    def prune_old_zones(self, max_age_hours: int | None = None) -> None:
        # Get asset-specific TTL from config if not provided explicitly.
        if max_age_hours is None:
            ttl_config = config.CONFIG.get("ZONE_CACHE_TTL_HOURS", {})
            # Default to 168 hours (7 days) if config missing or not a dict
            default_ttl = 168
            if isinstance(ttl_config, dict):
                default_ttl = ttl_config.get("forex", 168) or 168
            max_age_hours = default_ttl

        changed = False
        with self._lock:
            for key, bucket in list(self._zones.items()):
                kept = []
                for zone in bucket:
                    try:
                        created_at = datetime.fromisoformat(str(zone["created_at"]))
                    except Exception:
                        created_at = datetime.now(timezone.utc)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)

                    # Use asset-specific TTL from config if available.
                    zone_ttl = max_age_hours
                    asset_type = zone.get("asset_type", "unknown")
                    ttl_config = config.CONFIG.get("ZONE_CACHE_TTL_HOURS", {})
                    if isinstance(ttl_config, dict) and asset_type in ttl_config:
                        zone_ttl = ttl_config[asset_type] or max_age_hours

                    cutoff = datetime.now(timezone.utc) - timedelta(hours=zone_ttl)
                    stale = created_at < cutoff
                    untouched = int(zone.get("scan_count", 0)) <= 1 and not zone.get("mitigated", False)
                    if stale and untouched:
                        changed = True
                        continue
                    kept.append(zone)
                if kept:
                    self._zones[key] = kept
                else:
                    self._zones.pop(key, None)
            if changed:
                self._persist_locked()

    def clear(self) -> None:
        with self._lock:
            self._zones = {}
            self._persist_locked()

    def _key(self, symbol: str, timeframe: str) -> tuple[str, str]:
        return (str(symbol or "").upper(), str(timeframe or "").upper())

    def _normalize_many(self, zones: list[dict], zone_type: str) -> list[dict]:
        out = []
        for zone in zones:
            normalized = self._normalize_zone(zone, zone_type)
            if normalized is not None:
                out.append(normalized)
        return out

    def _normalize_zone(self, zone: dict, zone_type: str) -> dict | None:
        try:
            top = float(zone["top"])
            bottom = float(zone["bottom"])
        except Exception:
            return None
        top, bottom = max(top, bottom), min(top, bottom)
        direction = str(zone.get("direction") or zone.get("type") or "").lower()
        if direction not in {"bullish", "bearish"}:
            return None
        strength = float(zone.get("strength", abs(top - bottom)))
        mitigated = bool(zone.get("mitigated", False))
        return {
            "symbol": "",
            "timeframe": "",
            "type": zone_type,
            "direction": direction,
            "top": top,
            "bottom": bottom,
            "strength": strength,
            "created_at": zone.get("created_at") or _utc_now_iso(),
            "mitigated": mitigated,
            "mitigated_at": zone.get("mitigated_at") if mitigated else None,
            "scan_count": int(zone.get("scan_count", 1)),
        }

    def _find_match(self, bucket: list[dict], zone: dict, tolerance: float) -> dict | None:
        zone_center = (zone["top"] + zone["bottom"]) / 2.0
        for existing in bucket:
            if existing["type"] != zone["type"] or existing["direction"] != zone["direction"]:
                continue
            existing_center = (existing["top"] + existing["bottom"]) / 2.0
            if abs(existing_center - zone_center) <= tolerance:
                return existing
        return None

    def _refresh_persistence(self, force_reload: bool = False) -> None:
        enabled = bool(config.CONFIG.get("ENGINE_B_ZONE_PERSISTENCE", False))
        if not force_reload and enabled == self._persistence_enabled:
            return
        self._persistence_enabled = enabled
        if self._persistence_enabled:
            self._ensure_schema()
            self._load_from_db()

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zones (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    type TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    top REAL NOT NULL,
                    bottom REAL NOT NULL,
                    strength REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    mitigated INTEGER NOT NULL,
                    mitigated_at TEXT,
                    scan_count INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def _load_from_db(self) -> None:
        loaded: dict[tuple[str, str], list[dict]] = {}
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT symbol, timeframe, type, direction, top, bottom, strength,
                       created_at, mitigated, mitigated_at, scan_count
                FROM zones
                """
            ).fetchall()
        for row in rows:
            entry = {
                "symbol": row[0],
                "timeframe": row[1],
                "type": row[2],
                "direction": row[3],
                "top": float(row[4]),
                "bottom": float(row[5]),
                "strength": float(row[6]),
                "created_at": row[7],
                "mitigated": bool(row[8]),
                "mitigated_at": row[9],
                "scan_count": int(row[10]),
            }
            loaded.setdefault(self._key(entry["symbol"], entry["timeframe"]), []).append(entry)
        with self._lock:
            self._zones = loaded

    def _persist_locked(self) -> None:
        if not self._persistence_enabled:
            return
        flat_rows = []
        for key, bucket in self._zones.items():
            symbol, timeframe = key
            for zone in bucket:
                flat_rows.append(
                    (
                        symbol,
                        timeframe,
                        zone["type"],
                        zone["direction"],
                        float(zone["top"]),
                        float(zone["bottom"]),
                        float(zone["strength"]),
                        zone["created_at"],
                        1 if zone.get("mitigated") else 0,
                        zone.get("mitigated_at"),
                        int(zone.get("scan_count", 1)),
                    )
                )
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM zones")
            conn.executemany(
                """
                INSERT INTO zones (
                    symbol, timeframe, type, direction, top, bottom, strength,
                    created_at, mitigated, mitigated_at, scan_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                flat_rows,
            )
            conn.commit()


def get_zone_registry() -> ZoneRegistry:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = ZoneRegistry()
        else:
            _SINGLETON._refresh_persistence()
        return _SINGLETON


def reset_zone_registry() -> None:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            _SINGLETON.clear()
        _SINGLETON = None
