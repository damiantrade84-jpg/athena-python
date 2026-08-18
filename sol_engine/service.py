"""SOL scan orchestration, replay, and API-facing service methods."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any, Callable

from .config import SolConfig
from .execution import SolExecutionCoordinator
from .market_data import SolMarketDataProvider
from .persistence import SolRepository
from .replay import run_causal_replay
from .scoring import evaluate_snapshot


class ScanAlreadyRunning(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _symbol_key(value: Any) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


class SolService:
    def __init__(
        self,
        *,
        config: SolConfig,
        repository: SolRepository,
        market_data: SolMarketDataProvider,
        pair_provider: Callable[[], list[dict[str, Any]]],
        execution: SolExecutionCoordinator,
        log,
    ) -> None:
        self.config = config
        self.repository = repository
        self.market_data = market_data
        self.pair_provider = pair_provider
        self.execution = execution
        self.log = log
        self._state_lock = threading.RLock()
        self._state: dict[str, Any] | None = None
        self.repository.migrate()

    def health(self) -> dict[str, Any]:
        latest = self.current_scan()
        return {
            "success": True,
            "engine": "SOL",
            "contractVersion": self.config.version,
            "enabled": self.config.enabled,
            "researchStatus": str(self.config.execution["research_status"]),
            "scanStatus": latest.get("status") if latest else "IDLE",
            "brokerCapabilities": self.execution.capabilities(),
        }

    def config_dict(self) -> dict[str, Any]:
        payload = self.config.public_dict()
        payload["brokerCapabilities"] = self.execution.capabilities()
        return payload

    def _pairs(self, asset_types: set[str] | None, symbols: set[str] | None) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        symbol_keys = {_symbol_key(value) for value in symbols or set() if _symbol_key(value)}
        for pair in self.pair_provider() or []:
            if not isinstance(pair, dict) or not pair.get("enabled", True):
                continue
            asset_type = str(pair.get("type") or "unknown").strip().lower()
            if asset_types and asset_type not in asset_types:
                continue
            if symbol_keys:
                keys = {_symbol_key(pair.get("display")), _symbol_key(pair.get("symbol"))}
                if not keys.intersection(symbol_keys):
                    continue
            output.append(dict(pair))
        return output

    def start_scan(
        self,
        *,
        asset_types: set[str] | None = None,
        symbols: set[str] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise PermissionError("sol_engine_disabled")
        with self._state_lock:
            if self._state and self._state.get("status") == "RUNNING":
                raise ScanAlreadyRunning("scan_already_running")
            pairs = self._pairs(asset_types, symbols)
            if not pairs:
                raise ValueError("no_pairs_selected")
            request_payload = {
                "assetTypes": sorted(asset_types or []),
                "symbols": sorted(symbols or []),
                "pairCount": len(pairs),
            }
            scan_id = self.repository.create_scan(request_payload)
            self._state = {
                "scanId": scan_id,
                "status": "RUNNING",
                "startedAt": _now_iso(),
                "completedAt": None,
                "totalPairs": len(pairs),
                "processedPairs": 0,
                "readyCount": 0,
                "watchCount": 0,
                "blockedCount": 0,
                "errorCount": 0,
                "errors": [],
            }
            state = dict(self._state)
        thread = threading.Thread(
            target=self._run_scan,
            args=(scan_id, pairs),
            name=f"SOLScan-{scan_id[-8:]}",
            daemon=True,
        )
        thread.start()
        return state

    def _evaluate_pair(self, pair: dict[str, Any], generated_at: float) -> dict[str, Any]:
        snapshot = self.market_data.fetch_snapshot(pair, as_of_epoch=generated_at)
        return evaluate_snapshot(snapshot, self.config, generated_at_epoch=generated_at)

    def _run_scan(self, scan_id: str, pairs: list[dict[str, Any]]) -> None:
        generated_at = time.time()
        signals: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        reason_counter: Counter[str] = Counter()
        decision_counter: Counter[str] = Counter()
        try:
            workers = max(1, min(int(self.config.scan["max_workers"]), len(pairs)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="SOLPair") as pool:
                futures = {pool.submit(self._evaluate_pair, pair, generated_at): pair for pair in pairs}
                for future in as_completed(futures):
                    pair = futures[future]
                    try:
                        signal = future.result()
                        signals.append(signal)
                        decision_counter[str(signal["decision"])] += 1
                        for reason in signal.get("blockingReasons") or []:
                            reason_counter[str(reason).split(":", 1)[0]] += 1
                    except Exception as exc:
                        errors.append(
                            {
                                "pair": str(pair.get("display") or pair.get("symbol") or "UNKNOWN"),
                                "error": str(exc),
                            }
                        )
                    with self._state_lock:
                        if self._state and self._state.get("scanId") == scan_id:
                            self._state["processedPairs"] += 1
                            self._state["readyCount"] = decision_counter["READY"]
                            self._state["watchCount"] = decision_counter["WATCH"]
                            self._state["blockedCount"] = decision_counter["BLOCKED"]
                            self._state["errorCount"] = len(errors)
            self.repository.save_signals(scan_id, signals)
            summary = {
                "totalPairs": len(pairs),
                "processedPairs": len(signals) + len(errors),
                "readyCount": decision_counter["READY"],
                "watchCount": decision_counter["WATCH"],
                "blockedCount": decision_counter["BLOCKED"],
                "errorCount": len(errors),
                "topBlockingReasons": [
                    {"reason": reason, "count": count}
                    for reason, count in reason_counter.most_common(10)
                ],
                "errors": errors[: int(self.config.scan["max_errors_returned"])],
            }
            self.repository.complete_scan(scan_id, "COMPLETED", summary)
            with self._state_lock:
                if self._state and self._state.get("scanId") == scan_id:
                    self._state.update(
                        {
                            "status": "COMPLETED",
                            "completedAt": _now_iso(),
                            **summary,
                        }
                    )
        except Exception as exc:
            summary = {"error": str(exc), "errors": errors}
            self.repository.complete_scan(scan_id, "FAILED", summary)
            with self._state_lock:
                if self._state and self._state.get("scanId") == scan_id:
                    self._state.update({"status": "FAILED", "completedAt": _now_iso(), "error": str(exc)})
            self.log.exception("[SOL] scan failed scan_id=%s", scan_id)

    def current_scan(self) -> dict[str, Any] | None:
        with self._state_lock:
            if self._state:
                return dict(self._state)
        persisted = self.repository.latest_scan()
        if not persisted:
            return None
        summary = persisted.get("summary") or {}
        return {
            "scanId": persisted["scan_id"],
            "status": persisted["status"],
            "startedAt": persisted["started_at"],
            "completedAt": persisted["completed_at"],
            **summary,
        }

    def signals(
        self,
        *,
        decisions: set[str] | None = None,
        asset_types: set[str] | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        return self.repository.list_signals(decisions=decisions, asset_types=asset_types, limit=limit)

    def signal(self, signal_id: str) -> dict[str, Any]:
        signal = self.repository.get_signal(signal_id)
        if signal is None:
            raise LookupError("signal_not_found")
        return signal

    def preview_execution(self, signal_id: str) -> dict[str, Any]:
        return self.execution.preview(self.signal(signal_id))

    def execute_signal(
        self,
        signal_id: str,
        *,
        mode: str,
        idempotency_key: str,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        return self.execution.execute(
            self.signal(signal_id),
            mode=mode,
            idempotency_key=idempotency_key,
            confirm_live=confirm_live,
        )
    def execution_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_executions(limit=limit)

    def replay_pair(self, symbol: str, *, bars: int | None = None) -> dict[str, Any]:
        if not bool(self.config.replay.get("enabled", True)):
            raise PermissionError("sol_replay_disabled")
        matches = self._pairs(None, {symbol})
        if not matches:
            raise LookupError("pair_not_found")
        pair = matches[0]
        requested = int(bars or self.config.replay["default_bars"])
        maximum = int(self.config.replay["maximum_bars"])
        requested = max(int(self.config.scan["minimum_bars"]["M5"]) + 50, min(requested, maximum))
        limits = {
            "M5": requested,
            "M15": min(1000, max(int(self.config.scan["bars"]["M15"]), math.ceil(requested / 3) + 80)),
            "H1": min(1000, max(int(self.config.scan["bars"]["H1"]), math.ceil(requested / 12) + 64)),
            "H4": min(1000, max(int(self.config.scan["bars"]["H4"]), math.ceil(requested / 48) + 48)),
        }
        snapshot = self.market_data.fetch_snapshot(pair, limits=limits)
        return run_causal_replay(
            pair=pair,
            frames=snapshot.frames,
            provenance=snapshot.provenance,
            config=self.config,
        )
