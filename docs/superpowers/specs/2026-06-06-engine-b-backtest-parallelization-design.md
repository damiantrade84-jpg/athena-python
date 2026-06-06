# Engine B Backtest Parallelization — Design

**Date:** 2026-06-06
**Status:** Proposed (awaiting review)
**Scope:** Speed up Engine B ("naked" market-structure) backtesting *across all pairs*, with **bit-identical** results.

---

## 1. Problem

Running Engine B backtests across all pairs is very slow. Evidence from the current code:

- The dashboard "batch" path is a **client-side sequential loop**. `BacktestPanel.tsx:287-296` (`handleRun`, `batchMode`) iterates the pair list and `await`s `POST /api/backtest-naked` **one pair at a time**.
- `/api/backtest-naked` (`athena.py:8343`) is **single-pair only** — there is no server-side Engine B batch route. (Only Engine A has `run_full_backtest` via `/api/backtest`.)
- The server is Flask's `app.run()` (`athena.py:17268`); concurrent CPU-bound requests are throttled by the GIL anyway.
- Each per-pair `backtest_pair_naked` (`backtest_runner.py:4830`) is itself CPU-heavy: it loops every entry bar (~17,600 H1 bars/pair for crypto over 730d) and calls `precompute_structure_data` each bar, which rebuilds numpy arrays and re-runs `find_peaks`/zones/FVG/BOS over the full growing window (≈ O(N²) per pair).
- The matrix runner (`tools/run_backtest_matrix.py`) defaults to `--max-workers 1` and warns *"Engine B cache contention is possible"* when threaded — confirming Engine B is not thread-safe for in-process parallelism.

**Net:** N pairs × a slow per-pair backtest, executed serially.

## 2. Goal & Non-Goals

**Goal:** Run the independent per-pair Engine B backtests **concurrently across OS processes**, so a full all-pairs run finishes in a fraction of the wall-clock time — while producing results identical to today's.

**Non-goals (explicitly out of scope):**
- No change to `backtest_pair_naked`, `precompute_structure_data`, `market_structure.py`, or any scoring/threshold/structure logic. (The per-bar O(N²) structure caching — which would touch the live-shared engine — is a *separate, deferred* effort requiring a parity review.)
- No change to Engine A/C/D backtest paths or the single-pair `/api/backtest-naked` route.
- No live-execution surface touched.

## 3. Key Constraints (verified)

1. **Bit-identical parity (required).** Engine B structure code is shared with live. We therefore do **not** modify it — we only change *where* `backtest_pair_naked` runs. Identical inputs → identical outputs by construction.
2. **Clean worker bootstrap exists.** `set_runtime(...)` runs at athena.py module level (`16220`), but all live feeds (WebSockets, EODHD warmers, MT5 tick poller) live inside `ensure_runtime_services_started()` which is called **only** under `if __name__ == "__main__"` (`athena.py:17034`). So **importing athena as a module yields a fully-functional backtest runtime with zero live feeds** — the same mechanism the matrix runner already uses (`_load_athena_module`).
3. **MT5 single-connection limit.** The `MetaTrader5` package allows one terminal connection per process. MT5-sourced pairs (forex/commodity/index) cannot run across many worker processes; REST-sourced pairs (crypto via Binance, stocks/indices via EODHD/Polygon) parallelize freely.
4. **Worker import is heavy.** Importing athena (+pandas/numpy/all modules) costs seconds and RAM per worker → use few, but pay it once per worker per run.

## 4. Chosen Approach

**New server-side batch endpoint backed by a process pool**, with a **serial MT5 lane**. (Selected over: launching the matrix runner as a subprocess; and over an in-process thread pool, which the GIL + Engine B's documented cache contention rule out.)

### 4.1 Components

```
BacktestPanel.tsx (batchMode + engine B)
        │  POST /api/backtest-naked-all  { pairs?, style, validation_mode, purge_gap, folds }
        ▼
athena.py: api_backtest_naked_all()      ← new route, inline beside api_backtest_naked
        │  resolves tokens → pair dicts (ALL_PAIRS minus JSE; default = all)
        ▼
engine_b_batch.run_engine_b_batch(pairs, *, style, validation_mode, purge_gap, folds,
                                  executor_factory=None)
        ├─ split pairs:  REST-sourced  vs  MT5-sourced (pair["source"] == "mt5")
        ├─ REST lane  → ProcessPoolExecutor(max_workers=get_optimal_workers(...),
        │                                   initializer=_worker_init)
        │                 each task → _worker_run_pair(...) → backtest_pair_naked(...)  [unchanged]
        ├─ MT5 lane   → run serially **in the main process** (reuses its single live MT5 connection)
        └─ aggregate → { success, results: [...], errors: [...], totalPairs }
```

### 4.2 New module: `engine_b_batch.py` (repo root, sibling of `backtest_runner.py`)

Module-level (so they are importable/picklable for Windows `spawn`):

- `_worker_init()` — runs once per worker process. Imports athena as a module to bootstrap the runtime (`set_runtime` fires; **no** live feeds). Idempotent.
- `_worker_run_pair(payload) -> dict` — imports `backtest_pair_naked` from `backtest_runner`, calls it with the given pair + params, returns `_json_safe(result)` (pure-Python, picklable). Per-pair exceptions are caught and returned as `{ "pairKey": ..., "ok": False, "error": str(e) }`.
- `run_engine_b_batch(pairs, *, style, validation_mode, purge_gap, folds, executor_factory=None) -> dict` — orchestrates REST/MT5 split, drives the pool (REST) and the serial loop (MT5), aggregates into `BatchRow`-compatible rows, returns the summary dict. `executor_factory` defaults to a per-call `ProcessPoolExecutor`; tests inject an **inline (synchronous) executor** for parity testing.

**Pool lifecycle:** created per batch call and shut down on completion (memory hygiene; bootstrap cost is paid once per worker per run, in parallel). Worker count = `get_optimal_workers(configured_max=CONFIG.get("BACKTEST_MAX_WORKERS", 10), conservative=True)`.

**Single-flight:** a module-level lock rejects a second concurrent batch with a clear "already running" response (avoids stacking pools / overloading the live process).

### 4.3 Route: `athena.py`

Add `@app.route("/api/backtest-naked-all", methods=["POST"])` **inline beside `api_backtest_naked`** (matches the local convention — all backtest routes are inline in athena.py). It:
- reads `style` (default `"auto"`), `validation_mode`, `purge_gap` (200), `folds` (3) — same defaults as the single-pair route;
- reads optional `pairs` (list of symbols/displays); if absent/empty, uses all Engine-B-eligible pairs (`ALL_PAIRS` minus JSE, mirroring `run_full_backtest`);
- resolves tokens → pair dicts server-side;
- calls `run_engine_b_batch(...)` and returns `_json_safe(...)` of the summary.

**v1 is synchronous** (blocks until the run completes, then returns all rows). This matches current UX exactly — the existing client loop already shows no incremental progress and only renders rows at the end — and the app is localhost (no proxy timeout). Async job + polling is noted as a future enhancement if long runs hit timeouts.

### 4.4 Frontend: `BacktestPanel.tsx`

Surgical change in `handleRun`, **Engine B + `batchMode` only**:
- Replace the client-side `for...await` loop with a **single** `POST /api/backtest-naked-all` carrying the resolved token list (+ style/validation_mode).
- Map the returned `results`/`errors` into the existing `BatchRow[]` and call `setBatchRows(...)` — the results table UI is reused unchanged.
- Engines A/C/D keep their current client-side loop (out of scope).

## 5. Parity & Correctness Strategy

**Parity is structural:** workers call the *unmodified* `backtest_pair_naked`. Given identical input candles, output is identical regardless of process. The only real-world difference between two runs is live-data drift between fetches — a property of the existing single-pair path too, not introduced here.

**Tests (targeted, per CLAUDE.md — no full-suite runs):**
1. **Orchestration parity** (`tests/test_engine_b_batch.py`): with fixed/monkeypatched candle data and an **inline synchronous executor**, assert `run_engine_b_batch([...])` rows equal the result of calling `backtest_pair_naked` directly for each pair (exact equality, ignoring volatile fields: `runtime_seconds` and any wall-clock timestamps). Proves the split/aggregate logic preserves outputs.
2. **Routing**: assert MT5-source pairs go to the serial lane and REST-source pairs to the pool (inject a recording executor).
3. **Error isolation**: a pair that raises is reported as a failed row without aborting the batch.
4. **Single-flight**: a second concurrent call is rejected cleanly.

## 6. Success Criteria

- A full all-pairs Engine B run completes in ≈ `bootstrap + serial_MT5_time + REST_time / num_workers` instead of `Σ per-pair time` — a multi-× wall-clock win on the REST-sourced majority (≈ `0.6 × logical cores`×). *(Observed/timed on the user's machine; not a CI assertion.)*
- Batch results are field-for-field identical to serial single-pair results on the same inputs (Test 1).
- Worker processes start **no** live feeds (only `ensure_runtime_services_started` does, never called in workers).
- Single-pair route and Engines A/C/D are unchanged.

## 7. Risks & Mitigations

- **API rate limits** when many workers fetch crypto/EODHD concurrently → conservative default worker count; if 429s appear, lower `BACKTEST_MAX_WORKERS`. (Not pre-solved — YAGNI.)
- **Memory** (each worker imports athena + holds full candle sets) → `get_optimal_workers` already caps by RAM (~2.5 GB/worker); per-batch pool releases memory after each run.
- **Windows `spawn` re-import cost** → amortized by running many pairs per worker; initializer imports athena once.
- **Import-time side effects** → re-confirm during implementation that importing athena as a module performs no network/broker calls beyond `set_runtime` (already relied upon by the matrix runner).
- **Long synchronous request** → acceptable on localhost for v1; async+polling is the documented fallback.

## 8. Deferred (not this effort)

- Per-bar structure caching inside `precompute_structure_data` (recompute only when a new H4/D1 bar closes) — large per-pair win but touches live-shared code; requires the live/backtest parity review gate.
- Async job + progress polling for the batch endpoint.
- Extending the same batch pattern to Engines A/C/D.
