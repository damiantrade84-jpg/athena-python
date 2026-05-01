# Athena × Kimi Code — Full Integration Blueprint

> **Goal:** Kimi Code becomes a first-class citizen in Athena — it can read code, run tests, query databases, execute scans, modify strategies, and spawn parallel research agents.

---

## 🧠 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    ATHENA SENTINEL PRO v4.0                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Engine A │  │ Engine B │  │ Engine C │  │ Engine D │   │
│  │ (Factor) │  │ (Naked)  │  │ (Blend)  │  │ (Scalp)  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│           ↓                 ↓                ↓              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │         Kimi Code Integration Layer                  │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐ │   │
│  │  │ Tests  │ │ Backtest│ │ Scans  │ │ Strategy Gen │ │   │
│  │  └────────┘ └────────┘ └────────┘ └──────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  SQLite    │  │   OpenClaw   │  │  xAI / Grok-4    │   │
│  │ (audit.db) │  │  (WebBridge) │  │   (AI Debate)    │   │
│  └────────────┘  └──────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 Step 1: Project-Level Kimi Configuration

Create `.kimi/` inside Athena so Kimi Code **auto-loads** Athena context every time you open the project.

### 1.1 Create `.kimi/mcp.json`

```json
{
  "mcpServers": {
    "athena-audit": {
      "command": "sqlite3",
      "args": ["C:\\Users\\damia\\OneDrive\\Desktop\\athena-python\\audit.db"],
      "transport": "stdio"
    },
    "athena-microstructure": {
      "command": "sqlite3",
      "args": ["C:\\Users\\damia\\OneDrive\\Desktop\\athena-python\\microstructure.db"],
      "transport": "stdio"
    },
    "athena-candle-cache": {
      "command": "sqlite3",
      "args": ["C:\\Users\\damia\\OneDrive\\Desktop\\athena-python\\candle_cache.db"],
      "transport": "stdio"
    },
    "athena-filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\damia\\OneDrive\\Desktop\\athena-python"],
      "transport": "stdio"
    },
    "fetch": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch"],
      "transport": "stdio"
    }
  }
}
```

### 1.2 Create `.kimi/instructions.md` (Auto-Loaded System Prompt)

```markdown
# You are Kimi, Athena's embedded coding agent.

## Project: Athena Sentinel Pro v4.0
**Path:** `C:\Users\damia\OneDrive\Desktop\athena-python`

## Architecture
- **Engine A** — Factor-based confluence scoring (trend, momentum, volume, structure)
- **Engine B** — Naked market structure (BOS/CHoCH, order blocks, liquidity sweeps)
- **Engine C** — Meta-learner blend of A + B with AI debate (Bull/Bear/Judge)
- **Engine D** — Scalp engine (1m-15m, volume profile, absorption, CVD)
- **Execution** — MT5 (Pepperstone) + Bybit (crypto perpetuals)

## Key Files
| File | Purpose |
|------|---------|
| `athena.py` | Main Flask app, scan orchestrator, dashboard server |
| `config.yaml` | All tunable thresholds — EDIT THIS, not code |
| `factor_scoring.py` | Engine A scoring logic |
| `market_structure.py` | Engine B zone/structure detection |
| `risk_engine.py` | All pre-trade risk checks |
| `execution.py` | Broker order routing |
| `audit_repo.py` | SQLite audit log writes |
| `backtest_runner.py` | Backtesting engine |
| `scalp_engine.py` | Engine D implementation |
| `data_feeds.py` | EODHD, Binance WS, MT5 data |
| `ai_learning.py` | Trade outcome extraction + learning injection |
| `meta_learner.py` | Weekly meta-analysis engine |
| `signal_debate.py` | Bull/Bear/Judge LLM debate |
| `guardian.py` | Circuit breakers + kill switch |
| `tools/` | Helper scripts |
| `tests/` | 100+ pytest files |

## Databases
| Database | Tables | Purpose |
|----------|--------|---------|
| `audit.db` | audit_log, signals, trades, backtests, vision_samples | All trade history |
| `microstructure.db` | orderbook_snapshots, trade_flow | Live crypto order book |
| `candle_cache.db` | candles_d1, candles_h4, candles_h1, candles_m15, candles_m5 | Cached OHLCV |

## How to Run Athena Commands
```bash
# Run all tests
cd C:\Users\damia\OneDrive\Desktop\athena-python
pytest tests/ -n auto

# Run specific engine tests
pytest tests/test_engine_b_diagnostics.py -v

# Start Athena server
py athena.py
# Dashboard: http://localhost:5000

# Single scan (CLI)
py athena.py --scan

# Backtest a pair
# POST to http://localhost:5000/api/backtest with {pair, style, timeframe, start, end}
```

## Coding Rules
1. **Never touch hardcoded thresholds** — always edit `config.yaml`
2. **Paper mode is default** (`PAPER_SOAK.ENABLED: true`) — safe to test
3. **Risk engine runs ALL trades** — never bypass it
4. **Add tests for new logic** — 100+ test files exist, follow the pattern
5. **Use type hints** — Athena uses Python 3.10+ with `float | None` etc.
6. **DB writes go through audit_repo.py** — don't raw-insert

## Safety
- NEVER set `PAPER_SOAK.REAL_ORDERS_ALLOWED: true` without explicit human approval
- NEVER disable the kill switch (`guardian.py`)
- NEVER bypass `risk_engine.py` checks
```

### 1.3 Create `.kimi/tools/athena-cli.json` (Custom Tools)

```json
{
  "tools": [
    {
      "name": "run_athena_scan",
      "description": "Run a single Athena scan and return top signals",
      "parameters": {
        "pair": {"type": "string", "description": "Asset pair or 'all'"},
        "style": {"type": "string", "enum": ["scalp", "intraday", "swing"]},
        "engine": {"type": "string", "enum": ["A", "B", "C", "D"]}
      }
    },
    {
      "name": "run_backtest",
      "description": "Run backtest for a pair and return performance metrics",
      "parameters": {
        "pair": {"type": "string"},
        "style": {"type": "string"},
        "days": {"type": "integer", "default": 90}
      }
    },
    {
      "name": "query_audit",
      "description": "Query the audit database for trade history or signals",
      "parameters": {
        "sql": {"type": "string"},
        "limit": {"type": "integer", "default": 100}
      }
    },
    {
      "name": "update_config",
      "description": "Update a config.yaml value safely",
      "parameters": {
        "key": {"type": "string"},
        "value": {"type": "any"}
      }
    },
    {
      "name": "run_tests",
      "description": "Run pytest suite with optional coverage",
      "parameters": {
        "pattern": {"type": "string", "default": "tests/"},
        "coverage": {"type": "boolean", "default": false}
      }
    }
  ]
}
```

---

## 🔧 Step 2: Athena-Side Integration Scripts

Create `tools/kimi_integration.py` inside Athena so **Kimi Code can call Athena directly** via HTTP API.

### 2.1 `tools/kimi_bridge.py` — HTTP Bridge

```python
"""Kimi Code ↔ Athena HTTP Bridge.

Exposes Athena internals as REST endpoints that Kimi Code (or any agent)
can call via requests/httpx.

Add to athena.py:
    from kimi_bridge import register_kimi_routes
    register_kimi_routes(app)
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

PROJECT_ROOT = Path(__file__).parent.parent
DB_AUDIT = PROJECT_ROOT / "audit.db"
DB_MICRO = PROJECT_ROOT / "microstructure.db"
DB_CANDLE = PROJECT_ROOT / "candle_cache.db"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

kimi_bp = Blueprint("kimi", __name__, url_prefix="/api/kimi")


@kimi_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


@kimi_bp.route("/audit/query", methods=["POST"])
def audit_query():
    """Execute read-only SQL on audit.db."""
    data = request.get_json() or {}
    sql = data.get("sql", "").strip()
    limit = min(data.get("limit", 100), 5000)
    
    # Safety: only SELECT allowed
    if not sql.lower().startswith("select"):
        return jsonify({"error": "Only SELECT queries allowed"}), 403
    
    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(sql + f" LIMIT {limit}").fetchall()
            return jsonify({
                "columns": rows[0].keys() if rows else [],
                "rows": [dict(r) for r in rows],
                "count": len(rows)
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/config/read", methods=["GET"])
def config_read():
    """Return current config.yaml as structured data."""
    import yaml
    with open(CONFIG_PATH, "r") as f:
        return jsonify(yaml.safe_load(f))


@kimi_bp.route("/config/update", methods=["POST"])
def config_update():
    """Update a single config key (safe: backups old config)."""
    import yaml
    data = request.get_json() or {}
    key_path = data.get("key", "")
    value = data.get("value")
    
    if not key_path or value is None:
        return jsonify({"error": "Need 'key' and 'value'"}), 400
    
    # Backup
    backup = CONFIG_PATH.with_suffix(f".yaml.bak.{datetime.now():%Y%m%d_%H%M%S}")
    CONFIG_PATH.rename(backup)
    
    with open(backup, "r") as f:
        config = yaml.safe_load(f)
    
    # Navigate key path (e.g., "RISK_PCT" or "SCALP_ENGINE.MIN_RR")
    keys = key_path.split(".")
    target = config
    for k in keys[:-1]:
        target = target.setdefault(k, {})
    target[keys[-1]] = value
    
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    
    return jsonify({"status": "updated", "key": key_path, "value": value, "backup": str(backup)})


@kimi_bp.route("/backtest/run", methods=["POST"])
def backtest_run():
    """Trigger a backtest via Athena's existing backtest engine."""
    data = request.get_json() or {}
    # Delegate to existing backtest service
    from athena_app.services.scan_backtest_service import handle_backtest_request
    return handle_backtest_request(data)


@kimi_bp.route("/tests/run", methods=["POST"])
def tests_run():
    """Run pytest and return results."""
    data = request.get_json() or {}
    pattern = data.get("pattern", "tests/")
    cov = data.get("coverage", False)
    
    cmd = ["pytest", "-v", pattern]
    if cov:
        cmd += ["--cov=.", "--cov-report=term-missing"]
    
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300)
    return jsonify({
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "passed": result.returncode == 0
    })


@kimi_bp.route("/signals/latest", methods=["GET"])
def signals_latest():
    """Return latest signals from audit.db."""
    limit = min(request.args.get("limit", 20, type=int), 500)
    with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM signals ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return jsonify({"signals": [dict(r) for r in rows]})


@kimi_bp.route("/trades/performance", methods=["GET"])
def trades_performance():
    """Return aggregate trade performance."""
    days = min(request.args.get("days", 30, type=int), 365)
    with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
        row = con.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl) as net_pnl,
                AVG(pnl) as avg_pnl,
                AVG(r_multiple) as avg_r
            FROM trades
            WHERE ts > datetime('now', '-{} days')
        """.format(days)).fetchone()
        return jsonify({
            "period_days": days,
            "total_trades": row[0],
            "wins": row[1],
            "losses": row[2],
            "win_rate": round(row[1] / row[0] * 100, 2) if row[0] else 0,
            "net_pnl": row[3],
            "avg_pnl": row[4],
            "avg_r": row[5]
        })


def register_kimi_routes(app):
    app.register_blueprint(kimi_bp)
    print("[KIMI] Bridge routes registered at /api/kimi/*")
```

### 2.2 Wire Into `athena.py`

Add near the Flask app initialization:

```python
# Kimi Code Integration
from kimi_bridge import register_kimi_routes
register_kimi_routes(app)
```

---

## 🧪 Step 3: Kimi Code Test Runner Integration

Create `tools/run_kimi_tests.py` — a script Kimi Code calls to run Athena's test suite with rich output.

```python
#!/usr/bin/env python3
"""Test runner optimized for Kimi Code agent consumption.

Usage:
    python tools/run_kimi_tests.py --engine A --coverage
    python tools/run_kimi_tests.py --pattern tests/test_risk_engine.py
    python tools/run_kimi_tests.py --all --parallel
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TESTS_DIR = PROJECT_ROOT / "tests"

ENGINE_MAP = {
    "A": ["test_factor_scoring.py", "test_engine_a*.py", "test_scoring*.py"],
    "B": ["test_engine_b*.py", "test_market_structure.py", "test_naked*.py"],
    "C": ["test_engine_c*.py", "test_meta_learner.py", "test_signal_debate.py"],
    "D": ["test_scalp_engine.py", "test_engine_d*.py", "test_volume_profile*.py"],
    "risk": ["test_risk_engine.py", "test_risk*.py", "test_guardian.py"],
    "exec": ["test_execution.py", "test_bybit*.py", "test_mt5*.py"],
    "data": ["test_data_feeds.py", "test_candle*.py", "test_eodhd*.py"],
    "ai": ["test_ai_learning.py", "test_meta_learner.py", "test_confidence*.py"],
    "backtest": ["test_backtest*.py", "test_bt*.py"],
}


def run_tests(patterns, coverage=False, parallel=False, verbose=True):
    cmd = [sys.executable, "-m", "pytest"]
    if verbose:
        cmd.append("-v")
    if coverage:
        cmd += ["--cov=.", "--cov-report=json:tests/coverage.json", "--cov-report=term-missing"]
    if parallel:
        cmd += ["-n", "auto"]
    
    for p in patterns:
        cmd.append(str(TESTS_DIR / p))
    
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600)
    
    output = {
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "summary": extract_summary(result.stdout)
    }
    
    if coverage and Path("tests/coverage.json").exists():
        with open("tests/coverage.json") as f:
            cov_data = json.load(f)
        output["coverage_percent"] = cov_data.get("totals", {}).get("percent_covered", 0)
    
    return output


def extract_summary(stdout):
    """Extract pytest summary lines."""
    lines = stdout.strip().split("\n")
    for line in reversed(lines):
        if "passed" in line or "failed" in line or "error" in line:
            return line
    return "Unknown"


def main():
    parser = argparse.ArgumentParser(description="Athena test runner for Kimi Code")
    parser.add_argument("--engine", choices=list(ENGINE_MAP.keys()), help="Test engine-specific files")
    parser.add_argument("--pattern", help="Glob pattern for test files")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--coverage", action="store_true", help="Generate coverage report")
    parser.add_argument("--parallel", action="store_true", help="Run in parallel (-n auto)")
    parser.add_argument("--json", action="store_true", help="Output as JSON for agent parsing")
    args = parser.parse_args()
    
    if args.all:
        patterns = ["tests/"]
    elif args.engine:
        patterns = ENGINE_MAP[args.engine]
    elif args.pattern:
        patterns = [args.pattern]
    else:
        patterns = ["tests/"]
    
    result = run_tests(patterns, coverage=args.coverage, parallel=args.parallel)
    
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["stdout"])
        if result["stderr"]:
            print("--- STDERR ---", file=sys.stderr)
            print(result["stderr"], file=sys.stderr)
        print(f"\n=== SUMMARY: {result['summary']} ===")
        if "coverage_percent" in result:
            print(f"Coverage: {result['coverage_percent']:.1f}%")
    
    sys.exit(result["returncode"])


if __name__ == "__main__":
    main()
```

---

## 🔄 Step 4: OpenClaw ↔ Kimi Code Bridge

This lets **me spawn Kimi Code subagents** that work on Athena tasks and report back.

### 4.1 Create `tools/openclaw_kimi_agent.py`

```python
#!/usr/bin/env python3
"""OpenClaw → Kimi Code agent bridge.

Usage (called by OpenClaw):
    python tools/openclaw_kimi_agent.py <task_json>

Spawns Kimi Code with Athena context and streams results.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
KIMI_INSTRUCTIONS = PROJECT_ROOT / ".kimi" / "instructions.md"


def spawn_kimi_task(task: dict) -> dict:
    """Spawn Kimi Code CLI with a specific Athena task."""
    
    # Build task description
    task_type = task.get("type", "code")
    description = task.get("description", "")
    files = task.get("files", [])
    
    prompt = f"""You are working on Athena Sentinel Pro v4.0, a multi-engine quantitative trading system.

Task: {description}

Files to focus on: {', '.join(files) if files else 'See full codebase'}

Rules:
1. Read files before modifying
2. Run tests after changes: `python tools/run_kimi_tests.py --engine {task.get('engine', 'all')} --json`
3. Never modify config.yaml directly — suggest changes in comments
4. Paper mode is active — safe to test execution logic
5. Add type hints, docstrings, and tests for new code

Start by reading the relevant files and understanding the current implementation.
"""
    
    # Write prompt to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name
    
    # Spawn Kimi Code
    cmd = [
        "kimi",
        "--no-interactive",
        f"--instructions={KIMI_INSTRUCTIONS}" if KIMI_INSTRUCTIONS.exists() else "",
        f"Follow the task in {prompt_file}. After completing, write a summary to {PROJECT_ROOT}/KIMI_RESULT.md"
    ]
    cmd = [c for c in cmd if c]  # Remove empty strings
    
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=task.get("timeout", 300)
    )
    
    return {
        "task": description,
        "returncode": result.returncode,
        "stdout": result.stdout[-5000:],  # Last 5K chars
        "stderr": result.stderr[-2000:],   # Last 2K chars
        "result_file": str(PROJECT_ROOT / "KIMI_RESULT.md")
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python openclaw_kimi_agent.py '{\"type\": \"code\", \"description\": \"...\"}'")
        sys.exit(1)
    
    task = json.loads(sys.argv[1])
    result = spawn_kimi_task(task)
    print(json.dumps(result, indent=2))
```

---

## 🎯 Step 5: Usage Workflows

### 5.1 Solo Kimi Code (You Direct)

```bash
# Open Athena in Kimi Code
kimi C:\Users\damia\OneDrive\Desktop\athena-python

# Now inside Kimi Code, ask natural language:
"Show me the last 20 trades from the audit database"
"Refactor engine_b_ai.py to reduce cognitive complexity"
"Run backtest for EUR/USD swing style last 90 days"
"Find why test_backtest_integrity.py is failing and fix it"
"Write a new factor for Engine A that uses order book imbalance"
```

### 5.2 Parallel Kimi Agents (Multiple Terminals)

```bash
# Terminal 1 — Strategy Research
kimi C:\Users\damia\OneDrive\Desktop\athena-python
> "Research WorldQuant BRAIN data fields. Map them to Athena's existing factors. Write a new alpha strategy spec."

# Terminal 2 — Bug Fix
kimi C:\Users\damia\OneDrive\Desktop\athena-python
> "The risk_engine.py has a bug where portfolio_heat is miscalculated when 3+ correlated positions exist. Find and fix."

# Terminal 3 — Test Expansion
kimi C:\Users\damia\OneDrive\Desktop\athena-python
> "Write property-based tests for factor_scoring.py edge cases using hypothesis. Run and verify coverage."
```

### 5.3 OpenClaw Orchestrated (I Manage Kimi Agents)

You say to me:
> "Run 3 parallel Kimi agents on Athena: one to research WQ BRAIN, one to fix the backtest bug, one to add a new crypto microstructure factor"

I spawn:
- Agent 1 → Research task
- Agent 2 → Bug fix task
- Agent 3 → Feature task

I monitor all three, collect results, synthesize into a unified report.

### 5.4 Athena Calling Kimi (Reverse Bridge)

In `ai_learning.py` or `signal_debate.py`, add a hook:

```python
# In signal_debate.py, after Bull/Bear debate:
def invoke_kimi_review(signal: dict, debate_result: dict) -> str:
    """Call Kimi Code for a third opinion on debated signals."""
    import subprocess
    prompt = f"""
    Signal: {signal['pair']} {signal['direction']} score={signal['score']}
    Bull case: {debate_result['bull']}
    Bear case: {debate_result['bear']}
    Judge verdict: {debate_result['verdict']}
    
    As an independent quant analyst, what's YOUR verdict?
    Grade: A+/A/B/C/F
    Conviction: 0.0-1.0
    Reasoning: (2 sentences max)
    """
    # Write prompt, call kimi, read result
    ...
```

---

## 📋 Implementation Checklist

| Step | Task | Priority | Est. Time |
|------|------|----------|-----------|
| 1.1 | Create `.kimi/mcp.json` with SQLite + filesystem | 🔴 High | 5 min |
| 1.2 | Create `.kimi/instructions.md` | 🔴 High | 15 min |
| 1.3 | Create `.kimi/tools/athena-cli.json` | 🟡 Med | 10 min |
| 2.1 | Create `tools/kimi_bridge.py` | 🔴 High | 30 min |
| 2.2 | Wire bridge into `athena.py` | 🔴 High | 5 min |
| 3.0 | Create `tools/run_kimi_tests.py` | 🟡 Med | 20 min |
| 4.0 | Create `tools/openclaw_kimi_agent.py` | 🟢 Low | 20 min |
| 5.0 | Test solo Kimi Code workflow | 🔴 High | 10 min |
| 5.1 | Test parallel agents | 🟡 Med | 15 min |
| 5.2 | Test OpenClaw orchestration | 🟢 Low | 15 min |
| 5.3 | Add reverse bridge hook (optional) | 🟢 Low | 30 min |

**Total:** ~2.5 hours for full implantation

---

## 🚀 Quick Start (Do This First)

1. **Create `.kimi/` directory** inside `athena-python/`
2. **Write `mcp.json`** with the SQLite servers above
3. **Write `instructions.md`** with the Athena context
4. **Open Kimi Code in Athena:**
   ```bash
   kimi C:\Users\damia\OneDrive\Desktop\athena-python
   ```
5. **Test it:**
   ```
   > "What tables exist in audit.db?"
   > "Show me the risk_engine.py and explain the drawdown circuit breaker"
   > "Run tests for Engine B and report results"
   ```

If all that works, the deep integration is live. 🎯
