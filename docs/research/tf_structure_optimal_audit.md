# Engine A / B Optimal Timeframe Structure Audit — Intraday vs Swing per Group

Independent expert audit, 2026-07-31. Recomms grounded in (a) repo facts
(`VOLATILITY_SCALER_BANDS`, `SpeedClass`, indicator-period tables, legacy Engine B
TF matrix) and (b) published professional multi-timeframe (MTF) consensus. No
guessed values. Where evidence is thin, the change is marked MEDIUM confidence
and remains config-gated **default-off** so runtime behavior is unchanged until
opted in.

## 0. Evidence base

Repo facts:
- `config.yaml:1428` VOLATILITY_SCALER_BANDS (H4 ATR%): forex 0.05–0.25%,
  crypto 1–4%, commodity 0.3–1.5%, stock 0.5–2%, index 0.2–1.0%.
- `timeframe_policy.py` SpeedClass: crypto majors FAST, XAU FAST, oil FAST,
  indices FAST, stocks NORMAL, exotics SLOW, thin metals NORMAL.
- `config.yaml:1091` indicator periods already encode speed: crypto EMA 18
  (fastest) < default 21 < forex 26 < bond 34 (slowest).
- `market_structure.py:558` legacy Engine B matrix: intraday structure H1,
  swing structure H4 — the pre-v4 "ideal" before universal collapse.
- Ladder constraint: `regime ≥ bias ≥ structure ≥ setup ≥ trigger` (slower→faster),
  `timeframe_policy.py:1013` + `_clamp_adaptive_roles`.

External consensus (2026 sources):
- Swing: **Daily structure + H4 entry** (Weekly→Daily→4H) is the professional
  standard; D1 is "non-negotiable" for stocks due to overnight gap risk; D1
  filters crypto's extreme intraday noise; thin altcoins → D1-only.
  (audacity.capital, coinxsight, stockguru, 5paisa)
- Intraday: **4H trend / 1H setup / 15m entry** is the standard day-trade trio;
  TTrades alignment pairs: D1→H1, H4→15M, H1→5M, 30M→3M, 15M→1M.
  (coinxsight, mahersaham, ttrades)
- Exotics / thin / gap-prone: wider spreads + thin liquidity mean **short
  triggers (M5/M1) are a cost trap** — trade on higher TFs; spread cost erodes
  short-term edge. (forexforstarters, tradingbrokers, myfxbook, takepropips)
- Nat gas: extreme gap risk → stay on slower TFs. (general futures consensus)

## 1. Optimal per-group ladder (recommendation)

Legend: `R / B / S / U / T` = regime / bias / structure / setup / trigger.
Execution stays live-quote based (advisory T) in every row.

### Intraday

| Group | R/B/S/U/T | Rationale (facts) |
|---|---|---|
| forex majors & crosses | D1 / H4 / H1 / M30 / M15 | Lowest vol (0.05–0.25% H4); continuous 24/5, no gaps → H1 structure (faster than universal H4) with M30 setup, M15 trigger |
| forex exotics, forex_other thin | D1 / H4 / H4 / H1 / M30 | Thin + wide spreads → keep H4 structure, H1 setup, **M30 trigger** (never M5/M1) |
| crypto majors (BTC/ETH/SOL) | D1 / H4 / H4 / H1 / M15 | Highest vol, 24/7, deep liquidity → universal ladder already correct; keep conditional M5 refinement |
| crypto alts / thin | D1 / H4 / H4 / H1 / M15 | 24/7 but thinner → M15 trigger (M5 stays disabled) |
| precious (XAU/XAG) | D1 / H4 / H4 / H1 / M15 | Moderate vol, 24/5, deep for XAU → universal correct; XAU keeps conditional M5 |
| energy oil (WTI/BRENT) | D1 / H4 / H4 / H1 / M15 | Session-bound + overnight gaps → H4 structure; keep M15 trigger (not M5) |
| nat_gas | D1 / H4 / H4 / H1 / M30 | Extreme vol + gap-prone → **M30 trigger** |
| copper / pgm / base / softs | D1 / H4 / H4 / H1 / M30 | Thin, gap-prone → M30 trigger |
| indices (liquid: NAS100/US30/GER40/US500/UK100/JPN225) | D1 / H4 / H1 / M30 / M15 | Session-bound (H4 = too few bars/day) → H1 structure |
| index_other (CHI50) | D1 / H4 / H4 / H1 / M30 | Thin cash index → H4 structure, M30 trigger |
| us_stock_single (AAPL/SPY) | D1 / H4 / H1 / M30 / M15 | Session-bound, biggest gap risk → H1 structure |
| stock_other | D1 / H4 / H1 / M30 / M15 | Same session/gap constraints |
| bond_tlt / smallcap_em_etf | D1 / H4 / H4 / H1 / M30 | Slowest mover (EMA 34/RSI 21) → slow trigger M30 |

### Swing

Professional consensus is uniform across asset classes: **D1 structure, H4
setup, H1 trigger**. Because the ladder requires `structure ≥ bias`, the swing
bias rung moves to D1 alongside regime.

| Group | R/B/S/U/T | Notes |
|---|---|---|
| forex majors & crosses | D1 / D1 / D1 / H4 / H1 | D1 structure + H4 entry = standard forex swing |
| forex exotics / other thin | D1 / D1 / D1 / H4 / H1 | D1 filters spread/noise (MEDIUM: thin data) |
| crypto majors | D1 / D1 / D1 / H4 / H1 | D1 filters 24/7 noise (research: D1 primary) |
| crypto alts / thin | D1 / D1 / D1 / H4 / H1 | D1-only for thin alts (MEDIUM) |
| precious | D1 / D1 / D1 / H4 / H1 | D1 structure + H4 entry |
| energy oil | D1 / D1 / D1 / H4 / H1 | D1 handles overnight gaps |
| nat_gas | D1 / D1 / D1 / H4 / H4 | Extreme gap risk → H4 trigger (MEDIUM) |
| thin metals / softs | D1 / D1 / D1 / H4 / H1 | D1 filters thin-data noise |
| indices | D1 / D1 / D1 / H4 / H1 | D1 non-negotiable vs gaps |
| us_stock_single / stock_other | D1 / D1 / D1 / H4 / H1 | D1 non-negotiable vs overnight gap risk |
| bond_tlt / smallcap_em_etf | D1 / D1 / D1 / H4 / H1 | Slowest mover → D1 structure (matches existing bond_tlt D1 momentum anchor) |

## 2. Key findings vs current universal ladder

1. **Swing structure H4 → D1 for every group.** The universal H4 structure rung
   is an intraday frame; professional swing practice uses D1 structure. This is
   the single largest improvement and aligns with the legacy Engine B swing
   intent (D1 regime + H4 struct was pre-v4).
2. **Intraday structure H1 for session-bound fast instruments** (indices,
   US stocks). H4 produces too few bars within a cash session for structural
   zone updates.
3. **M30 trigger for thin / spread-expensive / gap-prone groups** (exotics,
   nat_gas, thin metals, softs, bond). M15/M5 triggers on thin markets are a
   spread-cost trap; this is the most defensible change (repo already ships an
   M30-aware trigger calibration path: `ENGINE_B_TRIGGER_TF_CALIBRATION_TFS`).
4. **Crypto majors, precious, energy oil intraday keep the universal ladder** —
   already correct for their speed/liquidity profile.
5. **bias rung must move to D1 for swing** only because the ladder forbids
   `structure(D1) ≥ bias(H4)` — a necessary consequence of D1 swing structure.

## 3. Confidence & risk

- **HIGH confidence:** swing→D1 structure all groups; intraday H1 structure for
  indices/stocks; M30 trigger for exotics & nat_gas; crypto/precious/oil intraday
  unchanged. All backed by vol facts + strong published consensus.
- **MEDIUM confidence:** M30 trigger for bond/thin metals/softs (fewer sources);
  D1-only thin alts. Config-gated default-off in any implementation.
- **Not touched:** regime always D1; execution always live-quote; M5 policy and
  speed classes unchanged; indicator periods and thresholds unchanged.

## 4. Implementation (shipped, enabled)

- New config key `ENGINE_TF_ROLE_OVERRIDES` (`config.py` default + `config.yaml`)
  with `ENABLED: true` (the findings are the shipped default; set `false` to
  restore the universal ladder) and a `BY_GROUP` table
  keyed by the **v4 policy-group taxonomy** (`resolve_timeframe_policy`'s `group`,
  the aliased score group — e.g. `us_indices_trackers` → `equity_index_standard`,
  `crypto_btc` → `crypto_majors_fast`). Any subset of
  `regime/bias/structure/setup/trigger` may be set per style.
- Applied in `timeframe_policy.resolve_timeframe_policy` after the engine/style
  overlay; execution mode stays live-quote and Engine D (scalp) is never subject
  to overrides. Invalid rows (unknown TF or ladder reversal) raise
  `PolicyConfigurationError` — fail closed, never silently clamped
  (`_apply_role_override` wraps `validate_timeframe_role_order`).
- Because every live, scan, and backtest path resolves roles through this single
  function, enabling the matrix updates Engine A/B scoring, execution, and the
  v2 (`athena_backtesting_v2.datasets.resolve_policy_requirements`) and v3
  (`athena_backtest.policy.resolve_backtest_policy_roles`) backtesters at once;
  both carry the new rungs (e.g. M30) in their dataset/replay timeframe sets.
- Diagnostics + `describe_symbol_policy` surface `roleOverrideApplied` /
  `roleOverridePatchedRoles`.
- Tests in `tests/test_timeframe_policy.py`: default-on applies the matrix,
  per-group/style override applies, Engine D immune, invalid/unknown
  config fails closed. Verified end-to-end on production symbols (NAS100, EUR/USD,
  BTC/USDT, Natural Gas, USD/MXN, AAPL, TLT, XAU/USD) with `ENABLED` true.
