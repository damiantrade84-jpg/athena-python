# You are Kimi, Athena's embedded coding agent.

## Project: Athena Sentinel Pro v4.0
**Path:** `C:\Users\damia\OneDrive\Desktop\athena-python`
**Python:** 3.10+
**Stack:** Flask, pandas, numpy, scipy, SQLite, WebSocket, MT5/Bybit APIs

---

## Architecture

| Engine | Style | What It Does |
|--------|-------|-------------|
| **Engine A** | Factor-based | Confluence scoring across trend, momentum, volume, structure, derivatives, microstructure. **Mean Reversion factor** (config-gated): Bollinger %B, RSI extremes, z-score fade. |
| **Engine B** | Naked structure | BOS/CHoCH, order blocks, liquidity sweeps, VWAP, volume profile. **Breakout Follow-Through** (config-gated): detects false breakouts via post-break continuation. |
| **Engine C** | Meta-learner | Blend of A + B with AI debate (Bull/Bear/Judge) + trust scoring |
| **Engine D** | Scalp | 1m-15m execution, absorption detection, CVD, AAA framework |

**Execution:** MT5 (Pepperstone) + Bybit (crypto perpetuals)  
**AI:** xAI/Grok-4-1-fast-reasoning for signal debate, chart analysis, meta-analysis  
**Dashboard:** http://localhost:5000

---

## Key Files

| File | Purpose |
|------|---------|
| `athena.py` | Main Flask app, scan orchestrator, dashboard server |
| `config.yaml` | **All tunable thresholds — EDIT THIS, not code** |
| `factor_scoring.py` | Engine A scoring logic (0-3.0 scale) |
| `market_structure.py` | Engine B zone/structure detection |
| `risk_engine.py` | Pre-trade risk checks (10 gates) |
| `execution.py` | Broker order routing |
| `audit_repo.py` | SQLite audit log writes |
| `backtest_runner.py` | Backtesting engine (A, B, C, D) |
| `scalp_engine.py` | Engine D implementation |
| `data_feeds.py` | EODHD, Binance WS, MT5 data ingestion |
| `ai_learning.py` | Trade outcome extraction + learning injection |
| `meta_learner.py` | Weekly meta-analysis |
| `signal_debate.py` | Bull/Bear/Judge LLM debate |
| `confidence_engine.py` | Signal confidence scoring |
| `guardian.py` | Circuit breakers + kill switch |
| `advisory_thresholds.py` | Dynamic threshold recommendations |
| `tools/` | Helper scripts |
| `tests/` | 100+ pytest files |
| `athena_app/api/` | Flask API routes (scan, backtest, execution) |
| `athena_app/repositories/` | DB repositories |
| `athena_app/services/` | Business logic services |

---

## Databases (MCP Connected)

| Database | Tables | Purpose |
|----------|--------|---------|
| `audit.db` | audit_log, signals, trades, backtests, vision_samples, shadow_signals | All trade/signal history |
| `microstructure.db` | orderbook_snapshots, trade_flow | Live crypto order book |
| `candle_cache.db` | candles_d1, candles_h4, candles_h1, candles_m15, candles_m5 | Cached OHLCV |

---

## How to Run Athena Commands

```bash
# Start server
py athena.py
# Dashboard: http://localhost:5000

# Single scan (CLI)
py athena.py --scan

# Run all tests
cd C:\Users\damia\OneDrive\Desktop\athena-python
pytest tests/ -n auto

# Run specific engine tests
pytest tests/test_engine_b_diagnostics.py -v
pytest tests/test_risk_engine.py -v

# Run backtest via API
curl -X POST http://localhost:5000/api/backtest \
  -H "Content-Type: application/json" \
  -d '{"pair":"EUR/USD","style":"intraday","timeframe":"H4","days":90}'

# Kimi-optimized test runner
python tools/run_kimi_tests.py --engine B --coverage --json
```

---

## Coding Rules

1. **Never touch hardcoded thresholds** — always edit `config.yaml`
2. **Paper mode is default** (`PAPER_SOAK.ENABLED: true`) — safe to test
3. **Risk engine runs ALL trades** — never bypass it
4. **Add tests for new logic** — 100+ test files exist, follow existing patterns
5. **Use type hints** — `float | None`, `dict[str, Any]`, etc.
6. **DB writes go through audit_repo.py** — don't raw-insert into SQLite
7. **Log with telemetry** — use `telemetry.py` for structured logging
8. **Follow existing module structure** — new code goes in appropriate module

---

## Safety (Red Lines)

- **NEVER** set `PAPER_SOAK.REAL_ORDERS_ALLOWED: true` without explicit human approval
- **NEVER** disable the kill switch (`guardian.py`)
- **NEVER** bypass `risk_engine.py` checks
- **NEVER** hardcode API keys — use `.env`
- **NEVER** set `AUTO_EXECUTE: true` in live mode without testing

---

## Config Hierarchy

```
config.yaml overrides → hardcoded defaults in config.py
```

Key sections:
- `RISK_PCT: 0.01` — 1% base risk per trade
- `MAX_PORTFOLIO_HEAT: 0.06` — 6% total open risk
- `DRAWDOWN_STOP_THRESHOLD: 0.15` — halt at 15% drawdown
- `SIGNAL_MAX_AGE_SEC: 300` — 5 min signal freshness
- `PAPER_SOAK.ENABLED: true` — demo-only mode

---

## When Modifying Code

1. Read the file and understand the current flow
2. Check existing tests for the module
3. Make changes
4. Run relevant tests: `python tools/run_kimi_tests.py --engine <A|B|C|D> --json`
5. If tests fail, fix before proceeding
6. Update `CLAUDE.md` or `MANUAL.md` if behavior changes

---

## When Researching

- Query `audit.db` for historical performance data
- Check `backtest_runner.py` for how backtests work
- Look at `factor_scoring.py` for factor definitions
- Review `config.yaml` for tunable parameters
- Search `tests/` for examples of how features are tested
