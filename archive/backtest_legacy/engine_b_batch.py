"""Engine B ("naked") batch backtest: parallel REST lane + serial MT5 lane.

Per-pair work calls the UNMODIFIED ``backtest_pair_naked`` so results are
bit-identical to running each pair through ``/api/backtest-naked``. REST-sourced
pairs run in a ProcessPoolExecutor (workers bootstrap the runtime via
``import athena`` -- no live feeds, since ``ensure_runtime_services_started`` is
``__main__``-only). MT5-sourced pairs run serially in the calling (main) process
to respect the single MetaTrader5 terminal connection.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

log = logging.getLogger("sentinel")

_batch_lock = threading.Lock()
_batch_running = False


def _pair_key(pair: dict) -> str:
    return str(pair.get("display") or pair.get("symbol") or "?")


def _is_mt5(pair: dict) -> bool:
    return str(pair.get("source") or "").lower() == "mt5"


def resolve_engine_b_batch_pairs(tokens, all_pairs: list[dict], jse_pairs) -> list[dict]:
    """Resolve symbol/display tokens to pair dicts.

    Empty/falsy ``tokens`` => all pairs except JSE (mirrors Engine A's
    ``run_full_backtest`` default). Tokens are matched case-insensitively against
    display or symbol; unknown tokens are dropped; order/uniqueness preserved.
    """
    jse_syms = {p.get("symbol") for p in (jse_pairs or [])}
    eligible = [p for p in all_pairs if p.get("symbol") not in jse_syms]
    if not tokens:
        return eligible
    by_key: dict[str, dict] = {}
    for p in all_pairs:
        for k in (p.get("display"), p.get("symbol")):
            if k:
                by_key.setdefault(str(k).strip().upper(), p)
    out: list[dict] = []
    seen: set[int] = set()
    for tok in tokens:
        p = by_key.get(str(tok).strip().upper())
        if p is not None and id(p) not in seen:
            out.append(p)
            seen.add(id(p))
    return out


def _worker_init() -> None:
    """Bootstrap the backtest runtime once per worker process (no live feeds).

    Loads the ``athena.py`` monolith *by file path*, NOT via ``import athena`` --
    the latter resolves to the ``athena/`` package, which never calls
    ``set_runtime``. This mirrors ``tools/run_backtest_matrix.py:_load_athena_module``.
    ``ensure_runtime_services_started`` is ``__main__``-only, so executing the
    module here starts no WebSockets/warmers/MT5 pollers.
    """
    import importlib.util
    import os
    import sys

    # Backtest-only runtime: keep auto_trader.configure() from starting the
    # TimedExitMonitor broker thread (guard at auto_trader.py). Without this,
    # exec'ing athena.py here spins up a monitor that closes open broker tickets.
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")

    repo_root = os.path.dirname(os.path.abspath(__file__))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    athena_path = os.path.join(repo_root, "athena.py")
    spec = importlib.util.spec_from_file_location("athena_monolith", athena_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load athena.py from {athena_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # runs set_runtime(...) at module level


def _run_one_pair(pair: dict, params: dict) -> dict:
    """Run one Engine B pair backtest and shape a BatchRow-compatible dict.

    Mirrors the single-pair route + the dashboard's per-pair ok/error handling so
    batch rows are behaviourally identical to the existing client-side loop.
    """
    from backtest_runner import backtest_pair_naked
    from config import _json_safe

    pk = _pair_key(pair)
    try:
        res = backtest_pair_naked(
            pair,
            style=params.get("style", "auto"),
            validation_mode=params.get("validation_mode", "standard"),
            purge_gap=params.get("purge_gap", 200),
            folds=params.get("folds", 3),
        )
    except Exception as exc:  # noqa: BLE001  -- isolate one pair's failure
        return {"pairKey": pk, "ok": False, "error": str(exc)}

    if res is None:
        return {
            "pairKey": pk,
            "ok": False,
            "error": "Insufficient candle data to run Engine B backtest for this pair.",
        }

    safe = _json_safe(res) if callable(_json_safe) else res
    if isinstance(safe, dict) and safe.get("error"):
        return {"pairKey": pk, "ok": False, "error": str(safe.get("error")), "result": safe}
    return {"pairKey": pk, "ok": True, "result": safe}


def _worker_run_pair(args: tuple[dict, dict]) -> dict:
    """ProcessPool entry point. ``args`` = (pair, params)."""
    pair, params = args
    return _run_one_pair(pair, params)


def run_engine_b_batch(
    pairs: list[dict],
    *,
    style: str = "auto",
    validation_mode: str = "standard",
    purge_gap: int = 200,
    folds: int = 3,
    max_workers: int | None = None,
    executor_factory=None,
) -> dict:
    """Backtest many Engine B pairs: REST pairs in a process pool, MT5 pairs serially.

    Returns ``{success, totalPairs, okCount, rows}`` where each row is
    ``{pairKey, ok, error?, result?}`` (input order preserved).
    ``executor_factory`` lets tests inject a synchronous in-process executor.
    """
    global _batch_running
    params = {
        "style": style,
        "validation_mode": validation_mode,
        "purge_gap": purge_gap,
        "folds": folds,
    }

    with _batch_lock:
        if _batch_running:
            return {
                "success": False,
                "error": "An Engine B batch run is already in progress.",
                "totalPairs": 0,
                "okCount": 0,
                "rows": [],
            }
        _batch_running = True

    try:
        rest_pairs = [p for p in pairs if not _is_mt5(p)]
        mt5_pairs = [p for p in pairs if _is_mt5(p)]
        rows_by_key: dict[str, dict] = {}

        if rest_pairs:
            if executor_factory is None:
                from config import CONFIG, get_optimal_workers

                workers = max_workers or get_optimal_workers(
                    configured_max=int(CONFIG.get("BACKTEST_MAX_WORKERS", 10) or 10),
                    conservative=True,
                )
                workers = max(1, min(int(workers), len(rest_pairs)))

                def _default_factory():
                    return ProcessPoolExecutor(max_workers=workers, initializer=_worker_init)

                factory = _default_factory
                log.info(
                    "[ENGINE B BATCH] REST lane: %d pairs across %d workers",
                    len(rest_pairs),
                    workers,
                )
            else:
                factory = executor_factory

            with factory() as pool:
                fut_map = {pool.submit(_worker_run_pair, (p, params)): p for p in rest_pairs}
                for fut in as_completed(fut_map):
                    p = fut_map[fut]
                    pk = _pair_key(p)
                    try:
                        rows_by_key[pk] = fut.result()
                    except Exception as exc:  # noqa: BLE001  -- worker process crash
                        rows_by_key[pk] = {
                            "pairKey": pk,
                            "ok": False,
                            "error": f"worker failed: {exc}",
                        }

        if mt5_pairs:
            log.info("[ENGINE B BATCH] MT5 lane: %d pairs (serial)", len(mt5_pairs))
        for p in mt5_pairs:
            rows_by_key[_pair_key(p)] = _run_one_pair(p, params)

        rows = [rows_by_key[_pair_key(p)] for p in pairs if _pair_key(p) in rows_by_key]
        ok_n = sum(1 for r in rows if r.get("ok"))
        return {"success": True, "totalPairs": len(pairs), "okCount": ok_n, "rows": rows}
    finally:
        with _batch_lock:
            _batch_running = False
