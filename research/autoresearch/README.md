# AutoResearch — Research-Only Autorun Loop

AutoResearch is a **research-only** automation layer for Athena. It repeatedly evaluates candidate strategy/config patches using objective backtest metrics, then **accepts** or **rejects** them through a deterministic scorer.

## What this system does

- Runs a **baseline** research/backtest evaluation and records metrics.
- Accepts candidate `.patch` files from a queue, external command, manual drop folder, or built-in `safe_stub` generator.
- **Safety-checks** every touched path (whitelist + keyword denylist).
- Applies patches with `git apply --check` then `git apply`.
- Runs configured **tests** and **evaluation commands**.
- Parses metrics (JSON first, regex fallback).
- Scores candidates and compares to baseline using fixed acceptance rules.
- **Accepts** improvements or **reverts** rejected changes via `git checkout`.
- Appends a full audit record to `results_ledger.jsonl`.

## What this system does NOT do

- Does **not** modify live execution, broker adapters, autotrade, schedulers, risk gates, or UI order flows.
- Does **not** place orders or touch credentials/API keys.
- Does **not** implement an uncontrolled self-coding agent — LLMs may *propose* patches, but only the deterministic evaluator accepts them.
- Is **not** production-ready; treat outputs as research discovery only.

## Why research-only

Athena separates live trading paths from research tooling. AutoResearch is confined to whitelisted research directories and rejects any patch whose path contains forbidden keywords (`execution`, `broker`, `autotrade`, etc.). This prevents accidental promotion of research experiments into production code.

## Layout

```
research/autoresearch/
  run_autoresearch.py      # Single baseline or candidate run
  autorun_loop.py          # Multi-iteration loop (modes below)
  autoresearch_config.yaml # Commands, thresholds, allow/deny lists
  results_ledger.jsonl     # Append-only JSONL audit log
  candidates/              # Incoming .patch files
  accepted/                # Accepted patch copies
  rejected/                # Rejected patch copies
  logs/                    # Baseline markers, evaluation artifacts
  stub/                    # safe_stub research config (whitelisted)
  stub_evaluator.py        # Default stub metrics emitter for testing
```

## Quick start — baseline only

```bash
python research/autoresearch/run_autoresearch.py --baseline-only
```

Baseline metrics are saved to `research/autoresearch/logs/baseline_metrics.json`.

## Evaluate one candidate patch

```bash
python research/autoresearch/run_autoresearch.py \
  --candidate-patch research/autoresearch/candidates/candidate_001.patch
```

## Autorun modes

### file_queue

Place `.patch` files in `research/autoresearch/candidates/`, then:

```bash
python research/autoresearch/autorun_loop.py --mode file_queue --max-iterations 3
```

### manual

Waits until you drop a patch into `candidates/`:

```bash
python research/autoresearch/autorun_loop.py --mode manual --max-iterations 3
```

### safe_stub (first end-to-end test)

Generates tiny research-only stub patches automatically — no LLM required:

```bash
python research/autoresearch/autorun_loop.py --mode safe_stub --max-iterations 3
```

**Recommended first command:**

```bash
python research/autoresearch/autorun_loop.py --mode safe_stub --max-iterations 3
```

Ensure `research/autoresearch/stub/stub_config.yaml` is tracked by git so rejected runs can revert cleanly:

```bash
git add research/autoresearch/stub/stub_config.yaml
```

### command

Runs `candidate_command` from config; the command must print or write a `.patch` path:

```bash
python research/autoresearch/autorun_loop.py --mode command --max-iterations 3
```

Configure in `autoresearch_config.yaml`:

```yaml
candidate_command: your-composer-or-codex-or-local-script.sh
```

## Acceptance / rejection

A candidate is **accepted** only if **all** are true:

1. Score beats baseline by at least `min_score_delta` (default `0.05`).
2. Trade count ≥ `min_trade_count` (default `30`).
3. Max drawdown ≤ `max_drawdown_R` (default `3.0`).
4. Out-of-sample score is not worse than baseline (`require_oos_not_worse`).
5. Symbol concentration ≤ `max_symbol_concentration` (default `0.60`).
6. Patch touches **only** whitelisted paths.
7. No denylist keyword appears in any touched path.
8. Tests pass (`test_command`).
9. Evaluation command succeeds and returns parseable metrics.

Otherwise the patch is **rejected**, touched files are reverted, and the patch copy moves to `rejected/`.

### Scoring formula

```
score =
  expR * 40
  + min(profit_factor, 3.0) * 15
  + min(SQN, 4.0) * 10
  - max_drawdown_R * 8
  + trade_count_score
  + oos_score
  + stability_score
  - concentration_penalty
  - cost_sensitivity_penalty
```

## Forbidden paths / keywords

**Whitelist only:**

- `research/`
- `config/research/`
- `configs/research/`
- `tests/research/`
- `athena_research/`
- `engine_a/research/`
- `engine_b/research/`

**Hard denylist keywords** (anywhere in path → reject):

`live`, `broker`, `order`, `execution`, `execute`, `autotrade`, `scheduler`, `credentials`, `secret`, `key`, `mt5_login`, `account`, `trade_router`, `position_manager`, `risk_live`, `ui`, `frontend`, `dashboard`, `button`, `send_order`, `place_order`, `close_order`

## Connecting Composer / Codex / Claude later

AutoResearch does **not** embed an LLM agent. To use an external generator:

1. Configure `candidate_command` to invoke your tool (Composer, Codex, Claude Code, local script).
2. The command must output a filesystem path to a unified diff `.patch` **or** write one into `candidates/`.
3. Run `--mode command` or pre-fill `candidates/` and use `--mode file_queue`.
4. The deterministic evaluator remains the only acceptance path.

Example wrapper pattern:

```bash
#!/usr/bin/env bash
# scripts/generate_research_patch.sh
codex exec "Propose a research-only patch under athena_research/ ..." > /tmp/out.txt
PATCH="research/autoresearch/candidates/$(date +%s).patch"
# extract or save patch to $PATCH
echo "$PATCH"
```

## Athena evaluation commands (detected)

Default config uses the local stub evaluator for safe testing. Alternatives documented in `autoresearch_config.yaml`:

| Key | Command |
|-----|---------|
| `stub_evaluator` (default) | `python research/autoresearch/stub_evaluator.py --scenario baseline` |
| `engine_a_ablation` | `python -m athena_research.engine_a_ablation.run --max-symbols 4 --step 4 --out research/autoresearch/logs/baseline_eval.json` |
| `vectorbt_lab` | `python tools/vectorbt_research_lab.py --mode tiny` |

For ablation JSON output, set `metrics_output_path` to the `--out` file path.

## Git safety

- Stops if the working tree is dirty **outside** `research/autoresearch/` (won't delete your work).
- Uses `git apply --check` before apply.
- Rejected candidates: `git checkout -- <touched files>`.

## Ledger fields

Each JSONL row includes: `timestamp`, `iteration`, `mode`, `candidate_patch`, `changed_files`, `baseline_metrics`, `candidate_metrics`, `baseline_score`, `candidate_score`, `score_delta`, `accepted`, `rejection_reasons`, `commands_run`, `git_diff_summary`, `patch_saved_to`, `duration_seconds`.

## Tests

```bash
pytest tests/research/autoresearch -q
```

## Limitations

- Not production-ready; stub evaluator is for pipeline testing only.
- Real ablation/vectorbt runs require data deps and may be slow.
- Patch application requires a git repository and tracked target files for reliable revert.
- `command` mode depends on your external generator honoring research-only path rules.
- Metric parsing fallback is best-effort; prefer JSON output from evaluation commands.
