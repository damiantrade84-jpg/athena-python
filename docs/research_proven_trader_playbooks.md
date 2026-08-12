# Proven Trader Playbook Research — Basis for Engine A / Engine B / Chart-Review AI Adjustments

Date: 2026-08-12 · Status: research summary (sources listed inline) · Advisory only — no deterministic gates changed.

This document summarizes what consistently profitable, documented traders actually do to spot
breakouts, read patterns, and read candles — and maps each principle to where it landed in the
Athena AI playbooks (`ai_playbooks/`, `prompts/chart_review_*.md`, `configs/ai_strategy_playbooks.yaml`).

## 1. What the proven traders converge on

### Mark Minervini — SEPA / VCP (2x US Investing Championship winner, audited 255% and 334.8% years)
- Trades only Stage 2 uptrends (8-point Trend Template: price above 50/150/200-day MAs, 200-day rising,
  within 25% of 52-week high, RS >= 70).
- Volatility Contraction Pattern: 2-6 pullbacks getting progressively tighter (e.g. 18% → 9% → 4%) with
  volume drying up into the final contraction. Contraction = supply absorbed = coiled spring.
- Entry: breakout above the pivot (high of final contraction) on volume >= 40-50% above average.
- Stop: below the final contraction low, typically 3-7%; risk 1-2% of equity per trade.
- SEPA confluence rule: Specific entry + Earnings + Price action + Announcement/catalyst must ALL align.
Source: financialtechwiz.com, finermarketpoints.com, tradingsim.com VCP guides.

### William O'Neil — CAN SLIM / cup-and-handle
- Prior advance of at least ~30% before the base; cup depth 12-33%, handle in the upper third of the cup,
  handle volume MUST decline.
- Entry above handle high on volume >= 50% above average ("real breakouts are loud"); low-volume breakout
  is a probe that likely fails.
- Stop below handle low; target = cup depth projected from breakout (measured move), hit ~65-70% with volume.
Source: dxpa.in cup-and-handle system guide, financialwisdomtv.com.

### Nicolas Darvas — Box theory
- Only stocks at/near 52-week highs; defined box (range) with multiple touches; buy stop just above box top;
  stop inside the box (5-8%); volume expansion required; pyramid into new higher boxes.
Source: financialwisdomtv.com.

### Kristjan Kullamägi (Qullamaggie) — momentum continuation
- Prior leg required: +25-30% (or more) in the last 60 days / 30-100% in 1-3 months — never trades breakouts
  from quiet stocks.
- Tight base: 20-bar range <= ~20% of close, price within 10% of EMA20, volume contraction. "A clean base
  looks boring."
- Entry at pivot/20-day high or opening-range high on volume; stop 1.5x ATR or breakout-day low (2-3%);
  trail EMA10/20; scale out at 2R/4R; risk 0.25-1% per trade. Win rate ~20-25%, winners 5-20R — asymmetry
  is the edge.
Source: sovascan.com, easyswing.trading, sharepredictions.com planner docs.

### Richard Wyckoff / price-action school — sweeps, springs, candle confirmation
- Spring/upthrust: wick through an obvious liquidity pool then close back inside the range + displacement =
  valid sweep; drift through and hold = no sweep.
- Candle confirmation at key events: engulfing, pin bar (hammer/shooting star, wick >= 2x body), inside-bar
  break — improves entry timing only when located AT a level (Nison Research: ~2.3 bars better timing).
- Breakout confirmation requires body close beyond the level — shadows alone are invalid.
- Volume: expands on the break/sweep candle, dries up in the handle/contraction.
Source: fxnx.com, tradealgo.com, technicalresources.in, colibritrader.com, docsbot.ai gold-trading curriculum.

### Universal risk frame (all of the above)
- Risk 0.25-2% of equity per trade; stop at structural invalidation (contraction low, handle low, box floor,
  sweep extreme) — not arbitrary percentages.
- Measured-move targets as sanity check; trail winners, never widen stops.
- Environment filter: most successful breakouts occur when the broader market is trending up — regime matters.

## 2. The five principles that map directly onto Athena

| Proven-trader principle | Where it now lives in Athena |
|---|---|
| Bases must contract (VCP/box/handle); loose wide bases = distribution | Engine A `chartReadingProtocol.baseQuality`; YAML `BASE_BREAKOUT_CONTINUATION` |
| Breakout = confirmed body close + volume expansion; wicks are probes | Engine A `breakoutValidity`; YAML confirmation/invalid_if |
| Close back inside the range = failed breakout, read the other side | Engine A invalidations; YAML `invalid_if` + existing `FAILED_BREAKOUT_REVERSAL` |
| Sweep = wick through obvious pool + close back inside + displacement (spring/upthrust) | Engine B `chartReadingProtocol.sweepAndReclaim`; `prompts/chart_review_b_v3.md` |
| Entry-quality candles: engulfing / pin bar (wick >= 2x body) / inside-bar break AT a level, confirmed bars only | Engine A `candleConfirmation`; Engine B `zoneRetestConfirmation` |
| Extension measured in ATR; retest entries preferred over chasing impulse candles | Engine A `breakoutValidity`; Engine B `acceptanceVsChase` |
| Measured-move target sanity check; server TP/SL always authoritative | Both playbooks `targetContext`; YAML `target_logic` |

## 3. Changes made in this pass

1. `ai_playbooks/engine_a_playbook.py` — added `chartReadingProtocol` (baseQuality, breakoutValidity,
   candleConfirmation, targetContext) + one failed-breakout invalidation line.
2. `ai_playbooks/engine_b_playbook.py` — added `chartReadingProtocol` (sweepAndReclaim,
   zoneRetestConfirmation, acceptanceVsChase, targetContext) + one sweep-invalidation line.
3. `ai_playbooks/__init__.py` — `chartReadingProtocol` added to the compact prompt-render whitelist so it
   actually reaches the model.
4. `prompts/chart_review_a_v3.md` — new workflow step 5: chart-reading protocol before output.
5. `prompts/chart_review_b_v3.md` — chart-reading paragraph added to the preamble (the file's
   "Workflow (required):" ending is by design; numbered steps are appended by `ai_review/prompt_builder.py`).
6. `configs/ai_strategy_playbooks.yaml` — new classifier playbook `BASE_BREAKOUT_CONTINUATION`
   (engines A, B) filling the gap: previously there was no positive breakout-continuation model, only
   pullback / failed-breakout / sweep / wick / mean-reversion / chop.

## 4. Explicit non-changes (safety contract preserved)

- No deterministic score, gate, threshold, SL/TP, RR, or sizing logic touched. All additions are advisory
  prompt/playbook text; every new line defers to server-emitted deterministic levels and flags.
- Engine A and Engine B playbooks remain independent — no scoring semantics copied between engines.
- No thresholds hardcoded into Python logic; volume figures (40-50%, 1.4-1.5x) live only in advisory
  prompt text, consistent with how the playbooks already phrase guidance.
- AI remains advisory: it can confirm, question, or downgrade timing; it cannot approve execution.
