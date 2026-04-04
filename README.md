# Sentinel Pro (Athena) — Python trading stack

Multi-asset algorithmic trading and research platform built on **Flask**. It scores markets, runs naked price-action structure (Engine B), optional consensus with chart vision (Engine C), and a separate scalp lab (Engine D). Execution targets **MetaTrader 5** (forex, indices, commodities, stock CFDs) and **Bybit USDT-M linear futures** (crypto).

> **Risk:** This software is for research and automation you control. Trading involves substantial risk of loss. Nothing here is investment advice.

---

## Features at a glance

| Area | What it does |
|------|----------------|
| **Engine A** | Multi-factor / confluence scoring (MFQS + dedicated forex rules) |
| **Engine B** | Naked market structure (zones, BOS/CHoCH, FVG, checklist) |
| **Engine C** | Blends A + B with optional multimodal chart vision |
| **Engine D (Scalp Lab)** | M15 structure + M5 tactical setups (dashboard + API) |
| **Dashboard** | `static/index.html` — scans, backtests, Engine C, Scalp Lab, lottery lab, guardian |
| **Data** | MT5 OHLC (non-crypto), Binance futures candles for crypto, EODHD where configured |

---

## Requirements

- **Python:** 3.11–3.13 (see `pyproject.toml` and `.python-version`)
- **OS:** Windows or Linux (project is actively developed on Windows 11)
- **Brokers / terminals (optional but needed for live):**
  - **MT5** — forex, commodities, indices, US stock/ETF CFDs (`MetaTrader5` package)
  - **Bybit** — crypto execution (API keys via environment)

---

## Quick start

```bash
# Create and activate a virtual environment (example: Windows)
py -3.13 -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt
```

Copy environment variables you need into a **`.env`** file in the repo root (loaded on startup). Common keys include:

| Variable | Purpose |
|----------|---------|
| `EODHD_KEY` | EODHD market data / news where used |
| `XAI_API_KEY` | xAI (Grok) — Marcus Reid, chart vision, lottery AI, some Engine B advisory routes |
| `ANTHROPIC_API_KEY` | Optional news sentiment helpers |
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | Crypto execution (`BYBIT_TESTNET=true` for testnet) |
| Telegram bot vars | As configured for `telegram_notify` |

Run the web app (default **http://127.0.0.1:5000**):

```bash
python athena.py
```

Optional CLI flags (see `athena.py` near `if __name__ == "__main__"`) include scan/backtest-style entry points.

---

## Configuration

- **`config.yaml`** — tunable thresholds, pair profiles, engine settings. Prefer editing YAML over hardcoding in Python.
- **`athena.py`** — pair universes (`FOREX_PAIRS`, `CRYPTO_PAIRS`, etc.), Flask routes, prompts.
- **Scalp universe** — `SCALP_ENGINE.SCALP_PAIRS` in `config.yaml` overrides the default; otherwise Engine D uses active **MT5 + Binance crypto** pairs from the runtime list.

---

## Tests

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

---

## Data and safety

- **`audit.db`** and **`candle_cache.db`** hold live history and cached candles. **Do not delete or commit them** (they should stay gitignored). Back up before major changes: `python backup_db.py`.
- **Execution** always goes through **`risk_check()`** — do not bypass it in forks.

---

## Repository layout (high level)

| Path | Role |
|------|------|
| `athena.py` | Main Flask app, APIs, scan/analysis wiring |
| `scoring.py`, `factor_scoring.py`, `forex_scoring.py` | Engine A scoring |
| `market_structure.py` | Engine B (`NakedEngine`) |
| `engine_c.py` | Consensus + vision modifier |
| `scalp_engine.py` | Engine D |
| `execution.py` / `mt5_executor.py` / `bybit_executor.py` | Order routing |
| `candles_cache.py`, `candle_feeds.py` | Candles and live feeds |
| `static/index.html` | Dashboard UI |
| `CLAUDE.md` | Maintainer / AI agent rules (scoring gates, feed routing, file map) |

---

## Contributing and strict policies

If you change **live or backtest scoring gates**, **thresholds**, or **feed routing**, treat `CLAUDE.md` as the contract: many behaviors are intentionally locked unless explicitly changed. Cosmetic UI copy is fine; silent “tuning” of gates is not.

---

## License

No root `LICENSE` file is shipped in this repository; treat usage as **private / all rights reserved** unless the owner adds a license. Dependencies retain their own licenses.

---

## Support

Issues and pull requests should describe **repro steps**, **pair/symbol**, and whether the path is **live**, **backtest**, or **dashboard-only**. For deep internals, start from `CLAUDE.md` and the file map there.
