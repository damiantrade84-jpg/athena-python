# AI Research Agent — Phase 4 Implementation

## What the Research Agent Does

The Research Agent is an advisory-only layer that reads completed research/backtest results, identifies strong and weak clusters, proposes the next useful research tests, and produces machine-readable research plans and recommendations.

- Reads existing outputs from `logs/research_lab/`, `logs/backtest_matrix/`, `logs/strategy_lab/`
- Computes aggregate metrics: win rate, profit factor, expectancy, drawdown
- Groups by engine, strategy family, asset group, symbol, timeframe, session, direction, regime
- Identifies top clusters (PF >= 1.3, min 30 trades) and weak clusters (PF < 1.0 or < 30 trades)
- Proposes research plans with hypotheses, jobs, and acceptance criteria
- Validates plans for safety (no live config, no live execution, requires approval)
- Compares runs across metrics with statistical reliability warnings
- Generates recommendations (all with `do_not_auto_apply=True`)

## What It Cannot Do

- **Cannot** modify live thresholds or config.yaml
- **Cannot** execute live trades
- **Cannot** auto-apply recommendations
- **Cannot** bypass guardian, freshness, kill-switch, RR, spread, fee, or risk gates
- **Cannot** override deterministic execution gates

## How It Proposes Plans

1. `load_latest_research_results()` scans known output directories for CSV/JSON files
2. `summarize_research_results()` computes aggregate metrics and clusters
3. `propose_next_research_plan()` generates hypotheses:
   - Weak clusters with insufficient sample → "need more data" hypothesis
   - Strong clusters with adequate sample → "validate OOS" hypothesis
   - No data → "initial discovery" hypothesis
4. Each hypothesis becomes a job with mode, symbols, timeframes, acceptance criteria
5. Guardrails enforce: max 20 jobs, max 20 symbols/job, max 5 timeframes/job

## Sample-Size Reliability Rules

| Trades | Label | Action |
|--------|-------|--------|
| 0 | unknown | Cannot draw conclusions |
| 1-29 | insufficient | Need more data; no recommendations |
| 30-99 | weak | Preliminary; avoid strong language |
| 100-499 | moderate | Validation possible with caution |
| 500+ | strong | Reliable clusters may exist |

## How to Run an Approved Plan

1. The plan must have `approved: true` (POST `/api/ai/research-agent/run-approved-plan`)
2. The system uses the existing `athena_research.run_manager.run_research()` if available
3. If runner unavailable, returns `not_implemented` safely
4. Outputs are written to `logs/research_agent/<plan_id>/`
5. Plans are persisted in `logs/research_agent/plans.jsonl`

## How Recommendations Should Be Used

- All recommendations set `do_not_auto_apply=True` at the Pydantic validator level
- Types: ADD_FILTER, REMOVE_FILTER, ADJUST_THRESHOLD_CANDIDATE, DISABLE_WEAK_SETUP, PROMOTE_TO_VALIDATION, NEED_MORE_DATA, DO_NOT_CHANGE
- Each recommendation includes: evidence, confidence, sample_size, reliability, implementation_notes
- Manual review required before any action

## Safety Boundaries

| Boundary | Enforcement |
|----------|-------------|
| No live config mutation | Pydantic validator forces `can_modify_live_config=False` |
| No live execution | Pydantic validator forces `can_execute_live_trades=False` |
| User approval required | `requires_user_approval=True` default |
| No auto-apply | `do_not_auto_apply=True` at validator level |
| No execution imports | Research Agent files never import broker modules |
| API rejection | Routes return 400 if approved flag missing/false |

## API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/ai/research-agent/latest` | GET | Latest research summaries |
| `/api/ai/research-agent/summarize` | POST | Summarize specific or latest run |
| `/api/ai/research-agent/propose` | POST | Propose next research plan |
| `/api/ai/research-agent/validate-plan` | POST | Validate plan safety |
| `/api/ai/research-agent/run-approved-plan` | POST | Execute approved plan |
| `/api/ai/research-agent/runs/<run_id>` | GET | Run detail/summary |
| `/api/ai/research-agent/runs` | GET | List stored runs |
| `/api/ai/research-agent/compare` | POST | Compare two runs |
| `/api/ai/research-agent/recommendations` | POST | Generate recommendations |
| `/api/ai/research-agent/plans` | GET | List stored plans |
| `/api/ai/research-agent/plan/<plan_id>` | GET | Get specific plan |

## AI Chat Integration

The AI chat (Marcus Reid) routes research questions to Research Agent tools:

- "what works best for crypto" → `get_latest_research_summary_tool`
- "what should we test next" → `propose_research_plan_tool`
- "compare runs X and Y" → `compare_research_runs_tool`
- "research plan for forex" → `get_latest_research_summary_tool`

Chat can propose research and explain findings. It cannot auto-run plans.
