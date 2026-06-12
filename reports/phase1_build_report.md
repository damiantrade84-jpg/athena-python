# ASE Phase 1 build report

Generated after Phase 0 close-out + Phase 1 implementation pass.

## 1. Files created / modified (line counts)

| Lines | Path |
|------:|------|
| 391 | `athena_ase/data/ptis.py` — append_rows fix, `load_window` |
| 188 | `athena_ase/data/availability.py` |
| 129 | `athena_ase/signals/common.py` |
| 120 | `athena_ase/signals/arbitrate.py` |
| 119 | `athena_ase/signals/carry.py` |
| 109 | `athena_ase/signals/engine.py` |
| 200 | `athena_research/ase/event_backtest.py` |
| 99 | `athena_ase/signals/xsec.py` |
| 92 | `athena_research/ase/reports.py` |
| 50 | `athena_ase/signals/tsmom.py` |
| 46 | `athena_ase/signals/meanrev.py` |
| 42 | `athena_ase/horizon.py` |
| 43 | `athena_ase/instruments.py` |
| 40 | `athena_research/ase/run_phase1.py` |
| 55 | `athena_research/ase/trials_registry.py` |
| 14 | `athena_ase/features/build.py` — `COT_MIN_WEEKS` |
| + | `tests/test_ptis_window.py`, `test_signals_*.py`, `test_arbitration.py`, `test_event_backtest.py`, `test_signal_causality.py`, `test_ase_import_graph.py` |
| + | `tests/test_ptis_ingest.py` — append_rows + auto_register cases |

## 2. Test results (each file run individually)

| Test file | Result |
|-----------|--------|
| `tests/test_ptis_ingest.py` | **7 passed** |
| `tests/test_ptis_window.py` | **3 passed** |
| `tests/test_ptis_availability.py` | **11 passed** |
| `tests/test_feature_causality.py` | **2 passed** |
| `tests/test_signals_tsmom.py` | **1 passed** |
| `tests/test_signals_carry.py` | **2 passed** |
| `tests/test_signals_xsec.py` | **2 passed** |
| `tests/test_signals_meanrev.py` | **2 passed** |
| `tests/test_arbitration.py` | **4 passed** |
| `tests/test_event_backtest.py` | **2 passed** |
| `tests/test_signal_causality.py` | **1 passed** |
| `tests/test_ase_import_graph.py` | **1 passed** |

Command: `pytest tests/<file>.py -q --basetemp=tests/.tmp/pytest_basetemp`

## 3. Phase 1 exit gate (`reports/phase1_layer1_report.md`)

Probe run against `tests/.tmp/ptis_live_probe` (limited EODHD depth ~100 bars/series):

| family / horizon | candidates | mean net_R | gate |
|------------------|------------|------------|------|
| (none) | 0 | n/a | **FAIL** |

**Status:** awaiting full PTIS history run — current ingest depth is below TSMOM warm-up (168 H1 / 252 D1 bars) for default universe, so zero candidates is expected until T0.2 ingest is deepened and `run_phase1` is re-run against production PTIS.

Re-run after ingest:

```powershell
py ase_cli.py ingest --sources eodhd,dukascopy,cot,fred,bybit
py -m athena_research.ase.run_phase1
```

## 4. Spec deviations (explicit)

| Item | Deviation | Reason |
|------|-----------|--------|
| Default instrument universe | Only 10 instruments in `athena_ase/instruments.py` | Phase 1 scaffold; xsec requires ≥10 same-family names — expand before swing equity/crypto xsec production runs |
| Commodity carry | Always NONE | Open decision #3 default (v2.1) |
| JSE intraday | Skipped via `swing_only` flag pattern (no JSE in default list yet) | Open decision #2 default |
| `load_window` availability | Returns all rows in window; callers mask `available_time <= decision_time` | Per work order A2 — backtester needs per-bar masks |
| Phase 1 probe gate | FAIL on shallow PTIS | Not a code defect — insufficient bar history in probe store |

No silent deviations: no legacy Engine A imports, no EMA/RSI/ADX/VWAP/ATR scoring, no executor/risk_engine wiring.
