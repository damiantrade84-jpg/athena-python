# Engine B Funnel Output Analysis

**Mode:** Diagnostics analysis only — no code, config, or trading-logic changes.  
**Audit date:** 2026-05-14  

**Important (updated after native persistence):** Per-scan **`engine_b_scan_gate_funnel`** extracts are now written under **`logs/engine_b_gate_funnel/`** when **`ENGINE_B_SCAN_GATE_FUNNEL_ENABLED`** and **`ENGINE_B_SCAN_GATE_FUNNEL_PERSIST_ENABLED`** are on (see **`tasks/engine_b_native_funnel_persistence_report.md`**). The historical note below about *committed* repo snapshots without funnel files may still apply until you run a scan locally. **`logs/threshold_audit/signal_funnel.jsonl`** remains a **separate** threshold-audit pipeline — use only as proxy, not proof of the native funnel schema.

---

## Engine B Funnel Output Analysis

### 1. Data Source

#### Intended source: `engine_b_scan_gate_funnel`

| Check | Result |
|-------|--------|
| Grep **`logs/`** for **`engine_b_scan_gate_funnel`** | **No committed matches** in-repo; after a local full scan, see **`logs/engine_b_gate_funnel/latest_funnel_rows.jsonl`** |
| Produced in code | **`scanner.run_full_scan()`** → attaches dict to **`sig`**; **`_patch_engine_b_funnel_final_tier`** sets **`final_tier` / `final_reason`** |
| Persistence | **`athena.py`** **`_last_scan_results`** (memory) + optional disk under **`logs/engine_b_gate_funnel/`** ( **`latest_full_scan.json`**, **`latest_funnel_summary.json`**, JSONL rows) |

**Bottom line:** After a full scan with this code, native funnel files are written under **`<repo>/logs/engine_b_gate_funnel/`** (relative paths are tied to the checkout root, not the shell’s current directory). Check the scan JSON for **`engine_b_scan_gate_funnel_saved`** / **`engine_b_scan_gate_funnel_output_dir`** if a folder is still missing.

**Producing native funnel output (`ENGINE_B_SCAN_GATE_FUNNEL_ENABLED`):** Defaults in **`config.py`** already enable the funnel unless overridden in **`config.local.yaml`**. Use **`POST /api/scan`** and save the JSON; inspect **`tradeSignals`**, **`watchlist`**, and **`skipped`** for **`engine_b_scan_gate_funnel`** per row. **`ATHENA_THRESHOLD_AUDIT=1`** writes **`signal_funnel.jsonl`** — **different schema**, not the new funnel. Pairs without **`sig_a`** typically land in **`skipped`** with reason **"No data"** and often **no** funnel object.

#### Proxy: threshold audit (`signal_funnel.jsonl`)

| Aspect | Detail |
|--------|--------|
| Path | `logs/threshold_audit/signal_funnel.jsonl` |
| Enable | Env **`ATHENA_THRESHOLD_AUDIT=1`** and/or **`THRESHOLD_AUDIT.ENABLED`** in YAML |
| File size snapshot | **4134** lines (this workspace) |

**Dedupe applied for tables below:** Latest row **per symbol** by **timestamp** (max) — **116** symbols.

Other searches: **`audit.db` / SQLite** paths were not used here (no **`engine_b_scan_gate_funnel`** column contract); **`logs/crypto_engine_b_gate_calibration*.json`** is calibration tooling, **not** per-scan funnel.

---

### 2. Overall Funnel Counts

**Latest row per symbol** (threshold audit proxy):

| Metric | Count |
|--------|-------|
| Unique symbols (`symbol`) | **116** |
| **`scan_tier == trade`** | **34** |
| **`scan_tier == watchlist`** | **41** |
| **`scan_tier == skip`** | **41** |
| **`engine_a_passed == true`** | **54** |

**Raw line totals** (symbols repeat across scans—**do not** compare directly to dedup table):

| `asset_type` | Lines |
|--------------|-------|
| crypto | 1147 |
| forex | 796 |
| stock | 1051 |
| commodity | 755 |
| index | 385 |

| `scan_tier` | Lines |
|-------------|-------|
| skip | 1847 |
| trade | 1021 |
| watchlist | 1266 |

**Not available without saved funnel:** `sig_a_present`, `engine_b_evaluated`, `structure_executed`, `engine_b_skip_stage`, `score_group`-stratified tables, **`execution_level_reject_reason`**.

---

### 3. Crypto Funnel Counts

Latest row per **`asset_type=crypto`** symbol: **31** symbols.

| Metric | Count |
|--------|-------|
| **`scan_tier == trade`** | **11** |
| **`scan_tier == watchlist`** | **10** |
| **`scan_tier == skip`** | **10** |
| **`engine_b_structural_verdict == CLEAR`** | **31** |
| **`engine_b_confidence_passed == false`** | **31** |
| **`engine_b_confidence_passed == true`** | **0** |

**Checklist string proxies (`engine_b_checklist_components`):**

| Field | Crypto count (max 31) |
|-------|-----------------------|
| `rr_ok == "False"` | **22** |
| `entry_ok == "False"` | **26** |
| `structure_ok == "False"` | **14** |
| `room_ok == "False"` | **2** |

**`engine_b_hard_fail_reasons` multiplicity (tags per symbol may overlap):**

| Tag | Aggregate hits |
|-----|----------------|
| `engine_b_confidence_passed_false` | **31** |
| `engine_b_entry_ok_false` | **26** |
| `engine_b_rr_ok_false` | **22** |
| `engine_b_structure_ok_false` | **14** |
| `engine_b_location_ok_false` | **4** |

**`engine_b_structural_tp_diagnostics.fail_reason`:** **empty `""`** on **29** symbols; **`structural_tp_too_close`** on **2**.

**Universal soft warning:** `no_trigger_pattern_structure_ready`: **31** / **31**.

---

### 4. Forex Funnel Counts

Latest **`asset_type == forex`** per symbol: **21** symbols.

| `scan_tier` | Count |
|-------------|-------|
| trade | **5** |
| watchlist | **5** |
| skip | **11** |

| Proxy gate | Fail count / 21 |
|------------|-----------------|
| `rr_ok == "False"` | **15** |
| `entry_ok == "False"` | **13** |

---

### 5. Top Blockers (ranked — crypto proxy universe)

| Rank | Blocker proxy | Severity | Approx. prevalence (crypto `/31`) |
|------|----------------|----------|-------------------------------------|
| 1 | **Engine B gates fail (`confidence_passed_false`) despite structural `CLEAR`** | High | **31** / **31** hard-fails |
| 2 | **`entry_ok`** / catalyst stack | High | **`entry_ok`** false **26** |
| 3 | **`rr_ok`** (execution RR checklist) | High | **`rr_ok`** false **22** |
| 4 | **`structure_ok` checklist divergence** | Medium | **14** |
| 5 | **Space / liquidity proximity (`room_ok`)** | Low | **`room_ok`** false **2** |
| *(unscoped)* | **Engine A skips / unseen “No data”** | Unknown | Requires saved **`skipped`** payloads |

**Dominant blocker:**

- **blocker:** Composite **Engine B confidence / checklist failure** dominates crypto **despite structural `CLEAR` on every audited row** (`confidence_passed_false` on **31**/31)—**`entry_ok`** and **`rr_ok`** checklist failures concentrate mass (**26** and **22** respectively). **`scan_tier=trade`** can still arise from **Engine A** (**11**/31 crypto)—aligns with “few trades,” not “trade = B-pass.”  
- **count:** Confidence hard-fail **31**/31 crypto; **`entry_ok`** false **26**/31; **`rr_ok`** false **22**/31 (proxy).  
- **affected assets:** Crypto subset (**31** symbols in audit universe); analogous **`rr_ok`** failures on forex (**15**/21).  
- **evidence:** `logs/threshold_audit/signal_funnel.jsonl` (latest row per **`symbol`**, aggregates 2026-05-14).  
- **why it matters:** Separates **`structural_verdict`** from **`calculate_confidence` pass**, explaining sparse operator trust in Engine B overlays even where structure prints **CLEAR**.

---

### 6. RR Blocker Detail

| Question | Evidence (proxy) |
|----------|-------------------|
| **`execution_level_reject_reason == structural_tp_below_min_rr`** | **Not present** in `signal_funnel.jsonl` rows |
| **RR-ish failures crypto** | **`rr_ok == False`** checklist on **22** / **31** |
| **`structural_tp_below_min_rr` vs `structural_tp_too_close`** | Different layers: checklist **`rr`** vs **`engine_b_structural_tp_diagnostics.fail_reason`** (**2 × `structural_tp_too_close`**) |

**Interpretation:** Proxy shows **heavy RR disappointment on crypto rows** alongside **all-rows `CLEAR` verdicts** → likely **confidence stack + structural TP sizing / min_rr interaction** deserves funnel-native confirmation.

---

### 7. ATR / Level Feed Blocker Detail

| Issue | Repo evidence |
|-------|---------------|
| `bybit_atr_unavailable` count | **Unavailable** offline—field lives on `engine_b_scan_gate_funnel` / `sig` payloads not saved in `logs/` here |
| Pre-structure skips (`structure_executed=false`) | Requires saved `run_full_scan` JSON |

Code-path expectations (documentation only): **`scanner.py`** Bybit/crypto ATR overlay + **`config.yaml`** **`ENGINE_B_CRYPTO_LEVELS_FEED` / FALLBACK**.

---

### 8. Engine A-first Architecture Impact

| Question | Quantified? |
|----------|--------------|
| Pairs discarded before Engine B (**`sig_a` None**) | **No** (`signal_funnel` excludes **pure “No data”** skips) |
| Code guarantee | **`if not sig_a: return pair, None, None`** — see **`scanner.py`** around **lines 1106–1108** |

Silent fallout volume **unknown** empirically in this artifact set.

---

#### Crypto questions A–H (strict funnel vs threshold-audit proxy)

| ID | Question | Strict funnel answer | Proxy answer (crypto, latest row per symbol — **31** symbols) |
|----|---------|---------------------|------------------------------------------------------------|
| A | `sig_a` missing skips | Not counted without saved `skipped[]` payloads | Unknown from `signal_funnel.jsonl` |
| B | Reached Engine B evaluation | Requires `engine_b_evaluated` in funnel JSON | Implied **31**/31 audited rows overlaid (no literal flag) |
| C | Reached `analyze_structure` | Requires funnel `structure_executed` | `engine_b_structural_verdict=CLEAR` on **31**/31 (**infer only**) |
| D | Bybit ATR unavailable | Requires `engine_b_error` / `engine_b_skip_stage` in funnel | **Not countable** here |
| E | RR failed | Requires funnel `rr_ok` / execution reject reason | **`rr_ok` checklist false ⇒ 22**/31 |
| F | Space failed | Requires `space_gate_ok` / `room_ok` in funnel | **`room_ok` checklist false ⇒ 2**/31 |
| G | Watchlist-only | Requires `final_tier` in funnel | **`scan_tier=watchlist` ⇒ 10**/31 |
| H | Trade rows | Requires `final_tier` in funnel | **`scan_tier=trade` ⇒ 11**/31 |

Discrepancy note: Operators reporting **zero** crypto trades may be using **narrower universe**, **`testMode`**, **correlation cap**, or **non-audited scans** versus this **historical cumulative JSONL**.

---

### 9. Recommended Fix Sequence

| Order | Recommendation | Category |
|-------|----------------|---------|
| 1 | **Export one live scan JSON with funnel arrays** (`/api/scan`) + notebook aggregation | Diagnostics completion |
| 2 | **Optional funnel JSONL writer** behind config (future, not instructed now) | Observability |
| 3 | **Paper-only YAML experiments:** crypto levels **`SIGNAL_FEED_FALLBACK`**, **`RR_CAN_SATISFY_SPACE_GATE.crypto`**, synthetic RR TP toggle | Config-only experiments |
| 4 | **True Engine-B-first scanning** (orthogonal path) | Product / architecture |
| 5 | **`SCAN_B_ONLY` / **`STRUCTURE_READY`** surfacing knobs | Visibility (non-promotion defaults) |

---

### 10. What NOT To Change Yet

- `NAKED_ENGINE.style_profiles` `min_score` / `min_rr` — wait for funnel-derived histograms repeated across regimes.  
- **`ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP`** stays **policy-level** approval.  
- **`ENGINE_AB_CRYPTO_SIGNAL_FEED`** / venue pairing — treat as **risk surface**, not knob for row counts absent evidence.  

---

_End of analysis._
