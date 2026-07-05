# Athena EdgeLab — Research-Only Edge Discovery

Athena EdgeLab is a **persistent research daemon** that monitors data freshness, runs engine research loops, evaluates candidate patches, and surfaces suggestions in the UI — without touching live trading or execution.

## What EdgeLab does

- Runs continuously (or `--once`) while the server is up — typically as a sidecar process
- Checks data freshness per symbol/timeframe before engine research
- Runs research-safe engine loops (Engine B → Cascade → Engine A priority)
- Evaluates candidate patches via deterministic scoring
- Stores suggestions in SQLite (`edgelab_suggestions.sqlite`)
- Exports JSON for UI consumption
- Writes JSONL audit logs per cycle

## What EdgeLab does NOT do

- Does **not** modify live trading, broker execution, autotrade, schedulers, or risk gates
- Does **not** place orders or touch credentials/API keys
- Does **not** auto-promote changes to production
- Is **not** an uncontrolled self-coding agent
- Is **not** production-ready

## Why research-only

Production changes require **manual review and promotion**. EdgeLab only accepts patches under whitelisted research paths and rejects any path containing forbidden keywords (`execution`, `broker`, `autotrade`, etc.).

## Layout

```
research/edgelab/
  edgelab_daemon.py       # Main daemon CLI
  edgelab_config.yaml     # Configuration
  edgelab_suggestions.sqlite
  patch_evaluator.py      # Single-patch evaluation CLI
  export_suggestions.py   # JSON export for UI
  data_freshness.py       # Freshness monitor
  scoring.py              # Deterministic scorer
  candidate_generator.py  # Patch acquisition modes
  cycle.py                # One-cycle orchestration
  routes.py               # Flask API (research-only)
  engine_loops/           # Per-engine research loops
  candidates/ accepted/ rejected/ logs/ ledgers/ out/
```

## Quick start

**First milestone (safe dry-run):**

```bash
python research/edgelab/edgelab_daemon.py --dry-run --once
```

**One production-style cycle:**

```bash
python research/edgelab/edgelab_daemon.py --once
```

**Continuous (15 min interval):**

```bash
python research/edgelab/edgelab_daemon.py --interval-seconds 900
```

**Limited continuous run:**

```bash
python research/edgelab/edgelab_daemon.py --max-cycles 3 --interval-seconds 60
```

**Evaluate one patch:**

```bash
python research/edgelab/patch_evaluator.py --candidate-patch research/edgelab/candidates/candidate_001.patch
```

**Export suggestions JSON:**

```bash
python research/edgelab/export_suggestions.py
```

## UI / API

- **Sidebar tab:** EdgeLab Suggestions (research-only buttons: Mark reviewed / Reject / Promote to review branch)
- **API endpoints:**
  - `GET /api/edgelab/suggestions`
  - `GET /api/edgelab/freshness`
  - `GET /api/edgelab/export`
  - `GET /api/edgelab/status`
  - `POST /api/edgelab/suggestions/<id>/review` — `{ "action": "mark_reviewed|reject|promote" }`

JSON export: `research/edgelab/out/suggestions.json`

## Candidate generation modes

| Mode | Behavior |
|------|----------|
| `none` | Findings + freshness suggestions only (default) |
| `file_queue` | Test patches from `research/edgelab/candidates/` |
| `command` | External command outputs `.patch` path |
| `manual` | Wait for user to drop patch |
| `safe_stub` | Harmless stub patches for pipeline testing |

Configure in `edgelab_config.yaml`:

```yaml
candidate_generation_mode: none
candidate_command: your-composer-or-cursor-script.sh
```

## Data freshness blocking

If data is stale or insufficient for a symbol/timeframe, EdgeLab:

1. Skips engine tests for that pair
2. Creates a UI suggestion explaining the data issue
3. Blocks patch acceptance (`data_freshness_failed`) unless `--dry-run`

## Acceptance / rejection

A candidate patch is **accepted** only if **all** pass:

- Safe whitelisted paths (denylist wins)
- Score beats baseline by `min_score_delta`
- Trade count ≥ `min_trade_count`
- Max drawdown ≤ `max_drawdown_R`
- OOS not worse than baseline
- Symbol concentration ≤ `max_symbol_concentration`
- Tests pass (if `require_tests_pass`)
- Data freshness passed
- Evaluation command succeeded

Rejected patches are reverted via `git checkout` or file backup restore.

## Manual promotion

1. Review suggestion in EdgeLab UI tab
2. Click **Promote to review branch** (sets status `promoted_to_review` in SQLite only)
3. Manually create a git branch and PR — EdgeLab never writes production files

## Forbidden paths / keywords

Same denylist as AutoResearch: `live`, `broker`, `order`, `execution`, `autotrade`, `scheduler`, `credentials`, `secret`, `key`, `mt5_login`, `account`, `trade_router`, `position_manager`, `risk_live`, `ui`, `frontend`, `dashboard`, `button`, `send_order`, `place_order`, `close_order`

**Allowed:** `research/`, `config/research/`, `configs/research/`, `tests/research/`, `athena_research/`, `engine_a/research/`, `engine_b/research/`, `research/edgelab/`, `research/autoresearch/`

## Detected Athena research commands

Documented in config (uncomment/configure as needed):

| Engine | Command |
|--------|---------|
| Stub (default) | `python research/edgelab/stub_evaluator.py --scenario engine_b_stub` |
| Engine A ablation | `python -m athena_research.engine_a_ablation.run --max-symbols 4 --step 4` |
| Engine B quality | `python -m athena_research.engine_b_quality_report` |
| VectorBT lab | `python tools/vectorbt_research_lab.py --mode tiny` |

## Tests

```bash
pytest tests/research/edgelab -q
```

## Safe operating rules

- Run EdgeLab as a separate process; it never blocks live Athena
- Keep `candidate_generation_mode: none` until external patch generators are configured
- Never bypass EdgeLab safety checks for production promotion
- Treat all suggestions as diagnostic discovery, not live gate tuning
