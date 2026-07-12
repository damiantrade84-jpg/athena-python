"""Server-authoritative Ghost Trade scan and shadow lifecycle service."""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Callable, Mapping, Sequence

from .config import GhostConfig
from .models import GhostMode, GhostSignal, SignalStatus, Style
from .persistence import GhostRepository, signal_id_for
from .reconciliation import resolve_shadow_position
from .scanner import json_safe_component
from .scoring import score_confirmed


class ScanAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScanResult:
    scan_id: str
    started_at: datetime
    completed_at: datetime
    discovered_count: int
    scored_count: int
    skipped_count: int
    errors: tuple[str, ...]
    signal_ids: tuple[str, ...]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanId": self.scan_id,
            "startedAt": self.started_at.isoformat(),
            "completedAt": self.completed_at.isoformat(),
            "discoveredCount": self.discovered_count,
            "scoredCount": self.scored_count,
            "skippedCount": self.skipped_count,
            "errorCount": self.error_count,
            "errors": list(self.errors),
            "signalIds": list(self.signal_ids),
        }


class GhostService:
    def __init__(
        self,
        *,
        config: GhostConfig,
        repository: GhostRepository,
        universe_providers: Sequence[Any],
        candle_provider: Any,
        score_fn: Callable[..., Any] = score_confirmed,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.repository = repository
        self._universe_providers = tuple(universe_providers)
        self._candle_provider = candle_provider
        self._score_fn = score_fn
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._scan_lock = threading.Lock()

    def scan(self, *, style: Style = Style.INTRADAY) -> ScanResult:
        if not self._scan_lock.acquire(blocking=False):
            raise ScanAlreadyRunning("scan_already_running")
        started = self._clock()
        scan_id = str(uuid.uuid4())
        source_names = [provider.__class__.__name__ for provider in self._universe_providers]
        self.repository.create_scan(scan_id, started, source_names)
        discovered = scored = skipped = 0
        errors: list[str] = []
        signals: list[GhostSignal] = []
        started_monotonic = time.monotonic()
        try:
            instruments = []
            for provider in self._universe_providers:
                universe = provider.discover()
                instruments.extend(universe.instruments)
                errors.extend(universe.errors)
            instruments.sort(key=lambda item: (item.canonical_symbol, item.broker_symbol))
            discovered = len(instruments)
            for item in instruments:
                self.repository.upsert_instrument(item)
                if not item.eligible_for_scoring:
                    skipped += 1
                    continue
                try:
                    bundle = self._candle_provider.load(item, style, started)
                    profile = self.config.group_profiles.get(item.asset_group.value, {})
                    volume_mode = str(profile.get("volume_quality_mode") or "unavailable")
                    score = self._score_fn(
                        bundle,
                        volume_mode=volume_mode,
                        minimum_confirmed_score=self.config.minimum_confirmed_score,
                        minimum_entry_quality=self.config.minimum_entry_quality,
                        minimum_raw_rr=self.config.minimum_raw_rr,
                    )
                except Exception as exc:
                    errors.append(f"candle_load_failed:{item.broker_symbol}:{exc}")
                    continue
                scored += 1
                signal = self._signal_from_score(
                    scan_id=scan_id,
                    instrument=item,
                    style=style,
                    bundle=bundle,
                    score=score,
                )
                signals.append(signal)

            ranked = self._rank_signals(signals)
            for signal in ranked:
                self.repository.upsert_signal(signal)
                if self.config.mode is GhostMode.SHADOW and signal.status is SignalStatus.ELIGIBLE:
                    self.repository.open_shadow_position(signal)
            completed = self._clock()
            status = "COMPLETED_WITH_ERRORS" if errors else "COMPLETED"
            self.repository.complete_scan(
                scan_id,
                completed_at=completed,
                status=status,
                discovered_count=discovered,
                scored_count=scored,
                skipped_count=skipped,
                errors=errors,
                duration_ms=(time.monotonic() - started_monotonic) * 1000.0,
            )
            return ScanResult(
                scan_id, started, completed, discovered, scored, skipped,
                tuple(errors), tuple(signal.signal_id for signal in ranked),
            )
        except Exception as exc:
            completed = self._clock()
            errors.append(f"scan_failed:{exc}")
            self.repository.complete_scan(
                scan_id,
                completed_at=completed,
                status="FAILED",
                discovered_count=discovered,
                scored_count=scored,
                skipped_count=skipped,
                errors=errors,
                duration_ms=(time.monotonic() - started_monotonic) * 1000.0,
            )
            raise
        finally:
            self._scan_lock.release()

    def _signal_from_score(self, *, scan_id, instrument, style, bundle, score) -> GhostSignal:
        confirmed_times = {
            label: candles[-1].close_time.isoformat()
            for label, candles in (("D1", bundle.d1), ("H4", bundle.h4), ("H1", bundle.h1))
            if candles
        }
        decision_time = bundle.as_of
        signal_id = signal_id_for(
            engine_version="ghost-v1",
            venue=instrument.venue,
            broker_symbol=instrument.broker_symbol,
            style=style,
            decision_time=decision_time,
            confirmed_times=confirmed_times,
        )
        geometry = score.geometry.geometry
        reasons = list(score.reasons)
        if self.config.mode is GhostMode.SHADOW:
            reasons.append("shadow_mode")
        components = {
            "D1Structure": json_safe_component(score.structure),
            "H4Momentum": json_safe_component(score.momentum),
            "H4Volatility": json_safe_component(score.volatility),
            "H1EntryQuality": json_safe_component(score.entry_quality),
            "H1Exhaustion": json_safe_component(score.exhaustion),
        }
        return GhostSignal(
            signal_id=signal_id,
            signal_version="ghost-v1",
            scan_id=scan_id,
            instrument=instrument,
            style=style,
            direction=score.direction,
            decision_time=decision_time,
            confirmed_score=score.confirmed_score,
            live_adjustment=0.0,
            display_score=score.confirmed_score,
            direction_confidence=score.direction_confidence,
            entry_quality=score.entry_quality.score,
            entry=geometry.entry if geometry else None,
            stop=geometry.stop if geometry else None,
            target=geometry.target if geometry else None,
            raw_rr=geometry.raw_rr if geometry else None,
            volatility_regime=score.volatility.regime,
            can_execute=False,
            status=SignalStatus.ELIGIBLE if score.selected else SignalStatus.ANALYSED,
            reasons=tuple(dict.fromkeys(reasons)),
            components=components,
            confirmed_times=confirmed_times,
            data_freshness="FRESH",
        )

    @staticmethod
    def _rank_signals(signals: list[GhostSignal]) -> list[GhostSignal]:
        ordered = sorted(signals, key=lambda item: (-item.confirmed_score, item.instrument.canonical_symbol))
        groups: dict[str, list[GhostSignal]] = defaultdict(list)
        for signal in ordered:
            groups[signal.instrument.asset_group.value].append(signal)
        global_count = len(ordered)
        ranked: list[GhostSignal] = []
        for global_rank, signal in enumerate(ordered, 1):
            group_rows = groups[signal.instrument.asset_group.value]
            group_rank = group_rows.index(signal) + 1
            ranked.append(
                replace(
                    signal,
                    global_rank=global_rank,
                    global_count=global_count,
                    group_rank=group_rank,
                    group_count=len(group_rows),
                )
            )
        return ranked

    def reconcile_shadow(self, candles_by_symbol: Mapping[str, Sequence[Any]]) -> dict[str, int]:
        checked = closed = ambiguous = 0
        for position in self.repository.list_positions(status="OPEN"):
            if position.mode != "SHADOW":
                continue
            candles = candles_by_symbol.get(position.canonical_symbol)
            if not candles:
                continue
            checked += 1
            result = resolve_shadow_position(position, candles)
            if result is None:
                continue
            closed += 1
            ambiguous += int(result["ambiguous"])
            self.repository.close_position(
                position,
                closed_at=result["closed_at"],
                exit_price=result["price"],
                gross_r=result["gross_r"],
                exit_reason=result["reason"],
                payload={"ambiguous": result["ambiguous"], "shadowOnly": True},
            )
        return {"checked": checked, "closed": closed, "ambiguous": ambiguous}

    def performance(self) -> dict[str, Any]:
        rows = self.repository.list_closed_trades()
        values = [float(row["net_r"]) for row in rows]
        wins = [value for value in values if value > 0]
        losses = [value for value in values if value < 0]
        profit_factor = sum(wins) / abs(sum(losses)) if losses else None
        return {
            "totalClosedTrades": len(rows),
            "meanR": fmean(values) if values else None,
            "medianR": sorted(values)[len(values) // 2] if values else None,
            "winRate": sum(value > 0 for value in values) / len(values) if values else None,
            "profitFactor": profit_factor if profit_factor is not None else (0.0 if losses else None),
            "totalR": sum(values),
        }
