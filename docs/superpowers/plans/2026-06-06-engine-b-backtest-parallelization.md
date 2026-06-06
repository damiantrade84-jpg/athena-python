# Engine B Backtest Parallelization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Engine B ("naked") backtests across all pairs concurrently (parallel REST lane + serial MT5 lane) with bit-identical per-pair results, exposed via a new batch endpoint and wired into the dashboard's existing batch mode.

**Architecture:** A new `engine_b_batch.py` module runs each pair's *unchanged* `backtest_pair_naked` in a `ProcessPoolExecutor` for REST-sourced pairs (workers bootstrap the athena runtime once via `import athena`, which starts no live feeds because `ensure_runtime_services_started()` is `__main__`-only) and serially in the main process for MT5-sourced pairs (one terminal connection). A thin inline route `POST /api/backtest-naked-all` calls it; `BacktestPanel.tsx` batch-mode for Engine B posts once instead of looping client-side.

**Tech Stack:** Python 3 / Flask (`athena.py`), `concurrent.futures.ProcessPoolExecutor`, pytest; React + TypeScript (`static/react-app/app`).

**Parity invariant:** No edits to `backtest_pair_naked`, `market_structure.py`, or any scoring/structure code. Identical inputs → identical outputs by construction. Spec: `docs/superpowers/specs/2026-06-06-engine-b-backtest-parallelization-design.md`.

**Commit note:** CLAUDE.md says commit only when the user asks. Treat each "Commit" step as *staged pending the user's go-ahead* unless they have authorized commits.

---

### Task 1: `engine_b_batch.py` — orchestration core

**Files:**
- Create: `engine_b_batch.py`
- Test: `tests/test_engine_b_batch.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_engine_b_batch.py
from concurrent.futures import Future

import backtest_runner
import engine_b_batch


class _InlineExecutor:
    """Runs submitted work synchronously, in-process (no subprocesses)."""
    def __enter__(self):
        return self
    def __exit__(self, *_a):
        return False
    def submit(self, fn, *args, **kwargs):
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001
            f.set_exception(exc)
        return f


def _fake_naked(pair, **kwargs):
    # Deterministic per-pair result echoing the params so we can assert pass-through.
    return {"pair": pair["display"], "engine": "NAKED", "totalTrades": 1,
            "sqn": 1.23, "_params": kwargs}


def test_batch_matches_direct_serial(monkeypatch):
    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", _fake_naked)
    pairs = [
        {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"},
        {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"},
    ]
    out = engine_b_batch.run_engine_b_batch(
        pairs, style="naked", validation_mode="standard", purge_gap=200, folds=3,
        executor_factory=lambda: _InlineExecutor(),
    )
    assert out["success"] is True
    assert out["totalPairs"] == 2
    assert [r["pairKey"] for r in out["rows"]] == ["AAPL", "BTCUSDT"]  # input order preserved
    for r in out["rows"]:
        assert r["ok"] is True
        direct = _fake_naked({"display": r["pairKey"]},
                             style="naked", validation_mode="standard",
                             purge_gap=200, folds=3)
        assert r["result"]["_params"] == direct["_params"]
        assert r["result"]["sqn"] == direct["sqn"]


def test_mt5_pairs_run_serially_not_in_pool(monkeypatch):
    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", _fake_naked)
    submitted = []

    class _RecordingExecutor(_InlineExecutor):
        def submit(self, fn, *args, **kwargs):
            submitted.append(args)
            return super().submit(fn, *args, **kwargs)

    pairs = [
        {"display": "EURUSD", "symbol": "EURUSD", "type": "forex", "source": "mt5"},
        {"display": "BTCUSDT", "symbol": "BTCUSDT", "type": "crypto", "source": "binance"},
    ]
    out = engine_b_batch.run_engine_b_batch(
        pairs, executor_factory=lambda: _RecordingExecutor(),
    )
    pool_pairs = [a[0][0]["symbol"] for a in submitted]  # args = ((pair, params),)
    assert pool_pairs == ["BTCUSDT"]              # only REST pair went to the pool
    assert {r["pairKey"] for r in out["rows"]} == {"EURUSD", "BTCUSDT"}
    assert all(r["ok"] for r in out["rows"])      # MT5 pair ran serially and succeeded


def test_per_pair_error_isolated(monkeypatch):
    def _boom(pair, **kwargs):
        if pair["symbol"] == "BAD":
            raise RuntimeError("kaboom")
        return {"pair": pair["display"], "totalTrades": 0}

    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", _boom)
    pairs = [
        {"display": "BAD", "symbol": "BAD", "type": "stock", "source": "eodhd"},
        {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"},
    ]
    out = engine_b_batch.run_engine_b_batch(pairs, executor_factory=lambda: _InlineExecutor())
    by_key = {r["pairKey"]: r for r in out["rows"]}
    assert by_key["BAD"]["ok"] is False and "kaboom" in by_key["BAD"]["error"]
    assert by_key["AAPL"]["ok"] is True


def test_none_result_is_failed_row(monkeypatch):
    monkeypatch.setattr(backtest_runner, "backtest_pair_naked", lambda pair, **k: None)
    pairs = [{"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}]
    out = engine_b_batch.run_engine_b_batch(pairs, executor_factory=lambda: _InlineExecutor())
    assert out["rows"][0]["ok"] is False
    assert "Insufficient" in out["rows"][0]["error"]


def test_resolve_pairs_empty_means_all_non_jse():
    all_pairs = [
        {"display": "AAPL", "symbol": "AAPL"},
        {"display": "JSE:NPN", "symbol": "NPN"},
    ]
    jse = [{"display": "JSE:NPN", "symbol": "NPN"}]
    resolved = engine_b_batch.resolve_engine_b_batch_pairs([], all_pairs, jse)
    assert [p["symbol"] for p in resolved] == ["AAPL"]


def test_resolve_pairs_by_token():
    all_pairs = [
        {"display": "AAPL", "symbol": "AAPL"},
        {"display": "BTCUSDT", "symbol": "BTCUSDT"},
    ]
    resolved = engine_b_batch.resolve_engine_b_batch_pairs(["btcusdt"], all_pairs, [])
    assert [p["symbol"] for p in resolved] == ["BTCUSDT"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_engine_b_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine_b_batch'`.

- [ ] **Step 3: Create `engine_b_batch.py`**

```python
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
    """Bootstrap the backtest runtime once per worker process (no live feeds)."""
    import athena  # noqa: F401  (module-level set_runtime() runs; live services are __main__-only)


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
        return {"pairKey": pk, "ok": False,
                "error": "Insufficient candle data to run Engine B backtest for this pair."}

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
    params = {"style": style, "validation_mode": validation_mode,
              "purge_gap": purge_gap, "folds": folds}

    with _batch_lock:
        if _batch_running:
            return {"success": False, "error": "An Engine B batch run is already in progress.",
                    "totalPairs": 0, "okCount": 0, "rows": []}
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
                log.info("[ENGINE B BATCH] REST lane: %d pairs across %d workers",
                         len(rest_pairs), workers)
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
                        rows_by_key[pk] = {"pairKey": pk, "ok": False,
                                           "error": f"worker failed: {exc}"}

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
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python -m pytest tests/test_engine_b_batch.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit** (pending user go-ahead)

```bash
git add engine_b_batch.py tests/test_engine_b_batch.py
git commit -m "feat(backtest): Engine B batch orchestrator (parallel REST + serial MT5)"
```

---

### Task 2: Batch route in `athena.py`

**Files:**
- Modify: `athena.py` (add route immediately after `api_backtest_naked`, ends ~line 8409)

- [ ] **Step 1: Add the route**

Insert after the `api_backtest_naked` function (after its closing `return ... 500` block, before `@app.route("/api/backtest-scalp" ...)`):

```python
@app.route("/api/backtest-naked-all", methods=["POST"])
def api_backtest_naked_all():
    """Engine B batch backtest across many pairs (parallel REST + serial MT5).

    Body: {pairs?: [symbol|display, ...], style?, validation_mode?, purge_gap?, folds?}.
    Empty/absent ``pairs`` => all non-JSE pairs. Returns {success, totalPairs, okCount, rows}.
    """
    try:
        from engine_b_batch import run_engine_b_batch, resolve_engine_b_batch_pairs

        data = request.get_json(force=True, silent=True) or {}
        tokens = data.get("pairs") or data.get("symbols") or []
        pairs = resolve_engine_b_batch_pairs(tokens, ALL_PAIRS, JSE_PAIRS)
        if not pairs:
            return jsonify(
                {"success": False, "error": "No valid pairs resolved for Engine B batch."}
            ), 400

        _vm = str(data.get("validation_mode") or "standard").strip().lower()
        try:
            _pg = int(data.get("purge_gap", 200))
        except (TypeError, ValueError):
            _pg = 200
        try:
            _fd = int(data.get("folds", 3))
        except (TypeError, ValueError):
            _fd = 3

        out = run_engine_b_batch(
            pairs,
            style=data.get("style", "auto"),
            validation_mode=_vm,
            purge_gap=_pg,
            folds=max(1, _fd),
        )
        safe = _json_safe(out) if callable(globals().get("_json_safe")) else out
        return jsonify(safe)

    except Exception as exc:
        log.exception("[ENGINE B BT] Unhandled error in api_backtest_naked_all")
        return jsonify(
            {"success": False, "error": f"Engine B batch backtest failed: {str(exc)}"}
        ), 500
```

- [ ] **Step 2: Verify it imports/compiles**

Run: `python -c "import ast; ast.parse(open('athena.py', encoding='utf-8').read()); print('athena.py parses OK')"`
Expected: `athena.py parses OK`.

- [ ] **Step 3: Commit** (pending user go-ahead)

```bash
git add athena.py
git commit -m "feat(backtest): add POST /api/backtest-naked-all Engine B batch route"
```

---

### Task 3: Wire dashboard batch mode (Engine B) to the batch endpoint

**Files:**
- Modify: `static/react-app/app/src/components/panels/BacktestPanel.tsx`

- [ ] **Step 1: Add the batch-response type** (after the `BacktestResult` interface, ~line 105)

```tsx
interface EngineBBatchRow {
  pairKey: string;
  ok: boolean;
  error?: string;
  result?: BacktestResult;
}
interface EngineBBatchResponse {
  success?: boolean;
  totalPairs?: number;
  okCount?: number;
  rows?: EngineBBatchRow[];
  error?: string;
}
```

- [ ] **Step 2: Add a typed post hook** (next to `postBacktest`, ~line 259)

```tsx
  const { post: postBatchB } = useApiPost<EngineBBatchResponse>();
```

- [ ] **Step 3: Short-circuit Engine B batch in `handleRun`** — inside `if (batchMode) {`, right after the `tokens` empty-check (~line 285), before `const rowsOut: BatchRow[] = [];`:

```tsx
      if (engine === 'B') {
        const resolved = tokens.map((t) => resolvePairKey(t, allPairs));
        const res = await postBatchB('/api/backtest-naked-all', {
          pairs: resolved,
          style,
          validation_mode: validationMode,
        });
        if (!res || res.error || !res.rows) {
          showToast(`Batch failed: ${res?.error || 'unknown'}`, 'error');
          return;
        }
        const rowsOut: BatchRow[] = res.rows.map((r) => ({
          pairKey: r.pairKey,
          ok: !!r.ok,
          error: r.error,
          result: r.result,
        }));
        setBatchRows(rowsOut);
        setResult(null);
        const okN = rowsOut.filter((r) => r.ok).length;
        showToast(`Batch: ${okN}/${rowsOut.length} pairs OK`,
          okN === rowsOut.length ? 'success' : 'info');
        refreshHistory();
        return;
      }
```

- [ ] **Step 4: Add `postBatchB` to the `handleRun` `useCallback` deps array** (~line 317-320)

Change the deps array to include `postBatchB`:

```tsx
  }, [
    pair, engine, style, validationMode, postBacktest, postBatchB, showToast, refreshHistory,
    batchMode, batchList, allPairs,
  ]);
```

- [ ] **Step 5: Typecheck the frontend**

Run (from `static/react-app/app`): `npm run build` (or `npx tsc --noEmit` if a typecheck script exists).
Expected: builds with no TypeScript errors in `BacktestPanel.tsx`.

- [ ] **Step 6: Commit** (pending user go-ahead)

```bash
git add static/react-app/app/src/components/panels/BacktestPanel.tsx
git commit -m "feat(dashboard): Engine B batch mode posts to /api/backtest-naked-all"
```

---

### Task 4: Final verification

- [ ] **Step 1: Targeted backend tests**

Run: `python -m pytest tests/test_engine_b_batch.py tests/test_backtest_integrity.py -q`
Expected: PASS (new batch tests + existing naked-backtest integrity tests unaffected).

- [ ] **Step 2: Confirm parity invariant untouched**

Run: `git diff --stat`
Expected: changes limited to `engine_b_batch.py`, `tests/test_engine_b_batch.py`, `athena.py`, and `BacktestPanel.tsx`. No diff in `backtest_runner.py`, `market_structure.py`, or scoring files.

- [ ] **Step 3 (manual, on user's machine): timed smoke run**

Start the app, run an Engine B batch over a handful of REST pairs (e.g. AAPL, BTCUSDT, ETHUSDT) via the dashboard, and confirm: results render in the existing table, and wall-clock time is materially lower than the serial loop. Spot-check one pair against `/api/backtest-naked` for the same pair → identical metrics.
