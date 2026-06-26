"""Lower-timeframe *shadow* diagnostics for Engine B and ASE.

WARNING — DIAGNOSTIC ONLY
=========================
Lower-TF shadow diagnostics are **not used for scoring, gating, ranking, or
execution**. Nothing in this module may influence signal direction, confidence,
eligibility, ranking, stop loss, take profit, risk sizing, autotrade, or any
deterministic safety gate. It only *observes* M30/M15/M5 context and reports it
on diagnostic/report payloads so dashboards and audits can see lower-timeframe
alignment that the production scan engines (which consume D1/H4/H1 only) do not.

Design rules enforced here:
  * Default OFF — controlled by ``CONFIG["LOWER_TF_SHADOW"]`` (``enabled: false``).
  * Uses an *injected* ``fetch_candles`` callable (the same fetcher the caller
    already uses), so existing symbol mapping / routing / caching is respected.
  * Never falls back from a missing lower TF to H1 — a missing TF is reported as
    missing/BLOCKED, full stop.
  * Never treats a forming candle as closed — trend state is computed on
    confirmed bars only and the forming bar is reported, not consumed.
  * Pure/observational: returns a plain dict; raises nothing the caller must
    handle for correctness (callers still wrap in try/except as a belt).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

log = logging.getLogger("lower_tf_shadow")

# Diagnostic-only marker stamped on every payload this module emits.
DIAGNOSTIC_ONLY_WARNING = (
    "Lower-TF shadow diagnostics are not used for scoring, gating, ranking, "
    "or execution."
)

# Default config — OFF unless explicitly enabled in config.yaml / CONFIG.
LOWER_TF_SHADOW_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "tfs": ["M30", "M15", "M5"],
    "apply_to": ["engine_b", "ase"],
    "diagnostic_only": True,
}

# Canonical role per timeframe (only used for human-readable field aliases).
_TF_ROLE = {
    "M30": ("m30_trend_state", "m30_setup_alignment"),
    "M15": ("m15_trigger_state", "m15_trigger_alignment"),
    "M5": ("m5_micro_state", "m5_precision_alignment"),
}

_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

_MIN_BARS_FOR_TREND = 10


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def lower_tf_shadow_config() -> dict[str, Any]:
    """Return the merged LOWER_TF_SHADOW config (defaults + CONFIG overrides)."""
    try:
        import config

        raw = config.CONFIG.get("LOWER_TF_SHADOW")
    except Exception:
        raw = None
    merged = dict(LOWER_TF_SHADOW_DEFAULTS)
    if isinstance(raw, dict):
        for key, val in raw.items():
            merged[key] = val
    # diagnostic_only is an invariant — it can never be turned off.
    merged["diagnostic_only"] = True
    return merged


def lower_tf_shadow_enabled(component: str) -> bool:
    """True only if the shadow is enabled AND the component opts in.

    ``component`` is "engine_b" or "ase". Default is always False.
    """
    cfg = lower_tf_shadow_config()
    if not bool(cfg.get("enabled", False)):
        return False
    apply_to = cfg.get("apply_to") or []
    try:
        return str(component) in {str(x) for x in apply_to}
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------
def _parse_bar_time(raw: Any) -> datetime | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            val = float(raw)
            # Heuristic ms vs s.
            secs = val / 1000.0 if val > 1e11 else val
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _bar_close(c: Any) -> float | None:
    if not isinstance(c, dict):
        return None
    raw = c.get("close")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _split_confirmed(
    candles: Sequence[dict], tf: str
) -> tuple[list[dict], bool, datetime | None]:
    """Split into (confirmed_bars, had_forming, last_confirmed_close_time).

    Never treats a forming bar as closed. Detection order:
      1. explicit per-bar ``confirmed`` flag if present;
      2. otherwise a timestamp heuristic (open_time + tf_seconds > now).
    """
    bars = [c for c in candles if isinstance(c, dict)]
    if not bars:
        return [], False, None

    last = bars[-1]
    had_forming = False

    if "confirmed" in last:
        if last.get("confirmed") is False:
            had_forming = True
            bars = bars[:-1]
    else:
        tf_sec = _TF_SECONDS.get(str(tf).upper())
        open_dt = _parse_bar_time(last.get("time") or last.get("ts") or last.get("t"))
        if tf_sec and open_dt is not None:
            close_dt = open_dt.timestamp() + tf_sec
            if close_dt > datetime.now(timezone.utc).timestamp():
                had_forming = True
                bars = bars[:-1]

    last_close_dt = None
    if bars:
        last_close_open = _parse_bar_time(
            bars[-1].get("time") or bars[-1].get("ts") or bars[-1].get("t")
        )
        tf_sec = _TF_SECONDS.get(str(tf).upper())
        if last_close_open is not None and tf_sec:
            last_close_dt = datetime.fromtimestamp(
                last_close_open.timestamp() + tf_sec, tz=timezone.utc
            )
        else:
            last_close_dt = last_close_open
    return bars, had_forming, last_close_dt


def _trend_state(confirmed: Sequence[dict]) -> str:
    """Diagnostic EMA/SMA-stack trend label on confirmed closes only."""
    closes = [v for v in (_bar_close(c) for c in confirmed) if v is not None]
    if len(closes) < _MIN_BARS_FOR_TREND:
        return "insufficient_data"
    fast_n = min(8, len(closes))
    slow_n = min(21, len(closes))
    sma_fast = sum(closes[-fast_n:]) / fast_n
    sma_slow = sum(closes[-slow_n:]) / slow_n
    slope = closes[-1] - closes[-min(5, len(closes))]
    if sma_fast > sma_slow and slope > 0:
        return "up"
    if sma_fast < sma_slow and slope < 0:
        return "down"
    return "flat"


def _alignment(trend: str, direction: str | None) -> str:
    d = (direction or "").upper()
    if d not in ("LONG", "SHORT"):
        return "n/a"
    if trend in ("insufficient_data", "missing"):
        return "n/a"
    if trend == "flat":
        return "neutral"
    if (d == "LONG" and trend == "up") or (d == "SHORT" and trend == "down"):
        return "aligned"
    return "counter"


def _source_label(source: Any) -> str:
    s = str(source or "").lower()
    if s in ("bybit", "binance"):
        return "Bybit"
    if s in ("mt5", "eodhd", "polygon", "yfinance"):
        return "MT5"
    return "unavailable" if not s else "MT5"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_lower_tf_shadow(
    *,
    pair: dict,
    source: Any,
    fetch_candles: Callable[[dict, str, int], Any] | None,
    direction: str | None = None,
    atr: float | None = None,
    bars: int = 120,
    component: str = "engine_b",
) -> dict[str, Any]:
    """Compute lower-TF shadow diagnostics for one pair.

    DIAGNOSTIC ONLY — see module docstring. The returned dict NEVER influences
    scoring, gating, ranking, SL/TP, sizing, or execution. Missing lower TFs are
    reported (never silently substituted with H1). Forming bars are reported and
    excluded from trend computation, never treated as closed.

    Returns a dict always carrying ``lower_tf_status`` in
    {PASS, WARN, FAIL, BLOCKED, NOT_USED} and ``diagnostic_only: True``.
    """
    cfg = lower_tf_shadow_config()
    tfs = [str(t).upper() for t in (cfg.get("tfs") or LOWER_TF_SHADOW_DEFAULTS["tfs"])]

    out: dict[str, Any] = {
        "lower_tf_shadow_enabled": True,
        "diagnostic_only": True,
        "warning": DIAGNOSTIC_ONLY_WARNING,
        "component": component,
        "lower_tf_shadow_source": _source_label(source),
        "configured_tfs": list(tfs),
        "lower_tf_available_tfs": [],
        "lower_tf_missing_tfs": [],
        "direction_ref": (direction or None),
        "per_tf": {},
        "lower_tf_notes": "",
    }

    if fetch_candles is None:
        out["lower_tf_status"] = "BLOCKED"
        out["lower_tf_missing_tfs"] = list(tfs)
        out["lower_tf_freshness_status"] = "unavailable"
        out["lower_tf_partial_candle_status"] = "unknown_no_fetcher"
        out["lower_tf_spread_atr_status"] = "no_spread_data"
        out["lower_tf_alignment_score_diagnostic_only"] = None
        out["lower_tf_conflict_reason"] = None
        out["lower_tf_notes"] = "no lower-TF fetcher available; reporting BLOCKED"
        return out

    now = datetime.now(timezone.utc)
    aligned_n = counter_n = 0
    had_forming_any = False
    fresh_flags: list[bool] = []
    conflicts: list[str] = []

    for tf in tfs:
        entry: dict[str, Any] = {"tf": tf}
        try:
            raw = fetch_candles(pair, tf, int(bars))
        except Exception as exc:
            raw = None
            entry["fetch_error"] = str(exc)

        if not raw or not isinstance(raw, (list, tuple)) or len(raw) < _MIN_BARS_FOR_TREND:
            entry["available"] = False
            entry["trend_state"] = "missing"
            entry["alignment"] = "n/a"
            entry["bar_count"] = len(raw) if isinstance(raw, (list, tuple)) else 0
            out["lower_tf_missing_tfs"].append(tf)
            out["per_tf"][tf] = entry
            continue

        confirmed, had_forming, last_close_dt = _split_confirmed(raw, tf)
        had_forming_any = had_forming_any or had_forming
        trend = _trend_state(confirmed)
        align = _alignment(trend, direction)

        age_s = None
        is_fresh = None
        if last_close_dt is not None:
            age_s = (now - last_close_dt).total_seconds()
            tf_sec = _TF_SECONDS.get(tf, 0) or 0
            # Stale if the last confirmed bar closed more than ~2 bars ago.
            is_fresh = age_s <= (2 * tf_sec + 60) if tf_sec else None
            fresh_flags.append(bool(is_fresh))

        entry.update(
            {
                "available": True,
                "bar_count": len(raw),
                "confirmed_bar_count": len(confirmed),
                "trend_state": trend,
                "alignment": align,
                "had_forming_bar": had_forming,
                "last_confirmed_close": (
                    last_close_dt.isoformat() if last_close_dt else None
                ),
                "age_seconds": round(age_s, 1) if age_s is not None else None,
                "fresh": is_fresh,
            }
        )
        if align == "aligned":
            aligned_n += 1
        elif align == "counter":
            counter_n += 1
            conflicts.append(f"{tf.lower()}_counter_trend")

        out["lower_tf_available_tfs"].append(tf)
        out["per_tf"][tf] = entry

    # Role-named aliases (m30_trend_state / m15_trigger_state / m5_micro_state
    # and the *_alignment ASE fields) for whichever canonical TFs were requested.
    for tf, (state_field, align_field) in _TF_ROLE.items():
        ent = out["per_tf"].get(tf)
        out[state_field] = ent.get("trend_state") if ent else "not_requested"
        out[align_field] = ent.get("alignment") if ent else "not_requested"

    available = len(out["lower_tf_available_tfs"])
    out["lower_tf_partial_candle_status"] = (
        "forming_excluded" if had_forming_any else "confirmed_only"
    )
    out["lower_tf_spread_atr_status"] = "no_spread_data"

    if not fresh_flags:
        out["lower_tf_freshness_status"] = "unavailable"
    elif all(fresh_flags):
        out["lower_tf_freshness_status"] = "fresh"
    elif any(fresh_flags):
        out["lower_tf_freshness_status"] = "partial_stale"
    else:
        out["lower_tf_freshness_status"] = "stale"

    if available:
        out["lower_tf_alignment_score_diagnostic_only"] = round(
            (aligned_n - counter_n) / available, 3
        )
    else:
        out["lower_tf_alignment_score_diagnostic_only"] = None
    out["lower_tf_conflict_reason"] = ", ".join(conflicts) if conflicts else None

    # Overall status — purely descriptive, never a gate.
    if component not in (cfg.get("apply_to") or []):
        status = "NOT_USED"
    elif available == 0:
        status = "BLOCKED"
    else:
        score = out["lower_tf_alignment_score_diagnostic_only"] or 0.0
        stale = out["lower_tf_freshness_status"] in ("stale", "partial_stale")
        missing = bool(out["lower_tf_missing_tfs"])
        if score <= -0.5:
            status = "FAIL"
        elif stale or missing or counter_n > 0:
            status = "WARN"
        else:
            status = "PASS"
    out["lower_tf_status"] = status

    out["lower_tf_notes"] = (
        f"{available}/{len(tfs)} lower TFs available; "
        f"aligned={aligned_n} counter={counter_n}; "
        f"freshness={out['lower_tf_freshness_status']}; "
        f"status={status} (diagnostic-only, no effect on trade decisions)"
    )
    return out
