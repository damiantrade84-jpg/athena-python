# Engine A Audit Findings — Verification Report

**Date:** 2026-06-07  
**Scope:** Submitted Engine A calibration/feed audit vs current source  
**Method:** Line-by-line verification of `factor_scoring.py`, `config.py`, `config.yaml`, `cot_feed.py`, `carry_feed.py`, tests  
**Prior pass:** [`engine-a-factor-scoring-audit-verified-2026-06-01.md`](engine-a-factor-scoring-audit-verified-2026-06-01.md)

---

## Overall verdict

The submitted audit correctly identifies calibration **gaps** and feed **limitations**, but several claims are **stale** (ignore populated `config.yaml`), **overstated** (ADX/COT), or **misclassified as bugs** (forex regime floor). Severity counts below reflect verified production behavior (yaml loaded).

**Revised counts:** 0 critical, ~3 high, ~4 medium, ~8 low — not 1/4/6/3.

---

## Verdict summary

| # | Submitted | Verified | Adjusted severity |
|---|-----------|----------|-------------------|
| 1 | CRITICAL | **Confirmed** (code default False; yaml `true`; no startup gate) | **High** |
| 2 | HIGH | **Partially confirmed** (asset_type fallback, not 25+ hardcoded) | **Medium** |
| 3 | HIGH | **Partially confirmed** (Nat Gas claim wrong; softs/alt gaps real) | **Medium–High** |
| 4 | HIGH | **Confirmed** | **High** |
| 5 | HIGH | **Rejected as bug** — intentional + tested | **Low** (design tradeoff) |
| 6 | MEDIUM | **Rejected for production** — yaml populated | **Low** |
| 7 | MEDIUM | **Rejected** — `RSI_BOUNDS` in yaml + `config.py` | **Low** |
| 8 | MEDIUM | **Partially confirmed** — no asset gate; disabled in prod | **Low** |
| 9 | MEDIUM | **Confirmed** | **Medium** |
| 10 | MEDIUM | **Partially confirmed** — clamps to 0.25 combo cap | **Low** |
| 11 | MEDIUM | **Confirmed** | **Medium** |
| 12 | LOW | **Confirmed** mismatch | **Low** |
| 13 | LOW | **Confirmed** | **Low** |
| 14 | LOW | **Confirmed** | **Low** |
| 15 | LOW | **Confirmed** | **Low** |

---

## Code default vs production config

Many findings inspect `config.py` defaults only. Production loads [`config.yaml`](../config.yaml) overrides at boot.

| Key | `config.py` default | `config.yaml` production | Live behavior |
|-----|---------------------|--------------------------|---------------|
| `ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED` | `False` | `true` | Per-group tuning **on** |
| `ENGINE_A_FACTOR_WEIGHTS_BY_CLASS` | `{}` | Populated | Per-class weights **active** |
| `RSI_BOUNDS` | 5 asset_types | + score_group overrides | Differentiated bounds |
| `FACTOR_FOREX_SESSION_MULT.ENABLED` | `False` | `false` | Disabled |
| `ENGINE_A_VWAP_FILTER.ENABLED` | (absent) | `false` | Disabled |
| `FACTOR_FUNDING_USE_ZSCORE` | `False` | (was absent; now enabled) | See implementation |

---

## Finding details (corrections to submitted audit)

### 1 — Score-group adjustments gate

**Confirmed.** Fail-closed code default; yaml enables production tuning. `validate_config` did not warn when flag is `False` — fixed in this pass.

### 2 — ADX thresholds

**Partially confirmed — overstated.**

- **Wrong:** Nat gas missing (`nat_gas: 27` / `12` in yaml).
- **Wrong:** 25+ groups hit hardcoded `30/10` — most inherit `commodity`/`index`/`stock` via `_resolve_class_keyed`.
- **Real gap:** No per-score_group ADX for `precious_trackers`, `energy_oil`, `copper`, etc. — calibration limitation, not silent abort.

### 3 — COT coverage

**Partially confirmed.**

- **Wrong:** Nat Gas has `"Nat Gas": [(1.0, "NG")]`.
- **Confirmed:** Altcoins use `[]`; softs lacked formulas — fixed in this pass.
- **Confirmed:** `_cot_coverage` not propagated to `feed_status` — fixed in this pass.

### 4 — Carry staleness

**Confirmed.** Static rates dated 2026-06-07; FRED TTL daily; no expiry alert — fixed in this pass.

### 5 — Forex regime floor

**Rejected as bug.** Forex deliberately excluded from `ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES` for score reachability (`config.yaml:516-526`, `test_forex_keeps_flat_floor_in_ranging`). Higher floor in `RANGING` is **by design**, not inverted logic.

### 6 — Factor weights empty

**Rejected for production.** Yaml has explicit entries; `config.py` `{}` only applies when yaml missing or gate off.

### 7 — RSI bounds universal 70/30

**Rejected.** Both `config.py` and `config.yaml` define differentiated bounds (crypto 80/20, commodity 75/25, score-group overrides). Forex 70/30 is documented intent, not a gap.

### 8–15

See plan verification report for VWAP, funding z-score, research cap, DI thresholds, session mult, stoch RSI, mean reversion, volume thresholds — statuses unchanged from verification pass.

---

## Data feed table (corrected)

| Feed | Submitted claim | Verified |
|------|-----------------|----------|
| COT | No Nat Gas formula | **Wrong** — NG mapped |
| COT | Softs missing | **Confirmed** — fixed |
| COT | Coverage not in feed_status | **Confirmed** — fixed |
| Carry | Stale static + monthly lag | **Confirmed** — alerting added |
| Funding | Fixed 1bp band, z-score off | **Confirmed** — z-score enabled |

---

## Implementation from this verification (2026-06-07)

1. `CARRY_STATIC_RATES_AS_OF` + staleness warning in `carry_feed.py`
2. `validate_config` warning when `ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED` is `False`
3. Softs COT formulas + `feed_status["cot_coverage"]`
4. `FACTOR_FUNDING_USE_ZSCORE` + per-pair rolling stats in `_asset_addon`

---

## Not verified

- Live FRED fetch latency at runtime
- Full pair→score_group matrix for all instruments
- Orthogonal vote path with `ENGINE_A_ORTHO_VOTE_ENABLED: true`
