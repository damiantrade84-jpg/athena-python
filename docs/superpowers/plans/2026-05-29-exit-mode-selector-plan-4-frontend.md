# Exit-Mode Selector — Plan 4: Frontend (Exit Strategy tab + per-trade selector)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a UI to (a) set the Engine-A per-score-group default exit mode + advisable-pip band in a new **Exit Strategy** tab, and (b) override the exit mode **per trade** on the Engine-A execute surfaces — wiring straight into the Plan-2 backend, which already resolves and consumes these.

**Architecture:** Two surfaces, both Engine-A only. (1) A new `/api/exit-mode-config` GET/POST route in `athena.py` that reads/writes the four Plan-2 CONFIG keys; all validation + YAML persistence lives in a new **pure** `exit_mode_config.py` module (no `athena.py`/`config.py` import) so it is unit-testable, mirroring how `exit_policy.py`/`exit_mode_apply.py` stay import-light. The React `ExitStrategyPanel` GETs/POSTs it. (2) A per-trade `ExitModeField` (mirroring `VolumeModeField.tsx`) + `useExitModeState` hook (mirroring `useExecutionVolumeState.ts`) whose selection is threaded onto the execute payload's `signal.exit_mode` via `buildQuickExecutePayload` — the field that `exit_mode_apply.apply_engine_a_exit_mode` already reads (`sig.get("exit_mode")`).

**Tech Stack:** Python 3.13 + pytest (backend route/persist); React 18 + TypeScript + vitest + shadcn/ui (RadioGroup, Select, Label, Card) + Tailwind (frontend). Build via the existing Vite app under `static/react-app/app`.

**Spec:** `docs/superpowers/specs/2026-05-29-exit-mode-selector-design.md` → "Frontend" section (lines 122–130). **Depends on:** Plan 2 (committed — config keys, `exit_policy.py`, `exit_mode_apply.py`, monitor dispatch).

---

## No `/athena-audit` gate

Plan 4 touches **no** execution-safety file (`execution.py`, `risk_engine.py`, `guardian.py`, `auto_trader.py`, `mt5_executor.py`, `bybit_executor.py` are untouched). The new `athena.py` route only reads/writes the four already-existing exit-mode CONFIG keys + a YAML persist helper; it cannot place, size, or gate an order. The per-trade field reuses the `sig.get("exit_mode")` override the backend already consumes. AI plays no part. The selector chooses **geometry + management only**; every order still passes risk_engine/guardian/RR/SL-TP/freshness/kill-switch exactly as today.

## Scope decisions (locked, to avoid scope creep / audit-gated edits)

- **Per-trade field sends `exit_mode` only.** `time_based` hold-length (`ENGINE_A_TIME_EXIT_BARS`) and `manual` SL/TP are **not** new per-trade inputs: the monitor reads bars from per-group config, and `manual` reuses the existing level-override SL/TP inputs already on the execute surfaces. A per-trade bars override would require a `timed_exit_monitor.py` edit (audit-gated) and is out of scope. The field shows contextual helper text instead of new inputs.
- **Per-trade override is optional.** A 5th choice, **"Use default"**, sends no `exit_mode` so the backend resolves per-group → global. This is the default selection and matches `resolve_exit_mode` precedence exactly.
- **Exit Strategy tab edits three keys:** `ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT`, `ENGINE_A_EXIT_MODE_BY_SCORE_GROUP`, `ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP`. `ENGINE_A_TIME_EXIT_BARS` (block-style YAML) is **not** edited here (the monitor already reads seeded defaults); YAGNI.
- **Per-trade field surfaces:** the two Engine-A manual-execute panels — `SignalsPanel.tsx` and `TVChartPanel.tsx`. `EngineCPanel.tsx` also calls `buildQuickExecutePayload` but is Engine-C scope and is left untouched (backend no-ops `exit_mode` for non-`engine_a` anyway).

---

### Task 1: Backend — pure config module + `/api/exit-mode-config` route

**Files:**
- Create: `exit_mode_config.py` (pure: validation + YAML persist)
- Modify: `athena.py` (new route near `api_execution_config` at `:7883`)
- Test: `tests/test_exit_mode_config_api.py`

**Verified facts:** config.yaml keys are single-line flow style at `:2864` (`ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT: traditional_static`), `:2865` (`ENGINE_A_EXIT_MODE_BY_SCORE_GROUP: {}        # ...`), `:2869` (`ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP: {}    # ...`). Known groups: `engine_a_groups.ENGINE_A_KNOWN_SCORE_GROUPS` (frozenset, 27 entries incl. `unknown`). Valid modes: `exit_policy.VALID_EXIT_MODES`. Existing persist precedent: `athena.py:_persist_scan_settings_yaml` (`:8623`) — regex single-line replace preserving inline comments.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_mode_config_api.py
import shutil

import exit_mode_config as emc
import yaml

KNOWN = {"forex_majors", "crypto_btc", "unknown"}


def test_validate_accepts_valid_global_and_group_modes():
    updates, errors = emc.validate_exit_mode_updates(
        {
            "globalDefault": "adaptive_trail",
            "byScoreGroup": {"forex_majors": "time_based"},
            "advisablePipByScoreGroup": {"forex_majors": {"min_pip": 8, "max_pip": 60}},
        },
        KNOWN,
    )
    assert errors == []
    assert updates["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] == "adaptive_trail"
    assert updates["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] == {"forex_majors": "time_based"}
    assert updates["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {
        "forex_majors": {"min_pip": 8.0, "max_pip": 60.0}
    }


def test_validate_rejects_unknown_mode_unknown_group_and_bad_pip():
    _, errors = emc.validate_exit_mode_updates(
        {
            "globalDefault": "nonsense_mode",
            "byScoreGroup": {"forex_majors": "junk", "not_a_group": "manual"},
            "advisablePipByScoreGroup": {"forex_majors": {"min_pip": 80, "max_pip": 8}},
        },
        KNOWN,
    )
    assert any("globalDefault" in e for e in errors)
    assert any("forex_majors" in e and "junk" in e for e in errors)
    assert any("not_a_group" in e for e in errors)
    assert any("min_pip" in e and "max_pip" in e for e in errors)


def test_validate_drops_empty_pip_entries():
    updates, errors = emc.validate_exit_mode_updates(
        {"advisablePipByScoreGroup": {"forex_majors": {}}}, KNOWN
    )
    assert errors == []
    # an entry with no usable bound is dropped, not persisted as {}
    assert updates["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {}


def test_persist_round_trips_through_yaml(tmp_path):
    src = "config.yaml"
    dst = tmp_path / "config.yaml"
    shutil.copyfile(src, dst)
    emc.persist_exit_mode_config_yaml(
        str(dst),
        {
            "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "adaptive_trail",
            "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {"forex_majors": "time_based"},
            "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {
                "forex_majors": {"min_pip": 8, "max_pip": 60}
            },
        },
    )
    loaded = yaml.safe_load(dst.read_text(encoding="utf-8"))
    assert loaded["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] == "adaptive_trail"
    assert loaded["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] == {"forex_majors": "time_based"}
    assert loaded["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {
        "forex_majors": {"min_pip": 8, "max_pip": 60}
    }
    # untouched key remains a valid block-style map
    assert set(loaded["ENGINE_A_TIME_EXIT_BARS"]) == {"scalp", "intraday", "swing"}


def test_persist_empty_maps_round_trip(tmp_path):
    dst = tmp_path / "config.yaml"
    shutil.copyfile("config.yaml", dst)
    emc.persist_exit_mode_config_yaml(
        str(dst),
        {
            "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
            "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {},
            "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {},
        },
    )
    loaded = yaml.safe_load(dst.read_text(encoding="utf-8"))
    assert loaded["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] == {}
    assert loaded["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_mode_config_api.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'exit_mode_config'`.

- [ ] **Step 3: Create the pure module**

```python
# exit_mode_config.py
"""Pure validation + YAML persistence for the Engine A Exit Strategy tab.

Standalone (imports only exit_policy + stdlib; never athena.py/config.py, whose
import aborts on the real-orders gate) so the validation and the comment-preserving
YAML write are unit-testable. The Flask route in athena.py calls these, then
mutates the in-memory CONFIG.
"""

from __future__ import annotations

import re

import exit_policy

# Single-line flow-style keys in config.yaml this tab owns. ENGINE_A_TIME_EXIT_BARS
# is intentionally excluded (block-style; not edited here).
EXIT_MODE_YAML_KEYS = (
    "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT",
    "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP",
    "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP",
)


def _coerce_pip(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def validate_exit_mode_updates(d: dict, known_groups) -> tuple[dict, list[str]]:
    """Validate a POST body. Returns (updates, errors).

    updates maps CONFIG keys -> sanitized values (only for keys present in d).
    Modes are checked against exit_policy.VALID_EXIT_MODES; groups against
    known_groups; pip bounds must be positive numbers with min <= max. Empty/
    unusable pip entries are dropped (not persisted). Any invalid entry appends
    an error and the caller rejects the whole POST (HTTP 400).
    """
    updates: dict = {}
    errors: list[str] = []
    known = set(known_groups)

    if "globalDefault" in d:
        gd = exit_policy.normalize_mode(d.get("globalDefault"))
        if gd is None:
            errors.append(f"globalDefault is not a valid exit mode: {d.get('globalDefault')!r}")
        else:
            updates["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] = gd

    if "byScoreGroup" in d:
        raw = d.get("byScoreGroup") or {}
        clean: dict = {}
        if not isinstance(raw, dict):
            errors.append("byScoreGroup must be an object")
        else:
            for group, mode in raw.items():
                if group not in known:
                    errors.append(f"byScoreGroup: unknown score group {group!r}")
                    continue
                norm = exit_policy.normalize_mode(mode)
                if norm is None:
                    errors.append(f"byScoreGroup[{group}]: invalid mode {mode!r} ({mode})")
                    continue
                clean[group] = norm
        updates["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"] = clean

    if "advisablePipByScoreGroup" in d:
        raw = d.get("advisablePipByScoreGroup") or {}
        clean = {}
        if not isinstance(raw, dict):
            errors.append("advisablePipByScoreGroup must be an object")
        else:
            for group, band in raw.items():
                if group not in known:
                    errors.append(f"advisablePipByScoreGroup: unknown score group {group!r}")
                    continue
                band = band or {}
                lo = _coerce_pip(band.get("min_pip")) if "min_pip" in band else None
                hi = _coerce_pip(band.get("max_pip")) if "max_pip" in band else None
                if lo is None and hi is None:
                    continue  # nothing usable -> drop the entry
                if lo is not None and hi is not None and lo > hi:
                    errors.append(
                        f"advisablePipByScoreGroup[{group}]: min_pip ({lo}) > max_pip ({hi})"
                    )
                    continue
                entry = {}
                if lo is not None:
                    entry["min_pip"] = lo
                if hi is not None:
                    entry["max_pip"] = hi
                clean[group] = entry
        updates["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"] = clean

    return updates, errors


def persist_exit_mode_config_yaml(cfg_path: str, current: dict) -> None:
    """Write the owned keys back to config.yaml, preserving inline comments.

    Renders dict values as single-line flow YAML and scalars bare, then does a
    single-line regex replace per key (mirrors athena._persist_scan_settings_yaml).
    """
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        content = f.read()

    for key in EXIT_MODE_YAML_KEYS:
        if key not in current:
            continue
        value = current[key]
        if isinstance(value, dict):
            rendered = _yaml.safe_dump(value, default_flow_style=True).strip()
        else:
            rendered = str(value)
        content, count = re.subn(
            rf"^({re.escape(key)}\s*:\s*)([^#\n]+?)(\s*(?:#.*)?)$",
            lambda m, v=rendered: f"{m.group(1)}{v}{m.group(3)}",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if count == 0:
            raise ValueError(f"config.yaml: {key} not found")

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_mode_config_api.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Add the Flask route**

In `athena.py`, immediately after `api_execution_config` ends (`:7969`), add:

```python
@app.route("/api/exit-mode-config", methods=["GET", "POST"])
def api_exit_mode_config():
    """Get/update the Engine A exit-mode + advisable-pip config (Exit Strategy tab).

    Advisory config only: mutates the four CONFIG keys the deterministic exit path
    reads; it cannot execute, size, or bypass a gate.
    """
    import exit_mode_config
    import exit_policy
    from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS

    if request.method == "GET":
        return jsonify(
            {
                "globalDefault": CONFIG.get(
                    "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT", exit_policy.DEFAULT_EXIT_MODE
                ),
                "byScoreGroup": CONFIG.get("ENGINE_A_EXIT_MODE_BY_SCORE_GROUP") or {},
                "advisablePipByScoreGroup": CONFIG.get(
                    "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"
                )
                or {},
                "knownScoreGroups": sorted(ENGINE_A_KNOWN_SCORE_GROUPS),
                "validModes": sorted(exit_policy.VALID_EXIT_MODES),
            }
        )

    d = request.get_json(silent=True) or {}
    updates, errors = exit_mode_config.validate_exit_mode_updates(
        d, ENGINE_A_KNOWN_SCORE_GROUPS
    )
    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    for key, value in updates.items():
        CONFIG[key] = value
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        exit_mode_config.persist_exit_mode_config_yaml(cfg_path, updates)
    except Exception as exc:  # persistence failure must not leave a half-written file silently
        log.error(f"[EXIT MODE] config persist failed: {exc}")
        return jsonify({"success": False, "errors": [f"persist failed: {exc}"]}), 500

    log.info(f"[EXIT MODE] config updated: {sorted(updates)}")
    return jsonify(
        {
            "success": True,
            "globalDefault": CONFIG.get("ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"),
            "byScoreGroup": CONFIG.get("ENGINE_A_EXIT_MODE_BY_SCORE_GROUP") or {},
            "advisablePipByScoreGroup": CONFIG.get("ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP")
            or {},
        }
    )
```

- [ ] **Step 6: Commit**

```bash
git add exit_mode_config.py athena.py tests/test_exit_mode_config_api.py
git commit -m "feat(exit_mode): /api/exit-mode-config route + pure validate/persist module" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `buildExitModePayload` + thread `exit_mode` into `buildQuickExecutePayload`

**Files:**
- Modify: `static/react-app/app/src/lib/manualExecuteHelpers.ts`
- Test: `static/react-app/app/src/lib/manualExecuteHelpers.exitMode.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// static/react-app/app/src/lib/manualExecuteHelpers.exitMode.test.ts
import { describe, expect, it } from 'vitest';
import { buildExitModePayload, buildQuickExecutePayload } from './manualExecuteHelpers';
import type { EngineASignal } from '@/types/athena';

const sig = {
  symbol: 'EURUSD', pair: 'EURUSD', display: 'EUR/USD', type: 'forex',
  direction: 'LONG', entry: 1.1, price: 1.1, sl: 1.09, tp1: 1.12, tp2: 1.13,
  style: 'swing',
} as unknown as EngineASignal;

describe('buildExitModePayload', () => {
  it('returns empty object for the default (no override)', () => {
    expect(buildExitModePayload()).toEqual({});
    expect(buildExitModePayload({ exitMode: 'default' })).toEqual({});
  });
  it('returns exit_mode for an explicit mode', () => {
    expect(buildExitModePayload({ exitMode: 'time_based' })).toEqual({ exit_mode: 'time_based' });
  });
});

describe('buildQuickExecutePayload exit_mode threading', () => {
  it('stamps signal.exit_mode for an Engine-A execute with an override', () => {
    const payload = buildQuickExecutePayload({
      signal: sig, pipMode: 'swing', exitMode: 'traditional_static',
    });
    expect((payload.signal as Record<string, unknown>).exit_mode).toBe('traditional_static');
  });
  it('omits exit_mode when default selected', () => {
    const payload = buildQuickExecutePayload({ signal: sig, pipMode: 'swing', exitMode: 'default' });
    expect('exit_mode' in (payload.signal as Record<string, unknown>)).toBe(false);
  });
  it('omits exit_mode for an Engine-B-only execute even if a mode is passed', () => {
    const payload = buildQuickExecutePayload({
      signal: sig, pipMode: 'swing', isEngineBOnly: true, exitMode: 'manual',
    });
    expect('exit_mode' in (payload.signal as Record<string, unknown>)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix static/react-app/app run test -- --run manualExecuteHelpers.exitMode`
Expected: FAIL — `buildExitModePayload` is not exported.

- [ ] **Step 3: Implement**

In `manualExecuteHelpers.ts`, add the type + builder near `ExecutionVolumeMode` (top of file, after line 9):

```ts
export type ExitMode = 'traditional_static' | 'adaptive_trail' | 'manual' | 'time_based';
// 'default' = no per-trade override; backend resolves per-group -> global.
export type ExitModeSelection = ExitMode | 'default';

export function buildExitModePayload(
  args: { exitMode?: ExitModeSelection } = {},
): { exit_mode?: ExitMode } {
  const m = args.exitMode;
  return m && m !== 'default' ? { exit_mode: m } : {};
}
```

Extend `buildQuickExecutePayload`'s args type (line 114–121) with `exitMode?: ExitModeSelection;`, destructure it (line 122), and stamp it onto the `signal` object only for Engine-A executes. Replace the `signal: { ... source: ... }` block (lines 130–144) so `exit_mode` is conditionally spread:

```ts
  const { signal, engineBOverlay, isEngineBOnly, pipMode, volumeMode, sizingOverride, exitMode } = args;
  const signalPayload = isEngineBOnly ? signal : stripEngineBFromSignal(signal);
  const nakedData = isEngineBOnly
    ? (signal.naked_data ?? signal.engine_b ?? {})
    : {};
  const effectiveStyle = pipMode || signal.style || 'swing';
  const volumePayload = buildExecutionVolumePayload({ volumeMode, sizingOverride });
  // Per-trade exit-mode override is Engine-A only (backend no-ops it for engine_b).
  const exitModePayload = isEngineBOnly ? {} : buildExitModePayload({ exitMode });
  return {
    signal: {
      ...signalPayload,
      symbol: signal.symbol || signal.pair || signal.display,
      pair: signal.pair || signal.display,
      display: signal.display || signal.pair,
      type: signal.type,
      direction: signal.direction,
      price: signal.entry ?? signal.price,
      entry: signal.entry ?? signal.price,
      sl: signal.sl,
      tp1: signal.tp1 ?? signal.tp,
      tp2: signal.tp2 ?? signal.tp,
      style: effectiveStyle,
      source: isEngineBOnly ? 'engine_b' : 'engine_a',
      ...exitModePayload,
    },
    engine_b: (engineBOverlay ?? nakedData) as Record<string, unknown>,
    pip_mode: effectiveStyle,
    ...volumePayload,
  };
```

(Add `exitMode?: ExitModeSelection;` to the inline args type literal at lines 114–121.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix static/react-app/app run test -- --run manualExecuteHelpers.exitMode`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add static/react-app/app/src/lib/manualExecuteHelpers.ts static/react-app/app/src/lib/manualExecuteHelpers.exitMode.test.ts
git commit -m "feat(exit_mode): thread per-trade exit_mode onto the Engine A execute payload" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `useExitModeState` hook + `ExitModeField` component

**Files:**
- Create: `static/react-app/app/src/hooks/useExitModeState.ts`
- Create: `static/react-app/app/src/components/execution/ExitModeField.tsx`
- Test: `static/react-app/app/src/components/execution/ExitModeField.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// static/react-app/app/src/components/execution/ExitModeField.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import ExitModeField from './ExitModeField';

describe('ExitModeField', () => {
  it('renders the default + four modes and reports changes', () => {
    const onChange = vi.fn();
    render(<ExitModeField exitMode="default" onExitModeChange={onChange} />);
    expect(screen.getByLabelText(/use default/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/traditional/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/adaptive/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/manual/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/time/i)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/time/i));
    expect(onChange).toHaveBeenCalledWith('time_based');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix static/react-app/app run test -- --run ExitModeField`
Expected: FAIL — cannot resolve `./ExitModeField`.

- [ ] **Step 3: Implement the hook + component**

```ts
// static/react-app/app/src/hooks/useExitModeState.ts
import { useState } from 'react';
import type { ExitModeSelection } from '@/lib/manualExecuteHelpers';

// Per-trade exit-mode override. 'default' = no override (backend resolves
// per-group -> global). Mirrors useExecutionVolumeState's shape.
export function useExitModeState(defaultMode: ExitModeSelection = 'default') {
  const [exitMode, setExitMode] = useState<ExitModeSelection>(defaultMode);
  return { exitMode, setExitMode };
}
```

```tsx
// static/react-app/app/src/components/execution/ExitModeField.tsx
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import type { ExitModeSelection } from '@/lib/manualExecuteHelpers';

export interface ExitModeFieldProps {
  exitMode: ExitModeSelection;
  onExitModeChange: (mode: ExitModeSelection) => void;
  compact?: boolean;
  className?: string;
}

const OPTIONS: { value: ExitModeSelection; label: string; hint: string }[] = [
  { value: 'default', label: 'Use default', hint: 'Resolve from the group / global setting.' },
  { value: 'traditional_static', label: 'Traditional (static)', hint: 'Fixed broker SL + TP. No trail.' },
  { value: 'adaptive_trail', label: 'Adaptive trail', hint: 'Chandelier trail + profit-protect.' },
  { value: 'manual', label: 'Manual', hint: 'Uses the SL/TP you entered. No clamp.' },
  { value: 'time_based', label: 'Time-based', hint: 'Closes after the group-configured bars.' },
];

export default function ExitModeField({
  exitMode,
  onExitModeChange,
  compact = false,
  className = '',
}: ExitModeFieldProps) {
  return (
    <div className={`space-y-2 ${className}`}>
      <Label className="text-[10px] uppercase text-muted-foreground tracking-wide">
        Exit strategy
      </Label>
      <RadioGroup
        value={exitMode}
        onValueChange={(v) => onExitModeChange(v as ExitModeSelection)}
        className="grid gap-1.5"
      >
        {OPTIONS.map((opt) => (
          <div
            key={opt.value}
            className="flex items-start gap-2 rounded-md border border-border/50 px-2 py-1.5"
          >
            <RadioGroupItem value={opt.value} id={`exit-${opt.value}`} className="mt-0.5" />
            <Label
              htmlFor={`exit-${opt.value}`}
              className="text-xs font-normal leading-snug cursor-pointer"
            >
              {opt.label}
              {!compact && (
                <span className="block text-[10px] text-muted-foreground">{opt.hint}</span>
              )}
            </Label>
          </div>
        ))}
      </RadioGroup>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix static/react-app/app run test -- --run ExitModeField`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add static/react-app/app/src/hooks/useExitModeState.ts static/react-app/app/src/components/execution/ExitModeField.tsx static/react-app/app/src/components/execution/ExitModeField.test.tsx
git commit -m "feat(exit_mode): ExitModeField per-trade selector + useExitModeState hook" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire `ExitModeField` into the Engine-A execute surfaces

**Files:**
- Modify: `static/react-app/app/src/components/panels/SignalsPanel.tsx`
- Modify: `static/react-app/app/src/components/panels/TVChartPanel.tsx`

No new tests (covered by Task 2/3 unit tests + the build/typecheck in Task 6). This is wiring: render the field next to each existing `VolumeModeField`, hold the state via `useExitModeState`, and pass `exitMode` into `buildQuickExecutePayload`.

- [ ] **Step 1: SignalsPanel — imports + state**

After the existing `useExecutionVolumeState` import (`:36-38`) add:
```tsx
import ExitModeField from '@/components/execution/ExitModeField';
import { useExitModeState } from '@/hooks/useExitModeState';
```
After the `useExecutionVolumeState('min_lot')` destructure (`:302-307`) add:
```tsx
  const { exitMode, setExitMode } = useExitModeState('default');
```

- [ ] **Step 2: SignalsPanel — thread into the payload**

In `onConfirmExecute`, add `exitMode` to the `buildQuickExecutePayload({ ... })` call (after `sizingOverride,` at `:624`):
```tsx
      sizingOverride,
      exitMode,
```
Add `exitMode` to that `useCallback`'s deps (`:649`): `}, [confirmRow, style, pendingStyle, volumeMode, sizingOverride, exitMode, showToast]);`

- [ ] **Step 3: SignalsPanel — render the field**

Immediately after **each** `<VolumeModeField ... />` block (the per-style execute toolbar at `:904-910` and the confirm surface at `:1243`), add:
```tsx
                        <ExitModeField
                          compact
                          exitMode={exitMode}
                          onExitModeChange={setExitMode}
                        />
```
(Match the surrounding indentation at each site.)

- [ ] **Step 4: TVChartPanel — same wiring**

Read `TVChartPanel.tsx`; locate its `useExecutionVolumeState` destructure, its `<VolumeModeField>` render site(s), and its `buildQuickExecutePayload({ ... })` call. Apply the identical four edits: import `ExitModeField` + `useExitModeState`; add `const { exitMode, setExitMode } = useExitModeState('default');`; render `<ExitModeField compact exitMode={exitMode} onExitModeChange={setExitMode} />` next to each `VolumeModeField`; add `exitMode,` to the `buildQuickExecutePayload` args and to the enclosing callback deps.

- [ ] **Step 5: Typecheck + commit**

Run: `npm --prefix static/react-app/app run build`
Expected: build succeeds (no TS errors).

```bash
git add static/react-app/app/src/components/panels/SignalsPanel.tsx static/react-app/app/src/components/panels/TVChartPanel.tsx
git commit -m "feat(exit_mode): per-trade exit selector on Signals + TV Chart execute surfaces" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: New "Exit Strategy" tab + `ExitStrategyPanel`

**Files:**
- Modify: `static/react-app/app/src/types/index.ts:174-193` (PanelId union)
- Modify: `static/react-app/app/src/pages/Home.tsx` (import + panels map)
- Modify: `static/react-app/app/src/components/layout/Sidebar.tsx` (icon import + navItems)
- Create: `static/react-app/app/src/components/panels/ExitStrategyPanel.tsx`

- [ ] **Step 1: Register the panel id**

`types/index.ts` — add to the `PanelId` union (after `| 'guardian'` at `:191`):
```ts
  | 'exitStrategy'
```

`Home.tsx` — add the import (after the `GuardianPanel` import, `:19`):
```tsx
import ExitStrategyPanel from '@/components/panels/ExitStrategyPanel';
```
and the panels-map entry (after `guardian: GuardianPanel,` at `:40`):
```tsx
  exitStrategy: ExitStrategyPanel,
```

`Sidebar.tsx` — add `LogOut` to the lucide import (`:14-17`) and a nav item after the `guardian` row (`:37`):
```tsx
  { id: 'exitStrategy', label: 'Exit Strategy', icon: LogOut },
```

- [ ] **Step 2: Create the panel**

```tsx
// static/react-app/app/src/components/panels/ExitStrategyPanel.tsx
import { useCallback, useEffect, useState } from 'react';
import apiClient from '@/lib/apiClient';
import { useStore } from '@/hooks/useStore';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

interface ExitModeConfig {
  globalDefault: string;
  byScoreGroup: Record<string, string>;
  advisablePipByScoreGroup: Record<string, { min_pip?: number; max_pip?: number }>;
  knownScoreGroups: string[];
  validModes: string[];
}

const GROUP_SENTINEL = 'default'; // per-group "use global" row choice

export default function ExitStrategyPanel() {
  const { showToast } = useStore();
  const [cfg, setCfg] = useState<ExitModeConfig | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setCfg(await apiClient.get<ExitModeConfig>('/api/exit-mode-config'));
    } catch (err) {
      showToast(`Failed to load exit config: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    }
  }, [showToast]);

  useEffect(() => { void load(); }, [load]);

  const setGroupMode = (group: string, mode: string) => {
    setCfg((c) => {
      if (!c) return c;
      const next = { ...c.byScoreGroup };
      if (mode === GROUP_SENTINEL) delete next[group];
      else next[group] = mode;
      return { ...c, byScoreGroup: next };
    });
  };

  const setGroupPip = (group: string, bound: 'min_pip' | 'max_pip', raw: string) => {
    setCfg((c) => {
      if (!c) return c;
      const next = { ...c.advisablePipByScoreGroup };
      const entry = { ...(next[group] || {}) };
      const v = parseFloat(raw);
      if (!raw || !Number.isFinite(v) || v <= 0) delete entry[bound];
      else entry[bound] = v;
      if (Object.keys(entry).length === 0) delete next[group];
      else next[group] = entry;
      return { ...c, advisablePipByScoreGroup: next };
    });
  };

  const save = useCallback(async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      const res = await apiClient.post<{ success?: boolean; errors?: string[] }>(
        '/api/exit-mode-config',
        {
          globalDefault: cfg.globalDefault,
          byScoreGroup: cfg.byScoreGroup,
          advisablePipByScoreGroup: cfg.advisablePipByScoreGroup,
        },
      );
      if (res.success) {
        showToast('Exit strategy saved', 'success');
        await load();
      } else {
        showToast(`Save rejected: ${(res.errors || ['unknown']).join('; ')}`, 'error');
      }
    } catch (err) {
      showToast(`Save failed: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    } finally {
      setSaving(false);
    }
  }, [cfg, load, showToast]);

  if (!cfg) {
    return <div className="p-6 text-sm text-muted-foreground">Loading exit strategy…</div>;
  }

  return (
    <div className="p-4 space-y-4 max-w-4xl">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Engine A — Exit Strategy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Label className="text-xs w-40">Global default mode</Label>
            <Select
              value={cfg.globalDefault}
              onValueChange={(v) => setCfg((c) => (c ? { ...c, globalDefault: v } : c))}
            >
              <SelectTrigger className="h-8 w-[200px] text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {cfg.validModes.map((m) => (
                  <SelectItem key={m} value={m} className="text-xs">{m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="border-t border-border/40 pt-3">
            <div className="grid grid-cols-[1fr_180px_90px_90px] gap-2 text-[10px] uppercase text-muted-foreground pb-1">
              <span>Score group</span><span>Default mode</span><span>Min pip</span><span>Max pip</span>
            </div>
            <div className="space-y-1 max-h-[60vh] overflow-auto">
              {cfg.knownScoreGroups.map((g) => {
                const band = cfg.advisablePipByScoreGroup[g] || {};
                return (
                  <div key={g} className="grid grid-cols-[1fr_180px_90px_90px] gap-2 items-center">
                    <span className="text-xs font-mono">{g}</span>
                    <Select
                      value={cfg.byScoreGroup[g] ?? GROUP_SENTINEL}
                      onValueChange={(v) => setGroupMode(g, v)}
                    >
                      <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={GROUP_SENTINEL} className="text-xs">(use global)</SelectItem>
                        {cfg.validModes.map((m) => (
                          <SelectItem key={m} value={m} className="text-xs">{m}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Input
                      type="number" className="h-7 text-xs" placeholder="—"
                      value={band.min_pip ?? ''}
                      onChange={(e) => setGroupPip(g, 'min_pip', e.target.value)}
                    />
                    <Input
                      type="number" className="h-7 text-xs" placeholder="—"
                      value={band.max_pip ?? ''}
                      onChange={(e) => setGroupPip(g, 'max_pip', e.target.value)}
                    />
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex justify-end">
            <Button size="sm" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save exit strategy'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck/build + commit**

Run: `npm --prefix static/react-app/app run build`
Expected: build succeeds.

```bash
git add static/react-app/app/src/types/index.ts static/react-app/app/src/pages/Home.tsx static/react-app/app/src/components/layout/Sidebar.tsx static/react-app/app/src/components/panels/ExitStrategyPanel.tsx
git commit -m "feat(exit_mode): Exit Strategy tab for per-group default mode + advisable-pip band" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Final verification (build + lint + targeted tests)

**Files:** none (verification only).

- [ ] **Step 1: Backend targeted tests**

Run: `python -m pytest tests/test_exit_mode_config_api.py tests/test_exit_mode_config.py -q`
Expected: PASS (existing config test + the 5 new ones).

- [ ] **Step 2: Frontend unit tests**

Run: `npm --prefix static/react-app/app run test -- --run manualExecuteHelpers.exitMode ExitModeField`
Expected: PASS.

- [ ] **Step 3: Lint + production build**

Run: `npm --prefix static/react-app/app run lint`
Run: `npm --prefix static/react-app/app run build`
Expected: lint clean (no new errors), build emits the bundle. Confirm the built bundle is the served one (Plan-history note: a prior commit `ab9d769f` had to rebuild the React bundle so UI changes were served — verify the build output lands where Flask serves `static/`).

- [ ] **Step 4: Confirm wiring end-to-end (read-only)**

Verify by inspection (no live trade): GET `/api/exit-mode-config` returns the four-key snapshot; the Exit Strategy tab renders one row per known group; selecting a per-trade mode on Signals places `exit_mode` inside the `signal` of the `/api/quick-execute` payload (the field `exit_mode_apply.apply_engine_a_exit_mode` reads). No execution-path code changed.

---

## Self-Review

**Spec coverage (Frontend section, lines 122–130):**
- "New Exit Strategy tab … one row per Engine-A asset group → default-mode dropdown + advisable-pip min/max inputs. Persists via the existing config/settings API." → Task 5 panel + Task 1 route. ✅
- "Per-trade `ExitModeField` mirroring `VolumeModeField.tsx` (+ a `useExitModeState` hook mirroring `useExecutionVolumeState.ts`), shown only on Engine-A execute surfaces." → Tasks 2–4. ✅
- "`time_based` reveals a bars input; `manual` reveals the existing SL/TP inputs." → **Scoped down** (documented above): per-trade bars would need an audit-gated `timed_exit_monitor.py` change; v1 shows helper text and reuses existing manual SL/TP inputs. The bars value is editable per-group in the tab is **also** scoped out (block-style YAML); the monitor reads seeded defaults. Recorded as a deliberate v1 limit, not silent.

**Placeholder scan:** No TBD/TODO. Task 4 Step 4 (TVChartPanel) is described as "read the file, apply the identical four edits" rather than pre-baked line numbers because TVChartPanel's exact lines weren't pre-read; the four edits are fully specified and mirror the SignalsPanel steps above them.

**Type consistency:** `ExitModeSelection = ExitMode | 'default'` is defined in `manualExecuteHelpers.ts` (Task 2) and imported by `useExitModeState` (Task 3), `ExitModeField` (Task 3), and the panels (Task 4). `buildExitModePayload({ exitMode })` and `buildQuickExecutePayload({ ..., exitMode })` use the same prop name `exitMode`. Backend: `validate_exit_mode_updates(d, known_groups)` and `persist_exit_mode_config_yaml(cfg_path, current)` signatures match their tests and the route call. The route's GET keys (`globalDefault`, `byScoreGroup`, `advisablePipByScoreGroup`, `knownScoreGroups`, `validModes`) match `ExitModeConfig` in the panel.

**Governance:** No threshold/floor/DI change; no gate touched; AI not involved. The `traditional_static` default already shipped in Plan 2 — this plan only exposes editing it. Engine-A scoped throughout (`engine_b`-only executes strip `exit_mode`).
