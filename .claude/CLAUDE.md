---
description: "Claude Code project instructions for Athena Sentinel Pro v4.0 trading system"
---

# Claude Code — Athena Trading System Instructions

## Project Context

You are working on **Athena Sentinel Pro v4.0**, a multi-engine quantitative trading system with 4 analysis engines (A/B/C/D), AI-powered signal validation, and a React dashboard. The system runs in **paper/demo mode only** — no real money at risk.

**Repo:** `C:\dev\athena-python`  
**Backend:** Python Flask server (`athena.py`)  
**Frontend:** React + Vite (`static/react-app/app/`)  
**AI Stack:** Grok 4.3 (xAI) for signal analysis, vision, debate  
**Data:** SQLite (`audit.db`), MT5, Binance, Bybit demo, EODHD  
**Build:** `npm run build` required after ANY React change  

## Critical Workflow Rules

### 1. React Changes Require Build
**NEVER** edit `.tsx` files without running `npm run build` afterward.  
The browser loads pre-compiled JS from `static/assets/`, not raw TypeScript.

```bash
cd static/react-app/app
npm run build
```

Commit both source AND built files:
- `static/react-app/app/src/**/*.tsx` (source)
- `static/assets/index-*.js` (built bundle)
- `static/index.html` (updated script tag)

### 2. Python Changes — Verify Before Declaring Done
- Run syntax check: `python -c "import ast; ast.parse(open('file.py').read())"`
- If you changed `athena.py`, verify server restarts without crash
- Check for import cycles — NEVER import `athena.py` in test files

### 3. Config-First Changes
All thresholds, scoring gates, and safety settings live in `config.yaml`.  
**Never** hardcode thresholds in Python. Always:
1. Add to `config.yaml` with safe defaults
2. Read via `CONFIG.get("KEY", default)` in Python
3. Document the config key in comments

### 4. Safety Gates Are Non-Negotiable
- Paper mode: `PAPER_SOAK.ENABLED: true`
- Real orders: `REAL_ORDERS_ALLOWED: false`
- Never bypass `risk_check()`, freshness gates, kill switch
- AI can only **review/advise/downgrade** — never execute or override gates

## Engine Architecture

| Engine | Role | Scoring | Gate |
|--------|------|---------|------|
| **A** | Factor confluence | `final_score` 0.0–3.0 | `get_score_threshold(pair)` |
| **B** | Naked structure (SMC/ICT) | Score %, regime multipliers | `NAKED_ENGINE.style_profiles` min_score + min_rr |
| **C** | Consensus (A vs B) | Trust verdict, calibrated probability | `trade` boolean |
| **D** | Scalp (Volume Profile) | A/B/C/D grading | Three-pillar gate |

### Engine B Style Thresholds
```
scalp:     min_score=3.0, min_rr=1.0,  fallback_rr=1.4
intraday:  min_score=4.0, min_rr=1.2,  fallback_rr=1.8
swing:     min_score=4.0, min_rr=1.6,  fallback_rr=2.2
```

Regime multipliers: TRENDING=0.90, RANGING=0.90, HIGH_VOL=0.85, LOW_VOL=1.15

## AI Integration Rules

### Prompt-to-Payload Contract
**Before** modifying any AI prompt file, verify the message builder includes the data:

1. Read `engine_b_ai.py` → check `build_engine_b_signal_message()`
2. Read `engine_c_ai.py` → check message builder  
3. Read `vision_prompts.py` → check image + context payload
4. Compare: Does the prompt ask for data the message doesn't provide?
5. If mismatch: **fix message builder OR remove requirement from prompt**

### Never Ask AI to Generate Raw Floats
Engine C's `conviction_modifier` (-0.15 to +0.15) is unreliable. Use categorical:
- `UPGRADE` → +0.10
- `NEUTRAL` → 0.00
- `DOWNGRADE` → -0.10

### Vision Parser Contract
The footer parser expects EXACT tokens:
```
RIGHT EDGE: <CONFIRMS|REVIEW|POTENTIAL REVERSAL>
TF ALIGNMENT: <ALIGNED|CONFLICTED>
RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>
```
Never change these tokens without updating the parser in `athena.py`.

## Database Rules

- SQLite: `PRAGMA journal_mode=WAL`, timeout=15.0s
- Explicit commits after writes
- `learning_log` has NO `engine` or `outcome` columns — don't query them
- All tables init in `audit_repo.py` or `audit_repo_schema()`

## Testing Requirements

### Before Declaring Any Task Complete:
1. Run relevant tests: `pytest tests/ -k "pattern"`
2. If no test exists for your change, create one
3. Backtest parity: live and backtest must use identical logic
4. Verify no import cycles with `athena.py`

### Test File Pattern
```python
# Good: test only the module you changed
from scoring import get_score_threshold

# BAD: never do this in tests
from athena import something  # Creates circular import risk
```

## Git Workflow

1. Make changes
2. Verify with tests / build / syntax check
3. `git add` changed files (both source AND built for React)
4. `git commit` with descriptive message
5. `git push origin main`
6. Restart Athena if backend changed

## Common Mistakes to Avoid

| Mistake | Why It's Wrong | Correct Approach |
|---------|---------------|-----------------|
| Edit `.tsx` without `npm run build` | Browser sees old JS bundle | Always build and commit `static/assets/` |
| Change Python thresholds inline | Loses config flexibility, unsafe | Add to `config.yaml`, read via `CONFIG.get()` |
| Trust AI grades for execution | AI is optimistic, grades don't predict | AI = review-only, deterministic engines = execution |
| Add widget/UI before fixing data | Garbage data → garbage UI | Fix prompt-payload contract first |
| Blame model when grades are wrong | Usually the prompt asks for missing data | Verify message builder provides what prompt requests |

## When Given a Bug Report

1. **Read logs** — check `.codex-bonus-ui-server.log` or console output
2. **Find root cause** — trace from error to source, not symptoms
3. **Fix the actual bug** — don't add workarounds
4. **Prove it works** — run test, check UI, verify no new errors
5. **Document the fix** — commit message explains root cause

## Response Format

When presenting changes:
- **What changed** — high-level summary
- **Why it changed** — root cause, not just symptom
- **Files touched** — list with purpose
- **Verification** — how you proved it works
- **Risk level** — safe/medium/high

## Forbidden Operations

- ❌ `REAL_ORDERS_ALLOWED = true`
- ❌ Bypassing `risk_check()` or freshness gates
- ❌ AI directly executing trades
- ❌ `docker prune -f` or destructive automation
- ❌ Modifying `/etc/passwd`, `/etc/sudoers`
- ❌ `chmod 777` anywhere
- ❌ Scripts that silently log credentials or exfiltrate data
- ❌ Hardcoded passwords or backdoor logic

---

_These instructions are specific to Claude Code working on Athena. For general OpenClaw agent behavior, see AGENTS.md._
