"""Tuning Lab worker: run one pair's baseline vs. overlay comparison.

Invoked from a real OS subprocess (``python -m athena_experiment.worker_cli``,
see that module and ``subprocess_runner.py`` for why it's a plain subprocess
rather than ``ProcessPoolExecutor``). Mirrors
``athena_backtest/jobs.py::run_symbol_worker``'s cold-import pattern:
symbol/engine/style/overlay go in, plain dict rows come out — no Flask state
crosses the process boundary.

The isolation property this whole feature depends on: ``run_experiment_worker``
mutates ``config.CONFIG`` after computing the baseline run (restoring it
afterward), but that mutation happens inside a freshly spawned *child*
process. A concurrently running scan lives in a different OS process entirely
and never observes this mutation — this is what lets the Tuning Lab run
without adding any latency or risk to live scanning.
"""

from __future__ import annotations

from typing import Any


def run_experiment_worker(
    symbol: str,
    engine: str,
    style: str,
    overlay: dict[str, Any],
    n_trials_hint: int = 1,
) -> dict[str, Any]:
    """Run the same pair through Engine A/B twice: once with today's real
    config (baseline), once with ``overlay`` deep-merged on top (variant).
    Both reads come from the same frozen ``ParquetCandleStore`` data — no new
    candle fetch, same store the existing V3 backtester already uses.
    """
    from athena_backtest.data import fetchers
    from athena_backtest.data.store import ParquetCandleStore
    from athena_backtest.runner import run_pair
    from athena_experiment.overlay import apply_overlay

    mod = fetchers._load_monolith()
    wanted = str(symbol).strip().upper()
    pair = next(
        (
            p
            for p in getattr(mod, "ALL_PAIRS", [])
            if str(p.get("symbol") or "").upper() == wanted
            or str(p.get("display") or "").upper() == wanted
        ),
        None,
    )
    if pair is None:
        return {"symbol": symbol, "error": "unknown_symbol"}

    store = ParquetCandleStore()
    engine_u = str(engine).upper()
    style_l = str(style).lower()

    baseline = run_pair(
        pair, engine=engine_u, horizon=style_l, store=store, n_trials_hint=n_trials_hint
    )

    from config import CONFIG

    # Snapshot before mutating so this function is safe to call more than once
    # in the same process (e.g. a script driving several experiments back to
    # back): without restoring afterward, a second call's "baseline" would
    # silently inherit the first call's overlay.
    pristine = dict(CONFIG) if overlay else None
    if overlay:
        merged = apply_overlay(CONFIG, overlay)
        # In-place mutation of the same dict object every already-imported
        # module holds a reference to (config.CONFIG is a module-level
        # singleton) — this is what makes the overlay take effect for the
        # variant run without threading a config param through every scoring
        # function. Confined to this one spawned worker process.
        CONFIG.clear()
        CONFIG.update(merged)

    variant = run_pair(
        pair, engine=engine_u, horizon=style_l, store=store, n_trials_hint=n_trials_hint
    )

    if pristine is not None:
        CONFIG.clear()
        CONFIG.update(pristine)

    return {
        "symbol": pair.get("symbol"),
        "pair": pair.get("display"),
        "_pair_meta": {
            "display": pair.get("display"),
            "symbol": pair.get("symbol"),
            "type": pair.get("type"),
        },
        "baseline": baseline,
        "variant": variant,
        "overlayApplied": overlay,
    }
