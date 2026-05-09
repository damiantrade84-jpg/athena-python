# Codex — Athena Trading System Instructions

## Project

**Athena Sentinel Pro v4.0** — Multi-engine quantitative trading system (A/B/C/D) with AI validation and React dashboard. Paper/demo mode only.

- **Backend:** Python Flask (`athena.py`), SQLite (`audit.db`)
- **Frontend:** React + Vite (`static/react-app/app/`)
- **AI:** Grok 4.3 (xAI) for analysis, vision, debate
- **Data:** MT5, Binance, Bybit demo, EODHD
- **Repo:** `C:\dev\athena-python`

## Critical: Frontend Build Step

**ALWAYS** run `npm run build` after editing any React/TypeScript file.

```bash
cd static/react-app/app && npm run build
```

The browser loads compiled JS from `static/assets/`, not raw `.tsx`. Failing to build means your changes are invisible.

Commit BOTH:
- Source: `static/react-app/app/src/**/*`
- Built: `static/assets/index-*.js`, `static/index.html`

## Safety (Non-Negotiable)

| Setting | Value | Rule |
|---------|-------|------|
| Paper mode | `PAPER_SOAK.ENABLED: true` | Never disable |
| Real orders | `REAL_ORDERS_ALLOWED: false` | Never enable without 1 week clean paper + manual approval |
| AI role | Review-only | AI cannot execute, override gates, or size positions |
| Kill switch | Active | Never bypass |

## Engine Thresholds (Config-Driven)

All engine thresholds live in `config.yaml` under `NAKED_ENGINE.style_profiles`:

```yaml
style_profiles:
  scalp:      { min_score: 3.0, min_rr: 1.0,  fallback_rr: 1.4 }
  intraday:   { min_score: 3.0, min_rr: 1.2,  fallback_rr: 1.8 }
  swing:      { min_score: 4.0, min_rr: 1.6,  fallback_rr: 2.2 }
```

Regime multipliers: `ENGINE_B_REGIME_MULTIPLIERS` — TRENDING=0.90, LOW_VOL=1.15

**Never** hardcode thresholds in Python. Read via `CONFIG.get("KEY", default)`.

## AI Prompt Contract Verification

Before modifying any AI prompt:

1. Read the prompt file (`engine_b_ai.py`, `engine_c_ai.py`, `vision_prompts.py`)
2. Read the message builder function in the same file
3. Verify: Does the message include everything the prompt asks for?
4. If NO: Fix message builder OR remove requirement from prompt
5. If YES: Proceed with prompt change

**Never** let the prompt ask for data the message doesn't provide — this causes hallucinations.

## Vision Parser Tokens (Immutable)

The regex parser in `athena.py` expects these exact footer tokens:
```
RIGHT EDGE: <CONFIRMS|REVIEW|POTENTIAL REVERSAL>
TF ALIGNMENT: <ALIGNED|CONFLICTED>
RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>
```
Changing these in the prompt without updating the parser breaks vision analysis.

## Database

- SQLite WAL mode, 15s timeout, explicit commits
- `learning_log` has NO `engine` or `outcome` columns
- Table init lives in `audit_repo.py`

## Testing

Before completing any task:
- Run `pytest tests/ -k "pattern"` for affected modules
- Create test if none exists for your change
- Verify no circular imports (never import `athena.py` in tests)
- For React: build, then verify in browser

## Common Errors

| Error | Root Cause | Fix |
|-------|-----------|-----|
| UI changes invisible | Forgot `npm run build` | Build and commit `static/assets/` |
| AI grades unreliable | Prompt asks for data not in message | Verify prompt-to-payload contract |
| Threshold changes lost | Hardcoded in Python | Move to `config.yaml` |
| Vision parse fails | Changed footer tokens | Keep exact tokens parser expects |

## Git

1. Edit source
2. Build if React / Test if Python
3. `git add` source + built files
4. `git commit -m "fix(scope): what and why"`
5. `git push origin main`
6. Restart Athena if backend changed

## When Debugging

1. Read logs: `.codex-bonus-ui-server.log` or console
2. Find root cause, not symptom
3. Fix properly (no workarounds)
4. Prove it works
5. Document in commit message

## Response Format

- What changed
- Why (root cause)
- Files touched
- How verified
- Risk level

## Forbidden

- Real order execution
- Bypassing risk/freshness/kill-switch
- AI trade execution
- Hardcoded credentials
- `chmod 777`
- Destructive automation (prune, wipe)
