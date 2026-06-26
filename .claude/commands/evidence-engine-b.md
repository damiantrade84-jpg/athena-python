---
description: Single-pass Engine B evidence report vs Engine A standard (n≥30, SQN>2.0). Live + frozen backtest. Measurement only — no config or execution changes.
argument-hint: "[optional --days N for live window]"
---

# /evidence-engine-b — Hold Engine B to the Engine A evidence standard

Single-pass evidence report. Goal: produce n, SQN, win%, avg_R for Engine B
per pair (and score_group × regime), from BOTH live closed trades and a
frozen-snapshot backtest, judged against the same bar Engine A faces:
n ≥ 30 and SQN > 2.0. Output is a side-by-side A vs B verdict table.

This is measurement, not a trial to acquit. "Felt reliable for a few days"
is the exact failure mode this report exists to replace.

**Arguments:** `$ARGUMENTS` — optional live window override (e.g. `--days 90`).
When omitted, run default window first, then `--days` matching however long
Engine B has been firing live.

## Known facts — do NOT re-derive (verified 2026-06-11)

- Live trades carry `engine` = `engine_b` in `learning_log`
  (normalization: `regime_performance_report.py:47`,
  auto_trader engine handling: `auto_trader.py:255+`).
- `regime_performance_report.py` already supports
  `--engine engine_b`, reads the audit DB READ-ONLY, and enforces a hard
  30-trade sufficiency floor (`MIN_TRADES_PER_CELL = 30`, line 20; line 228
  clamps any lower request back up to 30). Do not attempt to lower it.
- The report outputs win% / avg_R / med_R / tot_R but NOT SQN. SQN must be
  computed from the same `learning_log` rows:
  SQN = mean(r_multiple) / stdev(r_multiple) × sqrt(n), per pair and overall.
- Engine B has full backtest support in `backtest_runner.py` (engine_b paths
  at lines 80-82, 406, 740+). Frozen snapshot `BACKTEST_DATA_AS_OF=2026-05-30`
  is verified working; pin it so A and B are compared on identical data.
- Known telemetry gap from the 2026-06-11 funnel report: one Engine B logging
  path emits `pair: null` (symbol carried in another field). Affects
  calibration events; whether it leaks into `learning_log` is NOT REVIEWED —
  see precheck below.
- Engine A comparators already on file: AB6 FX majors n=192, SQN −3.7,
  26% win, avg −0.344R (`tmp/diag_majors_summary.json`); XAU/USD SQN +1.42.
  AB7 sweep pending.

## Evidence standard (shared with Engine A)

| Criterion | Value | Config anchor |
|-----------|-------|---------------|
| Minimum n | ≥ 30 | `ENGINE_A_TRADE_EVIDENCE_MIN_N: 30` (`config.yaml:532`); `MIN_TRADES_PER_CELL = 30` (`regime_performance_report.py:20`) |
| Minimum SQN | > 2.0 | `ENGINE_A_TRADE_EVIDENCE_MIN_SQN: 2.0` (`config.yaml:533`) |

SQN formula (apply per pair, per cell, and overall):

```
SQN = mean(r_multiple) / stdev(r_multiple) * sqrt(n)
```

Use population stdev when n ≥ 2; when stdev = 0, SQN is undefined (report as
N/A, not a pass).

## Source reference map (file:line anchors)

| Leg | What | Source anchor |
|-----|------|---------------|
| Live read | Closed trades from `learning_log` | `regime_performance_report.py:118-165` (`load_closed_trades`); READ-ONLY connect `106-115` |
| Live CLI | Regime-bucketed report | `regime_performance_report.py:428-469` (`main`); run `python regime_performance_report.py --engine engine_b` |
| Live SQN | Not in report — compute separately | Same rows as above; save script to `tmp/evidence_engine_b_<date>.py` + output `.json` |
| Live ingest | `learning_log` pair from `audit_log` | `ai_learning.py:117-157` (`pair = row["pair"] or ""`; schema `pair TEXT NOT NULL` at line 25) |
| Backtest | Engine B backtest path | `backtest_runner.py:80-82`, `406`, `740+`, `4892+`, `5746` (`engine="engine_b"`) |
| Frozen data | Pin identical A/B snapshot | `BACKTEST_DATA_AS_OF=2026-05-30`; manifest `tmp/ab6_manifest_2026-05-30.txt` |
| Engine A comparators | AB6 frozen backtest aggregates | `tmp/diag_majors_summary.json`; funnel probe `tmp/verify_gate_funnel_probe.json` |
| `pair: null` (calibration) | Engine B calibration row builder | `calibration_diagnostics.py:231` (`pair` from ctx/conf — null when both missing); consumer example `tmp/diag_engine_a_funnel_20260611.py:80` |
| Engine B scoring | Calibration diagnostic write site | `market_structure.py:1142-1157` (`engine_b_confidence_passes` → `build_engine_b_calibration_row`) |

**Precheck expectation for `pair: null`:** `learning_log.pair` is NOT NULL in
schema (`ai_learning.py:25`). Null-pair rows in funnel reports come from
`calibration_events.jsonl`, not closed-trade evidence. Verify with a read-only
count on `learning_log` where `engine = engine_b` AND (`pair IS NULL OR pair = ''`).

## Procedure

1. **Data-quality precheck (live).** Read-only count of `learning_log` rows
   where engine = engine_b: total, closed (r_multiple not null), null/blank
   pair, date range covered. If null-pair rows exist, report the count and
   exclude them from per-pair cells (keep them in the overall aggregate).
   If closed engine_b rows < 30 total, say so plainly — the live verdict is
   then INSUFFICIENT EVIDENCE, full stop, and only the backtest leg proceeds.

   ```text
   # Read-only URI pattern (match regime_performance_report.py:106-115)
   sqlite3 "file:<repo>/audit.db?mode=ro"
   ```

2. **Live evidence.** Run `regime_performance_report.py --engine engine_b`
   (default window, then `--days` matching however long B has been firing).
   Then compute SQN per pair and overall from the same rows via a small
   read-only script saved to `tmp/` (pattern:
   `tmp/evidence_engine_b_<date>.py` + `.json`, rerunnable).

   ```bash
   python regime_performance_report.py --engine engine_b
   python regime_performance_report.py --engine engine_b --days 90 --markdown
   python tmp/evidence_engine_b_<YYYYMMDD>.py
   ```

3. **Backtest evidence.** Frozen-snapshot Engine B backtest
   (`BACKTEST_DATA_AS_OF=2026-05-30`) over the same pair universe and window
   scope as the Engine A AB7 sweep. Collect n and SQN per pair. Reuse any
   existing AB7-aligned Engine B backtest artifacts in `tmp/` before
   launching new runs.

   **Reuse before new runs:**
   - `tmp/verify_gate_funnel_probe.json` — per-pair backtest funnel counts
   - `tmp/ab6_manifest_2026-05-30.json` — frozen manifest
   - `tmp/diag_majors_summary.json` — Engine A AB6 pair-level SQN (comparator)
   - Any `tmp/*engine_b*` or AB7-aligned JSON from prior sweeps

   Launch the B sweep **once, full universe** — not pair-by-pair iterations.

4. **Verdict table.** One table, both engines, both evidence sources:

   `pair | engine | source (live/backtest) | n | SQN | win% | avg_R | clears n≥30? | clears SQN>2.0?`

   Mark each cell VERIFIED / INSUFFICIENT (n<30) / NOT RUN.

5. **Findings block.**
   - Which Engine B pairs/classes clear the bar on backtest evidence; which
     clear on live evidence; where live and backtest disagree (flag
     disagreement as SUSPECT — possible live/backtest parity issue, name the
     surface).
   - Explicit statement: is Engine B currently executing live on any
     pair/class whose evidence cell is INSUFFICIENT or failing? List them.
     Report only — enabling/disabling execution is a user decision.
   - Close out the `pair: null` question: which logging path, file:line,
     and whether it touches `learning_log`.

## Boundaries

- Audit DB opened READ-ONLY only. No writes to `learning_log`.
- No config edits, no threshold changes, no enabling/disabling any engine,
  no changes to the 30-trade floor or evidence-gate params.
- Diagnosis/measurement only; fixes (e.g. the null-pair logging path) are a
  separate user-approved session.

## Output contract

Deliver one final report (no incremental narration):

1. **Coverage map** — live date range, backtest snapshot date, DB path,
   telemetry files read, pair universe.
2. **Live precheck** — row counts, null-pair count, INSUFFICIENT EVIDENCE
   verdict if n < 30 closed.
3. **Verdict table** — A vs B, live vs backtest, per pair (and score_group ×
   regime where cells have n ≥ 30).
4. **Findings** — pairs/classes clearing bar; live/backtest disagreements
   (SUSPECT); live execution vs evidence gaps; `pair: null` resolution.
5. **Handoff block** — artifacts written (`tmp/evidence_engine_b_*`), exact
   rerun commands, open follow-ups (e.g. AB7 sweep, null-pair fix session).

## Budget

- Batched reads; reuse `tmp/` artifacts before generating new runs.
- Backtest runs are the expensive step: launch the B sweep once, full
  universe, rather than pair-by-pair iterations.
- Zero pytest runs.
- One findings report; end with the standard handoff block (artifacts
  written, rerun command, open follow-ups).
