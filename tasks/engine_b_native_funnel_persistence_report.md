# Engine B Native Funnel Persistence Report

**Scope:** Diagnostics-only persistence and summarization for **`engine_b_scan_gate_funnel`** on full-scan payloads.  
**Date:** 2026-05-14  

## Summary

Native funnel rows and histograms are persisted under **`logs/engine_b_gate_funnel/`** after each successful **`run_full_scan()`**, when **`ENGINE_B_SCAN_GATE_FUNNEL_ENABLED`** is true and persistence is enabled (default **on**). Writes are **fail-open** (warnings only). **`POST /api/scan`** merges persistence metadata into the JSON response.

No trading logic, thresholds, gates, execution, classification, or AI code paths were intentionally changed beyond (1) optional diagnostic field **`engine_b_confidence_passed`** on the funnel dict and (2) file I/O + response metadata.

## Files Changed / Added

| Path | Role |
|------|------|
| **`athena_app/diagnostics/__init__.py`** | Package marker |
| **`athena_app/diagnostics/engine_b_gate_funnel_persist.py`** | Flatten rows, **`summarize_funnel_rows`**, **`save_engine_b_scan_gate_funnel`**, **`maybe_persist_engine_b_scan_gate_funnel`**, **`regenerate_funnel_artifacts_from_scan_file`** |
| **`tools/summarize_engine_b_gate_funnel.py`** | CLI to rebuild JSONL + summary from **`latest_full_scan.json`** |
| **`scanner.py`** | After correlation cap, merges **`maybe_persist_engine_b_scan_gate_funnel`** into scan result; **`engine_b_confidence_passed`** copied into funnel attach |
| **`config.py`** | **`ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED`**, **`ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR`** |
| **`tests/test_engine_b_gate_funnel_persist.py`** | Persist + summary tests |
| **`tests/test_engine_b_full_scan_audit.py`** | Funnel shape includes **`engine_b_confidence_passed`** |
| **`tasks/engine_b_funnel_output_analysis.md`** | Note added: persistence + paths |

## Where Persistence Is Wired

1. **`scanner.run_full_scan()`** builds **`_scan_out`** (same keys as before).
2. **`maybe_persist_engine_b_scan_gate_funnel(_scan_out, pair_types_by_display=…)`** runs in a **non-fatal** try/except.
3. Display → asset-type map is derived from **`candidate_pairs`** for skipped rows without **`type`** on the skip dict.
4. **`athena.py`** **`POST /api/scan`** receives the merged dict via **`handle_scan_request`** → **`run_full_scan`** (unchanged route contract except new top-level metadata keys).

## Config and Environment

Relative ``logs/...`` defaults are resolved from the **Athena repository root** (the directory that contains ``athena_app``), not from ``os.getcwd()`` , so artifacts appear under ``<repo>/logs/engine_b_gate_funnel`` even when the app is launched with a different working directory. Absolute ``ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR`` values are unchanged.

| Key / env | Meaning |
|-----------|---------|
| **`ENGINE_B_SCAN_GATE_FUNNEL_ENABLED`** | Must be **true** for attach + persist wiring to run (existing). |
| **`ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED`** | Default **true**; set **false** to skip disk writes while keeping funnel on signals. |
| **`ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR`** | Default **`logs/engine_b_gate_funnel`**. |
| **`ENGINE_B_SCAN_GATE_FUNNEL_PERSIST=0`** | Force-disable persistence. |
| **`ENGINE_B_SCAN_GATE_FUNNEL_PERSIST=1`** | Force-enable persistence (overrides CONFIG **false**). |

## Output Files (per successful save)

| File | Content |
|------|---------|
| **`latest_full_scan.json`** | Latest full **`run_full_scan`** payload (same shape as API body). |
| **`full_scan_YYYYMMDD_HHMMSS.json`** | Timestamped snapshot of the same payload. |
| **`latest_funnel_rows.jsonl`** | One JSON object per line: flattened funnel row or minimal **`has_funnel:false`** stub. |
| **`latest_funnel_summary.json`** | **`overall`**, **`by_asset_type`**, **`crypto_section`** (A–H), **`forex_section`** (A–H), **`top_blockers`**. |

Each successful full scan also writes **`scan_funnel_touch.json`** (small diagnostic payload: resolved output dir, config flags, persist outcome). That runs even when full JSON persistence is skipped, so **`logs/engine_b_gate_funnel/`** should exist after a completed scan that reached the end of **`run_full_scan`**.

## Summary Schema (high level)

- **`overall`:** `total_rows`, `rows_with_funnel`, rows by list source, **`sig_a_present_*`**, **`engine_b_evaluated_*`**, **`structure_executed_*`** (funnel rows only where fields exist).
- **`by_asset_type[asset]`:** `trade` / `watchlist` / `skipped` counts, blocker-style counts (`sig_a_missing`, `bybit_atr_unavailable`, `structure_not_executed`, `rr_failed`, `structural_tp_below_min_rr`, `final_pass`, etc.).
- **`crypto_section` / `forex_section`:** Keys **`A_*` … `H_*`** matching the analysis checklist (sig_a missing, B evaluated, structure executed, Bybit ATR, RR, space gate, watchlist rows, trade rows).
- **`top_blockers`:** ranked **`failed_gate_names`** and **`engine_b_error`** (tuples `[name, count]`).

## HTTP Response Additions

When persistence runs, the scan JSON may include:

- **`engine_b_scan_gate_funnel_saved`:** boolean  
- **`engine_b_scan_gate_funnel_summary_path`:** path to **`latest_funnel_summary.json`** (workspace-relative when under repo root), or **`null`** when skipped/failed  
- **`engine_b_scan_gate_funnel_output_dir`:** output directory  
- **`engine_b_scan_gate_funnel_saved_error`:** populated on write failure  
- **`engine_b_scan_gate_funnel_persist_skipped`:** reason when persist or funnel disabled  
- **`engine_b_scan_funnel_touch`:** result dict from the touch helper (`touch_path`, `wrote_touch_file`, `touch_error`)  

## How to Run a Scan and Capture Native Funnel

1. **`python athena.py`** then trigger **SCAN MODE**, or **`POST /api/scan`** with `{"style":"auto"}` (optional **`asset_class`**).
2. Confirm **`ENGINE_B_SCAN_GATE_FUNNEL_ENABLED`** (and persist flag) in effective config.
3. Read **`logs/engine_b_gate_funnel/latest_funnel_summary.json`** and **`latest_funnel_rows.jsonl`**.

**Re-summarize without re-scan:**

```text
python tools/summarize_engine_b_gate_funnel.py --input logs/engine_b_gate_funnel/latest_full_scan.json
```

(Optional **`--output-dir`** to override target directory.)

## Tests Run

```text
pytest tests/test_engine_b_gate_funnel_persist.py tests/test_engine_b_full_scan_audit.py -q
```

(15 passed at time of authoring.)

## Confirmation: No Trading Logic Change

- **`_classify_signal`**, **`_apply_engine_b_scan_gate`**, tiers, thresholds, execution, and **`calculate_confidence`** outcomes were not modified.
- **Added** mirroring field **`engine_b_confidence_passed`** onto the report-only funnel dict for histogram accuracy.
- Persistence failures do not alter scan results aside from optional metadata fields indicating save status.
