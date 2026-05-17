# Engine A — Nuclear-Grade Audit (Cursor)

**Scope:** Audit-only. No patches applied.
**Reads:** `scoring.py`, `factor_scoring.py`, `indicators.py`, `config.yaml` (ENGINE_A-adjacent sections), `regime.py`, `intermarket.py`, `forex_scoring.py`, `confidence_engine.py`, `calibration.py`, plus `scanner.py` / `auto_trader.py` / `athena.py` for `combinedConviction` and `scoreNorm`.

---

## Section 1 — Scoring mathematics

### Normalization (final_score)

Engine A v2 does **not** build `final_score` as “sum of weighted factors ÷ sum(active weights).” It is a **multiplicative chain** on `abs(trend_score)` with ADX, vol scaler, session, DI alignment, directional ramp, VWAP, then a **conviction blend**, penalties, bounded adjustments, optional intermarket.

```1770:1881:factor_scoring.py
    base_score = (
        abs(trend_score)
        * adx_mult
        * vol_scaler
        * session_mult
        * di_align_mult
        * dir_ramp_mult
        * vwap_mult
    )
    ...
    final_score = base_score * (_eff_floor + (1.0 - _eff_floor) * conviction)
    final_score = final_score * (1.0 - _cost_penalty)
    final_score = final_score * (1.0 + _total_adj)
    ...
    final_score = final_score + mean_rev_adj
    final_score = max(0.0, min(3.0, final_score))
```

Momentum **within** `_momentum_quality` does divide by `total_w = rsi_w + macd_w`:

```601:618:factor_scoring.py
    ind_weights = CONFIG.get("INDICATOR_WEIGHTS", {}).get("momentum", {})
    ...
    rsi_w = float(ind_weights.get("rsi_z", 0.6)) if isinstance(ind_weights, dict) else 0.6
    macd_w = float(ind_weights.get("macdLine_z", 0.4)) if isinstance(ind_weights, dict) else 0.4
    total_w = rsi_w + macd_w
    ...
    raw = (rsi_score * rsi_w + macd_score * macd_w) / total_w
```

There is **no** single hardcoded denominator for `final_score` like “divide by 5”; the scale comes from `_coherent_trend_score` magnitude (up to 3.0 with TF coverage), ADX ramp, and the conviction blend.

### combinedConviction

Not computed in `scoring.py` / `factor_scoring.py`. Live scan builds it in `scanner.py` from `scoreNorm` and Engine B (or A-only weight):

```886:912:scanner.py
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    b_norm = min(b_score / b_max, 1.0) if b_max else 0
                                    ...
                                    combined_conviction = (a_norm * _w_a) + (b_norm * _w_b)
                                    sig_a["combinedConviction"] = round(combined_conviction, 4)
                                else:
                                    ...
                                    _w_a_fb = _a_only_auto_weight(pair)
                                    sig_a["combinedConviction"] = round(a_norm * _w_a_fb, 4)
```

`scoreNorm` is `min(1.0, score / maxScore)`:

```11573:11577:athena.py
    score_norm = (
        min(1.0, float(res["score"]) / float(max_score))
        if max_score and float(max_score) > 0
        else 0.0
    )
```

A-only weight default:

```120:137:scanner.py
def _a_only_auto_weight(pair: dict | None, config: dict | None = None) -> float:
    ...
    weight_cfg = cfg.get("AUTO_TRADE_A_ONLY_WEIGHT", {}) or {}
    ...
            weight = float(weight_cfg.get(asset_type, weight_cfg.get("default", 0.60)))
```

### Structural ceiling (A-only vs auto-trade)

Maximum A-only `combinedConviction` = **`scoreNorm × AUTO_TRADE_A_ONLY_WEIGHT`**; with perfect `scoreNorm = 1.0` and default weight **0.60**, **max = 0.60**.
`AUTO_TRADE_MIN_CONVICTION` default in `config.yaml` is **0.50**, so a full-scale A-only signal **can** exceed the gate (`0.60 > 0.50`). If an operator sets `AUTO_TRADE_MIN_CONVICTION` **above** the per-asset `AUTO_TRADE_A_ONLY_WEIGHT` (e.g. min **0.65**, weight **0.60**), **no** A-only signal can pass even at `scoreNorm = 1.0`; arithmetic gap = **`min_conviction − weight`** (e.g. **0.05**). This remains a **configuration coupling risk**, not removed by code.

### FACTOR_SCORE_GROUP_MULTIPLIERS

Config states v2 does not read them:

```1374:1376:config.yaml
# Legacy/inactive Engine A v1 factor-weight metadata.
# Current Engine A v2 does not read REGIME_WEIGHTS, FACTOR_SCORE_GROUP_MULTIPLIERS,
# or CRYPTO_FACTOR_WEIGHT_CAPS; see factor_scoring.py 3-factor multiplicative score.
```

Confirmed: **no** references in `factor_scoring.py` / `scoring.py` to `FACTOR_SCORE_GROUP_MULTIPLIERS`. **Not applied** before or after normalization — **inactive**.

### trend_score vs mom_quality

Direction and `trend_score` come from `_coherent_trend_score`; `mom_quality` is computed **after** direction is fixed and only feeds **conviction** and `factor_scores["momentum"]`, not the trend vote:

```1524:1587:factor_scoring.py
    trend_score, direction, trend_detail = _coherent_trend_score(
        ...
    )
    ...
    mom_quality = _momentum_quality(h4_snap, direction, asset_type, score_group=score_group)
```

No feedback from `mom_quality` into `_coherent_trend_score` on the same bar — **no directional cross-contamination**. `conviction` does combine `mom_quality` into the scalar that multiplies `base_score` (magnitude coupling by design).

### BTC bias conditional

Gate: crypto, non-neutral `btc_bias`, direction set; for alts (`"BTC" not in _pair_display`), correlation drives the band; `btc_corr < 0.50` forces **no bias** (`_btc_mult = 1.0`):

```677:708:scoring.py
    if pair.get("type") == "crypto" and btc_bias and btc_bias != "neutral" and _dir is not None:
        if "BTC" not in _pair_display:
            ...
            if btc_corr > 0.80:
                ...
            elif btc_corr < 0.50:
                _btc_mult = 1.0
            else:
                ...
```

For **BTC/USDT**, `"BTC"` is in `display`, so the inner block is **skipped** and `_btc_mult` stays **1.0** (BTC bias not applied to BTC itself). When correlation is computed and **not** in `(−∞,0.50) ∪ (0.80,∞)`, the **moderate** branch applies.

---

## Section 2 — Indicator mathematics

### RSI — Wilder

Initial average of `p` gains/losses, then Wilder smoothing `(prev*(p-1)+x)/p`:

```60:92:indicators.py
def calc_rsi(c: list, p: int) -> list:
    """Wilder RSI (smoothed). Returns list aligned with input, None-padded."""
    ...
```

### Bollinger σ

Uses **sample** variance divisor **`(p - 1)`** → **not** population std:

```237:241:indicators.py
        mn = sum(sl) / p
        sd = math.sqrt(sum((x - mn) ** 2 for x in sl) / (p - 1)) if p > 1 else 0
```

### ATR — Wilder-style

First TR sum `/p`, then `(a[i-1]*(p-1)+tr[i])/p`:

```127:144:indicators.py
def calc_atr(h: list, lo: list, c: list, p: int) -> list:
    ...
```

### ADX

+DI/−DI from **Wilder-smoothed** TR and DM; **DX** = `|+DI−−DI|/(+DI+−DI)*100`; **ADX** = smoothed DX (initial mean of `p` DX values, then Wilder on DX):

```177:220:indicators.py
    for i in range(p, len(true_range)):
        smooth_tr = smooth_tr - smooth_tr / p + true_range[i]
        ...
        dx_values.append(abs(pdi_val - mdi_val) / di_sum * 100 if di_sum else 0)
    ...
```

### Config vs periods

`_calc_indicator_bundle` hardcodes **RSI/ATR/ADX 14**, **BB 20, mult 2** — not read from `config.yaml` period keys:

```1090:1107:indicators.py
        "rsi": calc_rsi(cl, 14),
        ...
        "atr": calc_atr(hi, lo, cl, 14),
        "adx": calc_adx(hi, lo, cl, 14),
        "bb": calc_bb(cl, 20, 2),
```

`NORMALIZATION_LOOKBACK` is consumed via `get_normalization_lookback` in **`calc_indicators_with_normalized`**, not in `_calc_indicator_bundle`:

```1110:1118:indicators.py
def calc_indicators_with_normalized(candles: list, asset_type: str = "crypto") -> dict:
    ...
    lookback = get_normalization_lookback(asset_type)
```

---

## BUG findings

### BUG-A-1 — INDICATOR_WEIGHTS blocks largely disconnected from Engine A v2 score

| Field | Value |
|--------|--------|
| **Severity** | HIGH (operational / contract drift) |
| **File** | `config.yaml` (e.g. lines 1152–1295), `factor_scoring.py` |
| **Line** | Config claims alignment with `directional_factors` / `nondirectional_factors` at `config.yaml:1154-1157`; those structures are **not** how v2 scores. |
| **What** | Large `INDICATOR_WEIGHTS` trees (`derivatives`, `microstructure`, `volatility`, `volume`, `carry`) are **not** consumed in `compute_factor_scores` for the published 3-factor + addon model. |
| **Should** | Either wire factors, or narrow config/docs to keys actually used (`trend`, `momentum` subset only). |
| **Impact** | Operators tune dead knobs; research parity and UI expectations drift. |
| **Fix** | Document “confidence_engine only” for unused groups, or delete/namespace under `LEGACY_INDICATOR_WEIGHTS`. |
| **Test** | Assert `compute_factor_scores` only reads `INDICATOR_WEIGHTS.trend` and `INDICATOR_WEIGHTS.momentum` (fixture config with bogus `derivatives` → score unchanged). |

---

### BUG-A-2 — `volume_momentum_spread` in momentum weights never applied

| Field | Value |
|--------|--------|
| **Severity** | MEDIUM |
| **File** | `factor_scoring.py` |
| **Line** | `factor_scoring.py:601-618` — only `rsi_z` and `macdLine_z` keys read; crypto YAML assigns `volume_momentum_spread: 0.2` at `config.yaml:1222-1228`. |
| **What** | Third momentum weight is **ignored**; `total_w` excludes it → implicit renormalization **against config intent**. |
| **Should** | Include `volume_momentum_spread` from snap in weighted sum, or remove from YAML. |
| **Impact** | Crypto momentum blend mis-tuned vs documented weights. |
| **Fix** | Add optional term e.g. `vms = h4_snap.get("volume_momentum_spread")` with same scale as other components, or drop config key. |
| **Test** | Monkeypatch snap + weights; changing `volume_momentum_spread` weight changes `mom_quality`. |

---

### BUG-A-3 — Bollinger bands use sample std (ddof 1), not population

| Field | Value |
|--------|--------|
| **Severity** | MEDIUM |
| **File** | `indicators.py` |
| **Line** | `indicators.py:237-241` |
| **What** | `sd` uses divisor `(p-1)`. |
| **Should** | Population std `p` (ddof=0) for conventional Bollinger. |
| **Impact** | Slightly wider/narrower bands vs textbook BB; BB-dependent research lab / mean-reversion factors skew. |
| **Fix** | `variance = sum(...) / p` (with `p>0`). |
| **Test** | Fixed window compare to reference BB with population σ. |

---

### BUG-A-4 — `CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED` has no code consumer

| Field | Value |
|--------|--------|
| **Severity** | LOW |
| **File** | `config.yaml:1148`, `config.py` default only |
| **Line** | No `.py` references outside config defaults — toggle is **dead**. |
| **What** | Key exists but nothing reads it in product code. |
| **Should** | Gate WS micro injection in scoring path, or remove key. |
| **Impact** | False belief microstructure affects Engine A score. |
| **Fix** | Wire factor path or delete from validator. |
| **Test** | Flip flag; assert score path differs or key removed. |

---

### BUG-A-5 — Unreachable `state == 2` branches in `detect_regime`

| Field | Value |
|--------|--------|
| **Severity** | LOW |
| **File** | `regime.py` |
| **Line** | `regime.py:65-87` — `state` is only **0 or 1** before BB relabel; `elif state == 2` at lines **72–76** cannot run on that pass. |
| **What** | Dead / misleading confidence tuning for `HIGH_VOLATILITY` before state can be 2. |
| **Should** | Reorder (apply BB upgrades first) or remove dead branches. |
| **Impact** | Maintenance confusion; intended BB+high-vol confidence logic never runs as written. |
| **Fix** | Compute provisional label, apply BB state transitions, **then** adjust confidence. |
| **Test** | `bb_width_pct` high, ADX ranging path → assert confidence rules exercised. |

---

### BUG-A-6 — `FACTOR_CONVICTION_FLOOR` comment contradicts formula

| Field | Value |
|--------|--------|
| **Severity** | MEDIUM (calibration / ops) |
| **File** | `config.yaml` line 183; `factor_scoring.py` formula `1876` |
| **What** | Comment says lowering floor “allows more signals through,” but `final_score = base_score * (floor + (1-floor)*conviction)` implies **lower floor reduces multiplier for every conviction ∈ [0,1)** vs a higher floor. |
| **Should** | Reconcile comment with math (or rename parameter if semantics changed). |
| **Impact** | Wrong operator interpretation when tuning. |
| **Fix** | Correct comment or invert intended semantics with a different formula. |
| **Test** | Parametrize floor → monotonicity expectations encoded in unit tests. |

---

### BUG-A-7 — A-only `combinedConviction` hard-capped by `AUTO_TRADE_A_ONLY_WEIGHT`

| Field | Value |
|--------|--------|
| **Severity** | HIGH when `AUTO_TRADE_MIN_CONVICTION` > weight |
| **File** | `scanner.py:120-137`, `909-912`; `config.yaml:813-823` |
| **What** | `combinedConviction` ≤ `AUTO_TRADE_A_ONLY_WEIGHT` at `scoreNorm=1`. |
| **Should** | Document as invariant or decouple gate from blend weight. |
| **Impact** | Autopilot can reject **all** A-only signals if min conviction > weight. |
| **Fix** | Use separate cap, or set default min ≤ min(weight_by_asset). |
| **Test** | `AUTO_TRADE_MIN_CONVICTION` > weight → A-only never passes `_can_execute`. |

---

### BUG-A-8 — `PAIR_PROFILES` weight_overrides ineffective for v2 factor core

| Field | Value |
|--------|--------|
| **Severity** | MEDIUM |
| **File** | `tests/test_factor_group_overrides.py:28-47` |
| **What** | Test proves `weight_overrides` do **not** change `compute_factor_scores`. |
| **Should** | Config comments imply per-pair vote weighting; v2 ignores. |
| **Impact** | XAU/USD etc. `weight_overrides` misleading. |
| **Fix** | Wire overrides or mark `LEGACY_SCANNER_VOTES_ONLY` in YAML. |
| **Test** | Already exists — extend to fail if profile claims affect factor scores. |

---

### BUG-A-9 — Config comment falsely ties INDICATOR_WEIGHTS to `directional_factors` in factor_scoring

| Field | Value |
|--------|--------|
| **Severity** | LOW |
| **File** | `config.yaml:1154-1157` |
| **What** | No `directional_factors` / `nondirectional_factors` lists in `factor_scoring.py`. |
| **Should** | Update comment to actual code references (`_coherent_trend_score`, `_momentum_quality`). |
| **Impact** | Audit confusion. |
| **Fix** | Edit comment only. |

---

### BUG-A-10 — `CRYPTO_TRANSITION_PENALTY` / `CRYPTO_TRANSITION_PENALTY_ENABLED` unused in scoring

| Field | Value |
|--------|--------|
| **Severity** | LOW |
| **File** | `regime.py`, `intermarket.py`, `factor_scoring.py` (no matches); `config.py` ~644 |
| **What** | YAML note “see regime.py” is **inaccurate** — **no** transition penalty in `regime.py`. |
| **Should** | Remove keys or implement. |
| **Impact** | Dead configuration surface. |
| **Test** | Grep / import-time registry test for orphan keys. |

---

## THRESHOLD ASSESSMENT

| Key / area | Verdict | One-line justification |
|------------|---------|------------------------|
| `ADX_TREND_MIN_CLASS` (crypto 18, forex 20, etc.) | **CALIBRATED** (with caveat) | Linear ADX ramp to 1.0 at `trend_min` with `hard_fail=10` yields a real band; **empirical distribution across asset classes NOT VERIFIED**. |
| `FACTOR_ADX_HARD_FAIL_CLASS` all 10 | **TOO_LOOSE vs old comment** | Comment said global default 15; YAML forces 10 — widens soft zone; intentional per yaml note. |
| `FACTOR_MIN_DIRECTIONAL` / crypto variants | **CALIBRATED** | Aborts weak `trend_score` before ADX/momentum spend; soft span smooths edge. |
| `RSI_BOUNDS` | **CALIBRATED** | Aligns with asset-class docs in yaml; drives `_momentum_quality` zones only. |
| `FACTOR_CONVICTION_FLOOR` 0.20 | **TOO_LOOSE vs stated intent** | Math + comment mismatch (BUG-A-6); floor value needs operator-facing correction. |
| `VOLATILITY_SCALER_BANDS` | **CALIBRATED** | Per-class bands fix old global crypto-scale bleed; `nat_gas` / `crypto_doge` clamp `vol_scaler >= 1` (`factor_scoring.py:1680-1681`). |
| `RANGING` (regime.py thresholds) | **CALIBRATED** | Sets ADX-based regime labels; does **not** directly multiply Engine A score except optional `CONVICTION_FLOOR_BY_REGIME` — **full matrix NOT VERIFIED**. |
| `PAIR_PROFILES.min_confluence` | **TOO_LOOSE** for listed pairs | e.g. XAU **1.05** vs tier stable **1.5** — deliberate loosening; risk if scan gate is profile-first (`scoring.py:288-297`). |
| `PAIR_PROFILES.bt_min` | **UNREACHABLE** for live threshold | Tests document `get_min_confluence_threshold` uses `min_confluence` only. |
| `AUTO_TRADE_MIN_CONVICTION` default 0.50 | **CALIBRATED** vs default A-only weight 0.60 | Headroom exists; **per-asset overrides NOT VERIFIED** in production configs. |
| `INTERMARKET_CONFIRMATION.engine_a_enabled` false | **TOO_STRICT** on feature | Default applies **no** intermarket delta (`intermarket.py:1377-1384`) — neutral default. |

---

## DEAD FACTORS (INDICATOR_WEIGHTS vs Engine A v2 `final_score`)

| Factor / key group | Status | Evidence |
|--------------------|--------|----------|
| `INDICATOR_WEIGHTS.derivatives` (`cot_z`, `funding_rate`, `oi_*`) | **DEAD** for weighted aggregate | Funding/OI enter via `_funding_addon` / `_oi_addon` + combo caps, **not** via these weights |
| `INDICATOR_WEIGHTS.microstructure` | **DEAD** for `final_score` | Only appears in `filtered_indicators` for confidence_engine |
| `INDICATOR_WEIGHTS.volatility` | **DEAD** for `final_score` | Volatility handled by `_volatility_scaler` on ATR/close, not `atr_z`/`bbWidth_z` weights |
| `INDICATOR_WEIGHTS.volume` | **DEAD** for `final_score` | `volume_ratio` adjusts via `_total_adj` path, not OBV weights |
| `INDICATOR_WEIGHTS.carry` | **DEAD** as weight | Carry incorporated as **addon** signal, not `carry_z` weight |
| `REGIME_WEIGHTS`, `FACTOR_SCORE_GROUP_MULTIPLIERS`, `CRYPTO_FACTOR_WEIGHT_CAPS` | **DEAD** | Explicit legacy in `config.yaml:1374-1376` |
| `NORMALIZATION_LOOKBACK` for core momentum | **PARTIAL** | Used for **normalized** indicator path (`indicators.py:1118`); `_momentum_quality` uses **raw** `h4_snap["rsi"]` / MACD hist |
| `volume_momentum_spread` (crypto momentum) | **PARTIAL** | Exposed to confidence inputs if present (`factor_scoring.py:427-437`), **omitted** from `mom_quality` blend (BUG-A-2) |

---

## NOT VERIFIED

- Git history proving `FACTOR_CONVICTION_FLOOR` “was 0.60” (only yaml comment at line 183).
- Empirical ADX distributions (forex/crypto/stocks/indices) vs class thresholds.
- Full `intermarket.py` universe / driver catalog (only `apply_confirmation_to_score` and aggregation excerpt read).
- `feature_normalizer.zscore_normalize` numerical stability / warm-up.
- End-to-end `calc_indicators` vs `calc_indicators_with_normalized` which path production scan uses for Engine A snaps.
- Whether operator `config.local.yaml` overrides change the A-only vs min-con conviction gap.

---

## Factor pipeline one-liners (Section 4, `INDICATOR_WEIGHTS`)

| Factor | Status |
|--------|--------|
| `trend.*` (`d1_ema_trend`, `h4_ema_trend`, `ema_trend`) | **ACTIVE** — `_coherent_trend_score` |
| `momentum.default` / class (`rsi_z`, `macdLine_z`) | **ACTIVE** — weights drive `_momentum_quality` / **WRONG alias**: keys named `*_z` but code uses **raw** RSI + MACD hist, not z-series |
| `momentum.crypto.volume_momentum_spread` | **PARTIAL** — key mismatch / skipped in `mom_quality` (BUG-A-2) |
| `derivatives.*` | **DEAD** for declared parent-weight semantics | Addon path separate |
| `microstructure.*` | **DEAD** for score | Confidence/UI only |
| `volatility.*` | **DEAD** as weighted factors | ATR-based scaler instead |
| `volume.*` | **DEAD** as weights | Volume ratio small adj |
| `carry.carry_z` | **DEAD** as weight | Carry addon |

**OVERKILL:** `trend` class weights sum to 1.0 per TF — they dominate *within* trend coherence; combined with ADX ramp and conviction, no single INDICATOR_WEIGHTS leaf “overkills” total score except **trend magnitude** being the primary structural driver by design.

---

## Section 5 — Regime and intermarket

- **regime.py:** Single `state` then optional BB relabel **1→2** or **1→3** (`regime.py:78-87`); **HIGH_VOL** never set from ADX alone in first branch (only via BB). **ADX `None` → RANGING** (`regime.py:45-48`).
- **`CRYPTO_TRANSITION_PENALTY`:** **No effect** on scores in `regime.py` / `factor_scoring.py` — note in yaml referencing `regime.py` is **misleading**.
- **intermarket.py:** **Modulation** (bounded delta), not a universal hard gate, when enabled (`intermarket.py:1366-1399`); default `engine_a_enabled: false` (`config.yaml:1446`).

---

## Section 6 — PAIR_PROFILES

- **Threshold:** Profile **`min_confluence` replaces** base resolution first (`scoring.py:288-297`) — **override**, not multiplier.
- **Fallback:** Without profile, `ENGINE_A_SCORE_GROUP_THRESHOLDS` → 3-tier (`scoring.py:251-297`).
- **Silent pass below global tier:** Possible when profile sets **lower** `min_confluence` (e.g. 1.05 < 1.5) — **by design**, not silent bug.
- **`weight_overrides`:** **No** effect on v2 `compute_factor_scores` (test-backed in `tests/test_factor_group_overrides.py`).

---

## Reference — forex_scoring.py, confidence_engine.py, calibration.py

- **forex_scoring.py:** Legacy; live path is `athena.py` → `scoring.calc_confluence` → `factor_scoring.compute_factor_scores` (stated in module docstring).
- **confidence_engine.py:** Uses `filtered_indicators` / factor map for **confidence** (0–1), not execution threshold; `combinedConviction` not defined here.
- **calibration.py:** Audit DB score normalization and calibrated probability; no auto-trade gate; no `combinedConviction`.

---

*End of audit document.*
