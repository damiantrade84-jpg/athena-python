# Athena.py Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `athena.py` from a 17k-line monolith into focused route/service modules without changing scoring, risk, freshness, execution, AI prompt contracts, or runtime startup behavior.

**Architecture:** Keep `athena.py` as the app bootstrap and compatibility shell during the refactor. Move one low-risk route group at a time into `athena_app/api/routes_*.py` modules that register routes through `register_*_routes(app, runtime)`, while `athena.py` passes the existing globals through a small runtime namespace. Route contract tests must understand both old `@app.route` decorators and new module-level `app.add_url_rule(...)` registrations before any route is moved.

**Tech Stack:** Flask route registration, existing `athena_runtime.set_runtime`, Python `py_compile`, focused `pytest`, static AST route-contract tests.

---

## Current Evidence Snapshot

- `athena.py` currently has 17,220 lines.
- AST parse found 229 top-level functions and 106 Flask route decorators.
- Largest functions include `analyze_pair` (984 lines), `api_chart_analysis` (814), `api_scan_naked` (711), `_build_signal_message` (554), `api_performance` (459), `_compute_naked_analysis` (353), `api_live_dashboard_snapshot` (333), and `run_ai` (285).
- Existing extraction package already exists: `athena_app/api/routes_scan.py`, `athena_app/api/routes_backtest.py`, `athena_app/api/routes_execution.py`, and `athena_app/services/*`.
- Current dirty worktree includes user/workspace changes in `athena.py`, `config.py`, `config.yaml`, and `tests/test_ai_config_routing.py`; do not overwrite or bundle those with refactor work.

## Non-Negotiable Guardrails

- Do not change Engine A/B/D scoring thresholds.
- Do not change risk, freshness, kill-switch, broker, or live execution logic.
- Do not change AI Vision footer tokens.
- Do not mix Chart Vision, Marcus/Text Review, Engine B AI, and Lottery AI.
- Do not import `athena.py` from new tests.
- Keep every extraction behavior-neutral: same URL, method, response shape, status codes, logging side effects, cache writes, and audit writes.
- Stage and commit one route group at a time.

---

### Task 1: Make Route Contract Tests Module-Aware

**Files:**
- Modify: `tests/test_api_contract_smoke.py`
- Modify: `tests/test_live_dashboard.py`
- Create: `tests/route_contract_helpers.py`

- [ ] **Step 1: Create the shared route-map helper**

Add `tests/route_contract_helpers.py`:

```python
"""Static route-map helpers for monolith and extracted Flask modules."""

from __future__ import annotations

import ast
from pathlib import Path


def _literal_methods(value: ast.AST | None) -> set[str]:
    if isinstance(value, (ast.List, ast.Tuple)):
        methods = {
            str(elt.value)
            for elt in value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
        if methods:
            return methods
    return {"GET"}


def endpoint_map_from_source(src: str) -> dict[str, set[str]]:
    tree = ast.parse(src)
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if not (isinstance(func, ast.Attribute) and func.attr == "route"):
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str):
                    continue
                methods = {"GET"}
                for kw in dec.keywords or []:
                    if kw.arg == "methods":
                        methods = _literal_methods(kw.value)
                out[path] = methods
        if isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add_url_rule"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            path = node.args[0].value
            if not isinstance(path, str):
                continue
            methods = {"GET"}
            for kw in node.keywords or []:
                if kw.arg == "methods":
                    methods = _literal_methods(kw.value)
            out[path] = methods
    return out


def endpoint_map_from_files(paths: list[Path]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for path in paths:
        if not path.exists():
            continue
        for route, methods in endpoint_map_from_source(path.read_text(encoding="utf-8")).items():
            merged[route] = methods
    return merged
```

- [ ] **Step 2: Update static route tests to use the helper**

In `tests/test_api_contract_smoke.py`, replace local route parsing helpers with:

```python
from tests.route_contract_helpers import endpoint_map_from_files
```

Then set:

```python
ROUTE_FILES = [
    ROOT / "athena.py",
    ROOT / "execution.py",
    ROOT / "guardian_routes.py",
    ROOT / "athena_app" / "api" / "routes_backtest.py",
    ROOT / "athena_app" / "api" / "routes_execution.py",
    ROOT / "athena_app" / "api" / "routes_scan.py",
]


def _endpoint_map() -> dict[str, set[str]]:
    return endpoint_map_from_files(ROUTE_FILES)
```

In `tests/test_live_dashboard.py`, replace `_route_map()` with a helper call over `athena.py` plus any future live-dashboard route module:

```python
from tests.route_contract_helpers import endpoint_map_from_files


def _route_map() -> dict[str, set[str]]:
    return endpoint_map_from_files([
        ATHENA_PATH,
        ROOT / "athena_app" / "api" / "routes_live_dashboard.py",
    ])
```

- [ ] **Step 3: Run the route contract tests before any extraction**

Run:

```powershell
python -m pytest tests/test_api_contract_smoke.py tests/test_live_dashboard.py -q
```

Expected: tests pass or only expose existing unrelated failures. Do not move routes until this is green for route discovery.

- [ ] **Step 4: Commit**

```powershell
git add tests/test_api_contract_smoke.py tests/test_live_dashboard.py tests/route_contract_helpers.py
git diff --cached --check
git commit -m "test(routes): support extracted route modules"
```

---

### Task 2: Extract Lottery Routes First

**Files:**
- Create: `athena_app/api/routes_lottery.py`
- Modify: `athena.py`
- Modify: `tests/test_api_contract_smoke.py`

**Why first:** Lottery routes are non-trading and isolated from risk/execution gates. They still use AI, so keep Lottery AI separate from Marcus/Text and Chart Vision.

- [ ] **Step 1: Add lottery routes to route-contract file list**

In `tests/test_api_contract_smoke.py`, add:

```python
ROOT / "athena_app" / "api" / "routes_lottery.py",
```

to `ROUTE_FILES`.

- [ ] **Step 2: Create `routes_lottery.py` with registration shape**

Create `athena_app/api/routes_lottery.py`:

```python
"""Lottery API route registration.

Behavior-neutral extraction from athena.py. Lottery AI stays separate from
Marcus/Text review and Chart Vision.
"""

from __future__ import annotations

from types import SimpleNamespace


def register_lottery_routes(app, runtime: SimpleNamespace) -> None:
    """Register lottery routes using callables supplied by athena.py runtime."""

    app.add_url_rule("/api/lottery/import", "api_lottery_import", runtime.api_lottery_import, methods=["POST"])
    app.add_url_rule("/api/lottery/dashboard", "api_lottery_dashboard", runtime.api_lottery_dashboard)
    app.add_url_rule("/api/lottery/frequency", "api_lottery_frequency", runtime.api_lottery_frequency)
    app.add_url_rule("/api/lottery/pairs", "api_lottery_pairs", runtime.api_lottery_pairs)
    app.add_url_rule("/api/lottery/triplets", "api_lottery_triplets", runtime.api_lottery_triplets)
    app.add_url_rule("/api/lottery/history", "api_lottery_history", runtime.api_lottery_history)
    app.add_url_rule("/api/lottery/distributions", "api_lottery_distributions", runtime.api_lottery_distributions)
    app.add_url_rule("/api/lottery/sum-range", "api_lottery_sum_range", runtime.api_lottery_sum_range)
    app.add_url_rule("/api/lottery/positional", "api_lottery_positional", runtime.api_lottery_positional)
    app.add_url_rule("/api/lottery/rolling-frequency", "api_lottery_rolling_frequency", runtime.api_lottery_rolling_frequency)
    app.add_url_rule("/api/lottery/pair-lift", "api_lottery_pair_lift", runtime.api_lottery_pair_lift)
    app.add_url_rule("/api/lottery/anomalous-draws", "api_lottery_anomalous_draws", runtime.api_lottery_anomalous_draws)
    app.add_url_rule("/api/lottery/bonus-intelligence", "api_lottery_bonus_intelligence", runtime.api_lottery_bonus_intelligence)
    app.add_url_rule("/api/lottery/ai-analysis", "api_lottery_ai_analysis", runtime.api_lottery_ai_analysis, methods=["POST"])
    app.add_url_rule("/api/lottery/draws", "api_lottery_draws", runtime.api_lottery_draws)
    app.add_url_rule("/api/lottery/stats", "api_lottery_stats", runtime.api_lottery_stats)
    app.add_url_rule("/api/lottery/clear", "api_lottery_clear", runtime.api_lottery_clear, methods=["POST"])
    app.add_url_rule("/api/lottery/add-draw", "api_lottery_add_draw", runtime.api_lottery_add_draw, methods=["POST"])
    app.add_url_rule("/api/lottery/delete-draw", "api_lottery_delete_draw", runtime.api_lottery_delete_draw, methods=["POST"])
    app.add_url_rule("/api/lottery/generate", "api_lottery_generate", runtime.api_lottery_generate, methods=["POST"])
    app.add_url_rule("/api/lottery/wheel", "api_lottery_wheel", runtime.api_lottery_wheel, methods=["POST"])
    app.add_url_rule("/api/lottery/score-ticket", "api_lottery_score_ticket", runtime.api_lottery_score_ticket, methods=["POST"])
    app.add_url_rule("/api/lottery/simulate", "api_lottery_simulate", runtime.api_lottery_simulate, methods=["POST"])
    app.add_url_rule("/api/lottery/compare-modes", "api_lottery_compare_modes", runtime.api_lottery_compare_modes, methods=["POST"])
```

- [ ] **Step 3: Register lottery routes from `athena.py` without moving bodies yet**

Near the existing late route registration section in `athena.py`, after `register_execution_routes(app)`, add:

```python
from athena_app.api.routes_lottery import register_lottery_routes  # noqa: E402

register_lottery_routes(
    app,
    SimpleNamespace(
        api_lottery_import=api_lottery_import,
        api_lottery_dashboard=api_lottery_dashboard,
        api_lottery_frequency=api_lottery_frequency,
        api_lottery_pairs=api_lottery_pairs,
        api_lottery_triplets=api_lottery_triplets,
        api_lottery_history=api_lottery_history,
        api_lottery_distributions=api_lottery_distributions,
        api_lottery_sum_range=api_lottery_sum_range,
        api_lottery_positional=api_lottery_positional,
        api_lottery_rolling_frequency=api_lottery_rolling_frequency,
        api_lottery_pair_lift=api_lottery_pair_lift,
        api_lottery_anomalous_draws=api_lottery_anomalous_draws,
        api_lottery_bonus_intelligence=api_lottery_bonus_intelligence,
        api_lottery_ai_analysis=api_lottery_ai_analysis,
        api_lottery_draws=api_lottery_draws,
        api_lottery_stats=api_lottery_stats,
        api_lottery_clear=api_lottery_clear,
        api_lottery_add_draw=api_lottery_add_draw,
        api_lottery_delete_draw=api_lottery_delete_draw,
        api_lottery_generate=api_lottery_generate,
        api_lottery_wheel=api_lottery_wheel,
        api_lottery_score_ticket=api_lottery_score_ticket,
        api_lottery_simulate=api_lottery_simulate,
        api_lottery_compare_modes=api_lottery_compare_modes,
    ),
)
```

Then remove `@app.route(...)` decorators from the lottery functions in `athena.py`, leaving the function bodies unchanged. This avoids duplicate route registration while preserving function implementation.

- [ ] **Step 4: Validate**

Run:

```powershell
python -m py_compile athena.py athena_app/api/routes_lottery.py
python -m pytest tests/test_api_contract_smoke.py -q
python -m pytest tests/test_vectorbt_research_lab.py -k "not live_imports" -q
```

Expected: `py_compile` passes and route smoke tests still find every lottery endpoint.

- [ ] **Step 5: Commit**

```powershell
git add athena.py athena_app/api/routes_lottery.py tests/test_api_contract_smoke.py
git diff --cached --check
git commit -m "refactor(routes): register lottery routes from module"
```

---

### Task 3: Move Lottery Function Bodies Out Of Athena.py

**Files:**
- Modify: `athena_app/api/routes_lottery.py`
- Modify: `athena.py`

- [ ] **Step 1: Move only lottery helper + route bodies**

Move these functions from `athena.py` into `athena_app/api/routes_lottery.py`:

```text
api_lottery_import
api_lottery_dashboard
api_lottery_frequency
api_lottery_pairs
api_lottery_triplets
api_lottery_history
api_lottery_distributions
api_lottery_sum_range
api_lottery_positional
api_lottery_rolling_frequency
api_lottery_pair_lift
api_lottery_anomalous_draws
api_lottery_bonus_intelligence
api_lottery_ai_analysis
api_lottery_draws
api_lottery_stats
api_lottery_clear
api_lottery_add_draw
api_lottery_delete_draw
api_lottery_generate
api_lottery_wheel
api_lottery_score_ticket
api_lottery_simulate
api_lottery_compare_modes
```

If those functions need shared objects, pass them through `runtime` instead of importing `athena.py`.

- [ ] **Step 2: Preserve endpoint names**

Keep every `app.add_url_rule(..., endpoint_name, handler, methods=[...])` endpoint name equal to the old function name, for example:

```python
app.add_url_rule("/api/lottery/dashboard", "api_lottery_dashboard", api_lottery_dashboard)
```

- [ ] **Step 3: Validate**

Run:

```powershell
python -m py_compile athena.py athena_app/api/routes_lottery.py
python -m pytest tests/test_api_contract_smoke.py -q
python -m pytest tests/test_vectorbt_research_lab.py -q
```

Expected: route contract remains green and research modules still do not import live `athena.py`.

- [ ] **Step 4: Commit**

```powershell
git add athena.py athena_app/api/routes_lottery.py
git diff --cached --check
git commit -m "refactor(lottery): move lottery route handlers"
```

---

### Task 4: Extract Market Metadata Read-Only Routes

**Files:**
- Create: `athena_app/api/routes_market_data.py`
- Modify: `athena.py`
- Modify: `tests/test_api_contract_smoke.py`

**Candidate routes:**

```text
/api/prices
/api/yield-curve
/api/bulk-prices
/api/pairs
/api/intermarket-matrix
/api/candles
/api/news-sentiment
/api/market-hours
```

- [x] **Step 1: Add route contract coverage for `routes_market_data.py`**

Add this path to `ROUTE_FILES` in `tests/test_api_contract_smoke.py`:

```python
ROOT / "athena_app" / "api" / "routes_market_data.py",
```

- [x] **Step 2: Create registration module with `app.add_url_rule`**

Use the same shape as `routes_lottery.py`, with `register_market_data_routes(app, runtime)`.

- [x] **Step 3: Move decorators first, bodies later**

Remove only decorators from `athena.py` and register the old functions from the new module. Validate before moving bodies.

- [x] **Step 4: Move bodies after route registration passes**

Move read-only route bodies into `routes_market_data.py`. Pass dependencies through `runtime`.

- [x] **Step 5: Validate**

Run:

```powershell
python -m py_compile athena.py athena_app/api/routes_market_data.py
python -m pytest tests/test_api_contract_smoke.py tests/test_health_routes.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add athena.py athena_app/api/routes_market_data.py tests/test_api_contract_smoke.py
git diff --cached --check
git commit -m "refactor(routes): move market data routes"
```

---

### Task 5: Extract Live Dashboard Routes Only After Tests Are Module-Aware

**Files:**
- Create: `athena_app/api/routes_live_dashboard.py`
- Modify: `athena.py`
- Modify: `tests/test_live_dashboard.py`

**Candidate routes:**

```text
/api/live-dashboard/snapshot
/api/live-dashboard/paper-execute
/api/shadow-signals
/api/live-feed-diagnostics
```

- [x] **Step 1: Update tests that inspect function bodies**

Replace body lookup in `tests/test_live_dashboard.py` so it can find `api_live_dashboard_snapshot` in either `athena.py` or `athena_app/api/routes_live_dashboard.py`.

- [x] **Step 2: Move registration only**

Move route decorators to `register_live_dashboard_routes(app, runtime)` while function bodies stay in `athena.py`.

- [x] **Step 3: Validate no execution leak**

Run:

```powershell
python -m pytest tests/test_live_dashboard.py tests/test_api_contract_smoke.py -q
```

- [x] **Step 4: Move bodies**

Move only after Step 3 is green. Preserve the read-only guarantee for snapshot routes and keep paper execute logging as paper-only.

- [x] **Step 5: Validate**

Run:

```powershell
python -m py_compile athena.py athena_app/api/routes_live_dashboard.py
python -m pytest tests/test_live_dashboard.py tests/test_api_contract_smoke.py tests/test_health_routes.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add athena.py athena_app/api/routes_live_dashboard.py tests/test_live_dashboard.py
git diff --cached --check
git commit -m "refactor(routes): move live dashboard routes"
```

---

### Task 6: Extract Status/Support Read-Only Routes

**Files:**
- Create: `athena_app/api/routes_status.py`
- Modify: `athena.py`
- Modify: `tests/test_api_contract_smoke.py`
- Create: `tests/test_routes_status.py`

**Routes:**

```text
/
/api/last-scan
/api/conductor/last
/api/kimi/conductor/last
/api/conductor/pairs
/api/health
/api/signal-stability
/api/debug/routes
/api/microstructure-health
```

- [x] **Step 1: Add module-aware route contract coverage**
- [x] **Step 2: Move read-only route handlers into `routes_status.py`**
- [x] **Step 3: Preserve monkeypatch/runtime behavior with getters for mutable status state**
- [x] **Step 4: Add fake-runtime module tests**
- [x] **Step 5: Validate compile, route contract, status module, health subset, and combined route-focused checks**
- [ ] **Step 6: Commit**

---

### Task 7: Extract Read-Only Broker Status Routes

**Files:**
- Create: `athena_app/api/routes_broker_status.py`
- Modify: `athena.py`
- Modify: `tests/test_api_contract_smoke.py`
- Create: `tests/test_routes_broker_status.py`

**Routes:**

```text
/api/mt5-status
/api/mt5-positions
/api/bybit-status
/api/binance-status
```

`/api/close-position` is explicitly excluded because it mutates broker state.

- [x] **Step 1: Add module-aware route contract coverage**
- [x] **Step 2: Move read-only broker status handlers into `routes_broker_status.py`**
- [x] **Step 3: Keep `/api/close-position` in `athena.py`**
- [x] **Step 4: Add fake MT5/Bybit module tests**
- [x] **Step 5: Validate compile, route contract, broker status module, health subset, and combined route-focused checks**
- [x] **Step 6: Commit**

---

### Task 8: Extract Read-Only Backtest History Routes

**Files:**
- Modify: `athena_app/api/routes_backtest.py`
- Modify: `athena.py`
- Modify: `tests/test_api_contract_smoke.py`
- Create: `tests/test_routes_backtest_history.py`

**Routes:**

```text
/api/backtest-history
/api/backtest-history/<pair_name>
/api/backtest-best
```

POST backtest execution routes are explicitly excluded from this slice.

- [x] **Step 1: Add module-aware route contract coverage for the history routes**
- [x] **Step 2: Move read-only SQLite SELECT handlers into `routes_backtest.py`**
- [x] **Step 3: Keep POST backtest execution routes in `athena.py`**
- [x] **Step 4: Add isolated Flask + repo-local SQLite tests**
- [x] **Step 5: Validate compile, route contract, route module, health, and frontend baseline checks**
- [x] **Step 6: Commit**

---

### Task 9: Extract Read-Only Audit Route

**Files:**
- Create: `athena_app/api/routes_audit.py`
- Modify: `athena.py`
- Modify: `tests/test_api_contract_smoke.py`
- Create: `tests/test_routes_audit.py`

**Routes:**

```text
/api/audit
```

`/api/performance`, `/api/score-decay`, and `/api/regime-shift` are explicitly excluded because they have a wider calculation surface or mutate in-memory state.

- [x] **Step 1: Add module-aware route contract coverage**
- [x] **Step 2: Move read-only audit-log SELECT handler into `routes_audit.py`**
- [x] **Step 3: Add isolated Flask + repo-local SQLite tests**
- [x] **Step 4: Validate compile, route contract, and route module**
- [x] **Step 5: Commit**

---

### Task 10: Defer High-Risk Extraction Until Guard Rails Exist

Do not move these until the lower-risk route groups are already passing and committed:

```text
analyze_pair
run_ai
api_analyze
api_chart_analysis
api_scan_naked
_compute_naked_analysis
api_scalp_execute
risk/execution/freshness startup wiring
```

Before touching those, add or identify focused tests for:

```powershell
python -m pytest tests/test_ai_review_safety.py tests/test_ai_config_routing.py -q
python -m pytest tests/test_engine_b_ai.py tests/test_naked_style_persistence.py -q
python -m pytest tests/test_scalp_execution.py tests/test_scalp_engine.py tests/test_scalp_fixes.py -q
python -m pytest tests/test_live_dashboard.py tests/test_api_contract_smoke.py -q
```

If any of these are red before refactor, record them as baseline failures and do not claim the refactor caused or fixed them.

---

## Review Checklist

- [ ] No route path or method changed.
- [ ] No scoring/risk/freshness/execution logic changed.
- [ ] No AI prompt/footer contract changed.
- [ ] Every moved route has an AST route contract test.
- [ ] Every commit is one route group only.
- [ ] `python -m py_compile athena.py <new_module>.py` passes for each commit.
- [ ] Focused pytest slice passes for each moved group.
- [ ] Dirty unrelated files are left unstaged unless explicitly requested.
