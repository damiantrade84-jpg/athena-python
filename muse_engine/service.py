"""MUSE scan orchestration, sounding replay, and API-facing service methods."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable, Protocol

from .config import MuseConfig
from .execution import MuseExecutionCoordinator, MuseExecutionError
from .market_data import MuseMarketDataProvider
from .scoring import evaluate_snapshot
from .persistence import MuseRepository
from .sessions import market_is_closed, tide_state, window_schedule
from .sounding import run_sounding


class ScanAlreadyRunning(RuntimeError):
    pass


class ContextFeeds(Protocol):
    def gather(self, pair: dict[str, Any]) -> dict[str, Any]: ...


class NullContextFeeds:
    def gather(self, pair: dict[str, Any]) -> dict[str, Any]:
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _symbol_key(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _redact_account(raw: dict[str, Any], venue: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"venue": venue, "connected": False, "error": "ACCOUNT_UNAVAILABLE"}
    if raw.get("error"):
        return {"venue": venue, "connected": False,
                "error": str(raw.get("detail") or raw.get("error") or "ACCOUNT_UNAVAILABLE")}
    return {"venue": venue, "connected": True,
            "environment": raw.get("accountEnvironment") or ("demo" if raw.get("demo") else "real"),
            "demo": bool(raw.get("demo")), "testnet": bool(raw.get("testnet")),
            "login": raw.get("login") or raw.get("accountId"),
            "server": raw.get("server") or raw.get("exchange"),
            "balance": raw.get("balance"), "equity": raw.get("equity"),
            "currency": raw.get("currency")}


class MuseService:
    def __init__(self, *, config: MuseConfig, repository: MuseRepository,
                 market_data: MuseMarketDataProvider,
                 pair_provider: Callable[[], list[dict[str, Any]]],
                 execution: MuseExecutionCoordinator, log,
                 context_feeds: ContextFeeds | None = None) -> None:
        self.config = config
        self.repository = repository
        self.market_data = market_data
        self.pair_provider = pair_provider
        self.execution = execution
        self.log = log
        self.context_feeds = context_feeds or NullContextFeeds()
        self._scan_lock = threading.Lock()
        self._scanning = False
        self._state: dict[str, Any] = {}
        self.repository.migrate()

    # ── health / config / accounts ──────────────────────────────
    def health(self) -> dict[str, Any]:
        now = time.time()
        return {"engine": "MUSE", "contractVersion": "muse.v1",
                "enabled": self.config.enabled,
                "timeframes": {"atlas": "D1", "current": "H4", "vector": "M15", "spark": "M5"},
                "tideSchedule": window_schedule(now, self.config),
                "capabilities": self.execution.capabilities(),
                "scan": dict(self._state),
                "asOf": _now_iso()}

    def config_dict(self) -> dict[str, Any]:
        return {"engine": "MUSE", "config": self.config.public_dict()}

    def accounts(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for venue in ("mt5", "bybit"):
            try:
                out[venue] = _redact_account(self.execution.gateway.account(venue), venue)
            except Exception as exc:
                out[venue] = {"venue": venue, "connected": False, "error": str(exc)}
        return {"success": True, "accounts": out}

    # ── scan ────────────────────────────────────────────────────
    def start_scan(self, *, asset_types: set[str] | None = None,
                   symbols: set[str] | None = None) -> dict[str, Any]:
        if not self.config.enabled:
            raise PermissionError("muse_engine_disabled")
        with self._scan_lock:
            if self._scanning:
                raise ScanAlreadyRunning("scan_already_running")
            self._scanning = True
        try:
            return self._run_scan(asset_types=asset_types, symbols=symbols)
        finally:
            with self._scan_lock:
                self._scanning = False

    def current_scan(self) -> dict[str, Any] | None:
        return dict(self._state) if self._state else None

    def _universe(self, asset_types: set[str] | None, symbols: set[str] | None) -> list[dict[str, Any]]:
        pairs = list(self.pair_provider() or [])
        if asset_types:
            pairs = [p for p in pairs if str(p.get("type") or "").lower() in asset_types]
        if symbols:
            keys = {_symbol_key(s) for s in symbols}
            pairs = [p for p in pairs if _symbol_key(p.get("display")) in keys or _symbol_key(p.get("symbol")) in keys]
        return pairs

    def _scan_one(self, pair: dict[str, Any], now: float) -> dict[str, Any]:
        try:
            snapshot = self.market_data.snapshot(pair, now_epoch=now)
        except Exception as exc:
            return {"pair": str(pair.get("display") or "?"), "error": f"SNAPSHOT_FAILED:{type(exc).__name__}"}
        try:
            context = self.context_feeds.gather(pair)
        except Exception:
            context = {}
        try:
            signal = evaluate_snapshot(snapshot, self.config, context)
        except Exception as exc:
            return {"pair": snapshot.display, "error": f"SCORING_FAILED:{type(exc).__name__}"}
        signal["tideNow"] = tide_state(now, self.config)
        return {"signal": signal}

    def _run_scan(self, *, asset_types: set[str] | None, symbols: set[str] | None) -> dict[str, Any]:
        now = time.time()
        pairs = self._universe(asset_types, symbols)
        scan_id = self.repository.create_scan({"assetTypes": sorted(asset_types or []),
                                               "symbols": sorted(symbols or []),
                                               "pairs": len(pairs)})
        prime = stage = dormant = blocked = errors = 0
        blocker_counter: Counter[str] = Counter()
        with ThreadPoolExecutor(max_workers=int(self.config.scan["max_workers"])) as pool:
            futures = {pool.submit(self._scan_one, pair, now): pair for pair in pairs}
            for future in as_completed(futures):
                result = future.result()
                if "error" in result and "signal" not in result:
                    errors += 1
                    continue
                signal = result["signal"]
                try:
                    self.repository.upsert_signal(scan_id, signal)
                except Exception:
                    errors += 1
                    continue
                decision = str(signal.get("decision"))
                if decision == "PRIME":
                    prime += 1
                elif decision == "STAGE":
                    stage += 1
                elif decision == "DORMANT":
                    dormant += 1
                else:
                    blocked += 1
                for reason in signal.get("blockingReasons") or []:
                    blocker_counter[str(reason)] += 1
        summary = {"scanId": scan_id, "status": "COMPLETED", "totalPairs": len(pairs),
                   "primeCount": prime, "stageCount": stage, "dormantCount": dormant,
                   "blockedCount": blocked, "errorCount": errors,
                   "topBlockingReasons": [{"reason": r, "count": c} for r, c in blocker_counter.most_common(8)],
                   "startedAt": _now_iso(), "completedAt": _now_iso()}
        self.repository.complete_scan(scan_id, "COMPLETED", summary)
        self._state = summary
        return summary

    # ── signals / execution ─────────────────────────────────────
    def signals(self, *, decisions: set[str] | None = None,
                asset_types: set[str] | None = None, limit: int = 250) -> list[dict[str, Any]]:
        normalized_decisions = {str(d).upper() for d in decisions} if decisions else None
        normalized_assets = {str(a).lower() for a in asset_types} if asset_types else None
        return self.repository.list_signals(decisions=normalized_decisions,
                                            asset_types=normalized_assets, limit=limit)

    def signal(self, signal_id: str) -> dict[str, Any]:
        row = self.repository.get_signal(signal_id)
        if row is None:
            raise LookupError(signal_id)
        return row

    def _require_signal(self, signal_id: str) -> dict[str, Any]:
        row = self.repository.get_signal(signal_id)
        if row is None:
            raise LookupError(signal_id)
        return row

    def preview_execution(self, signal_id: str) -> dict[str, Any]:
        signal = self._require_signal(signal_id)
        try:
            result = self.execution.preview(signal)
        except MuseExecutionError as exc:
            raise exc
        return {"signalId": signal_id, "executable": bool(result.get("executable")), **result}

    def execute_signal(self, signal_id: str, *, mode: str, idempotency_key: str,
                       confirm_live: bool = False) -> dict[str, Any]:
        signal = self._require_signal(signal_id)
        # Re-resolve the pair against the live book: removed pairs fail closed.
        live = {_symbol_key(p.get("display")): p for p in (self.pair_provider() or [])}
        key = _symbol_key(signal.get("pair"))
        if key not in live and _symbol_key(signal.get("symbol")) not in live:
            raise MuseExecutionError("PAIR_NOT_IN_ACTIVE_BOOK")
        return self.execution.execute(signal, mode=mode, idempotency_key=idempotency_key,
                                      confirm_live=confirm_live)

    def execution_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.execution_history(limit=limit)

    def sounding_pair(self, symbol: str, *, bars: int | None = None) -> dict[str, Any]:
        if not self.config.enabled:
            raise PermissionError("muse_engine_disabled")
        pairs = list(self.pair_provider() or [])
        needle = _symbol_key(symbol)
        match = next((p for p in pairs if _symbol_key(p.get("display")) == needle
                      or _symbol_key(p.get("symbol")) == needle), None)
        if match is None:
            raise LookupError(symbol)
        if not bool(self.config.sounding.get("enabled", True)):
            raise PermissionError("muse_sounding_disabled")
        requested = int(bars) if bars else int(self.config.sounding["default_bars"])
        requested = max(10, min(requested, int(self.config.sounding["maximum_bars"])))
        return run_sounding(pair=match, bars=requested, config=self.config,
                            market_data=self.market_data,
                            context_fn=lambda pair: self.context_feeds.gather(pair))
