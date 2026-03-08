"""config.py — Athena Pro configuration loading and validation.

CONFIG is built from hard-coded defaults then overlaid with config.yaml values.
Import CONFIG from here; never import from athena.py directly.
"""
import os
import logging

log = logging.getLogger("athena")

# ── Load YAML overrides ──────────────────────────────────────────────────────
_yaml_overrides: dict = {}
try:
    import yaml as _yaml
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if os.path.exists(_cfg_path):
        with open(_cfg_path, "r") as _f:
            _yaml_overrides = _yaml.safe_load(_f) or {}
        log.info(f"Loaded config.yaml ({len(_yaml_overrides)} keys)")
except ImportError:
    pass  # pyyaml optional
except Exception as _e:
    log.warning(f"config.yaml load failed: {_e}")

# ── Default CONFIG ───────────────────────────────────────────────────────────
CONFIG: dict = {
    "ANTHROPIC_KEY":   os.environ.get("ANTHROPIC_KEY", "YOUR_ANTHROPIC_API_KEY"),
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    "CRYPTOPANIC_KEY": os.environ.get("CRYPTOPANIC_KEY", ""),
    "FINNHUB_KEY":     os.environ.get("FINNHUB_KEY", ""),
    "RISK_PCT": 0.01, "SL_ATR_MULT": 1.5, "TP1_ATR_MULT": 2.0, "TP2_ATR_MULT": 3.5,
    "VOLUME_THRESHOLD": 1.5, "VOLUME_THRESHOLD_BACKTEST": 1.2, "ADX_TREND_MIN": 25,
    "D1_CANDLES": 250, "H4_CANDLES": 120, "H1_CANDLES": 120, "MIN_CONFLUENCE": 7.0,
    "RISK_MULT": {"commodity": 1.2, "crypto": 0.8, "forex": 0.6, "index": 0.6, "stock": 0.6},
    "RANGING": {
        "crypto":    {"dead": 14, "dead_pen": 1.5, "choppy": 18, "choppy_pen": 0.5},
        "commodity": {"dead": 18, "dead_pen": 1.5, "choppy": 23, "choppy_pen": 0.5},
        "forex":     {"dead": 16, "dead_pen": 1.5, "choppy": 20, "choppy_pen": 0.5},
        "stock":     {"dead": 16, "dead_pen": 1.5, "choppy": 21, "choppy_pen": 0.5},
        "index":     {"dead": 16, "dead_pen": 1.5, "choppy": 21, "choppy_pen": 0.5},
    },
    "ATR_CLASS": {
        "forex":     {"sl": 1.2, "tp1": 2.0, "tp2": 3.0},
        "commodity": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "index":     {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "stock":     {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "crypto":    {"sl": 2.0, "tp1": 3.5, "tp2": 5.0},
    },
    "ADX_TREND_MIN_CLASS":  {"crypto": 20, "forex": 22, "commodity": 25, "stock": 25, "index": 25},
    "COUNTER_TREND_PEN":    {"crypto": -1.0, "forex": -1.0, "commodity": -1.0, "stock": -1.0, "index": -1.0},
    "RSI_BOUNDS": {
        "crypto":    {"ob": 88, "os": 15},
        "forex":     {"ob": 80, "os": 20},
        "commodity": {"ob": 78, "os": 22},
        "stock":     {"ob": 78, "os": 22},
        "index":     {"ob": 78, "os": 22},
    },
    "MACRO_LOOKBACK":    {"crypto": 15, "forex": 30, "commodity": 50, "stock": 50, "index": 50},
    "WEINSTEIN_LOOKBACK":{"crypto": 60, "forex": 100, "commodity": 150, "stock": 150, "index": 150},
    "BT_MIN":              {"crypto": 4.0, "commodity": 4.0, "forex": 4.0, "stock": 4.5, "index": 4.0},
    "MIN_CONFLUENCE_CLASS":{"crypto": 5.0, "commodity": 5.0, "forex": 5.0, "stock": 5.5, "index": 5.0},
    # Per-class vote weight multipliers — route each indicator to where it's strongest
    "VOTE_WEIGHTS": {
        "crypto":    {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 0.75, "volume": 1.0, "funding": 1.0, "session": 0.0, "h4_fib": 0.5, "h1_bb": 1.0, "weinstein": 0.0, "divergence": 1.0},
        "forex":     {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 0.0, "funding": 0.0, "session": 1.0, "h4_fib": 1.0, "h1_bb": 0.5, "weinstein": 0.0, "divergence": 1.0},
        "stock":     {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 1.0, "funding": 0.0, "session": 0.0, "h4_fib": 1.0, "h1_bb": 1.0, "weinstein": 1.0, "divergence": 1.0},
        "commodity": {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 1.0, "funding": 0.0, "session": 0.0, "h4_fib": 1.0, "h1_bb": 1.0, "weinstein": 0.5, "divergence": 1.0},
        "index":     {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 1.0, "funding": 0.0, "session": 0.0, "h4_fib": 1.0, "h1_bb": 1.0, "weinstein": 0.5, "divergence": 1.0},
    },
    "BT_AUTO_TOGGLE": False,           # If False, backtest will never enable/disable live pairs
    "BT_PERCENTILE_FILTER": False,     # If False, disable rolling percentile floor in backtest
    # ── Execution engine ────────────────────────────────────────────────────
    "EXECUTION_ENABLED": False,         # Master switch — must be explicitly enabled
    "AUTO_EXECUTE": False,              # Auto-execute after AI grade (manual click only when False)
    "AUTO_EXECUTE_MIN_SCORE": 8.0,      # Minimum confluence score for auto-execute
    "AUTO_EXECUTE_MIN_GRADE": "B",      # Minimum AI grade for auto-execute
    "MAX_PORTFOLIO_HEAT": 0.06,         # 6% total risk across all positions
    "MAX_OPEN_POSITIONS": 5,            # Max simultaneous open trades
    "MAX_CORRELATED_POSITIONS": 2,      # Max positions in same correlation cluster
    "SIGNAL_MAX_AGE_SEC": 300,          # Reject signals older than 5 minutes
    "MAX_RISK_PER_TRADE": 0.03,         # Hard cap: never risk > 3% on single trade
    "DRAWDOWN_REDUCE_THRESHOLD": 0.10,  # At 10% drawdown, halve position sizes
    "DRAWDOWN_STOP_THRESHOLD": 0.15,    # At 15% drawdown, reject ALL new trades
}

# Apply YAML overrides — deep-merge dicts, overwrite scalars
for _k, _v in _yaml_overrides.items():
    if _k in CONFIG and isinstance(CONFIG[_k], dict) and isinstance(_v, dict):
        CONFIG[_k].update(_v)
    else:
        CONFIG[_k] = _v


def validate_config(cfg: dict) -> None:
    """Warn on mis-typed or dangerous CONFIG values after YAML overrides are applied."""
    for k in ("RISK_PCT", "SL_ATR_MULT", "TP1_ATR_MULT", "TP2_ATR_MULT", "ADX_TREND_MIN", "MIN_CONFLUENCE"):
        v = cfg.get(k)
        if not isinstance(v, (int, float)):
            log.warning(f"[CFG] {k} expected number, got {type(v).__name__!r}")
        elif v <= 0:
            log.warning(f"[CFG] {k}={v} is non-positive — check config.yaml")
    for k in ("D1_CANDLES", "H4_CANDLES", "H1_CANDLES"):
        v = cfg.get(k)
        if not isinstance(v, int) or v < 10:
            log.warning(f"[CFG] {k}={v} is too low — minimum 10 candles required")
    if cfg.get("RISK_PCT", 0) > 0.05:
        log.warning(f"[CFG] RISK_PCT={cfg['RISK_PCT']:.1%} exceeds 5% — verify this is intentional")
    _asset_classes = {"crypto", "forex", "commodity", "stock", "index"}
    for sub_key in ("RISK_MULT", "RANGING", "ATR_CLASS", "ADX_TREND_MIN_CLASS",
                    "COUNTER_TREND_PEN", "RSI_BOUNDS", "BT_MIN", "MIN_CONFLUENCE_CLASS"):
        missing = _asset_classes - set(cfg.get(sub_key, {}).keys())
        if missing:
            log.warning(f"[CFG] {sub_key} missing asset classes: {missing}")


validate_config(CONFIG)
