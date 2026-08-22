"""OX Alpha engine settings.

Defaults live here; config.yaml ``OX_ALPHA_ENGINE`` overrides them. Nothing in
this module imports app config at module load — ``load_settings`` receives the
running CONFIG dict (or None in tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OxSettings:
    trade_threshold_default: float = 64.0
    watch_threshold_default: float = 50.0
    min_bars_m15: int = 60
    min_bars_h1: int = 40
    atr_pct_min: float = 0.0004     # dead-market floor (per-M15-bar ATR/close)
    atr_pct_max: float = 0.03       # chaos ceiling
    sl_buffer_atr: float = 0.18
    tp2_rr_extension: float = 1.6   # TP2 distance as multiple of TP1 distance
    scan_workers: int = 8
    candle_limit_m15: int = 300
    candle_limit_h1: int = 200
    volume_lookback: int = 48
    quote_max_age_sec: float = 30.0
    enabled: bool = True
    extra: dict = field(default_factory=dict)


_DEFAULTS = OxSettings()


def load_settings(config: dict | None) -> OxSettings:
    raw = (config or {}).get("OX_ALPHA_ENGINE") or {}
    if not isinstance(raw, dict):
        return _DEFAULTS

    def _f(key: str, fallback: float) -> float:
        try:
            v = float(raw.get(key, fallback))
            return v if v == v and v not in (float("inf"), float("-inf")) else fallback
        except (TypeError, ValueError):
            return fallback

    def _i(key: str, fallback: int) -> int:
        try:
            return int(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    def _b(key: str, fallback: bool) -> bool:
        v = raw.get(key, fallback)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"true", "1", "yes", "on"}
        return fallback

    return OxSettings(
        trade_threshold_default=_f("TRADE_THRESHOLD", _DEFAULTS.trade_threshold_default),
        watch_threshold_default=_f("WATCH_THRESHOLD", _DEFAULTS.watch_threshold_default),
        min_bars_m15=_i("MIN_BARS_M15", _DEFAULTS.min_bars_m15),
        min_bars_h1=_i("MIN_BARS_H1", _DEFAULTS.min_bars_h1),
        atr_pct_min=_f("ATR_PCT_MIN", _DEFAULTS.atr_pct_min),
        atr_pct_max=_f("ATR_PCT_MAX", _DEFAULTS.atr_pct_max),
        sl_buffer_atr=_f("SL_BUFFER_ATR", _DEFAULTS.sl_buffer_atr),
        tp2_rr_extension=_f("TP2_RR_EXTENSION", _DEFAULTS.tp2_rr_extension),
        scan_workers=max(1, _i("SCAN_WORKERS", _DEFAULTS.scan_workers)),
        candle_limit_m15=_i("CANDLE_LIMIT_M15", _DEFAULTS.candle_limit_m15),
        candle_limit_h1=_i("CANDLE_LIMIT_H1", _DEFAULTS.candle_limit_h1),
        volume_lookback=_i("VOLUME_LOOKBACK", _DEFAULTS.volume_lookback),
        quote_max_age_sec=_f("QUOTE_MAX_AGE_SEC", _DEFAULTS.quote_max_age_sec),
        enabled=_b("ENABLED", _DEFAULTS.enabled),
        extra=raw if isinstance(raw, dict) else {},
    )
