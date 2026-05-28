---
name: athena-ui-chart-review
description: Use for static/react-app chart UI, native chart overlays, chart AI review panels, Vision payload wiring, screenshot/review contracts, and Engine B/D visual surfaces. Do not use for execution.py, risk gates, broker adapters, or changing Engine A/B/C/D scoring thresholds.
---

# Athena UI and chart review

UI and read-only review surfaces only.

## Contracts

- Native chart is the active review surface; avoid new legacy TradingView review features.
- Server-trusted diagnostics for score, threshold, ATR, RR — never accept client-only values for review truth.
- Chart AI review and Vision are advisory; must not connect to execution paths.
- Engine B overlays on TV Chart; Scalp Workbench is Engine D only.
- Preserve exact Vision footer tokens when touching parsers: `RIGHT EDGE`, `TF ALIGNMENT`, `RATING`, `LEVELS`.

## Steps

1. Trace API route → payload builder → React consumer.
2. Build a **coverage map** before verdict; follow `docs/codex-code-review-discipline.md` for reviews.
3. Compare UI display fields to backend contract and focused tests.
4. Run targeted frontend checks for touched packages only.
5. Adversarial pass: alternate endpoints, stale fallbacks, client-only values masquerading as server truth.

For shipped-change verification, use `athena-anti-miss-review` (UI/API lane).

## Inspect

`static/react-app/`, chart/review routes in `athena_app/api/`, `ai_context.py`, native chart screenshot builders, `vision_prompts.py` / `vision_hybrid.py` when Vision is in scope.

Read `static/react-app/AGENTS.md` when working in the frontend tree.
