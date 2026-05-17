# Master audit priority — Cursor phases 1–3 (Engine A, B, D)

Synthesized from:

- `tasks/audit_phase_1cursor_engine_a.md` → **Phase 1 (Engine A)**
- `tasks/audit_phase_2cursor_engine_b.md` → **Phase 2 (Engine B)**
- `tasks/audit_phase_3cursor_engine_d.md` → **Phase 3 (Engine D)**

Apply in order: CRITICAL → HIGH → threshold review → dead-code manifest → MEDIUM → LOW.

---

## 1. CRITICAL — fix first

| ID | Phase | File | Line(s) | Summary |
|----|-------|------|---------|---------|
| BUG-D-1 | Phase 3 | `scalp_engine.py` | `3606–3620` | Global pre-scan abort when `scalp_session_window("forex")` and `scalp_session_window("crypto")` are both false; incorrectly vetoes scanning for stocks/indices/commodities that may still be valid per `SESSION_MODE_BY_ASSET` / per-pair `scalp_session_window(asset_type)` (`3692–3697`). |

---

## 2. HIGH — fix second

| ID | Phase | File | Line(s) | Summary |
|----|-------|------|---------|---------|
| BUG-A-1 | Phase 1 | `config.yaml`; `factor_scoring.py` | e.g. `1152–1295` (config); implementation spans module | Large `INDICATOR_WEIGHTS` trees (`derivatives`, `microstructure`, `volatility`, `volume`, `carry`) largely unused by Engine A v2 `compute_factor_scores`; operators tune dead knobs; contract drift vs comments at `config.yaml:1154–1157`. |
| BUG-A-7 | Phase 1 | `scanner.py`; `config.yaml` | `120–137`, `909–912`; `813–823` | A-only `combinedConviction` capped by `AUTO_TRADE_A_ONLY_WEIGHT` at `scoreNorm=1`; if `AUTO_TRADE_MIN_CONVICTION` exceeds per-asset weight, no A-only signal can pass autopilot. |
| BUG-B-4 | Phase 2 | `risk_engine.py` | `701–723`, `835–867` | Engine B checklist + `ENGINE_C_EXEC_MIN_RR` apply only for consensus-shaped signals (`verdict` / `engine` / `components`); structural naked payloads may bypass geometric RR and checklist paths. |
| BUG-B-5 | Phase 2 | `backtest_runner.py` | `4725–4765` (consensus ~`4760–4765`) | Engine C backtest feeds `compute_consensus` without `engine_b_confidence_passes`; live uses `_engine_c_accepts_engine_b` → `engine_b_confidence_passes` (`execution.py:118–131`). BT/live mismatch when `passed` true but `score < min_score_scaled`. |
| BUG-D-2 | Phase 3 | `backtest_runner.py` | `5114–5116`, `5389–5403` | `_min_grade_str` uses `MIN_GRADE_AUTO_EXECUTE` / `MIN_GRADE` (default `"C"`), not `_scalp_execution_min_grade` / `EXECUTION_MIN_GRADE`; live vs backtest grade floor diverges. |
| BUG-D-3 | Phase 3 | `scalp_engine.py` | `3070–3072`; signal ~`4332` (`rr_partial`) | `tp1_r_mult = max(TP1_R_MULT, MIN_RR)` couples TP1 to `MIN_RR`; payload `rr_partial: 1.0` can disagree with geometry; doc/operator expectation of literal 1R TP1 may be wrong. |

---

## 3. Threshold changes recommended — review with operator before applying

These are **calibration / policy** findings from the phase audits, not necessarily bugs. **Do not change locked scoring thresholds without explicit approval** (project rule).

### Phase 1 — Engine A (`audit_phase_1cursor_engine_a.md` § THRESHOLD ASSESSMENT)

| Key / area | Verdict (audit) | Evidence / notes (from audit) |
|------------|-----------------|--------------------------------|
| `FACTOR_ADX_HARD_FAIL_CLASS` (all `10`) | TOO_LOOSE vs old comment | Comment said global default 15; YAML forces 10 — widens soft zone (`config.yaml` + audit narrative). |
| `FACTOR_CONVICTION_FLOOR` (`0.20`) | TOO_LOOSE vs stated intent | Coupled with BUG-A-6: comment vs formula mismatch (`config.yaml:183`; `factor_scoring.py:1876`). |
| `PAIR_PROFILES.min_confluence` | TOO_LOOSE for listed pairs | e.g. XAU **1.05** vs tier stable **1.5** — deliberate loosening; interacts with `scoring.py:288–297`. |
| `PAIR_PROFILES.bt_min` | UNREACHABLE for live threshold | Audit: `get_min_confluence_threshold` uses `min_confluence` only. |
| `INTERMARKET_CONFIRMATION.engine_a_enabled` `false` | TOO_STRICT on feature | Default no intermarket delta (`intermarket.py:1377–1384`) — neutral default, policy choice. |

### Phase 2 — Engine B (`audit_phase_2cursor_engine_b.md` § THRESHOLD ASSESSMENT)

| Key | Verdict (audit) | Evidence / notes |
|-----|-----------------|------------------|
| `NAKED_ENGINE.style_profiles.*.min_score` | CALIBRATED (partial); empirical pass-rate NOT VERIFIED | — |
| `NAKED_ENGINE.score_group_overrides.*` | NOT VERIFIED | Per audit. |
| `ENGINE_B_REGIME_MULTIPLIERS` HIGH_VOL (`0.85`) | TOO_LOOSE (score gate) | Lowers scaled `min_score` threshold. |
| `ENGINE_B_REGIME_MULTIPLIERS` LOW_VOL (`1.15`) | STRICTER | Raises scaled threshold (matches code comments). |
| `ENGINE_B_FOREX_ADX_MIN` | OVERKILL / misleading | Unused in production `.py` (ties BUG-B-1: `config.yaml:~2086–2088`). |
| `ENGINE_B_ROOM_GATE_REQUIRE_DISTANCE` | Mixed | Toggle vs `_get_min_room_atr` hardcoded ladders (`market_structure.py:~2901–2916`, `2945–2950`). |
| `ENGINE_C_EXEC_MIN_RR` | Context-dependent | Only consensus-shaped executions in `risk_check` (ties BUG-B-4). |

### Phase 3 — Engine D (`audit_phase_3cursor_engine_d.md` § Section 7)

| Key | Assessment (audit) | Notes |
|-----|---------------------|--------|
| `BALANCE_THRESHOLD` | CALIBRATED; **None → `"balance"`** arguably TOO_LOOSE | Fail-open toward MR (`scalp_engine.py` narrative ~`1369–1371`, BUG-D-5). |
| `TP1_R_MULT` vs `MIN_RR` (`max(...)`) | TOO_STRICT / MISLABELLED | Contradicts “1R pay-yourself” semantics (BUG-D-3). |
| Backtest grade gate vs `EXECUTION_MIN_GRADE` | TOO_LOOSE risk | Parity gap (BUG-D-2). |
| `BT_SESSION_MODE: all` | OVERKILL vs live | Intentional breadth vs live `london_ny`-style modes. |
| `SKIP_CRYPTO_ON_AGGTRADE_UNAVAILABLE` | OVERKILL for diagnostics | Skips entire pair per audit. |

---

## 4. Dead code removal manifest — apply after bugs are fixed

Use this as a checklist; some items are **config/documentation** cleanup rather than deleting Python.

| Phase | Item | File | Line(s) / location | Notes |
|-------|------|------|---------------------|--------|
| 1 | `INDICATOR_WEIGHTS` groups dead for Engine A v2 `final_score` | `config.yaml` | derivatives / microstructure / volatility / volume / carry subtrees (see Phase 1 § DEAD FACTORS) | Wire, namespace (`LEGACY_*`), or document-only — overlaps BUG-A-1. |
| 1 | `REGIME_WEIGHTS`, `FACTOR_SCORE_GROUP_MULTIPLIERS`, `CRYPTO_FACTOR_WEIGHT_CAPS` | `config.yaml` | `1374–1376` (legacy comment region) | Explicitly legacy / inactive per audit. |
| 1 | `CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED` | `config.yaml` | `1148`; no `.py` consumer | BUG-A-4 — wire or remove. |
| 1 | `CRYPTO_TRANSITION_PENALTY` / `CRYPTO_TRANSITION_PENALTY_ENABLED` | `config.py` ~`644`; YAML | No matches in `regime.py` / `factor_scoring.py` | BUG-A-10 — remove or implement. |
| 1 | Unreachable `state == 2` branches in `detect_regime` | `regime.py` | `65–87` | BUG-A-5 — dead/misleading logic paths (refactor or remove). |
| 2 | `ENGINE_B_REASON_FOREX_ADX_LOW` | `market_structure.py` | ~`174` | Never appended to diagnostics (~`3119–3135`); BUG-B-7. |
| 2 | `ENGINE_B_FOREX_ADX_MIN` | `config.yaml` | ~`2086–2088` | Unused by runtime Python (grep/registry/tests only); BUG-B-1. |
| 2 | Possibly unreachable `RuntimeError` | `engine_b_ai.py` | ~`94–96` | Defensive branch after retry — audit flags as likely unreachable. |
| 3 | `is_valid_session` | `scalp_engine.py` | ~`930–959` | Unused in production; BUG-D-8. |
| 3 | `GRADE_THRESHOLDS` | checked-in `config.yaml` | absent | Grading uses in-code defaults (`scalp_engine.py` ~`3140+`); add to YAML or formalize defaults. |
| 3 | `VP_PROXIMITY_USE_ATR` | checked-in `config.yaml` | absent | Code default `True` always; expose or document. |

**Partial / cross-cutting (Phase 1 audit):** `volume_momentum_spread` — partial exposure vs `mom_quality` omission (BUG-A-2); `NORMALIZATION_LOOKBACK` partial for core momentum (Phase 1 § DEAD FACTORS).

**Not Engine D–exclusive:** `classify_profile_interaction` in `volume_profile.py` — consumed from `market_structure.py` (Phase 3 § 8).

---

## 5. MEDIUM — fix after sections 1–4 unless blocking ops

| ID | Phase | File | Line(s) | Summary |
|----|-------|------|---------|---------|
| BUG-A-2 | Phase 1 | `factor_scoring.py`; `config.yaml` | `601–618`; `1222–1228` | `volume_momentum_spread` in YAML never applied to `mom_quality`; only `rsi_z` / `macdLine_z` read. |
| BUG-A-6 | Phase 1 | `config.yaml`; `factor_scoring.py` | `183`; `1876` | `FACTOR_CONVICTION_FLOOR` comment contradicts `final_score = base_score * (floor + (1-floor)*conviction)` math. |
| BUG-A-8 | Phase 1 | `tests/test_factor_group_overrides.py` | `28–47` | `PAIR_PROFILES.weight_overrides` ineffective for v2 `compute_factor_scores`; misleading config. |
| BUG-B-1 | Phase 2 | `config.yaml` | ~`2086–2088` | `ENGINE_B_FOREX_ADX_MIN` documented but not read by production `.py`; `market_structure.py:~2840–2842` notes structure gate does not use it. |
| BUG-B-3 | Phase 2 | `market_structure.py` | ~`2990` vs ~`3084–3089` | `space_ok = room_ok or rr_ok` vs `passed` requiring both `room_ok` and `rr_ok` — contract confusion for consumers. |
| BUG-B-6 | Phase 2 | `market_structure.py`; `config.yaml` | ~`645–652`; ~`1771–1774` | Research lab factors run when `ENABLED` but gate upgrades never apply unless `ALLOW_GATE_UPGRADE`; wasted computation / misleading semantics. |
| BUG-B-8 | Phase 2 | `engine_b_ai.py` | ~`568–576` | Partial parsed JSON gets soft defaults (e.g. grade C, edge 50%) instead of hard error — advisory integrity. |
| BUG-D-4 | Phase 3 | `execution.py` (`api_scalp_execute`) | ~`2006–2142` | Manual POST does not enforce Grade D or `executable: false` before execution path — crafted payload risk. |
| BUG-D-5 | Phase 3 | `scalp_engine.py` | `1369–1371` | `balance_ratio is None` → `"balance"` (MR-friendly); fail-open vs strict VP integrity. |

---

## 6. LOW — fix last

| ID | Phase | File | Line(s) | Summary |
|----|-------|------|---------|---------|
| BUG-A-3 | Phase 1 | `indicators.py` | `237–241` | Bollinger uses sample std `(p-1)` vs population σ — methodology drift vs conventional BB. |
| BUG-A-4 | Phase 1 | `config.yaml` | `1148` | `CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED` dead toggle (also listed in §4). |
| BUG-A-5 | Phase 1 | `regime.py` | `65–87` | Unreachable `elif state == 2` before BB relabel (also §4). |
| BUG-A-9 | Phase 1 | `config.yaml` | `1154–1157` | Comment falsely ties `INDICATOR_WEIGHTS` to `directional_factors` / `nondirectional_factors` not present in `factor_scoring.py`. |
| BUG-A-10 | Phase 1 | `config.py`; YAML | ~`644` | `CRYPTO_TRANSITION_PENALTY*` unused; YAML references `regime.py` inaccurately (also §4). |
| BUG-B-2 | Phase 2 | `market_structure.py` | ~`1591–1597` vs ~`1508–1538`, ~`1559–1587` | FVG docstring bullish/bearish swapped vs implementation. |
| BUG-B-7 | Phase 2 | `market_structure.py` | ~`174` | `ENGINE_B_REASON_FOREX_ADX_LOW` never emitted (also §4). |
| BUG-D-6 | Phase 3 | `config.yaml` (comment); `scalp_engine.py` | comment vs ~`50–56` | London open YAML comment vs `08:00 Europe/London` implementation (DST). |
| BUG-D-7 | Phase 3 | `scalp_engine.py` | `_engine_d_aggression_fidelity` vs `_engine_d_strict_fabio_shadow` funnel | `strict_fabio_pass` key collision / overwrite in diagnostics. |
| BUG-D-8 | Phase 3 | `scalp_engine.py` | ~`930–959` | `is_valid_session` dead in production (prefer removal via §4). |

---

## Cross-reference: items appearing in multiple sections

- **BUG-D-8** — listed as LOW bug (§6) and dead code (§4).
- **BUG-A-4, BUG-A-5, BUG-A-10, BUG-B-7** — LOW bugs with matching dead-code / cleanup entries (§4).
- **BUG-B-1** — MEDIUM bug (§5) and dead config surface (§4).
- **BUG-D-3, BUG-D-2** — HIGH bugs (§2) and threshold review rows (§3).

---

*End of master priority list.*
