"""config.py — Sentinel Pro configuration loading and validation.

CONFIG is built from hard-coded defaults then overlaid with config.yaml values.
Import CONFIG from here; never import from athena.py directly.
"""
import os
import logging

log = logging.getLogger("sentinel")

PAIR_PROFILE_VOTES = {
    "d1_trend", "h1_ema", "d1_adx", "h4_macd", "h4_oscillator",
    "volume", "funding", "session", "h4_fib", "h1_bb",
    "weinstein", "divergence", "aroon",
}
PAIR_PROFILE_FILTERS = {
    "weinstein", "session", "regime_transition", "obv", "funding",
    "squeeze", "mean_revert", "btc_bias", "divergence_warning",
}

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
    "DAILY_LOSS_LIMIT": 0.05,  # Kill switch: halt trading after losing 5% of account in a day
    "VOLUME_THRESHOLD": 1.5, "VOLUME_THRESHOLD_BACKTEST": 1.2, "ADX_TREND_MIN": 25,
    "D1_CANDLES": 250, "H4_CANDLES": 250, "H1_CANDLES": 250, "MIN_CONFLUENCE": 1.0,
    "RISK_MULT": {"commodity": 1.2, "crypto": 0.8, "forex": 0.6, "index": 0.6, "stock": 0.6},
    # Round-trip fee per trade (entry + exit) as fraction of notional.
    # Bybit taker=0.055%×2=0.11%, forex ECN~0.02%×2=0.04%, stocks~0.03%×2=0.06%
    "FEE_PCT": {"crypto": 0.0011, "forex": 0.0004, "commodity": 0.0004, "stock": 0.0006, "index": 0.0004},
    "RANGING": {
        "crypto":    {"dead": 14, "dead_pen": 1.5, "choppy": 18, "choppy_pen": 0.5},
        "commodity": {"dead": 18, "dead_pen": 1.5, "choppy": 23, "choppy_pen": 0.5},
        "forex":     {"dead": 18, "dead_pen": 1.5, "choppy": 23, "choppy_pen": 1.0},
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
    # Rolling windows for normalization (bars)
    "NORMALIZATION_LOOKBACK": {
        "crypto": 200,
        "forex": 200,
        "commodity": 300,
        "stock": 200,
        "index": 200,
    },
    # Realized volatility lookback and annualization factors (per asset class)
    "REALIZED_VOL_LOOKBACK": 30,
    "REALIZED_VOL_ANNUALIZATION": {
        "crypto": 365,
        "forex": 252,
        "stock": 252,
        "index": 252,
        "commodity": 252,
    },
    "ADX_TREND_MIN_CLASS":  {"crypto": 20, "forex": 22, "commodity": 25, "stock": 25, "index": 25},
    "COUNTER_TREND_PEN":    {"crypto": -1.0, "forex": -1.0, "commodity": -1.0, "stock": -1.0, "index": -1.0},
    # TA-Lib standard: RSI overbought=70, oversold=30 (confirmed OANDA/LiteFinance/Altrady 2024)
    "RSI_BOUNDS": {
        "crypto":    {"ob": 70, "os": 30},
        "forex":     {"ob": 70, "os": 30},
        "commodity": {"ob": 70, "os": 30},
        "stock":     {"ob": 70, "os": 30},
        "index":     {"ob": 70, "os": 30},
    },
    "MACRO_LOOKBACK":    {"crypto": 15, "forex": 30, "commodity": 50, "stock": 50, "index": 50},
    "WEINSTEIN_LOOKBACK":{"crypto": 60, "forex": 100, "commodity": 150, "stock": 150, "index": 150},
    "BT_MIN":              {"crypto": 0.55, "commodity": 0.55, "forex": 0.55, "stock": 0.65, "index": 0.55},
    "MIN_CONFLUENCE_CLASS":{"crypto": 0.70, "commodity": 0.70, "forex": 0.70, "stock": 0.80, "index": 0.70},
    # Per-class vote weight multipliers — route each indicator to where it's strongest
    "VOTE_WEIGHTS": {
        "crypto":    {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 0.75, "volume": 1.0, "funding": 1.0, "session": 0.0, "h4_fib": 0.5, "h1_bb": 1.0, "weinstein": 0.0, "divergence": 1.0, "aroon": 0.0},
        "forex":     {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 0.0, "funding": 0.0, "session": 1.0, "h4_fib": 1.0, "h1_bb": 0.5, "weinstein": 0.0, "divergence": 1.0, "aroon": 1.0},
        "stock":     {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 1.0, "funding": 0.0, "session": 0.0, "h4_fib": 1.0, "h1_bb": 1.0, "weinstein": 1.0, "divergence": 1.0, "aroon": 0.0},
        "commodity": {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 1.0, "funding": 0.0, "session": 0.0, "h4_fib": 1.0, "h1_bb": 1.0, "weinstein": 0.5, "divergence": 1.0, "aroon": 0.0},
        "index":     {"d1_trend": 2.0, "h1_ema": 1.0, "d1_adx": 1.0, "h4_macd": 1.0, "h4_oscillator": 1.0, "volume": 1.0, "funding": 0.0, "session": 0.0, "h4_fib": 1.0, "h1_bb": 1.0, "weinstein": 0.5, "divergence": 1.0, "aroon": 0.0},
    },
    # Factor weights per asset class (base, before regime overrides)
    "FACTOR_WEIGHTS": {
        "crypto":    {"trend": 2.0, "momentum": 1.5, "volatility": 1.0, "volume": 1.0, "structure": 1.0, "derivatives": 1.5, "microstructure": 1.5},
        "forex":     {"trend": 2.0, "momentum": 1.2, "volatility": 1.0, "volume": 0.0, "structure": 1.2, "derivatives": 0.5, "microstructure": 1.5},
        "stock":     {"trend": 2.0, "momentum": 1.5, "volatility": 1.0, "volume": 1.5, "structure": 1.0, "derivatives": 0.5, "microstructure": 1.5},
        "commodity": {"trend": 2.0, "momentum": 1.3, "volatility": 1.5, "volume": 1.0, "structure": 1.3, "derivatives": 0.5, "microstructure": 1.5},
        "index":     {"trend": 2.0, "momentum": 1.4, "volatility": 1.2, "volume": 0.8, "structure": 1.0, "derivatives": 0.8, "microstructure": 1.5},
    },
    # Regime-aware factor weight overrides
    "REGIME_WEIGHTS": {
        "TRENDING": {
            "trend": 2.0, "momentum": 1.5, "volatility": 1.0, "volume": 1.0, "structure": 0.5, "derivatives": 1.2, "microstructure": 1.5,
        },
        "RANGING": {
            "trend": 0.5, "momentum": 1.5, "volatility": 1.0, "volume": 1.0, "structure": 2.0, "derivatives": 1.0, "microstructure": 1.5,
        },
        "HIGH_VOLATILITY": {
            "trend": 1.0, "momentum": 1.0, "volatility": 2.0, "volume": 1.5, "structure": 1.0, "derivatives": 1.5, "microstructure": 1.5,
        },
        "LOW_VOLATILITY": {
            "trend": 1.2, "momentum": 1.0, "volatility": 0.5, "volume": 1.0, "structure": 1.0, "derivatives": 1.0, "microstructure": 1.5,
        },
    },
    # Indicator correlation control
    "INDICATOR_CORRELATION_ENABLED": False,  # Expensive O(n²); enable manually for live deep analysis
    "INDICATOR_CORRELATION_WINDOW": 200,
    # Microstructure datafeed configuration
    "MICROSTRUCTURE": {
        "exchanges": ["binance", "bybit"],
        "orderbook_depth": {"binance": 20, "bybit": 50},
        "update_interval_seconds": 1,
        "liquidity_wall_threshold": 5,
    },
    "PAIR_PROFILES": {},
    "BT_AUTO_TOGGLE": False,           # If False, backtest will never enable/disable live pairs
    "BT_PERCENTILE_FILTER": False,     # If False, disable rolling percentile floor in backtest
    # ── Execution engine ────────────────────────────────────────────────────
    "EXECUTION_ENABLED": True,          # Master switch — enabled for demo live-level testing
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
    # ── Auto-Trade Bot ────────────────────────────────────────────────────────
    "AUTO_TRADE_ENABLED":       False,  # Master toggle (also togglable via UI/API)
    "AUTO_TRADE_MIN_SCORE":     0.75,   # Min confluence score to auto-execute (z-score scale)
    "AUTO_TRADE_MAX_DAILY":     3,      # Max auto-trades per calendar day (UTC)
    "AUTO_TRADE_MAX_PER_SCAN":  1,      # Max executions per single scan run
    "AUTO_TRADE_SIZING_OVERRIDE": 1.0,  # Full live-level sizing on demo
    "AUTO_TRADE_SCAN_INTERVAL_MIN": 30, # Scan every N minutes (30 = twice per hour)
    "AUTO_TRADE_SESSIONS": {            # Sessions per asset class; "always" = 24/7
        "forex":     ["london", "new_york", "london_ny_overlap"],
        "crypto":    ["always"],
        "stock":     ["jse", "us_regular"],
        "commodity": ["always"],
        "index":     ["always"],
    },
    # ── AI Self-Learning ──────────────────────────────────────────────────────
    "LEARNING_ENABLED":         True,   # Extract learning data after each trade closes
    "LEARNING_MIN_TRADES":      5,      # Min trades before context injected into AI
    "LEARNING_LOOKBACK_DAYS":   90,     # Days of history to query for context
    "META_ANALYSIS_ENABLED":    True,   # Weekly meta-analysis via Claude
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
    pair_profiles = cfg.get("PAIR_PROFILES", {}) or {}
    if not isinstance(pair_profiles, dict):
        log.warning(f"[CFG] PAIR_PROFILES must be a dict, got {type(pair_profiles).__name__!r}")
        return
    for profile_name, profile in pair_profiles.items():
        if not isinstance(profile, dict):
            log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] must be a dict")
            continue
        disabled_votes = set(profile.get("disabled_votes", []) or [])
        unknown_votes = sorted(disabled_votes - PAIR_PROFILE_VOTES)
        if unknown_votes:
            log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown disabled_votes: {unknown_votes}")
        disabled_filters = set(profile.get("disable_filters", []) or [])
        unknown_filters = sorted(disabled_filters - PAIR_PROFILE_FILTERS)
        if unknown_filters:
            log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown disable_filters: {unknown_filters}")
        weight_overrides = profile.get("weight_overrides", {}) or {}
        if not isinstance(weight_overrides, dict):
            log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}].weight_overrides must be a dict")
            weight_overrides = {}
        for vote_name, weight in weight_overrides.items():
            if vote_name not in PAIR_PROFILE_VOTES:
                log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown vote override: {vote_name!r}")
                continue
            if vote_name in disabled_votes:
                log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] disables and overrides {vote_name!r}; disabled_votes wins")
            try:
                float(weight)
            except (TypeError, ValueError):
                log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] invalid weight for {vote_name!r}: {weight!r}")
        for numeric_key in ("min_confluence", "bt_min", "volume_threshold"):
            if numeric_key in profile:
                try:
                    float(profile[numeric_key])
                except (TypeError, ValueError):
                    log.warning(
                        f"[CFG] PAIR_PROFILES[{profile_name!r}] {numeric_key} must be numeric, got {profile[numeric_key]!r}"
                    )


validate_config(CONFIG)


def _json_safe(value):
    """Recursively convert NaN/inf float values to None so Flask emits valid JSON."""
    import math as _math
    if isinstance(value, float):
        return value if _math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value
