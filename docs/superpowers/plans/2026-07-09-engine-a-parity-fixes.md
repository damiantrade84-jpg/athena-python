# Engine A V3 Parity Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Engine A V3 backtest, scan, chart review, and manual demo execution consume the same explicit decision contract without changing thresholds, weights, risk sizing, or live-execution mode.

**Architecture:** Add one public V3 entry-timeframe resolver and put its result on the immutable signal contract. Backtests use that resolver and scanner classification while returning explicit metadata for unreplayed live context/gates. The UI and refresh verifier consume the scanner tier as a safety boundary; research diagnostics use a bar-close decision timestamp and zone-touch entry rule.

**Tech Stack:** Python 3, pytest, existing Engine A V3 dataclasses/configuration, React/TypeScript/Vitest.

## Global Constraints

- Work only in `C:\dev\athena-python\.worktrees\engine-a-parity-fixes`; preserve the user's dirty root worktree.
- Do not change thresholds, weights, SL/TP, risk sizing, auto-trading policy, broker adapters, freshness checks, demo attestation, kill switch, duplicate check, or live-execution mode.
- V3 remains demo-only; a missing/invalid decision contract must fail closed.
- Do not fabricate historical carry, sentiment, microstructure, event, macro, exchange-closed, or direction-conflict data.
- Do not run full pytest, backtest matrices, broker actions, or live trading; run focused test files/cases only.
- Keep Engine A V3 changes isolated from Engine B/C/D and ASE.

## File structure

- Create `engine_a_v3/timeframes.py`: validated public entry-timeframe resolver shared by evaluation, setup selection, and backtest.
- Modify `engine_a_v3/quant_scorer.py`, `engine_a_v3/evaluator.py`, `engine_a_v3/contract.py`, and `engine_a_v3/backtest.py`: carry the resolved timeframe through the V3 contract and use it as the backtest primary bar.
- Modify `backtest_runner.py`, `athena_app/services/scan_backtest_service.py`, and `static/react-app/app/src/components/panels/BacktestPanel.tsx`: reject unavailable V3 validation methods rather than running a standard backtest under another label.
- Modify `athena_app/services/engine_a_v3_classify.py`, `config.py`, and `config.yaml`: make deterministic scan gates authoritative and reject inactive pair filter overrides.
- Modify `static/react-app/app/src/components/panels/SignalsPanel.tsx`, `static/react-app/app/src/lib/manualExecuteHelpers.ts`, and `engine_a_v3/execution.py`: preserve scanner-tier demotions across chart routing and demo refresh.
- Modify `athena_research/tf_entry_path_audit/diagnostic_v3.py`: align research-only entry timing and zone semantics with production backtesting.
- Extend the nearest existing focused pytest/Vitest files instead of creating broad suites.

---

## Stage 1 — decision contract and backtest truthfulness

### Task 1: Add the shared V3 entry-timeframe contract

**Files:**
- Create: `engine_a_v3/timeframes.py`
- Modify: `engine_a_v3/quant_scorer.py:111-129, 629-667`
- Modify: `engine_a_v3/evaluator.py:23, 191-280, 530-590`
- Modify: `engine_a_v3/setups.py:89-117`
- Modify: `engine_a_v3/contract.py:53-140`
- Test: `tests/test_engine_a_v3_setup_parity.py`
- Test: `tests/test_engine_a_v3.py`

**Consumes:** `CONFIG["ENGINE_A_SCORING_PROFILE"]["BY_SCORE_GROUP"][group]["execution_tf"]`, existing `route_specialist(pair)`, and confirmed D1/H4/H1 candles.

**Produces:** `resolve_v3_entry_timeframe(score_group: str, asset_type: str, horizon: str) -> str | None`; `EngineASetupSignal.entryTimeframe: str | None`; serialized `entryTimeframe` in every V3 signal payload.

- [ ] **Step 1: Write the failing resolver and signal-contract tests**

```python
import pytest

from engine_a_v3.timeframes import resolve_v3_entry_timeframe


@pytest.mark.parametrize(
    ("score_group", "asset_type", "horizon", "expected"),
    [
        ("forex_majors", "forex", "intraday", "H1"),
        ("forex_exotics", "forex", "intraday", "H4"),
        ("crypto_other", "crypto", "intraday", "H4"),
    ],
)
def test_v3_entry_timeframe_is_group_aware(score_group, asset_type, horizon, expected):
    assert resolve_v3_entry_timeframe(score_group, asset_type, horizon) == expected


def test_v3_signal_serializes_resolved_entry_timeframe(v3_signal):
    payload = v3_signal.to_dict()
    assert payload["entryTimeframe"] == "H4"
```

Add an invalid-override test that monkeypatches only the `forex_exotics` group to `execution_tf: "M15"`; assert the resolver returns `None` and evaluator output is `NO_SIGNAL` with `invalid_entry_timeframe`. Keep `v3_signal` as the existing local fixture/factory pattern; if no fixture exists, construct the existing `EngineASetupSignal` with `entryTimeframe="H4"`.

- [ ] **Step 2: Run the focused tests to prove they fail**

Run: `pytest tests/test_engine_a_v3_setup_parity.py tests/test_engine_a_v3.py -q`

Expected: a collection/import failure for `engine_a_v3.timeframes` or an assertion failure because the payload has no `entryTimeframe`.

- [ ] **Step 3: Implement the resolver and contract plumbing**

Create `engine_a_v3/timeframes.py` with this behavior:

```python
from __future__ import annotations

VALID_V3_ENTRY_TIMEFRAMES = frozenset({"H1", "H4", "D1"})


def resolve_v3_entry_timeframe(score_group: str, asset_type: str, horizon: str) -> str | None:
    fallback = "H1" if str(horizon).lower() == "intraday" else "H4"
    try:
        from config import CONFIG
        by_group = (CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get("BY_SCORE_GROUP") or {}
        raw_override = (by_group.get(score_group) or {}).get("execution_tf")
    except Exception:
        raw_override = None
    resolved = str(raw_override or fallback).strip().upper()
    return resolved if resolved in VALID_V3_ENTRY_TIMEFRAMES else None
```

Replace evaluator/setup imports with this public resolver. Keep a thin `_resolve_v3_entry_tf = resolve_v3_entry_timeframe` compatibility alias in `quant_scorer.py` for existing private-import callers; do not duplicate its configuration lookup. In `evaluate_engine_a_v3`, append `invalid_entry_timeframe` to the existing early rejection path when `primary_tf is None`, and pass `entryTimeframe=primary_tf` in both return constructors. Add `entryTimeframe: str | None = None` after the existing defaulted V3 contract fields, so legacy test construction remains source-compatible and `asdict()` serializes it.

- [ ] **Step 4: Run focused contract/parity tests**

Run: `pytest tests/test_engine_a_v3_setup_parity.py tests/test_engine_a_v3.py -q`

Expected: PASS. The three groups resolve H1/H4/H4, valid signals serialize the same resolved value, and invalid configuration produces a non-trade V3 signal.

- [ ] **Step 5: Commit the independently testable contract change**

```powershell
git add engine_a_v3/timeframes.py engine_a_v3/quant_scorer.py engine_a_v3/evaluator.py engine_a_v3/setups.py engine_a_v3/contract.py tests/test_engine_a_v3_setup_parity.py tests/test_engine_a_v3.py
git commit -m "fix: share Engine A V3 entry timeframe contract"
```

### Task 2: Make V3 backtests use the resolved contract and disclose comparability

**Files:**
- Modify: `engine_a_v3/backtest.py:262-490`
- Test: `tests/test_engine_a_v3_backtest_parity.py`
- Test: `tests/test_engine_a_v3_backtest_costs.py`

**Consumes:** `resolve_v3_entry_timeframe`, `evaluate_engine_a_v3(...).to_dict()`, existing `confirmed_cutoff_open_epoch`, and `classify_engine_a_v3_signal` after Task 4.

**Produces:** `result["funnel"]["primaryTf"]`, `result["entryTimeframe"]`, `result["comparability"]`, and scan-eligible versus raw-qualification funnel counts.

- [ ] **Step 1: Write failing primary-timeframe, comparability, and scan-eligibility tests**

```python
@pytest.mark.parametrize(
    ("pair", "expected_tf"),
    [
        ({"display": "EUR/USD", "type": "forex"}, "H1"),
        ({"display": "USD/MXN", "type": "forex"}, "H4"),
        ({"display": "AAVE/USDT", "type": "crypto"}, "H4"),
    ],
)
def test_v3_backtest_funnel_uses_signal_entry_timeframe(pair, expected_tf, candles):
    result = run_v3_backtest(pair, candles, horizon="intraday", costs())
    assert result["entryTimeframe"] == expected_tf
    assert result["funnel"]["primaryTf"] == expected_tf


def test_v3_backtest_marks_unreplayed_live_context_not_promotion_eligible(pair, candles):
    result = run_v3_backtest(pair, candles, horizon="intraday", costs(), collect_funnel=True)
    assert result["comparability"] == {
        "liveComparable": False,
        "promotionEligible": False,
        "unreplayedInputs": ["live_context", "live_scan_gates"],
    }


def test_v3_backtest_excludes_confidence_demoted_raw_trade(monkeypatch, pair, candles):
    monkeypatch.setattr(backtest_module, "evaluate_engine_a_v3", lambda *args, **kwargs: raw_trade_signal(scoreNorm=0.1))
    result = run_v3_backtest(pair, candles, horizon="intraday", costs(), collect_funnel=True)
    assert result["funnel"]["rawQualified"] > 0
    assert result["funnel"]["scanEligible"] == 0
    assert result["funnel"]["tradesTaken"] == 0
```

Use the existing candle factory/cost helper in this test module. Build `raw_trade_signal` from the module's existing V3 signal fixture and use `.to_dict()` before calling the shared classifier, because the production classifier accepts mappings.

- [ ] **Step 2: Run the focused test file to prove it fails**

Run: `pytest tests/test_engine_a_v3_backtest_parity.py -q`

Expected: FAIL because `run_v3_backtest` hard-codes H1/H4, has no comparability block, and uses raw qualification instead of classifier tier.

- [ ] **Step 3: Implement time-frame and comparability behavior without replaying live data**

At the top of `run_v3_backtest`, resolve the primary timeframe from the routed pair and fail with a structured result when it is unavailable:

```python
route = route_specialist(pair)
primary_tf = resolve_v3_entry_timeframe(route.score_group, str(pair.get("type") or pair.get("asset_type") or "other"), horizon)
if primary_tf is None:
    return {
        "error": "ENGINE_A_V3_INVALID_ENTRY_TIMEFRAME",
        "entryTimeframe": None,
        "comparability": _v3_backtest_comparability(),
    }
```

Use `primary_tf` consistently for `primary`, prefix cutoff, next entry bar, simulated exit window, regime/efficiency source, and funnel metadata. Preserve the existing confirmed-bar prefix algorithm and cost calculations. Add a small local pure helper returning the exact conservative metadata:

```python
def _v3_backtest_comparability() -> dict[str, object]:
    return {
        "liveComparable": False,
        "promotionEligible": False,
        "unreplayedInputs": ["live_context", "live_scan_gates"],
    }
```

Attach it to every normal and early V3 result. Do not insert synthetic context into `evaluate_engine_a_v3`. For each evaluated signal, call `classify_engine_a_v3_signal(signal.to_dict(), pair)` and open a trade only when tier is `"trade"`. In the funnel, retain both `rawQualified` and `scanEligible` counts and reasons from classifier demotions; report `unreplayedScanGates` as `("exchangeClosed", "eventRisk", "macroEventRisk", "directionConflicted")` rather than evaluating wall-clock state against historical bars.

- [ ] **Step 4: Run the focused backtest tests**

Run: `pytest tests/test_engine_a_v3_backtest_parity.py tests/test_engine_a_v3_backtest_costs.py -q`

Expected: PASS. The H4 override pairs enter/simulate on H4 bars, raw trade rows can be withheld by deterministic tiering, and comparability is explicit.

- [ ] **Step 5: Commit the V3 backtest parity change**

```powershell
git add engine_a_v3/backtest.py tests/test_engine_a_v3_backtest_parity.py tests/test_engine_a_v3_backtest_costs.py
git commit -m "fix: align Engine A V3 backtest decision contract"
```

### Task 3: Reject unsupported V3 validation modes and make the UI truthful

**Files:**
- Modify: `backtest_runner.py:1775-2013`
- Modify: `athena_app/services/scan_backtest_service.py:26-82`
- Modify: `static/react-app/app/src/components/panels/BacktestPanel.tsx`
- Test: `tests/test_scan_backtest_service.py`
- Test: `tests/test_engine_a_v3_validation.py`
- Test: `static/react-app/app/src/lib/__tests__/backtestPayload.test.ts`

**Consumes:** requested `validation_mode`, pair routing, current `/api/backtest` service error shape.

**Produces:** stable V3 error code `ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED`, no false walk-forward/purged-CV response, Engine A UI controls disabled with explanatory text.

- [ ] **Step 1: Write failing API/service and UI behavior tests**

```python
@pytest.mark.parametrize("mode", ["walk_forward", "purged_cv"])
def test_v3_backtest_rejects_validation_mode_without_running_backtest(monkeypatch, pair, mode):
    monkeypatch.setattr(backtest_runner, "_rt", lambda: pytest.fail("must not fetch candles"))
    result = backtest_runner.backtest_pair(pair, style="intraday", validation_mode=mode)
    assert result == {
        "error": "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED",
        "status": 422,
        "validation_mode": mode,
    }


def test_v3_standard_validation_mode_remains_available(monkeypatch, pair):
    monkeypatch.setattr(backtest_runner, "run_v3_backtest", lambda *args, **kwargs: {"success": True})
    # Patch the existing candle fetch helpers with valid confirmed candles.
    assert backtest_runner.backtest_pair(pair, validation_mode="standard")["success"] is True
```

In the nearest Vitest file, render/inspect the backtest panel helper state and assert Engine A V3 exposes only `standard` plus the visible copy `"Walk-forward and purged-CV are not implemented for Engine A V3."`. Do not change payload serialization for other engines.

- [ ] **Step 2: Run the focused tests to prove they fail**

Run: `pytest tests/test_scan_backtest_service.py tests/test_engine_a_v3_validation.py -q`

Run: `npm --prefix static/react-app/app test -- --run src/lib/__tests__/backtestPayload.test.ts`

Expected: V3 accepts and silently ignores the unsupported mode; UI still permits it.

- [ ] **Step 3: Add explicit rejection and disable unavailable controls**

At the top of the V3 route in `backtest_pair`, normalize `validation_mode` only for comparison and return before fetching candles when it is not `standard`:

```python
_v3_validation_mode = str(validation_mode or "standard").strip().lower()
if _v3_validation_mode not in {"", "standard"}:
    return {
        "error": "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED",
        "status": 422,
        "validation_mode": _v3_validation_mode,
    }
```

Make `scan_backtest_service` preserve that dict unchanged instead of converting it to a generic success payload. In `BacktestPanel`, derive `engineAV3ValidationUnsupported = selectedEngine === "A"` from the existing Engine A selection state, force `validationMode` to `"standard"` when it becomes true, and render the two unavailable options with `disabled` and the explanatory copy. Do not disable the modes for Engine B, ASE, or naked backtests.

- [ ] **Step 4: Run focused server and client tests**

Run: `pytest tests/test_scan_backtest_service.py tests/test_engine_a_v3_validation.py -q`

Run: `npm --prefix static/react-app/app test -- --run src/lib/__tests__/backtestPayload.test.ts`

Expected: PASS. `standard` keeps its current path; unsupported V3 modes return a stable 422-style response and cannot be selected in the Engine A panel.

- [ ] **Step 5: Commit the validation truthfulness change**

```powershell
git add backtest_runner.py athena_app/services/scan_backtest_service.py static/react-app/app/src/components/panels/BacktestPanel.tsx tests/test_scan_backtest_service.py tests/test_engine_a_v3_validation.py static/react-app/app/src/lib/__tests__/backtestPayload.test.ts
git commit -m "fix: reject unsupported Engine A V3 validation modes"
```

## Stage 2 — safety, configuration, and research integrity

### Task 4: Make shared V3 classification enforce hard event blocks and preserve historical gate provenance

**Files:**
- Modify: `athena_app/services/engine_a_v3_classify.py:8-55`
- Test: `tests/test_engine_a_v3_classify_parity.py`

**Consumes:** scan annotations `eventRisk.hardBlock` and `macroEventRisk.blocked`, existing raw V3 signal fields.

**Produces:** deterministic `("watchlist", reason)` classification for event/macro hard blocks, used by scanner and Task 2 backtest when the data is supplied.

- [ ] **Step 1: Write failing classification tests**

```python
@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("eventRisk", {"hardBlock": True, "reason": "earnings_within_one_day"}, "earnings_within_one_day"),
        ("macroEventRisk", {"blocked": True, "reason": "high_impact_macro"}, "high_impact_macro"),
    ],
)
def test_v3_hard_risk_annotation_demotes_trade(field, value, expected_reason, trade_signal, enabled_pair):
    trade_signal[field] = value
    tier, reason = classify_engine_a_v3_signal(trade_signal, enabled_pair)
    assert tier == "watchlist"
    assert expected_reason in reason
```

- [ ] **Step 2: Run the focused classifier test to prove it fails**

Run: `pytest tests/test_engine_a_v3_classify_parity.py -q`

Expected: FAIL because classifier ignores both annotation objects.

- [ ] **Step 3: Add hard-block checks before confidence evaluation**

Use mappings only and fail closed on malformed truthy data:

```python
event_risk = signal.get("eventRisk")
if isinstance(event_risk, dict) and event_risk.get("hardBlock") is True:
    return "watchlist", str(event_risk.get("reason") or "V3 event-risk hard block")
macro_risk = signal.get("macroEventRisk")
if isinstance(macro_risk, dict) and macro_risk.get("blocked") is True:
    return "watchlist", str(macro_risk.get("reason") or "V3 macro-event hard block")
```

Leave absent historical annotations untouched; Task 2 records those gate inputs as unreplayed rather than applying present-time event state to historical bars.

- [ ] **Step 4: Run classifier and backtest parity tests**

Run: `pytest tests/test_engine_a_v3_classify_parity.py tests/test_engine_a_v3_backtest_parity.py -q`

Expected: PASS. Scanner/live inputs with explicit hard blocks are never tiered `trade`; historical absence stays disclosed as unavailable.

- [ ] **Step 5: Commit shared-classifier hardening**

```powershell
git add athena_app/services/engine_a_v3_classify.py tests/test_engine_a_v3_classify_parity.py
git commit -m "fix: enforce Engine A V3 event risk scan blocks"
```

### Task 5: Reject inactive V3 pair filter overrides and remove the inert configuration

**Files:**
- Modify: `config.py:2983-2988, 3342-3350`
- Modify: `config.yaml:3228-3258`
- Test: `tests/test_config_safety_validation.py`

**Consumes:** `PAIR_PROFILES` entries and existing fatal `ConfigValidationError` collection path.

**Produces:** boot-time configuration error whenever a V3 `PAIR_PROFILES.*.disable_filters` key is present; current configuration contains no unsupported key.

- [ ] **Step 1: Write the failing config-validation test**

```python
def test_pair_profile_disable_filters_is_rejected_for_engine_a_v3(config_with_defaults):
    config_with_defaults["PAIR_PROFILES"] = {"USD/MXN": {"disable_filters": ["obv"]}}
    errors = config_module._collect_fatal_config_errors(config_with_defaults)
    assert "PAIR_PROFILES.USD/MXN.disable_filters is unsupported for Engine A V3" in errors
```

Use the existing test helper that calls the repository's real fatal validation function; do not test logging-only warnings.

- [ ] **Step 2: Run the focused config test to prove it fails**

Run: `pytest tests/test_config_safety_validation.py -q`

Expected: FAIL because the key is accepted with at most an unknown-filter warning.

- [ ] **Step 3: Implement one fatal validator and remove only inert keys**

In the existing fatal error collector, add the exact error string when `"disable_filters" in profile`; retain unknown-filter warning logic only for legacy configuration if it is still reachable, but do not accept the key for V3. Delete only these eight key/value lines in `config.yaml`, retaining their profile names and all other settings:

```yaml
XAU/USD: { disable_filters: [obv, session] }
XAG/USD: { disable_filters: [obv, session] }
LINK/USDT: { disable_filters: [mean_revert] }
SUI/USDT: { disable_filters: [mean_revert] }
APT/USDT: { disable_filters: [mean_revert] }
LTC/USDT: { disable_filters: [mean_revert] }
USD/CHF: { disable_filters: [obv] }
USD/MXN: { disable_filters: [obv] }
```

Do not alter `score_group` profile mappings such as USDX/EURX/JPYX.

- [ ] **Step 4: Run focused config verification**

Run: `pytest tests/test_config_safety_validation.py -q`

Run: `rg -n "disable_filters" config.yaml config.py`

Expected: PASS; `config.yaml` produces no matches and `config.py` contains only the deliberate rejection logic.

- [ ] **Step 5: Commit configuration safety change**

```powershell
git add config.py config.yaml tests/test_config_safety_validation.py
git commit -m "fix: reject inactive Engine A V3 pair filters"
```

### Task 6: Preserve scanner tier through TV Chart and manual-demo refresh

**Files:**
- Modify: `static/react-app/app/src/lib/manualExecuteHelpers.ts:460-474`
- Modify: `static/react-app/app/src/components/panels/SignalsPanel.tsx:385-393`
- Modify: `engine_a_v3/execution.py:122-164`
- Test: `static/react-app/app/src/lib/__tests__/manualExecuteHelpers.test.ts`
- Test: `tests/test_engine_a_v3_execution.py`

**Consumes:** scanner-produced `signalTier`, Task 1 `entryTimeframe`, existing V3 refresh contract verification.

**Produces:** V3 TV chart uses `entryTimeframe`; a `watchlist`/`skip` original V3 row is non-executable on client and rejected by server refresh with `ENGINE_A_V3_REFRESH_ORIGINAL_SCAN_TIER_NOT_TRADE`.

- [ ] **Step 1: Write failing client and server safety tests**

```ts
it('blocks a V3 scanner watchlist despite raw TRADE fields', () => {
  expect(canExecuteEngineASignalTier({
    engine: 'ENGINE_A_V3', contractVersion: '3.1.0', decision: 'TRADE',
    qualified: true, engineATradeEnabled: true, signalTier: 'watchlist',
  } as EngineASignal)).toBe(false);
});
```

```python
def test_v3_refresh_cannot_promote_original_scanner_watchlist(v3_trade_payload):
    original = {**v3_trade_payload, "signalTier": "watchlist"}
    refreshed = {**v3_trade_payload, "signalTier": "trade"}
    ok, reason = verify_refreshed_signal(original, refreshed, now=FRESH_NOW)
    assert (ok, reason) == (False, "ENGINE_A_V3_REFRESH_ORIGINAL_SCAN_TIER_NOT_TRADE")
```

Add a `preferredTvChartTf` test through its exported/publicly testable chart-intent path, asserting `{ entryTimeframe: "H4", style: "intraday" }` selects H4.

- [ ] **Step 2: Run tests to prove the safety regression is present**

Run: `pytest tests/test_engine_a_v3_execution.py -q`

Run: `npm --prefix static/react-app/app test -- --run src/lib/__tests__/manualExecuteHelpers.test.ts`

Expected: server accepts the original watchlist after a raw `TRADE` refresh; V3 client branch accepts it; chart falls back to H1.

- [ ] **Step 3: Implement fail-closed tier checks without changing existing execution guards**

In `canExecuteEngineASignalTier`, normalize `signalTier`, `scan_tier`, or `signalClass` before the V3 decision check and reject `watch`, `skip`, or `blocked`; require an explicit `trade`/`criteria` tier for V3 rather than falling back to raw `trade`:

```ts
const tier = String(signal.signalTier || signal.scan_tier || signal.signalClass || '').toLowerCase();
if (tier.includes('watch') || tier === 'skip' || tier === 'blocked') return false;
if (isEngineAV3Signal(signal) && tier !== 'trade' && tier !== 'criteria') return false;
```

Have `preferredTvChartTf` choose `signal.entryTimeframe` before `signal.timeframe`, route, or style fallback. In `verify_refreshed_signal`, inspect the original scanner tier before checking refreshed fields and return the stable rejection code when it is not `trade`/`criteria`. Do not modify `merge_refreshed_signal`, attestation, risk, kill switch, freshness, duplicate, or broker logic: verifier rejection prevents promotion before merge.

- [ ] **Step 4: Run the focused client/server safety tests**

Run: `pytest tests/test_engine_a_v3_execution.py -q`

Run: `npm --prefix static/react-app/app test -- --run src/lib/__tests__/manualExecuteHelpers.test.ts`

Expected: PASS. A scan-tier demotion is consistently non-executable before and after refresh; H4 overrides route charts to H4.

- [ ] **Step 5: Commit the manual-demo tier preservation change**

```powershell
git add engine_a_v3/execution.py static/react-app/app/src/lib/manualExecuteHelpers.ts static/react-app/app/src/components/panels/SignalsPanel.tsx tests/test_engine_a_v3_execution.py static/react-app/app/src/lib/__tests__/manualExecuteHelpers.test.ts
git commit -m "fix: preserve Engine A V3 scanner tier on refresh"
```

### Task 7: Correct the research-only multi-timeframe entry diagnostic

**Files:**
- Modify: `athena_research/tf_entry_path_audit/diagnostic_v3.py:22-114`
- Test: `tests/test_tf_entry_path_audit.py`

**Consumes:** primary walk timeframe duration, existing `candle_timestamp_epoch`, V3 `entryZone`.

**Produces:** no entry before the walk bar close, no simulated fill unless the selected entry bar overlaps the submitted zone.

- [ ] **Step 1: Write failing timing and zone-touch tests**

```python
def test_v3_diagnostic_waits_for_h4_walk_bar_close_before_h1_entry():
    entry_rows = hourly_rows(start_epoch=0, count=8)
    assert _find_entry_index(entry_rows, decision_epoch=4 * 3600) == 5


def test_v3_diagnostic_skips_entry_bar_that_does_not_touch_zone(monkeypatch, h4_walk, h1_entry):
    monkeypatch.setattr(diagnostic_v3, "evaluate_engine_a_v3", lambda *args, **kwargs: v3_trade(entry_zone=(90.0, 91.0)))
    result = run_v3_diagnostic(pair(), {"H4": h4_walk, "H1": h1_entry}, walk_tf="H4", entry_tf="H1")
    assert result.trades == []
```

Use the module's actual public diagnostic runner/type names. If the output is a list, assert `result == []`; do not introduce a new API merely for testing.

- [ ] **Step 2: Run the focused research test to prove it fails**

Run: `pytest tests/test_tf_entry_path_audit.py -q`

Expected: FAIL because the diagnostic uses the H4 bar open and records a clamped entry even when the H1 high/low never overlaps the zone.

- [ ] **Step 3: Implement decision-close timing and overlap-only fills**

Add a local timeframe-to-seconds helper limited to the diagnostic's supported matrix and compute:

```python
decision_epoch = walk_epochs[index] + _tf_seconds(walk_tf)
entry_idx = _find_entry_index(entry_rows, decision_epoch)
```

Change `_find_entry_index` to return the first entry-bar index strictly after `decision_epoch`, not an extra bar beyond it:

```python
idx = bisect.bisect_right(epochs, decision_epoch)
return idx if idx < len(entry_candles) else None
```

Before clamping/recording `entry`, use production's overlap guard:

```python
if float(entry_bar["high"]) < zone_low or float(entry_bar["low"]) > zone_high:
    continue
```

Keep this research module separate from live scoring and execution.

- [ ] **Step 4: Run the focused diagnostic test**

Run: `pytest tests/test_tf_entry_path_audit.py -q`

Expected: PASS. H4 decisions cannot use H1 price action before H4 close and no-touch bars produce no record.

- [ ] **Step 5: Commit research integrity correction**

```powershell
git add athena_research/tf_entry_path_audit/diagnostic_v3.py tests/test_tf_entry_path_audit.py
git commit -m "fix: align V3 timeframe diagnostic entry timing"
```

### Task 8: Cross-surface regression verification and handoff

**Files:**
- Modify only if a focused test exposes a defect in the above tasks.
- Test: `tests/test_engine_a_v3.py`
- Test: `tests/test_engine_a_v3_backtest_parity.py`
- Test: `tests/test_engine_a_v3_classify_parity.py`
- Test: `tests/test_engine_a_v3_execution.py`
- Test: `tests/test_engine_a_v3_setup_parity.py`
- Test: `tests/test_tf_entry_path_audit.py`
- Test: `static/react-app/app/src/lib/__tests__/manualExecuteHelpers.test.ts`
- Test: `static/react-app/app/src/lib/__tests__/backtestPayload.test.ts`

**Consumes:** all earlier task outputs.

**Produces:** fresh evidence that the V3 contract, backtest, scan classifier, UI route, and execution refresh agree; no claim of profitability or live-feed parity.

- [ ] **Step 1: Run focused Python regression checks**

Run: `pytest tests/test_engine_a_v3.py tests/test_engine_a_v3_setup_parity.py tests/test_engine_a_v3_backtest_parity.py tests/test_engine_a_v3_backtest_costs.py tests/test_engine_a_v3_validation.py tests/test_engine_a_v3_classify_parity.py tests/test_engine_a_v3_execution.py tests/test_config_safety_validation.py tests/test_tf_entry_path_audit.py -q`

Expected: PASS. Do not run the full suite.

- [ ] **Step 2: Run focused frontend regression checks**

Run: `npm --prefix static/react-app/app test -- --run src/lib/__tests__/manualExecuteHelpers.test.ts src/lib/__tests__/backtestPayload.test.ts`

Expected: PASS. If the repository has a configured narrower TypeScript command, run it only for the touched files; do not invoke a production deployment.

- [ ] **Step 3: Inspect changed paths and safety boundaries**

Run: `git diff --check`

Run: `git status --short`

Run: `rg -n "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED|ENGINE_A_V3_REFRESH_ORIGINAL_SCAN_TIER_NOT_TRADE|entryTimeframe|liveComparable|promotionEligible" engine_a_v3 backtest_runner.py athena_app static/react-app/app/src tests`

Expected: no whitespace errors; each required contract/safety identifier has a producer and a focused test.

- [ ] **Step 4: Do not create speculative verification changes**

All behavioral changes and their tests belong to Tasks 1–7. If Task 8 reveals a failure, return to the owning task, add its focused regression test and minimal fix, then rerun this task's commands. If no new failure is found, do not create an empty commit.

## Plan self-review

- Spec coverage: Tasks 1–3 implement all Stage 1 requirements; Tasks 4–7 implement all Stage 2 requirements; Task 8 validates the end-to-end contract. No threshold, weight, risk, SL/TP, or live-mode changes are included.
- Safety coverage: invalid timeframes fail closed; unsupported validation modes reject before data fetch; unreplayed inputs are labeled instead of fabricated; original scanner tier blocks refresh; event blocks demote only; research code remains isolated.
- Contract consistency: the public function is `resolve_v3_entry_timeframe`; the payload field is `entryTimeframe`; the validation code is `ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED`; the refresh code is `ENGINE_A_V3_REFRESH_ORIGINAL_SCAN_TIER_NOT_TRADE`; and comparability keys are `liveComparable` and `promotionEligible` in all tasks.
- Completeness scan: every behavior-changing task names concrete files, a focused failing test, an implementation shape, a passing command, and a commit; Task 8 deliberately adds no speculative code.
