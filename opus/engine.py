"""The OPUS engine: evidence -> hypothesis -> geometry -> probability -> decision.

Order matters here and is deliberate.

  1. Read the market ONCE into a shared context, so every indicator sees
     identical inputs and derived structures are computed a single time.
  2. Classify the regime and the clock. These condition, never create.
  3. Score every archetype in BOTH directions. Picking a direction first and
     then scoring it hides two-sided evidence, which is the one case where
     standing aside is clearly correct.
  4. Build real geometry for the surviving hypotheses. A hypothesis that
     cannot produce a structural stop and a reachable target is not a trade,
     no matter how high it scored.
  5. Convert conviction to a calibrated probability, then to expectancy after
     the full round-trip cost.
  6. Run deterministic gates, and only then decide.

A signal is emitted for every hypothesis that clears geometry, including ones
that fail gates - blocked signals carry their reasons so the UI can show why
something did not qualify, rather than silently showing nothing.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np

import opus.config as config
from opus.data.feed import DataBundle, FeedError, fetch_bundle
from opus.indicators import (
    absorption, flow, kinetics, liquidity, regime as regime_mod, temporal,
    value as value_mod, voids,
)
from opus.indicators.base import MarketContext
from opus.risk import gates as gate_mod
from opus.risk.sizing import size_position
from opus.scoring import calibration as cal
from opus.scoring.conviction import best_hypothesis
from opus.scoring.expectancy import evaluate as evaluate_expectancy
from opus.setups import archetypes
from opus.types import (
    Decision, IndicatorReading, Readiness, Signal,
)
from opus.version import ENGINE_VERSION, SCORE_MODEL_VERSION


@dataclass
class ScanResult:
    signals: list[Signal] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    scanned: int = 0
    elapsed_sec: float = 0.0
    started_ts: float = 0.0

    def as_dict(self) -> dict:
        return {
            "signals": [s.as_dict() for s in self.signals],
            "errors": self.errors,
            "scanned": self.scanned,
            "elapsedSec": round(self.elapsed_sec, 3),
            "startedTs": self.started_ts,
            "engineVersion": ENGINE_VERSION,
            "scoreModelVersion": SCORE_MODEL_VERSION,
        }


def _signal_id(symbol: str, archetype: str, direction: str, bar_ts: float) -> str:
    """Stable per-setup id.

    Keyed on the trigger bar rather than wall clock, so re-scanning the same
    bar yields the same id and the execution layer can deduplicate against it.
    """
    raw = f"{symbol}|{archetype}|{direction}|{int(bar_ts)}|{SCORE_MODEL_VERSION}"
    return "opus_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def read_indicators(ctx: MarketContext, book: dict | None = None) -> list[IndicatorReading]:
    """Every directional indicator, computed against one shared context."""
    return [
        liquidity.compute(ctx),
        absorption.compute(ctx),
        voids.compute(ctx),
        value_mod.compute(ctx),
        kinetics.compute(ctx),
        flow.compute(ctx, book=book),
    ]


def evaluate(
    bundle: DataBundle,
    *,
    now: float | None = None,
    equity: float | None = None,
    calibrators: dict | None = None,
    validations: dict | None = None,
    max_hypotheses: int = 3,
) -> list[Signal]:
    """Score one symbol and return every viable signal, blocked ones included."""
    now = float(now if now is not None else time.time())
    calibrators = calibrators or {}
    validations = validations or {}

    ctx = MarketContext.build(
        symbol=bundle.symbol,
        display=bundle.display,
        asset_class=bundle.asset_class,
        context=bundle.context,
        structure=bundle.structure,
        trigger=bundle.trigger,
        quote=bundle.quote,
        now=now,
    )

    readings = read_indicators(ctx, book=bundle.book)
    regime_state = regime_mod.compute(ctx)
    temporal_state = temporal.compute(ctx)

    ranked = best_hypothesis(
        readings,
        regime=regime_state.regime,
        temporal_gain=temporal_state.gain,
    )

    data_gate_results = gate_mod.data_gates(ctx, bundle.timeframes)
    quote_gate_results = gate_mod.quote_gates(bundle.quote, now)

    signals: list[Signal] = []
    seen: set[tuple[str, str]] = set()

    for hypothesis in ranked:
        if len(signals) >= max_hypotheses:
            break
        if hypothesis.conviction <= 0.0 or hypothesis.reason:
            continue

        key = (hypothesis.archetype.value, hypothesis.direction.value)
        if key in seen:
            continue

        levels = archetypes.build(ctx, hypothesis.archetype, hypothesis.direction)
        if levels is None:
            continue
        seen.add(key)

        bucket = cal.bucket_key(hypothesis.archetype.value, bundle.asset_class)
        calibrator = calibrators.get(bucket) or cal.prior_calibrator(bucket)
        probability = calibrator.probability(hypothesis.conviction)

        expectancy, cost = evaluate_expectancy(
            probability=probability,
            levels=levels,
            quote=bundle.quote,
            asset_class=bundle.asset_class,
        )

        validation = validations.get(bucket)
        validated = bool(validation.passed) if validation is not None else None

        all_gates = (
            list(data_gate_results)
            + list(quote_gate_results)
            + gate_mod.levels_gates(levels, hypothesis.direction)
            + gate_mod.edge_gates(
                conviction=hypothesis.conviction,
                coherence=hypothesis.coherence,
                expectancy=expectancy,
                validated=validated,
            )
        )
        decision, readiness = gate_mod.decide(hypothesis.conviction, all_gates)

        # Size only what is actually executable. Publishing a size for a
        # blocked signal invites it to be acted on out of band.
        if decision is Decision.TRADE and readiness is Readiness.READY:
            sized = size_position(
                levels=levels,
                kelly_fraction=expectancy.kelly_fraction,
                equity=equity,
            )
        else:
            sized = size_position(levels=levels, kelly_fraction=0.0, equity=equity)

        bar_ts = float(bundle.trigger.last_ts or bundle.structure.last_ts or now)
        signal = Signal(
            symbol=bundle.symbol,
            display=bundle.display,
            asset_class=bundle.asset_class,
            direction=hypothesis.direction,
            archetype=hypothesis.archetype,
            regime=regime_state.regime,
            decision=decision,
            readiness=readiness,
            conviction=float(hypothesis.conviction),
            coherence=float(hypothesis.coherence),
            probability=float(probability),
            expectancy_r=float(expectancy.expectancy_r),
            cost_r=float(expectancy.cost_r),
            levels=levels,
            readings=readings,
            gates=all_gates,
            size_units=sized.units,
            risk_pct=sized.risk_pct,
            kelly_fraction=expectancy.kelly_fraction,
            quote=bundle.quote,
            timeframes=dict(bundle.timeframes),
            created_ts=now,
            signal_id=_signal_id(
                bundle.symbol, hypothesis.archetype.value,
                hypothesis.direction.value, bar_ts,
            ),
            provenance={
                "engineVersion": ENGINE_VERSION,
                "scoreModelVersion": SCORE_MODEL_VERSION,
                "venue": bundle.venue,
                "sigma": round(float(ctx.sigma), 8),
                "sigmaTrigger": round(float(ctx.sigma_trigger), 8),
                "barTs": bar_ts,
                "conviction": hypothesis.as_dict(),
                "regime": regime_state.as_dict(),
                "temporal": temporal_state.as_dict(),
                "cost": cost.as_dict(),
                "expectancy": expectancy.as_dict(),
                "calibration": calibrator.as_dict(),
                "validation": validation.as_dict() if validation is not None else None,
                "sizing": sized.as_dict(),
                "feedErrors": list(bundle.errors),
            },
            notes=_notes(hypothesis, regime_state, temporal_state, expectancy),
        )
        signals.append(signal)

    signals.sort(
        key=lambda s: (s.decision is not Decision.TRADE, -s.expectancy_r, -s.conviction)
    )
    return signals


def _notes(hypothesis, regime_state, temporal_state, expectancy) -> list[str]:
    notes = []
    if regime_state.confidence < 0.25:
        notes.append(
            f"Regime {regime_state.regime.value} is borderline "
            f"(confidence {regime_state.confidence:.2f})"
        )
    if temporal_state.fallback:
        notes.append("Time-of-day model has too few samples; using neutral gain")
    elif temporal_state.gain < 0.6:
        notes.append(
            f"Clock is unsupportive at {temporal_state.as_dict()['bucketLabel']} "
            f"(gain {temporal_state.gain:.2f})"
        )
    if expectancy.cost_r > 0.20:
        notes.append(
            f"Cost consumes {expectancy.cost_r:.0%} of risk; "
            f"breakeven needs {expectancy.breakeven_probability:.0%}"
        )
    if hypothesis.coherence < 0.45:
        notes.append(
            f"Indicators disagree (coherence {hypothesis.coherence:.2f}); "
            "conviction discounted"
        )
    return notes


def scan(
    symbols: list[str] | None = None,
    *,
    now: float | None = None,
    equity: float | None = None,
    max_workers: int | None = None,
    include_blocked: bool = True,
    store=None,
) -> ScanResult:
    """Evaluate the configured universe (or a subset) concurrently."""
    started = time.time()
    now = float(now if now is not None else started)

    universe = config.universe()
    if symbols:
        wanted = {s.strip().upper() for s in symbols if s and s.strip()}
        universe = [
            u for u in universe
            if u["symbol"].upper() in wanted or str(u.get("display", "")).upper() in wanted
        ]

    calibrators: dict = {}
    validations: dict = {}
    if store is not None:
        try:
            calibrators = store.calibrators()
            validations = store.validations()
        except Exception:  # noqa: BLE001 - calibration is an enhancement, not a dependency
            calibrators, validations = {}, {}

    result = ScanResult(started_ts=now)
    # The universe now tracks the whole active portfolio, which is an order of
    # magnitude larger than the old fixed list, so the pool width is config-
    # driven rather than pinned at 8.
    configured = int(config.get("SCAN.max_workers", 8) or 8)
    workers = int(max_workers or min(max(1, configured), max(1, len(universe))))

    def _one(spec: dict) -> tuple[dict, list[Signal] | None, str | None]:
        try:
            bundle = fetch_bundle(spec, now=now)
            if not bundle.usable:
                return spec, None, (
                    f"insufficient bars "
                    f"(ctx={len(bundle.context)}, str={len(bundle.structure)}, "
                    f"trg={len(bundle.trigger)})"
                )
            return spec, evaluate(
                bundle, now=now, equity=equity,
                calibrators=calibrators, validations=validations,
            ), None
        except FeedError as exc:
            return spec, None, str(exc)
        except Exception as exc:  # noqa: BLE001
            return spec, None, f"{type(exc).__name__}: {exc}"

    if not universe:
        result.elapsed_sec = time.time() - started
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, spec) for spec in universe]
        for future in as_completed(futures):
            spec, signals, error = future.result()
            result.scanned += 1
            if error:
                result.errors.append({"symbol": spec["symbol"], "error": error})
                continue
            for signal in signals or []:
                if not include_blocked and signal.decision is not Decision.TRADE:
                    continue
                result.signals.append(signal)

    result.signals.sort(
        key=lambda s: (s.decision is not Decision.TRADE, -s.expectancy_r, -s.conviction)
    )
    result.elapsed_sec = time.time() - started

    if store is not None:
        try:
            store.record_signals(result.signals)
        except Exception:  # noqa: BLE001
            pass
    return result


__all__ = ["ScanResult", "evaluate", "scan", "read_indicators"]
