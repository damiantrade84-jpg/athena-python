# Engine B full-scan blocker audit (repo-grounded)

**Scope:** `run_full_scan` in `scanner.py`, Engine B overlay + `calculate_confidence` / `resolve_engine_b_execution_levels` in `market_structure.py`, `_classify_signal` in `scoring.py`, checked-in `config.yaml` / `config.py` defaults.  
**Excluded by instruction:** tuning thresholds, strategy changes, execution / AI paths.

---

## 1. Active full scan path — call chain

| Step | Location | Evidence |
|------|-----------|----------|
| Entry | `run_full_scan()` | ```670:671:c:\dev\athena-python\scanner.py``` |
| Parallel worker `_analyse` | pool over `candidate_pairs`, then classify | ```1367:1525:c:\dev\athena-python\scanner.py``` |
| Engine A | `sig_a = r.analyze_pair(...)` | ```981:989:c:\dev\athena-python\scanner.py``` |
| Early exit (no Engine A payload) | `if not sig_a: return pair, None, None` | ```1004:1005:c:\dev\athena-python\scanner.py``` |
| Engine B overlay | Same worker, after candles + style resolution | ```1009:1347:c:\dev\athena-python\scanner.py``` |
| Per-row tier | `_classify_signal` → `_apply_engine_b_scan_gate` → `_apply_engine_b_only_watchlist_scan_tier` → `_apply_engine_b_structure_ready_scan_tier` | ```1525:1536:c:\dev\athena-python\scanner.py``` |
| Outputs | `tradeSignals` / `signals`, `watchlist`, `skipped` | ```1574:1608:c:\dev\athena-python\scanner.py``` |

**Answer:** This is **not** a standalone “Engine B discovery scan.” Full scan runs **Engine A first** (`analyze_pair`); rows only enter downstream classification when `sig_a` is truthy (`buffered_ok` is built solely from `(pair, sig)` where `sig` was returned — see ```1419:1444:c:\dev\athena-python\scanner.py```). Engine B runs as an **overlay** on pairs that already have an Engine A signal dict.

---

## 2. Engine A-first dependency

| Observation | Verdict |
|-------------|---------|
| Engine B runs when `sig_a` is None | **Rejected by code.** `return pair, None, None` before any overlay work. ```1004:1005:c:\dev\athena-python\scanner.py``` |
| B-only crypto “structures” visible when Engine A yields no signal | **No scan row is produced.** Those pairs are counted as `"No data"` / skip with no structured Engine B funnel on the skipped row (prior to diagnostics patch). Same lines. |
| B-only watchlist tier | Runs only if `tier != "trade"` **and** `engine_b_confidence_passed` (`_apply_engine_b_only_watchlist_scan_tier`) ```365:410:c:\dev\athena-python\scanner.py``` |


**Depends on sig_a:** B-only promotion still requires passing the `if sig_a:` branch; `_apply_engine_b_only_watchlist_scan_tier` never runs without a buffered signal.

---

## 3. Crypto pre-B blockers — Bybit ATR vs fallbacks

| Item | Repo fact |
|------|-----------|
| `ENGINE_AB_CRYPTO_SIGNAL_FEED` | **`binance`** in `config.yaml` ```131:132:c:\dev\athena-python\config.yaml``` — Engine A/B shared candle routing label for scans (paired with fallback `false`; does not exempt Engine B overlay). |
| `ENGINE_B_CRYPTO_LEVELS_FEED` / fallback | **`bybit`**, `ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK: false` ```133:134:c:\dev\athena-python\config.yaml``` |
| Overlay path | For `ptype == "crypto"` and levels feed `bybit`, scans call `bybit_atr_for_levels`; if unavailable and fallback off, sets `atr = 0`, `sig_a["engine_b_error"] = "bybit_atr_unavailable"` | ```1160:1170:c:\dev\athena-python\scanner.py``` |
| Gate before structure | Full `analyze_structure` only runs when `atr > 0` (`if atr and atr > 0:`) ```1173:1205:c:\dev\athena-python\scanner.py``` |
| Visibility | `annotate_signal_for_scan` adds diagnostics when `engine_b_error` present | ```626:631:c:\dev\athena-python\scanner.py``` |

**Confirmed:** Crypto can be **blocked before** `analyze_structure` when Bybit ATR is missing and signal-feed fallback is off (fail-closed to `atr == 0`).

---

## 4. RR and structural SL/TP

| Observation | Repo fact |
|-------------|-----------|
| Execution SL/TP for RR gate | `resolve_engine_b_execution_levels()` tightens SL (ATR vs structural), prefers structural TP, computes execution RR, applies `min_rr` vs **`ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP`** | ```1125:1347:c:\dev\athena-python\market_structure.py``` |
| Fallback default | **`false`** — `ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP: false` in `config.yaml` ```1077:1077:c:\dev\athena-python\config.yaml``` and `config.py` default ```398:398:c:\dev\athena-python\config.py``` (grep result) |
| Reject reason | `_exec_rr < _min_rr` with structural TP ⇒ `execution_levels_valid=False`, `execution_level_reject_reason` / `fallback_tp_reason` **`structural_tp_below_min_rr`** when synthetic TP off | ```1303:1327:c:\dev\athena-python\market_structure.py``` |
| Wired into gates | `calculate_confidence` sets `rr_ok` from `_exec_lvl["execution_levels_valid"] and rr >= min_rr` ```3338:3353:c:\dev\athena-python\market_structure.py``` |

**“How often”:** Not measurable from static code alone; **whenever** structural TP yields execution RR strictly below `style_profile.min_rr` and synthetic fallback stays disabled, **`rr_ok` is False** — tests already lock this (`tests/test_engine_b_rr_basis.py`).

Near-liquidity / `tp_structural_limited`: set in `analyze_structure` logic (see `tp_structural_limited` in result dict around ```2889:3027:c:\dev\athena-python\market_structure.py```); capped structural targets feed diagnostics and **`ENGINE_B_REASON_STRUCTURAL_TP_TOO_CLOSE`** paths in confidence ```3567:3568:c:\dev\athena-python\market_structure.py```. Structural TP placed **beyond** structural invalidity still fails **`structural_tp_below_min_rr`** when RR is insufficient — both can reject valid-looking structure.

Crypto **inherits profile `min_rr`** from `NAKED_ENGINE.style_profiles` for the resolved scan style unchanged by this audit (no edits).

---

## 5. Space gate and crypto

Computed in `calculate_confidence`:

| Step | Lines |
|------|-------|
| `room_ok` from distance vs `min_room_atr` × ATR | ```3356:3360:c:\dev\athena-python\market_structure.py``` |
| RR may satisfy space when enabled | ```3400:3412:c:\dev\athena-python\market_structure.py``` |
| `space_gate_ok` | **`room_ok` only for crypto as checked in** (`crypto: false` in YAML map). Config: ```152:160:c:\dev\athena-python\config.yaml``` — `ENGINE_B_RR_CAN_SATISFY_SPACE_GATE.crypto: false` |

**Confirmed:** Crypto **cannot** use “RR satisfies space” waiver; **`space_gate_ok == room_ok`** for crypto when only the scoped dict applies (unless other keys apply — **`crypto`** is explicitly `false`).

---

## 6. Style profile chain (conceptual anchor)

Resolved in-scan via **`r.naked_scan_style_profile(_pair_style, score_group=_pair_score_group, asset_type=ptype)`** ```1010:1014:c:\dev\athena-python\scanner.py```.  
Confidence uses **`profile.get("min_rr")`** and **`engine_b_confidence_passes` → `engine_b_min_score_threshold(style_profile, regime_label, asset_type)`** overlay on **`conf["passed"`** semantics ```890:907:c:\dev\athena-python\market_structure.py``` and `_apply_engine_b_scan_confidence_gate` ```133:151:c:\dev\athena-python\scanner.py```.

Regime multipliers:** `ENGINE_B_REGIME_MULTIPLIERS` for score threshold scaling (referenced by `engine_b_min_score_threshold` — not reopened here unless user requests edits).

---

## 7. Output classification — why few “trade” rows

**Trade tier** comes from **`_classify_signal`**: mainly **score ≥ threshold**, pair enabled, not blocked by event/exchange/trend, etc. ```1101:1145:c:\dev\athena-python\scoring.py```.

Important interaction when Engine B aligns poorly:

```1111:1122:c:\dev\athena-python\scoring.py
        if scan_ready:
            if signal.get("enginesAligned") is False:
                required = _a_only_required_score(pair, signal)
                ...
                    return (
                        "watchlist",
                        ...
                    )
```

`_apply_engine_b_scan_confirmation_gate`** default **OFF** (`ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED: false` ```973:973:c:\dev\athena-python\config.py```) — trades are **not** globally demoted for `enginesAligned` here ```350:362:c:\dev\athena-python\scanner.py```; **instead**, `_classify_signal` treats **explicit** `enginesAligned is False` and may downgrade to watchlist unless A score clears `required` (**A-only auto gate**, see `_a_only_required_score` ```1055:1070:c:\dev\athena-python\scoring.py```).

`_apply_engine_b_only_watchlist_scan_tier`: **never** promotes to `trade` ```372:382:c:\dev\athena-python\scanner.py```.

**Hence ~5–7 trade signals:** By code, **`tradeSignals` are Engine-A-threshold-qualified rows** passing `_classify_signal` (quantile-adjusted thresholds possible ```1485:1492:c:\dev\athena-python\scanner.py```), then **`apply_correlation_cap`** shrinking lists ```1655:1657:c:\dev\athena-python\scanner.py``` — not “Engine B count.”

Additional B setups hide in **`watchlist`** (near-floor or B-only tiers) **`skipped`** (< watch floor), **`engine_b_structure_ready_watchlist`** (config-gated, default OFF) — not in **`tradeSignals`**.

Crypto invisibility splits into two mechanisms: **no `sig_a` → discarded before overlay** **or** **`bybit_atr_unavailable` + `atr==0`** → **`analyze_structure` never runs.**

---

## 8. Diagnostics gap (before patch)

Threshold audit (**`threshold_audit.py`**, env / `THRESHOLD_AUDIT` gated) complements production scan payloads but optional. Per-pair **`engine_b_scan_gate_funnel`** was **missing** from default JSON returned to **`tradeSignals` / `watchlist` / `skipped`**. Skipped **`"No data"`** pairs still cannot carry funnel (no dict).

---

## Required answers

1. **Why only ~5–7 signals pass.**  
   **`trade`** tier is overwhelmingly **Engine A + `_classify_signal` + optional quantile + correlation cap**, not Engine B pass count (**§7**, **§1**).

2. **Why no crypto trade rows (typical hypotheses).**  
   **Combined:** many crypto pairs **`sig_a is None`** (never buffered); when buffered, **`bybit_atr_unavailable`** with **`fallback false`**/zero ATR skips **`analyze_structure`** (**§3**). Remaining overlay must still satisfy confidence gates including **`space_gate_ok == room_ok`** for crypto (**§5**) and **`rr_ok`** (**§4**).

3. **RR as confirmed blocker.**  
   **Confirmed for Engine B overlay pass path:** execution RR gated via **`resolve_engine_b_execution_levels`** with **`ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP: false`** → **`structural_tp_below_min_rr`** can force **`execution_levels_invalid`** and **`rr_ok` false** (**§4**, existing tests **`tests/test_engine_b_rr_basis.py`**).

4. **Engine A-first architecture as blocker.**  
   **Confirmed for “Engine B-first discovery”.** Rows without **`sig_a` never reach Engine B** (**§2**).

5. **`ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK=false`.**  
   **Confirmed pre-structure blocker path** under **`ENGINE_B_CRYPTO_LEVELS_FEED=bybit`** (**§3**, **YAML** §131–134).

6. **Minimal safe fix sequence (no threshold edits in-repo).**  
   1) **Operational:** enable **`ENGINE_B_SCAN_GATE_FUNNEL_ENABLED`** (defaults true after diagnostics patch) and inspect funnel / logs to separate **Engine A omission** vs **ATR omission** vs **RR/space** vs **gates**.  
   2) **Product decision (config, user-owned):** if crypto Engine B parity is desired when Bybit sizing is stale, selectively enable **`ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK`** (risk: venue mismatch; comment in **`config.yaml`** · ```121:123:c:\dev\athena-python\config.yaml```).  
   3) **Architectural:** if true B discovery is desired for full-scan, **`analyze_structure` requires a refactor** to invoke B without Engine A (**out of diagnostics patch scope**).


---

## Evidence appendix — key excerpts

 Engine A-first return:

 ```1004:1005:c:\dev\athena-python\scanner.py
                 if not sig_a:
                     return pair, None, None
 ```

 Crypto Bybit ATR collapse:

 ```1160:1173:c:\dev\athena-python\scanner.py
                         if (
                             ptype == "crypto"
                             and str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
                             and hasattr(r, "bybit_atr_for_levels")
                         ):
                             bybit_atr = r.bybit_atr_for_levels(pair, resolved_style_b)
                             if bybit_atr:
                                 atr = float(bybit_atr)
                             elif not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                                 atr = 0.0
                                 sig_a["engine_b_error"] = "bybit_atr_unavailable"
                         current_price = float(entry_candles_b[-1]["close"])
 
                         if atr and atr > 0:
 ```

 Space gate for crypto **`RR` cannot waive room:**

 ```3406:3419:c:\dev\athena-python\market_structure.py
         _rr_space_cfg = config.CONFIG.get("ENGINE_B_RR_CAN_SATISFY_SPACE_GATE", False)
         ...
         rr_space_gate_enabled = rr_space_gate_enabled or forex_rr_space_gate_enabled
         space_gate_ok = space_ok if rr_space_gate_enabled else room_ok
 ```

 YAML **`crypto: false`:**

 ```152:160:c:\dev\athena-python\config.yaml
 ENGINE_B_RR_CAN_SATISFY_SPACE_GATE:
   default: false
   forex: true
   stock: true
   index: true
   commodity: true
   etf: true
   etf_bond: true
   crypto: false
 ```
