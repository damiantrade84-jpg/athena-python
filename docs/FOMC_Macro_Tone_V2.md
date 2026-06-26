# FOMC Hawkish/Dovish Tone Scoring — V2 (Phase 13 Report)

Deterministic, explainable macro **CONTEXT** layer on top of the V1 FOMC feed. Scores an
FOMC statement on a −100 (dovish) … +100 (hawkish) scale with evidence and confidence,
and exposes it to API / UI / scan / AI review. **It never produces a trade signal, never
changes any engine score / threshold / SL-TP / RR / sizing, and never grants execution
permission.** Macro lockout still owns blocking.

## 1. Files changed

**New**
- `macro/fomc_tone.py` — phrase dictionaries, deterministic scoring engine, differential
  (added/removed/unchanged/intensified) comparison, confidence model, symbol pressure
  interpretation, `process_event()` store integration, CLI `--event`.
- `macro/tone_store.py` — `MacroToneStore` + `macro_tone_scores` table (idempotent, same
  `macro_events.db`), `default_tone_store()`.
- `tests/test_fomc_tone.py`, `tests/test_fomc_tone_api.py`, `tests/test_fomc_tone_ai_context.py`.
- `docs/FOMC_Macro_Tone_V2.md` (this report).

**Edited (additive only)**
- `athena_app/api/routes_macro.py` — `GET /api/macro/fomc-tone[?event_id=]` + `_tone_response`.
- `macro/scan_integration.py` — `_attach_tone()` adds tone metadata to candidates; `_latest_tone()`.
- `ai_review/macro_context.py` — `fomcTone` block + tone instruction in the AI prompt.
- `macro/fomc_rss.py` — best-effort `process_event()` after a statement is ingested.
- `static/.../shared/MacroBadge.tsx` — adds `ToneChip`; bundle rebuilt.

## 2. Existing V1 modules reused (no parallel systems)

- `macro/store.py` event store (`raw_text`, `raw_url`, previous/actual target range) — statement
  text source + previous-statement lookup via `LOCKOUT_EVENT_TYPES`.
- `macro/store._default_db_path` — tone table lives in the same DB.
- `macro/macro_guard.py` — lockout state + `macroBlockNewTrades`/`macroReason` for scan/AI.
- `macro/fomc_rss.py` ingestion path — drives tone scoring on release.
- `ai_review/macro_context.py` + `prompt_builder.py` wiring; `routes_macro.register_macro_routes`.

## 3. Existing FRED injection reused (confirmation only)

`target_range_change_bps` is derived from the event's **already FRED-confirmed**
`previous_lower/upper` + `actual_lower/upper` (populated by V1 `macro/fomc_fred.py` →
`carry_feed.get_fred_latest_rate`, series `DFEDTARU`/`DFEDTARL`/`FEDFUNDS`). **No new FRED
client or data path.** FRED remains historical/confirmation only.

## 4. Tone model / table

`macro_tone_scores` (all spec fields): id, macro_event_id, source, event_type, country,
currency, title, statement_url, statement_published_utc, previous_statement_event_id/url,
tone_label, tone_score, confidence, rate_decision_score, statement_language_score,
inflation_language_score, labor_market_score, growth_score, balance_sheet_score,
forward_guidance_score, dot_plot_score, press_conference_score,
market_expectation_surprise_bps, target_range_change_bps, evidence_json, warnings_json,
raw_text_hash, previous_text_hash, created_at, updated_at. Idempotent by `id = tone:{event_id}`
(created_at preserved on re-score).

## 5. Scoring rules implemented

- **Components & weights** (relative, over categories that carry signal so a lone decisive
  category is not diluted to neutral): statement_language 30%, inflation 20%, forward_guidance
  20%, labor 10%, growth 10%, balance_sheet 10%; rate_decision 25% **only when** target-range
  data exists.
- **Differential factors:** added 1.0 · unchanged 0.3 · removed 0.7 (reversed direction) ·
  absolute (no previous) 0.6. Intensify/soften transitions (e.g. "somewhat elevated"→"elevated").
- **Rate decision:** `rate_decision_score = clamp(target_range_change_bps × 2.4, −100, 100)`
  (hike = hawkish, cut = dovish, hold = 0). **Never called a surprise.**
- **Labels:** ≤−60 STRONGLY_DOVISH · ≤−35 DOVISH · <−10 MILDLY_DOVISH · −10..10 NEUTRAL ·
  <35 MILDLY_HAWKISH · <60 HAWKISH · ≥60 STRONGLY_HAWKISH · UNKNOWN if no text or confidence <0.40.
- **Confidence:** base 0.5; +prev text/diff/≥3 distinct/≥3 categories-agree/target-range;
  −no-prev/few-matches/conflicting; clamped 0–1.
- `dot_plot_score`, `press_conference_score`, `market_expectation_surprise_bps` stay **null**
  (no SEP / transcript / expectation feed) with explicit warnings.
- *Phase-4 vs Phase-5 conflict on "greater confidence … toward 2 percent" resolved as
  **dovish*** (Phase-5 governs — it is the stated precondition for cuts).

## 6. API / UI / AI integration

- **API:** `GET /api/macro/fomc-tone` (latest) / `?event_id=` → `{toneLabel, toneScore,
  confidence, componentScores, evidence, warnings, targetRangeChangeBps,
  marketExpectationSurpriseBps:null, executionUse:"CONTEXT_ONLY"}`.
- **Scan:** adds `fomcToneLabel/Score/Confidence/Direction/Warnings` + `fomcExecutionUse=CONTEXT_ONLY`;
  `confluenceScore` and all engine scores untouched; `macroBlockNewTrades`/`macroReason` from guard.
- **AI:** `fomcTone` block (label/score/confidence/components/major evidence/warnings/expected
  pressure) + instruction: *"FOMC tone is macro context only … During macro lockout, do not
  recommend immediate execution."* Lockout still forces "MUST NOT output execution-ready".
- **UI:** compact header `ToneChip` — `FOMC TONE: <LABEL> · ±score · conf%` (hawkish red /
  dovish green / `LOW CONFIDENCE` muted). No TV-chart clutter.

## 7. Tests added & 8. results

`tests/test_fomc_tone.py` (15), `tests/test_fomc_tone_api.py` (2),
`tests/test_fomc_tone_ai_context.py` (2). Cover all 17 required cases (hawkish/dovish add,
unchanged-limited, added>unchanged, removed-hawkish/dovish, missing-previous-lowers-confidence,
missing-text→UNKNOWN, FRED read/unavailable, FRED-not-surprise, surprise-null, bounds,
API shape, scan metadata w/o confluence change, AI "context only", lockout-precedence).

```
python -m pytest tests/test_fomc_tone.py tests/test_fomc_tone_api.py tests/test_fomc_tone_ai_context.py -q
  → 19 passed
python -m pytest tests/test_macro_guard.py -q
  → 9 passed
npm run build  → tsc -b clean; bundle index-BszRWlgN.js
```

## 9. Limitations

- Statement text quality depends on RSS `raw_text` (or fetched `statement_url`); partial text
  lowers confidence. No SEP/dot-plot or press-conference transcript parsing (fields null).
- **No market-expectation feed** → `market_expectation_surprise_bps` always null by design.
- Phrase dictionary is curated, not exhaustive; novel wording may under-score (low confidence
  surfaces this). Intensify/soften detection is substring-based and conservative.

## 10. Confirmation of non-changes

No Engine A/B/D score math, thresholds, SL/TP, RR, sizing, risk rules, execution, or autotrade
behavior were changed. No FOMC trade signal is produced. AI cannot override lockout. No duplicate
FRED ingestion. No FRED-derived "surprise". No live network in tests. No broad refactor. Tone is
strictly `executionUse = CONTEXT_ONLY`.
