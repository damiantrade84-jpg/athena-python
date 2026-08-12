# Phase 0 — AI Chart Review Fix Pack File Map

Generated 2026-08-12. Paths are absolute under `C:\dev\athena-python\`.

## 1. Symbol resolution / chart vs engine aliasing

| Responsibility | Path | Lines (approx) |
|----------------|------|----------------|
| Commodity catalog `SI=F` / `XAG/USD` pairs | `athena.py` | 484–515 |
| Vendor ticker overrides (EODHD/Polygon spot) | `athena.py` | 1530–1536 |
| YFinance pricing resolution (futures block) | `athena.py` → `athena_app/services/commodity_identity.py` | `resolve_yfinance_pricing_symbol` |
| Commodity identity / futures_proxy | `athena_app/services/commodity_identity.py` | full file |
| Chart pair lookup | `athena_app/api/routes_market_data.py` | `_chart_symbol_key` ~395, `_find_chart_pair` ~406 |
| Chart commodity instrument stamp | `athena_app/api/routes_market_data.py` | `_commodity_chart_instrument_identity`, `api_candles` |
| AI chart candidate join by symbol | `athena_app/api/routes_ai_chart_review.py` | `_resolve_server_candidate` 184–254, `_candidate_matches_symbol` |
| Provider mismatch only (not symbol) | `ai_review/engine_a_context.py` | ~1394–1516 |
| Intermarket alias map (unrelated to chart/engine join) | `intermarket.py` | `resolve_symbol_aliases` ~206 |
| UI “no Engine A candidate for chart symbol” | `static/react-app/app/src/lib/engineADiagnosticsDisplay.ts` | 52–55 |

**Gap:** no discrete `symbol_aliases.yaml` for intentional futures↔spot chart/engine joins. Provider mismatch checks providers only.

## 2. Deterministic review postprocessor (scores)

| Path | Role |
|------|------|
| `ai_review/summary.py` | `build_ai_review_summary`, `_score_visual` ~71, `_score_entry` ~91, `_score_risk` ~102, `_score_tradeability`, overall blend |
| `ai_review/concordance.py` | Engine A/B concordance / divergence |
| `ai_review/visual_text.py` | Directional vs non-directional visual contradiction |
| `ai_scalp_review/summary.py` | Engine D scalp mirror (separate surface) |

## 3. Payload builder (factorDiagnostics, atr, riskGeometry, components)

| Path | Role |
|------|------|
| `ai_review/engine_a_context.py` | `assemble_engine_a_context`, `_build_risk_geometry` ~50–89, `riskGeometry` / `activeEntryGate` / prompt projection ~1888 |
| `ai_review/engine_a_panel_adapter.py` | FieldSpec mapping into structured diagnostics panel |
| `ai_review/prompt_builder.py` | Prompt assembly from engine context |
| `ai_context.py` | Broader AI packet assembly (factorDiagnostics ~250–306) |
| `engine_a_v3/quant_scorer.py` | Produces factorDiagnostics / components / minDirectionalFailed |
| `engine_a_v3/evaluator.py` | Assembles EngineASignal with levels, validationArtifact, componentScores |
| `engine_a_v3/contract.py` | Signal contract / `to_dict` |

## 4. Context-completeness scorer

| Path | Role |
|------|------|
| `ai_review/context_diagnostics.py` | `build_context_diagnostics` ~693, `_classify_missing_items`, `_score` |
| `ai_scalp_review/context_diagnostics.py` | Scalp variant |
| `ai_review/routes` consumer | `athena_app/api/routes_ai_chart_review.py` attaches diagnostics after normalize |

## 5. Engine B structure context + overlay renderer

| Path | Role |
|------|------|
| Overlay API normalizer | `athena_app/api/routes_market_data.py` | `_normalize_engine_b_overlay_payload` ~700–771, `overlay_version: engine_b_legacy_v1` L743, `api_engine_b_overlays` |
| Engine B AI chart context | `ai_review/engine_b_context.py` | `assemble_engine_b_context` |
| Engine A structure_context pick | `ai_review/engine_a_context.py` | `_pick_engine_b_structure_payload`, `structure_context` attach |
| Empty structure warning | `athena_app/api/routes_ai_chart_review.py` | L548–569 `engine_b_overlays_enabled_but_server_structure_context_empty` |
| Naked analysis producer | `athena.py` | `_compute_naked_analysis` |
| Sanitize VP fields | `market_structure.py` | `sanitize_engine_b_structure_profile_fields` ~2100 |

## 6. Component gate evaluation

| Path | Role |
|------|------|
| Setup predicates (session, context_trend_long/short, volume, …) | `engine_a_v3/setups.py` | `_with_session_gate` ~418, `_trend_direction` ~452, setup candidates TRADE/WATCH |
| Quant predicates | `engine_a_v3/evaluator.py` | `_quant_predicates` ~233 |
| Quant minDirectional / components | `engine_a_v3/quant_scorer.py` | factor diagnostics |

## 7. Freshness policy

| Path | Role |
|------|------|
| Capture skew (max 120s) | `ai_review/timestamp_contract.py` | `evaluate_timestamp_mismatch` |
| Config constant | `config.py` | `AI_CHART_REVIEW.MISMATCH_WARN_MAX_SECONDS` ~998 |
| ATR freshness | `ai_review/freshness.py` | `classify_atr_freshness` |
| Candle freshness summary / policy_ok | `ai_review/engine_a_context.py` | `_build_candle_freshness_summary` ~1695, `freshness_is_policy_ok` ~1782 |
| Route wiring | `athena_app/api/routes_ai_chart_review.py` | mismatch_warnings then provider call (not pre-dispatch hard block) |

## 8. Validation artifact / setup labelling

| Path | Role |
|------|------|
| DEMO_UNVALIDATED artifact | `engine_a_v3/promotion.py` | `_demo_unvalidated` ~85–102 (`PromotionDecision(True, …)`) |
| Decision TRADE + qualified | `engine_a_v3/evaluator.py` | decision ~937, qualified ~981, validation_status ~1041–1050 |
| Execution refresh gate | `engine_a_v3/execution.py` | UNVALIDATED + DEMO_ONLY ~156–159 |
| Config flag | `config.yaml` L246 `ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED`; `config.py` ~1938 |
| UI validation display | `static/react-app/app/src/components/athena/EngineASignalCard.tsx` ~671 |

**Gap (closed by P0-2):** there was no discrete `setup_label` field; the UI used
`decision` (TRADE/WATCH/NO_SIGNAL) + `validationStatus`, and DEMO_UNVALIDATED could
still yield `decision=TRADE`. `setupLabel` now exists on the contract and is derived
from `artifact_status`.

---

## Delivered modules (added after Phase 0)

| Concern | Path |
|---------|------|
| P0-1 symbol parity + alias map | `ai_review/symbol_parity.py`, `configs/symbol_aliases.yaml` |
| P1-1..P1-4 + WATCH geometry | `ai_review/review_geometry.py` |
| P1-5/P1-6 shared structure contract | `ai_review/engine_b_structure_contract.py` |
| P2-1/P2-2/P2-3 gate hygiene | `ai_review/gate_hygiene.py` |
| P2-6 component decomposition | `ai_review/score_attribution.py` |
| Phase 4 WATCH transitions | `ai_review/watch_log.py` |

### `setup_label` consumers reading `== "TRADE"`

One, and it is conjunctive:

- `engine_a_v3/contract.py:132` — `execution_permitted` requires
  `setupLabel == "TRADE"` **and** `qualified` **and** `engineATradeEnabled` **and**
  `executionScope not in ("NONE", None, "")`.

No executor, `risk_engine`, or broker adapter reads `setup_label`; they continue to
read `decision` / `qualified` / `entryReadiness`. A DEMO_UNVALIDATED artifact forces
both `setup_label="SHADOW"` and `execution_scope="NONE"`, so the guard fails twice.
