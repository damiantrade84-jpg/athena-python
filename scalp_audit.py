"""scalp_audit.py — Engine D funnel diagnostics and shadow-simulation logging.

Rules:
- Report-only.  Does not alter thresholds, gates, or execution.
- Writes JSONL to logs/scalp_audit/engine_d_funnel.jsonl
- Shadow-simulates proximity variants without affecting live decisions.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("sentinel.scalp_audit")

_LOG_DIR = Path(__file__).parent / "logs" / "scalp_audit"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_FUNNEL_PATH = _LOG_DIR / "engine_d_funnel.jsonl"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_jsonl(path: Path, row: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        log.debug("[SCALP-AUDIT] write failed: %s", exc)


def log_engine_d_funnel(row: dict) -> None:
    """Write a single Engine D funnel row."""
    _write_jsonl(_FUNNEL_PATH, row)


def build_funnel_row(
    *,
    symbol: str,
    asset_type: str,
    source: str,
    scalp_enabled: bool = True,
    called: bool = True,
    data_available: bool = True,
    candle_timeframes_available: list[str] | None = None,
    lower_tf_candle_count: int | None = None,
    latest_lower_tf_candle: str | None = None,
    freshness_status: str = "unknown",
    spread: float | None = None,
    spread_ok: bool | None = None,
    atr: float | None = None,
    atr_ok: bool | None = None,
    volume_available: bool | None = None,
    volume_profile_available: bool | None = None,
    poc: float | None = None,
    vah: float | None = None,
    val: float | None = None,
    lvn_count: int | None = None,
    price_near_poc: bool | None = None,
    price_near_vah: bool | None = None,
    price_near_val: bool | None = None,
    cvd_available: bool | None = None,
    cvd_bias: str | None = None,
    absorption_detected: bool | None = None,
    vwap_available: bool | None = None,
    vwap_bias: str | None = None,
    liquidity_at_level: dict | None = None,
    setup_type: str | None = None,
    setup_direction: str | None = None,
    setup_score: float | None = None,
    setup_grade: str | None = None,
    min_grade_required: str | None = None,
    min_score_required: float | None = None,
    rr: float | None = None,
    rr_ok: bool | None = None,
    entry: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    gate_result: str = "NO_SETUP",
    fail_reasons: list[str] | None = None,
    soft_warnings: list[str] | None = None,
    diagnostic_notes: dict | None = None,
) -> dict:
    """Return a normalized funnel row dict."""
    return {
        "timestamp": _utc_iso(),
        "symbol": symbol,
        "asset_type": asset_type,
        "source": source,
        "scalp_enabled": scalp_enabled,
        "called": called,
        "data_available": data_available,
        "candle_timeframes_available": candle_timeframes_available or [],
        "lower_tf_candle_count": lower_tf_candle_count,
        "latest_lower_tf_candle": latest_lower_tf_candle,
        "freshness_status": freshness_status,
        "spread": spread,
        "spread_ok": spread_ok,
        "atr": atr,
        "atr_ok": atr_ok,
        "volume_available": volume_available,
        "volume_profile_available": volume_profile_available,
        "poc": poc,
        "vah": vah,
        "val": val,
        "lvn_count": lvn_count,
        "price_near_poc": price_near_poc,
        "price_near_vah": price_near_vah,
        "price_near_val": price_near_val,
        "cvd_available": cvd_available,
        "cvd_bias": cvd_bias,
        "absorption_detected": absorption_detected,
        "vwap_available": vwap_available,
        "vwap_bias": vwap_bias,
        "liquidity_at_level": liquidity_at_level,
        "setup_type": setup_type,
        "setup_direction": setup_direction,
        "setup_score": setup_score,
        "setup_grade": setup_grade,
        "min_grade_required": min_grade_required,
        "min_score_required": min_score_required,
        "rr": rr,
        "rr_ok": rr_ok,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "gate_result": gate_result,
        "fail_reasons": fail_reasons or [],
        "soft_warnings": soft_warnings or [],
        "diagnostic_notes": diagnostic_notes or {},
    }


def _shadow_near(price: float, level: float, proximity: float) -> bool:
    if not level or level <= 0:
        return False
    return abs(price - level) / level < proximity


def shadow_proximity_simulations(
    price: float,
    vp: dict,
    atr: float,
    tick_size: float = 1e-6,
    *,
    current_proximity_pct: float | None = None,
) -> dict:
    """Shadow-simulate how many levels price would be 'near' under different proximity rules.

    Returns dict of proximity_label -> count of nearby levels (POC/VAH/VAL/LVNs).
    Does NOT affect live decisions.
    """
    poc = vp.get("poc")
    vah = vp.get("vah")
    val = vp.get("val")
    lvns = vp.get("lvn_levels", [])
    ref_level = float(poc or vah or val or price or 1.0)
    if current_proximity_pct is None:
        try:
            from scalp_engine import engine_d_vp_proximity_pct
            from config import CONFIG

            cfg = CONFIG.get("SCALP_ENGINE", {})
            current_proximity_pct = engine_d_vp_proximity_pct(
                cfg,
                atr_m15=atr,
                ref_level=ref_level,
            )
        except Exception:
            current_proximity_pct = float(vp.get("_proximity_pct", 0.003))

    variants = {
        "current": float(current_proximity_pct),
        "atr_10pct": (atr * 0.10) / price if price > 0 else 0,
        "atr_15pct": (atr * 0.15) / price if price > 0 else 0,
        "atr_25pct": (atr * 0.25) / price if price > 0 else 0,
        "max_tick4_atr15": max((tick_size * 4) / price if price > 0 else 0, (atr * 0.15) / price if price > 0 else 0),
    }

    results: dict[str, dict[str, Any]] = {}
    for label, prox in variants.items():
        if prox <= 0:
            continue
        nearby = []
        if _shadow_near(price, poc, prox):
            nearby.append("poc")
        if _shadow_near(price, vah, prox):
            nearby.append("vah")
        if _shadow_near(price, val, prox):
            nearby.append("val")
        for lvn in lvns:
            if _shadow_near(price, lvn, prox):
                nearby.append(f"lvn@{lvn}")
        results[label] = {
            "proximity": round(prox * 100, 4),
            "nearby_levels": nearby,
            "count": len(nearby),
        }
    return results


def read_funnel_rows(
    *,
    since: str | None = None,
    symbol: str | None = None,
    gate_result: str | None = None,
) -> list[dict]:
    """Read back funnel rows for analysis."""
    rows: list[dict] = []
    if not _FUNNEL_PATH.exists():
        return rows
    try:
        with open(_FUNNEL_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if since and row.get("timestamp", "") < since:
                    continue
                if symbol and row.get("symbol") != symbol:
                    continue
                if gate_result and row.get("gate_result") != gate_result:
                    continue
                rows.append(row)
    except Exception as exc:
        log.debug("[SCALP-AUDIT] read failed: %s", exc)
    return rows


def clear_funnel_log() -> None:
    """Truncate funnel log (useful for tests)."""
    try:
        if _FUNNEL_PATH.exists():
            _FUNNEL_PATH.write_text("")
    except Exception as exc:
        log.debug("[SCALP-AUDIT] clear failed: %s", exc)
