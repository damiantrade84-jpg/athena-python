# Exit-Mode Backtest Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Annotate Research Lab Engine-A rows with the live-configured exit mode and a parity-honesty label, surfaced in reports — with no change to backtest exit math.

**Architecture:** Pure `exit_policy.exit_parity_label` classifies a mode. `annotate_research_results` (already the post-hoc engine/group enrichment point, isolated from live execution) resolves each ENGINE_A row's mode from maps injected into `cfg` and stamps two new `StrategyMetrics` fields. `run_manager` injects the live maps read-only from `config.yaml`. `reporting.py` saves the columns + a per-mode breakdown CSV.

**Tech Stack:** Python 3.13, pytest, dataclasses, pandas. Pure modules only (no live execution, gates, scoring, or thresholds touched).

**Spec:** `docs/superpowers/specs/2026-05-30-exit-mode-backtest-observability-design.md`. **Depends on:** Plan 1 (`exit_policy.py`).

---

### Task 1: `exit_policy.exit_parity_label` (pure)

**Files:**
- Modify: `exit_policy.py` (add function after `uses_timed_close`, ~:121)
- Test: `tests/test_exit_policy.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_exit_policy.py
def test_exit_parity_label_by_mode():
    import exit_policy as ep
    assert ep.exit_parity_label("traditional_static") == "faithful"
    assert ep.exit_parity_label("manual") == "faithful"
    assert ep.exit_parity_label("time_based") == "timeout_proxy"
    assert ep.exit_parity_label("adaptive_trail") == "trail_not_simulated"
    assert ep.exit_parity_label(None) == ""
    assert ep.exit_parity_label("junk") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_policy.py::test_exit_parity_label_by_mode -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: FAIL with `AttributeError: module 'exit_policy' has no attribute 'exit_parity_label'`.

- [ ] **Step 3: Implement**

Add to `exit_policy.py` after `uses_timed_close`:

```python
def exit_parity_label(mode: str | None) -> str:
    """Backtest-vs-live fidelity for a resolved exit mode (research reporting only).

    'faithful'            -> research fixed SL/TP matches the live static bracket
                             (traditional_static, and manual as an ATR-level proxy)
    'timeout_proxy'       -> time_based: research times out, but at the research
                             max-hold, not necessarily ENGINE_A_TIME_EXIT_BARS
    'trail_not_simulated' -> adaptive_trail: live trailing is not modeled in research
    ''                    -> unknown/None
    """
    m = normalize_mode(mode)
    if m in (EXIT_MODE_STATIC, EXIT_MODE_MANUAL):
        return "faithful"
    if m == EXIT_MODE_TIME:
        return "timeout_proxy"
    if m == EXIT_MODE_ADAPTIVE:
        return "trail_not_simulated"
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_policy.py::test_exit_parity_label_by_mode -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add exit_policy.py tests/test_exit_policy.py
git commit -m "feat(exit_mode): exit_parity_label for research backtest fidelity" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Annotate Engine-A rows with mode + parity

**Files:**
- Modify: `athena_research/metrics.py:138-141` (add two `StrategyMetrics` fields)
- Modify: `athena_research/research_context.py` (import `exit_policy`; resolve + stamp in `annotate_research_results`)
- Modify: `athena_research/run_manager.py` (inject live maps into `cfg` before the `annotate_research_results` call at `:490`)
- Test: `tests/test_research_context.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_research_context.py
from athena_research.metrics import StrategyMetrics
from athena_research.research_context import annotate_research_results


def _row(strategy_name, symbol="EUR/USD", asset_class="forex"):
    return StrategyMetrics(
        strategy_name=strategy_name, symbol=symbol, asset_class=asset_class,
        timeframe="H4", family="trend_momentum",
    )


def test_engine_a_row_gets_global_default_mode_and_parity():
    cfg = {"engine_a_exit_mode_by_score_group": {},
           "engine_a_exit_mode_global_default": "traditional_static"}
    out = annotate_research_results([_row("ema_cross")], cfg)
    assert out[0].engine == "ENGINE_A"
    assert out[0].engine_a_exit_mode == "traditional_static"
    assert out[0].engine_a_exit_parity == "faithful"


def test_engine_a_row_uses_per_group_override():
    cfg = {"engine_a_exit_mode_by_score_group": {"forex_majors": "adaptive_trail"},
           "engine_a_exit_mode_global_default": "traditional_static"}
    out = annotate_research_results([_row("ema_cross")], cfg)
    # EUR/USD infers pair_group=forex_majors
    assert out[0].pair_group == "forex_majors"
    assert out[0].engine_a_exit_mode == "adaptive_trail"
    assert out[0].engine_a_exit_parity == "trail_not_simulated"


def test_non_engine_a_row_keeps_empty_exit_annotation():
    cfg = {"engine_a_exit_mode_global_default": "traditional_static"}
    out = annotate_research_results([_row("ob_bos")], cfg)  # ENGINE_B
    assert out[0].engine == "ENGINE_B"
    assert out[0].engine_a_exit_mode == ""
    assert out[0].engine_a_exit_parity == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_research_context.py -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: FAIL — `TypeError`/`AttributeError` (no `engine_a_exit_mode` field yet).

- [ ] **Step 3a: Add the two `StrategyMetrics` fields**

In `athena_research/metrics.py`, after `atr_length: float = float("nan")` (`:141`):

```python
    engine_a_exit_mode: str = ""
    engine_a_exit_parity: str = ""
```

- [ ] **Step 3b: Resolve + stamp in `annotate_research_results`**

In `athena_research/research_context.py`, add at the top of the file (after the existing imports, ~:13):

```python
import exit_policy
```

In `annotate_research_results`, read the maps once at the start of the function (after `enriched = []`, ~:342):

```python
    cfg = cfg or {}
    _group_map = cfg.get("engine_a_exit_mode_by_score_group") or {}
    _global_default = cfg.get("engine_a_exit_mode_global_default") or exit_policy.DEFAULT_EXIT_MODE
```

In the **first** enrichment loop, the `replace(...)` call sets `engine=meta["engine"]`.
Compute the exit annotation from the resolved engine + pair_group and add the two
fields to that same `replace(...)`:

```python
        _ea_mode = ""
        _ea_parity = ""
        if meta["engine"] == "ENGINE_A":
            _ea_mode = exit_policy.resolve_exit_mode(
                per_trade=None,
                group_default=exit_policy.group_default_for(pair_group, _group_map),
                global_default=_global_default,
            )
            _ea_parity = exit_policy.exit_parity_label(_ea_mode)
        enriched.append(replace(
            m,
            engine=meta["engine"],
            engine_component=meta["component"],
            candidate_action=meta["candidate_action"],
            source_indicator=meta["source_indicator"],
            market_group=market_group,
            pair_group=pair_group,
            timeframe_zone=zone,
            zone=zone,
            session_bucket=session_bucket,
            structure_context=meta["structure_context"],
            engine_a_exit_mode=_ea_mode,
            engine_a_exit_parity=_ea_parity,
        ))
```

The second loop already uses `replace(m, ...)` on the enriched rows, so the two
new fields are preserved through it unchanged.

- [ ] **Step 3c: Inject the live maps in `run_manager`**

In `athena_research/run_manager.py`, immediately **before**
`all_results = annotate_research_results(all_results, cfg)` (`:490`), inject the
live exit-mode maps read-only from `config.yaml` (pure YAML; no live-engine import):

```python
    # Surface the live Engine-A exit-mode config in research reports (read-only).
    try:
        import yaml as _yaml
        _cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
        with open(_cfg_path, encoding="utf-8") as _fh:
            _live = _yaml.safe_load(_fh) or {}
        cfg.setdefault("engine_a_exit_mode_by_score_group",
                       _live.get("ENGINE_A_EXIT_MODE_BY_SCORE_GROUP") or {})
        cfg.setdefault("engine_a_exit_mode_global_default",
                       _live.get("ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT") or "traditional_static")
    except Exception as _exc:
        log.debug("[run_manager] exit-mode map load skipped: %s", _exc)
```

(`Path` is already imported in `run_manager.py`; `cfg` and `log` are in scope here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_research_context.py -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add athena_research/metrics.py athena_research/research_context.py athena_research/run_manager.py tests/test_research_context.py
git commit -m "feat(exit_mode): annotate Engine-A research rows with live exit mode + parity" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Reporting columns + per-mode breakdown

**Files:**
- Modify: `athena_research/reporting.py:40` (saved-column allowlist) and `:505-506` (breakdown CSVs)
- Test: `tests/test_reporting_exit_mode.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reporting_exit_mode.py
import pandas as pd
from athena_research import reporting


def test_exit_mode_columns_in_saved_allowlist():
    cols = set(reporting._COLUMN_ORDER)
    assert "engine_a_exit_mode" in cols
    assert "engine_a_exit_parity" in cols


def test_group_agg_by_engine_a_exit_mode():
    # _group_agg must aggregate cleanly keyed by the new column
    df = pd.DataFrame(
        {
            "engine_a_exit_mode": ["traditional_static", "traditional_static", "adaptive_trail"],
            "profit_factor": [1.2, 1.4, 0.9],
            "trade_count": [10, 12, 8],
        }
    )
    agg = reporting._group_agg(df, "engine_a_exit_mode")
    assert "engine_a_exit_mode" in agg.columns
    assert set(agg["engine_a_exit_mode"]) == {"traditional_static", "adaptive_trail"}
```

> Note: confirm the allowlist variable name. The list begins near `reporting.py:25-43`.
> If it is not `_COLUMN_ORDER`, update the test's `reporting._COLUMN_ORDER` reference
> and Step 3 to the actual name (grep the assignment that contains
> `"backtest_exit_mode", "exit_reason_breakdown"`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reporting_exit_mode.py -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: FAIL on the allowlist assertion (columns not yet listed).

- [ ] **Step 3: Implement**

In `athena_research/reporting.py`, add the two columns to the saved-column list,
right after `"backtest_exit_mode", "exit_reason_breakdown", "same_bar_policy", "atr_length",`
(`:40`):

```python
    "engine_a_exit_mode", "engine_a_exit_parity",
```

Then add a per-mode breakdown alongside the existing `by_backtest_exit_mode.csv`
(`:505-506`):

```python
    if "engine_a_exit_mode" in df.columns:
        _save(_group_agg(df, "engine_a_exit_mode"), "by_engine_a_exit_mode.csv")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reporting_exit_mode.py -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add athena_research/reporting.py tests/test_reporting_exit_mode.py
git commit -m "feat(exit_mode): report Engine-A exit mode column + by_engine_a_exit_mode breakdown" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run the full exit-mode + research test surface**

Run: `python -m pytest tests/test_exit_policy.py tests/test_research_context.py tests/test_reporting_exit_mode.py tests/test_backtest_exits.py -q --basetemp="C:/Users/damia/AppData/Local/Temp/em_p3"`
Expected: PASS (existing `test_backtest_exits.py` stays green — exit math unchanged).

- [ ] **Step 2: Confirm no exit-math change**

Run: `git diff --stat main -- backtest_exits.py athena_research/metrics.py`
Expected: `backtest_exits.py` shows **no** changes; `metrics.py` shows only the two added dataclass fields.

---

## Self-Review

**Spec coverage:**
- Pure parity label → Task 1 (`exit_parity_label`). ✅
- Annotation stamps `engine_a_exit_mode`/`engine_a_exit_parity` on Engine-A rows; empty for others → Task 2. ✅
- Two new `StrategyMetrics` fields → Task 2 Step 3a. ✅
- Config source: `run_manager` reads `config.yaml` read-only, injects into `cfg`; annotate reads from `cfg` with safe defaults → Task 2 Steps 3b/3c. ✅
- Reporting columns + `by_engine_a_exit_mode.csv` → Task 3. ✅
- No exit-math change → Task 4 Step 2 guard. ✅
- Honesty note in markdown summary: the `engine_a_exit_parity` value (`trail_not_simulated`) carries the signal in every saved row + the breakdown CSV; a prose note in the summary is optional and not separately tasked (the column is the durable artifact).

**Placeholder scan:** No TBD/TODO. The one "confirm the allowlist variable name" note in Task 3 is a named verification with an exact grep anchor, not a placeholder — the assignment was read at `reporting.py:30-43` but its variable name (line <30) must be confirmed at edit time.

**Type consistency:** `exit_parity_label` returns the exact strings `"faithful"`/`"timeout_proxy"`/`"trail_not_simulated"`/`""` used in Task 2's test and Task 3. Field names `engine_a_exit_mode`/`engine_a_exit_parity` are identical across metrics.py, research_context.py, reporting.py, and all tests. `cfg` keys `engine_a_exit_mode_by_score_group` / `engine_a_exit_mode_global_default` match between run_manager injection and annotate read.
