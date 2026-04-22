"""config.py — Sentinel Pro configuration loading and validation.

CONFIG is built from hard-coded defaults then overlaid with config.yaml values.
Import CONFIG from here; never import from athena.py directly.
"""

import os
import logging

log = logging.getLogger("sentinel")

AI_API_KEY_PLACEHOLDER = "YOUR_XAI_API_KEY"
_LEGACY_AI_API_KEY_PLACEHOLDER = "YOUR_MOONSHOT_API_KEY"
_AI_BASE_URL_DEFAULT = "https://api.x.ai/v1"
_AI_MODEL_DEFAULT = os.environ.get("AI_MODEL", "grok-4-1-fast-reasoning")


def _deep_merge_dict(base: dict, overrides: dict) -> dict:
    """Recursively merge nested dicts while preserving unspecified defaults."""
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _clean_ai_value(value: object) -> str:
    text = str(value or "").strip()
    if text in {
        "",
        AI_API_KEY_PLACEHOLDER,
        _LEGACY_AI_API_KEY_PLACEHOLDER,
    }:
        return ""
    return text


def get_ai_api_key(cfg: dict | None = None) -> str:
    cfg = cfg or CONFIG
    candidates = (
        os.environ.get("XAI_API_KEY", ""),
        cfg.get("XAI_API_KEY", ""),
        os.environ.get("MOONSHOT_API_KEY", ""),
        cfg.get("MOONSHOT_API_KEY", ""),
    )
    for candidate in candidates:
        cleaned = _clean_ai_value(candidate)
        if cleaned:
            return cleaned
    return ""


def ai_key_configured(cfg: dict | None = None) -> bool:
    return bool(get_ai_api_key(cfg))


def get_ai_base_url(cfg: dict | None = None) -> str:
    cfg = cfg or CONFIG
    return (
        str(os.environ.get("AI_BASE_URL", "") or "").strip()
        or str(cfg.get("AI_BASE_URL", "") or "").strip()
        or _AI_BASE_URL_DEFAULT
    )


def get_ai_model(
    cfg: dict | None = None,
    preferred_key: str = "AI_MODEL",
    fallback: str = "grok-4-1-fast-reasoning",
) -> str:
    cfg = cfg or CONFIG
    candidates = []
    if preferred_key:
        candidates.extend(
            [
                os.environ.get(preferred_key, ""),
                cfg.get(preferred_key, ""),
            ]
        )
    candidates.extend(
        [
            os.environ.get("AI_MODEL", ""),
            cfg.get("AI_MODEL", ""),
            cfg.get("XAI_MODEL", ""),
            fallback,
        ]
    )
    for candidate in candidates:
        cleaned = str(candidate or "").strip()
        if cleaned:
            return cleaned
    return fallback


def create_ai_client(cfg: dict | None = None, api_key: str | None = None):
    import openai

    resolved_key = _clean_ai_value(api_key) or get_ai_api_key(cfg)
    return openai.OpenAI(
        api_key=resolved_key,
        base_url=get_ai_base_url(cfg),
    )

PAIR_PROFILE_VOTES = {
    "d1_trend",
    "h1_ema",
    "d1_adx",
    "h4_macd",
    "h4_oscillator",
    "volume",
    "funding",
    "session",
    "h4_fib",
    "h1_bb",
    "weinstein",
    "divergence",
    "aroon",
}
PAIR_PROFILE_FILTERS = {
    "weinstein",
    "session",
    "regime_transition",
    "obv",
    "funding",
    "squeeze",
    "mean_revert",
    "btc_bias",
    "divergence_warning",
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
    "MOONSHOT_API_KEY": os.environ.get("MOONSHOT_API_KEY", AI_API_KEY_PLACEHOLDER),
    "XAI_API_KEY": os.environ.get("XAI_API_KEY", ""),
    "AI_BASE_URL": os.environ.get("AI_BASE_URL", _AI_BASE_URL_DEFAULT),
    "AI_MODEL": _AI_MODEL_DEFAULT,
    "XAI_MODEL": os.environ.get("XAI_MODEL", _AI_MODEL_DEFAULT),
    "LOTTERY_AI_MODEL": os.environ.get("LOTTERY_AI_MODEL", ""),  # empty → use AI_MODEL for /api/lottery/ai-analysis
    "DEBATE_MODEL": os.environ.get("DEBATE_MODEL", _AI_MODEL_DEFAULT),
    "VISION_MODEL": os.environ.get("VISION_MODEL", _AI_MODEL_DEFAULT),
    "NEWS_SENTIMENT_MODEL": os.environ.get("NEWS_SENTIMENT_MODEL", _AI_MODEL_DEFAULT),
    "NEWS_SENTIMENT_CONFLUENCE_ENABLED": False,
    "NEWS_SENTIMENT_CACHE_TTL_SEC": 900,
    "NEWS_SENTIMENT_SCORE_IMPACT": 0.06,
    "NEWS_SENTIMENT_ATTACH_SUMMARY": True,
    "NEWS_PAIR_CAP": 40,  # Max pairs for EODHD per-pair news + word-weights in news cache refresh
    "AI_STRUCTURED_OUTPUTS": True,
    "AI_TEMPERATURE": 0.3,
    "AI_VISION_TEMPERATURE": 0.2,  # /api/chart-analysis factual mode (override in config.yaml if needed)
    "ENGINE_B_PROFILE_SCORING_ENABLED": False,
    "CHART_VISION_DATASET_ENABLED": False,
    "CHART_VISION_V2_SHADOW_ENABLED": False,
    "CRYPTOPANIC_KEY": os.environ.get("CRYPTOPANIC_KEY", ""),
    "FINNHUB_KEY": os.environ.get("FINNHUB_KEY", ""),
    "RISK_PCT": 0.01,
    "SL_ATR_MULT": 1.5,
    "TP1_ATR_MULT": 2.0,
    "TP2_ATR_MULT": 3.5,
    "DAILY_LOSS_LIMIT": 0.05,  # Kill switch: halt trading after losing 5% of account in a day
    "VOLUME_THRESHOLD": 1.5,
    "VOLUME_THRESHOLD_BACKTEST": 1.2,
    "ADX_TREND_MIN": 25,
    # analyze_pair: fetch then drop last (forming) bar. H4/H1 align with /api/candles max (1000).
    # D1=1001 so closed D1 bars after drop ≈1000; tune in config.yaml. Lower = faster scans, chart diverges.
    "D1_CANDLES": 1001,
    "H4_CANDLES": 1000,
    "H1_CANDLES": 1000,
    "SCAN_MAX_WORKERS": 3,
    "SCAN_DEBUG_CANDLE_META": False,
    "FOREX_H4_RESAMPLE_OFFSET_HOURS": 0.0,
    "MIN_CONFLUENCE": 1.0,
    "RISK_MULT": {
        "commodity": 1.2,
        "crypto": 0.8,
        "forex": 0.6,
        "index": 0.6,
        "stock": 0.6,
    },
    # Round-trip fee per trade (entry + exit) as fraction of notional.
    # Bybit taker=0.055%×2=0.11%, forex ECN~0.02%×2=0.04%, stocks~0.03%×2=0.06%
    "FEE_PCT": {
        "crypto": 0.0011,
        "forex": 0.0004,
        "commodity": 0.0004,
        "stock": 0.0006,
        "index": 0.0004,
    },
    "RANGING": {
        "crypto": {"dead": 14, "dead_pen": 1.5, "choppy": 18, "choppy_pen": 0.5},
        "commodity": {"dead": 18, "dead_pen": 1.5, "choppy": 23, "choppy_pen": 0.5},
        "forex": {"dead": 18, "dead_pen": 1.5, "choppy": 23, "choppy_pen": 1.0},
        "stock": {"dead": 16, "dead_pen": 1.5, "choppy": 21, "choppy_pen": 0.5},
        "index": {"dead": 16, "dead_pen": 1.5, "choppy": 21, "choppy_pen": 0.5},
    },
    # ATR_CLASS: fallback when no style is set. Primary path is STYLE_ATR_MULTS below.
    "ATR_CLASS": {
        "forex": {"sl": 1.2, "tp1": 2.0, "tp2": 3.0},
        "commodity": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "index": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "stock": {"sl": 1.5, "tp1": 2.5, "tp2": 4.0},
        "crypto": {"sl": 2.0, "tp1": 3.5, "tp2": 5.0},
    },
    # Style-specific ATR multipliers — calibrated to industry benchmarks:
    #   quantstock.org, bestmt4ea.com, atrindicator.com, luxalgo.com, fxnx.com,
    #   cryptotrading-guide.com (2026), fxpremiere.com (XAU/USD), tapbit.com (2026).
    #
    # ATR timeframe reference per style:
    #   Scalp   → H1 ATR  (EUR/USD H1 ATR ≈ 12 pips)
    #   Intraday → H4 ATR  (EUR/USD H4 ATR ≈ 40 pips)
    #   Swing    → D1 ATR  (EUR/USD D1 ATR ≈ 70-90 pips; crypto also D1)
    #
    # Industry benchmark SL ranges:
    #   Scalp (H1):     1.0–1.5× ATR minimum
    #   Intraday (H4):  1.5–2.0× ATR minimum
    #   Swing (D1):     1.5–2.0× ATR (forex/stock); 1.5–2.5× ATR (crypto/commodity)
    #
    # RR ratios maintained across all styles:
    #   Scalp:    tp1/sl = 1.5:1, tp2/sl = 2.5:1
    #   Intraday: tp1/sl = 2.0:1, tp2/sl = 3.33:1
    #   Swing:    tp1/sl = 1.67:1, tp2/sl = 2.67:1
    #
    # TP1: quick partial exit at ~1.5–2× risk (take 50–70% here).
    # TP2: runner target — move SL to breakeven after TP1 fills.
    "STYLE_ATR_MULTS": {
        "scalp": {
            # H1 ATR ref. Industry min 1.0–1.5× ATR. Previous 0.50 (6 pip SL on EURUSD) was
            # sub-spread on ECN brokers and guaranteed noise stop-outs.
            "forex":     {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
            "crypto":    {"sl": 1.20, "tp1": 1.80, "tp2": 3.00},
            "stock":     {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
            "commodity": {"sl": 1.20, "tp1": 1.80, "tp2": 3.00},
            "index":     {"sl": 1.00, "tp1": 1.50, "tp2": 2.50},
        },
        "intraday": {
            # H4 ATR ref. Industry min 1.5–2.0× ATR. Previous 0.75 (30 pip SL on EURUSD)
            # absorbable by a single 5-minute news spike.
            # tp2 set to 4.50 (3.0 RR with sl=1.50) so swing tp2 is strictly wider.
            "forex":     {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
            "crypto":    {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
            "stock":     {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
            "commodity": {"sl": 2.00, "tp1": 4.00, "tp2": 6.00},
            "index":     {"sl": 1.50, "tp1": 3.00, "tp2": 4.50},
        },
        "swing": {
            # D1 ATR ref for all (crypto now also D1 — see LEVEL_ATR_PRIORITY below).
            # Crypto sl reduced from 2.0 to 1.5 because previous H4-based 2.0× produced
            # SLs of 400–600+ pips on altcoins (e.g. XRP ~$0.57 with H4 ATR ~$0.022).
            # All swing sl values ≥ 1.80 to keep scalp < intraday < swing ordering
            # with the same atr reference (intraday sl = 1.50).
            "forex":     {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
            "crypto":    {"sl": 1.50, "tp1": 2.50, "tp2": 4.00},
            "stock":     {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
            "commodity": {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
            "index":     {"sl": 1.80, "tp1": 3.00, "tp2": 5.00},
        },
    },
    "LEVEL_ATR_PRIORITY": {
        "default": {
            "scalp": ["H1", "H4", "D1"],
            "intraday": ["H4", "H1", "D1"],
            "swing": ["D1", "H4", "H1"],
        },
        "crypto": {
            "scalp": ["H1", "H4", "D1"],
            "intraday": ["H4", "H1", "D1"],
            # Changed from ["H4","D1","H1"]: crypto swing now uses D1 ATR first,
            # consistent with all other asset classes. Coordinated with sl reduction
            # from 2.0→1.5 so the wider D1 ATR doesn't produce larger stops.
            "swing": ["D1", "H4", "H1"],
        },
    },
    # Regime scaling factors applied inside calc_levels (indicators.py).
    # Applied multiplicatively to sl_mult, tp1_mult, tp2_mult.
    # Reduced HIGH_VOLATILITY from 1.35 to 1.15 — the old 35% SL inflation compounded
    # with wide base multipliers to produce structurally absurd SL distances on altcoins.
    # Reduced TRENDING from 1.25 to 1.10 — slight breathing room is sufficient.
    # Exposed here (not hardcoded in indicators.py) so yaml overrides are possible.
    "CALC_LEVELS_REGIME_FACTOR": {
        0: 1.10,   # TRENDING — slight breathing room for trend continuation
        1: 1.00,   # RANGING — base multipliers apply unchanged
        2: 1.15,   # HIGH_VOLATILITY — modest widening; was 1.35 (too aggressive)
        3: 0.90,   # LOW_VOLATILITY — tighter stops in compressed environments
    },
    # Rolling windows for normalization (bars)
    "NORMALIZATION_LOOKBACK": {
        "crypto": 300,
        "forex": 400,
        "commodity": 300,
        "stock": 350,
        "index": 350,
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
    "ADX_TREND_MIN_CLASS": {
        "crypto": 20,
        "forex": 22,
        "commodity": 25,
        "stock": 25,
        "index": 25,
    },
    "COUNTER_TREND_PEN": {
        "crypto": -1.0,
        "forex": -1.0,
        "commodity": -1.0,
        "stock": -1.0,
        "index": -1.0,
    },
    # TA-Lib standard: RSI overbought=70, oversold=30 (confirmed OANDA/LiteFinance/Altrady 2024)
    "RSI_BOUNDS": {
        "crypto": {"ob": 70, "os": 30},
        "forex": {"ob": 70, "os": 30},
        "commodity": {"ob": 70, "os": 30},
        "stock": {"ob": 70, "os": 30},
        "index": {"ob": 70, "os": 30},
    },
    "MACRO_LOOKBACK": {
        "crypto": 15,
        "forex": 30,
        "commodity": 50,
        "stock": 50,
        "index": 50,
    },
    "WEINSTEIN_LOOKBACK": {
        "crypto": 60,
        "forex": 100,
        "commodity": 150,
        "stock": 150,
        "index": 150,
    },
    "BT_MIN": {
        "crypto": 2.15,
        "commodity": 1.80,
        "forex": 1.60,
        "stock": 1.10,
        "index": 1.17,
    },
    # BT_MIN_GROUP removed — backtest uses BT_MIN class-level thresholds directly.
    # Engine A backtest: use BT_MIN / pair bt_min when True (live unchanged).
    # RESEARCH_MODE also enables the same BT chain (legacy alias).
    "RESEARCH_MODE": False,
    "BACKTEST_USE_BT_MIN_THRESHOLDS": False,
    "BACKTEST_EVENT_RISK_GATING": False,
    "BACKTEST_SENTIMENT_GATING": False,
    "MIN_CONFLUENCE_CLASS": {
        "crypto": 2.4,
        "forex": 2.1,
        "commodity": 1.8,
        "stock": 1.8,
        "index": 1.5,
    },
    # PHASE 3: Engine C Backtest Exit Controls - explicit config for MAX_HOLD and BE parameters
    "ENGINE_C_BT_EXIT": {
        "forex": {
            "intraday": {"max_hold_bars": 30, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "swing": {"max_hold_bars": 60, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "scalp": {"max_hold_bars": 12, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
        },
        "crypto": {
            "intraday": {"max_hold_bars": 40, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "swing": {"max_hold_bars": 80, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
            "scalp": {"max_hold_bars": 12, "be_arm_rr": 1.5, "be_min_target_rr": 2.0},
        },
        "stock": {
            "intraday": {"max_hold_bars": 24, "be_arm_rr": 1.5, "be_min_target_rr": 1.5},
            "swing": {"max_hold_bars": 50, "be_arm_rr": 1.5, "be_min_target_rr": 1.5},
            "scalp": {"max_hold_bars": 8, "be_arm_rr": 1.5, "be_min_target_rr": 1.5},
        },
        "commodity": {
            "intraday": {"max_hold_bars": 24, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "swing": {"max_hold_bars": 50, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "scalp": {"max_hold_bars": 10, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
        },
        "index": {
            "intraday": {"max_hold_bars": 24, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "swing": {"max_hold_bars": 50, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
            "scalp": {"max_hold_bars": 10, "be_arm_rr": 1.5, "be_min_target_rr": 1.8},
        },
    },
    # Full-scan cross-sectional quantile (see scanner.compute_scan_quantile_floors).
    "SCAN_QUANTILE_ENABLED": False,
    "SCAN_QUANTILE_MIN_SAMPLES": 5,
    "SCAN_QUANTILE_EXCLUDE_TYPES": ["crypto"],
    "SCAN_QUANTILE_TOP_FRACTION": {
        "default": 0.20,
        "crypto": 0.18,
        "forex": 0.22,
        "stock": 0.15,
        "index": 0.15,
        "commodity": 0.18,
    },
    # MIN_CONFLUENCE_GROUP removed — live uses MIN_CONFLUENCE_CLASS directly.
    # Factor scoring gates — see factor_scoring.py
    "FACTOR_MIN_DIRECTIONAL": 0.25,  # Skip if abs(dir_score) < this (near-directionless signal)
    "FACTOR_DIRECTIONAL_SOFT_SPAN": 0.20,  # Smooth transition width for directional confidence
    "FACTOR_MIN_DIRECTIONAL_CRYPTO": 0.15,
    "FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO": 0.30,
    "CRYPTO_TRANSITION_PENALTY_ENABLED": True,
    "CRYPTO_LIVE_MICROSTRUCTURE_SCORING_ENABLED": True,
    "REGIME_SMOOTHING_BARS": 3,  # Consecutive bars required before committing to a regime change
    # Factor weights per asset class (base, before regime overrides)
    "FACTOR_WEIGHTS": {
        "crypto": {
            "trend": 2.0,
            "trend_strength": 1.0,
            "momentum": 1.5,
            "volatility": 1.0,
            "volume": 1.0,
            "structure": 1.0,
            "derivatives": 1.0,
            "microstructure": 0.75,
            "carry": 0.0,
        },
        "forex": {
            "trend": 2.0,
            "trend_strength": 1.0,
            "momentum": 1.0,
            "volatility": 1.0,
            "volume": 0.5,
            "structure": 1.5,
            "derivatives": 0.0,
            "microstructure": 0.5,
            "carry": 0.0,
        },
        "stock": {
            "trend": 2.0,
            "trend_strength": 1.0,
            "momentum": 1.5,
            "volatility": 1.0,
            "volume": 1.5,
            "structure": 1.0,
            "derivatives": 0.5,
            "microstructure": 0.75,
            "carry": 1.0,
        },
        "commodity": {
            "trend": 2.0,
            "trend_strength": 1.0,
            "momentum": 1.3,
            "volatility": 1.5,
            "volume": 1.0,
            "structure": 1.3,
            "derivatives": 0.0,
            "microstructure": 0.75,
            "carry": 0.0,
        },
        "index": {
            "trend": 2.0,
            "trend_strength": 1.0,
            "momentum": 1.4,
            "volatility": 1.2,
            "volume": 0.8,
            "structure": 1.0,
            "derivatives": 1.2,
            "microstructure": 0.75,
            "carry": 1.0,
        },
    },
    # Optional subgroup multipliers for factor-group weights (Engine A non-forex).
    "FACTOR_SCORE_GROUP_MULTIPLIERS": {
        "us_stock_single": {"volatility": 1.2, "volume": 1.2, "momentum": 0.9},
        "bond_tlt": {"trend": 0.8, "volatility": 1.2, "carry": 1.3},
        "smallcap_em_etf": {"volatility": 1.2, "momentum": 1.1},
        "asian_indices": {"volatility": 1.15, "momentum": 1.1},
        "energy_oil": {"volatility": 1.15, "structure": 1.1},
        "precious_trackers": {"structure": 1.1, "volatility": 1.1},
        "nat_gas": {"volatility": 1.35, "structure": 1.15},
        "copper": {"trend": 1.1, "structure": 1.15},
        "pgm_metals": {"volatility": 1.2, "structure": 1.15},
        "crypto_btc": {"derivatives": 1.1},
        "crypto_eth": {"derivatives": 1.05},
        "crypto_doge": {"volatility": 1.35},
        "crypto_alt_majors": {"trend": 1.05, "momentum": 1.05},
        "crypto_other": {"trend": 1.05, "momentum": 1.05},
    },
    # Regime-aware factor weight overrides
    "REGIME_WEIGHTS": {
        "TRENDING": {
            "trend": 2.0,
            "trend_strength": 1.5,
            "momentum": 1.5,
            "volatility": 1.0,
            "volume": 1.0,
            "structure": 0.5,
            "derivatives": 1.2,
            "microstructure": 1.5,
            "carry": 1.0,
        },
        "RANGING": {
            "trend": 0.5,
            "trend_strength": 0.5,
            "momentum": 1.5,
            "volatility": 1.0,
            "volume": 1.0,
            "structure": 2.0,
            "derivatives": 1.0,
            "microstructure": 1.5,
            "carry": 1.0,
        },
        "HIGH_VOLATILITY": {
            "trend": 1.0,
            "trend_strength": 1.0,
            "momentum": 1.0,
            "volatility": 2.0,
            "volume": 1.5,
            "structure": 1.0,
            "derivatives": 1.5,
            "microstructure": 1.5,
            "carry": 1.0,
        },
        "LOW_VOLATILITY": {
            "trend": 1.2,
            "trend_strength": 0.8,
            "momentum": 1.0,
            "volatility": 0.5,
            "volume": 1.0,
            "structure": 1.0,
            "derivatives": 1.0,
            "microstructure": 1.5,
            "carry": 1.0,
        },
    },
    # Per-indicator weights within each factor group (multiplied with correlation weights).
    # Missing keys default to 1.0. Set to 0.0 to disable an indicator.
    "INDICATOR_WEIGHTS": {
        "trend": {
            "crypto": {"d1_ema_trend": 0.5, "h4_ema_trend": 0.3, "ema_trend": 0.2},
            "forex": {"d1_ema_trend": 0.5, "h4_ema_trend": 0.3, "ema_trend": 0.2},
            "commodity": {
                "d1_ema_trend": 0.34,
                "h4_ema_trend": 0.33,
                "ema_trend": 0.33,
            },
            "stock": {"d1_ema_trend": 0.4, "h4_ema_trend": 0.35, "ema_trend": 0.25},
            "index": {"d1_ema_trend": 0.4, "h4_ema_trend": 0.35, "ema_trend": 0.25},
        },
        "momentum": {
            "default": {"rsi_z": 0.6, "macdLine_z": 0.4},
            "crypto": {
                "rsi_z": 0.5,
                "macdLine_z": 0.3,
                "volume_momentum_spread": 0.2,
            },
        },
        "derivatives": {
            "default": {"cot_z": 0.6, "funding_rate": 0.4},
            "crypto": {"funding_rate": 0.75, "cot_z": 0.25},
        },
        "microstructure": {
            "order_book_imbalance": 0.4,
            "liquidity_wall_detection": 0.25,
            "orderflow_delta": 0.2,
            "liquidity_pressure": 0.15,
        },
        "volatility": {"atr_z": 0.5, "bbWidth_z": 0.3, "realized_vol_z": 0.2},
        "volume": {"volume_ratio": 0.7, "obv_trend": 0.3},
    },
    "CRYPTO_FACTOR_WEIGHT_CAPS": {
        "derivatives": 1.0,
        "microstructure": 0.75,
        "carry": 0.0,
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
    # Log Engine C ALIGNED+tradeable rows to shadow_signals (no broker); Performance tab reads them.
    "SHADOW_LEDGER_ENABLED": True,
    "MICROSTRUCTURE_FEEDS_ENABLED": True,
    "MICROSTRUCTURE_BYBIT_FEEDS_ENABLED": False,
    "MARKET_DATA_WS_SSL_VERIFY": False,
    "BYBIT_TIME_SYNC_ENABLED": False,
    "BYBIT_RECV_WINDOW_MS": 30000,
    "PAIR_PROFILES": {},
    "BT_AUTO_TOGGLE": False,  # If False, backtest will never enable/disable live pairs
    "BT_PERCENTILE_FILTER": False,  # If False, disable rolling percentile floor in backtest
    # ── Execution engine ────────────────────────────────────────────────────
    "EXECUTION_ENABLED": True,  # Master switch — enabled for demo live-level testing
    "AUTO_EXECUTE": False,  # Auto-execute after AI grade (manual click only when False)
    "AUTO_EXECUTE_MIN_SCORE": 8.0,  # Minimum confluence score for auto-execute
    "AUTO_EXECUTE_MIN_GRADE": "B",  # Minimum AI grade for auto-execute
    "MAX_PORTFOLIO_HEAT": 0.06,  # 6% total risk across all positions
    "MAX_OPEN_POSITIONS": 20,  # Max simultaneous open trades
    "MAX_CORRELATED_POSITIONS": 2,  # Max positions in same correlation cluster
    "SIGNAL_MAX_AGE_SEC": 1800,  # Reject signals older than 30 minutes
    "MAX_RISK_PER_TRADE": 0.03,  # Hard cap: never risk > 3% on single trade
    "MAX_SL_PCT": {
        "forex":     0.025,
        "crypto":    0.08,
        "commodity": 0.04,
        "index":     0.04,
        "stock":     0.08,
    },
    "DRAWDOWN_REDUCE_THRESHOLD": 0.10,  # At 10% drawdown, halve position sizes
    "DRAWDOWN_STOP_THRESHOLD": 0.15,  # At 15% drawdown, reject ALL new trades
    # ── Auto-Trade Bot ────────────────────────────────────────────────────────
    "AUTO_TRADE_ENABLED": False,  # Master toggle (also togglable via UI/API)
    "AUTO_TRADE_MIN_SCORE": {  # Scan floor that determines which signals reach the auto-trader candidate list
        "crypto": 1.40,
        "commodity": 1.40,
        "stock": 1.55,
        "index": 1.35,
        "forex": 1.0,
    },
    "AUTO_TRADE_MIN_CONVICTION": {  # Live auto-execute gate on combinedConviction (0-1 scale)
        "default": 0.50,
    },
    "AUTO_TRADE_MAX_DAILY": 20,  # Max auto-trades per calendar day (UTC)
    "AUTO_TRADE_MAX_PER_SCAN": 1,  # Max executions per single scan run
    "AUTO_TRADE_SIZING_OVERRIDE": 1.0,  # Full live-level sizing on demo
    "AUTO_TRADE_SCAN_INTERVAL_MIN": 30,  # Scan every N minutes (30 = twice per hour)
    "AUTO_TRADE_SESSIONS": {  # Sessions per asset class; "always" = 24/7
        "forex": ["london", "new_york", "london_ny_overlap"],
        "crypto": ["always"],
        "stock": ["jse", "us_regular"],
        "commodity": ["always"],
        "index": ["always"],
    },
    "AUTO_TRADE_BLOCKED_TREND_STATES": {
        "default": ["DEAD RANGING", "RANGING"],
        "crypto": ["DEAD RANGING", "RANGING"],
        "forex": [],
        "commodity": ["DEAD RANGING", "RANGING"],
        "stock": ["DEAD RANGING", "RANGING"],
        "index": ["DEAD RANGING", "RANGING"],
    },
    # Engine A trend-state block list for live scan tier classification AND
    # backtest entry. Research 2026-04-18 (n=393, 4 crypto + 4 forex, intraday)
    # showed DEAD RANGING (WR 18.5%, avgR -0.59R) and DEVELOPING (WR 32.7%,
    # avgR -0.19R) are reliably unprofitable. Accepts a flat list (global)
    # or a per-asset-class dict with optional "default" fallback.
    "ENGINE_A_BLOCKED_TREND_STATES": ["DEAD RANGING", "DEVELOPING"],
    "AUTO_TRADE_BLOCKED_REGIMES": {
        "default": ["RANGING"],
        "crypto": ["RANGING"],
        "forex": [],
        "commodity": ["RANGING"],
        "stock": ["RANGING"],
        "index": ["RANGING"],
    },
    "SIGNAL_DEBATE_ENABLED": True,
    "SENTIMENT_GATE_ENABLED": True,
    "SENTIMENT_BLOCK_THRESHOLD": 0.4,
    "SENTIMENT_ALIGN_THRESHOLD": 0.3,
    "SENTIMENT_API_FAIL_CLOSED": False,
    "EVENT_RISK_ENABLED": True,
    "EVENT_RISK_HOURS": 4,
    "EVENT_RISK_API_FAIL_CLOSED": False,
    # ── AI Self-Learning ──────────────────────────────────────────────────────
    "LEARNING_ENABLED": True,  # Extract learning data after each trade closes
    "LEARNING_MIN_TRADES": 5,  # Min trades before context injected into AI
    "LEARNING_LOOKBACK_DAYS": 90,  # Days of history to query for context
    "META_ANALYSIS_ENABLED": True,  # Weekly meta-analysis via configured AI provider
    "EODHD_EARNINGS_CALENDAR_ENABLED": False,  # Disabled by default; unsupported on many EODHD plans
    # ── Engine B AI Controls ─────────────────────────────────────────────────
    "ENGINE_B_NEWS_CONTEXT_ENABLED": True,  # Feed news into Engine B AI advisory (not checklist)
    "ENGINE_B_ZONE_PERSISTENCE": False,  # Persist Engine B OB/FVG registry to zones.db
    "AI_ON_DEMAND_ONLY": True,  # AI runs on user-initiated actions only, not auto-scans
    # ── Engine C B-side fallback controls ─────────────────────────────────────
    "ENGINE_C_B_ONLY_MULT": 0.65,  # Scale B-only conviction when A has no signal
    "ENGINE_C_B_CONFLICT_OVERRIDE_ENABLED": True,  # Allow strong B to override weak opposing A
    "ENGINE_C_B_CONFLICT_MIN_SCORE": 0.70,  # Minimum B normalized score for conflict override
    "ENGINE_C_A_CONFLICT_MAX_SCORE": 0.45,  # Max opposing A normalized score for B override
    "ENGINE_C_B_CONFLICT_PENALTY": 0.85,  # Penalty applied to B score during conflict override
    # ── Engine B (Naked Scalp) ────────────────────────────────────────────────
    "NAKED_ENGINE": {
        "zone_multipliers": {
            "TRENDING": {"upper": 0.3, "lower": 1.0, "sl": 1.5},
            "RANGING": {"upper": 0.5, "lower": 1.2, "sl": 1.0},
            "HIGH_VOLATILITY": {"upper": 0.4, "lower": 1.5, "sl": 1.8},
            "LOW_VOLATILITY": {"upper": 0.2, "lower": 0.8, "sl": 1.0},
        },
        "style_profiles": {
            "scalp": {
                "min_score": 3.0,
                "min_rr": 1.5,
                "fallback_rr": 2.0,
                "require_macro_align": False,
            },
            "intraday": {
                "min_score": 4.0,
                "min_rr": 1.5,
                "fallback_rr": 2.0,
                "require_macro_align": False,
            },
            "swing": {
                "min_score": 5.0,
                "min_rr": 2.0,
                "fallback_rr": 3.0,
                "require_macro_align": False,
            },
        },
        # Optional subgroup-level Engine B strictness overrides by style.
        "score_group_overrides": {
            "forex_exotics": {
                "scalp": {"min_room_atr": 0.5, "min_rr": 1.2},
                "intraday": {"min_room_atr": 0.85, "min_rr": 1.35},
                "swing": {"min_room_atr": 1.2, "min_rr": 1.8},
            },
            "nat_gas": {
                "scalp": {"min_score": 4.0, "min_rr": 1.4},
                "intraday": {"min_score": 4.0, "min_rr": 1.6},
                "swing": {"min_score": 5.0, "min_rr": 2.0},
            },
            "crypto_doge": {
                "scalp": {"min_score": 4.0, "min_room_atr": 0.5, "min_rr": 1.3},
                "intraday": {"min_score": 4.0, "min_room_atr": 0.9, "min_rr": 1.5},
                "swing": {"min_score": 5.0, "min_room_atr": 1.2, "min_rr": 1.9},
            },
            "crypto_btc": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "crypto_eth": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "crypto_alt_majors": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "crypto_other": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "energy_oil": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "precious_trackers": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "commodity_other": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "copper": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "pgm_metals": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "us_stock_single": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "stock_other": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "smallcap_em_etf": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
            "bond_tlt": {
                "scalp": {"min_score": 4.0},
                "intraday": {"min_score": 4.0},
                "swing": {"min_score": 5.0},
            },
        },
    },
    "ENGINE_B_REGIME_MULTIPLIERS": {
        "TRENDING": 0.90,
        "RANGING": 1.30,
        "HIGH_VOLATILITY": 1.15,
        "LOW_VOLATILITY": 1.10,
    },
    "FOREX_ENGINE": {
        "hurst_gate_enabled": True,
        "hurst_gate_threshold": 0.52,
        "trend_gate_adx_source": "d1",
        "trend_gate_adx_min": 20.0,
        "trend_margin_min": 0.003,
        "adx_confirm_min": 22.0,
        "h1_ema_entry_filter": True,
        "trend_support_weights": {
            "momentum": 0.15,
            "adx": 0.10,
            "carry": 0.05,
        },
        "score_group_adjustments": {
            "forex_majors": {
                "momentum_mult": 1.0,
                "adx_mult": 1.0,
                "carry_mult": 1.0,
                "score_mult": 1.0,
            },
            "forex_crosses": {
                "momentum_mult": 1.1,
                "adx_mult": 1.05,
                "carry_mult": 0.85,
                "score_mult": 1.0,
            },
            "forex_exotics": {
                "momentum_mult": 1.15,
                "adx_mult": 1.15,
                "carry_mult": 0.9,
                "score_mult": 0.95,
            },
        },
    },
}

# Apply YAML overrides — deep-merge dicts, overwrite scalars
for _k, _v in _yaml_overrides.items():
    if _k in CONFIG and isinstance(CONFIG[_k], dict) and isinstance(_v, dict):
        CONFIG[_k] = _deep_merge_dict(CONFIG[_k], _v)
    else:
        CONFIG[_k] = _v


def validate_config(cfg: dict) -> None:
    """Warn on mis-typed or dangerous CONFIG values after YAML overrides are applied."""
    for k in (
        "RISK_PCT",
        "SL_ATR_MULT",
        "TP1_ATR_MULT",
        "TP2_ATR_MULT",
        "ADX_TREND_MIN",
        "MIN_CONFLUENCE",
    ):
        v = cfg.get(k)
        if not isinstance(v, (int, float)):
            log.warning(f"[CFG] {k} expected number, got {type(v).__name__!r}")
        elif v <= 0:
            log.warning(f"[CFG] {k}={v} is non-positive — check config.yaml")
    for k in ("D1_CANDLES", "H4_CANDLES", "H1_CANDLES"):
        v = cfg.get(k)
        if not isinstance(v, int) or v < 10:
            log.warning(f"[CFG] {k}={v} is too low — minimum 10 candles required")
    try:
        float(cfg.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 0.0) or 0.0)
    except (TypeError, ValueError):
        log.warning("[CFG] FOREX_H4_RESAMPLE_OFFSET_HOURS must be numeric")
    if cfg.get("RISK_PCT", 0) > 0.05:
        log.warning(
            f"[CFG] RISK_PCT={cfg['RISK_PCT']:.1%} exceeds 5% — verify this is intentional"
        )
    _asset_classes = {"crypto", "forex", "commodity", "stock", "index"}
    for sub_key in (
        "RISK_MULT",
        "RANGING",
        "ATR_CLASS",
        "ADX_TREND_MIN_CLASS",
        "COUNTER_TREND_PEN",
        "RSI_BOUNDS",
        "BT_MIN",
        "MIN_CONFLUENCE_CLASS",
    ):
        missing = _asset_classes - set(cfg.get(sub_key, {}).keys())
        if missing:
            log.warning(f"[CFG] {sub_key} missing asset classes: {missing}")
    pair_profiles = cfg.get("PAIR_PROFILES", {}) or {}
    if not isinstance(pair_profiles, dict):
        log.warning(
            f"[CFG] PAIR_PROFILES must be a dict, got {type(pair_profiles).__name__!r}"
        )
        return
    for profile_name, profile in pair_profiles.items():
        if not isinstance(profile, dict):
            log.warning(f"[CFG] PAIR_PROFILES[{profile_name!r}] must be a dict")
            continue
        disabled_votes = set(profile.get("disabled_votes", []) or [])
        unknown_votes = sorted(disabled_votes - PAIR_PROFILE_VOTES)
        if unknown_votes:
            log.warning(
                f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown disabled_votes: {unknown_votes}"
            )
        disabled_filters = set(profile.get("disable_filters", []) or [])
        unknown_filters = sorted(disabled_filters - PAIR_PROFILE_FILTERS)
        if unknown_filters:
            log.warning(
                f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown disable_filters: {unknown_filters}"
            )
        weight_overrides = profile.get("weight_overrides", {}) or {}
        if not isinstance(weight_overrides, dict):
            log.warning(
                f"[CFG] PAIR_PROFILES[{profile_name!r}].weight_overrides must be a dict"
            )
            weight_overrides = {}
        for vote_name, weight in weight_overrides.items():
            if vote_name not in PAIR_PROFILE_VOTES:
                log.warning(
                    f"[CFG] PAIR_PROFILES[{profile_name!r}] unknown vote override: {vote_name!r}"
                )
                continue
            if vote_name in disabled_votes:
                log.warning(
                    f"[CFG] PAIR_PROFILES[{profile_name!r}] disables and overrides {vote_name!r}; disabled_votes wins"
                )
            try:
                float(weight)
            except (TypeError, ValueError):
                log.warning(
                    f"[CFG] PAIR_PROFILES[{profile_name!r}] invalid weight for {vote_name!r}: {weight!r}"
                )
        for numeric_key in ("min_confluence", "bt_min", "volume_threshold"):
            if numeric_key in profile:
                try:
                    float(profile[numeric_key])
                except (TypeError, ValueError):
                    log.warning(
                        f"[CFG] PAIR_PROFILES[{profile_name!r}] {numeric_key} must be numeric, got {profile[numeric_key]!r}"
                    )


validate_config(CONFIG)


def scan_candle_limits() -> dict[str, int]:
    """Bar counts for Engine A (`analyze_pair`), Engine B, Engine C B-leg, and naked scans.

    Single source of truth: ``D1_CANDLES``, ``H4_CANDLES``, ``H1_CANDLES`` in CONFIG / config.yaml.
    ``fetch_candles`` (athena) routes by pair ``source`` to Binance (crypto), EODHD (forex/stocks/
    commodities/indices/ETFs), Polygon, or yfinance — same limits apply to every asset class.
    Callers should drop the last possibly-forming bar after fetch, matching ``analyze_pair``.
    """
    return {
        "D1": int(CONFIG["D1_CANDLES"]),
        "H4": int(CONFIG["H4_CANDLES"]),
        "H1": int(CONFIG["H1_CANDLES"]),
    }


def _json_safe(value):
    """Recursively convert NaN/inf float values to None so Flask emits valid JSON."""
    import math as _math

    try:
        import numpy as _np
    except Exception:
        _np = None

    if isinstance(value, float):
        return value if _math.isfinite(value) else None
    if _np is not None and isinstance(value, _np.generic):
        return _json_safe(value.item())
    if _np is not None and isinstance(value, _np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value
