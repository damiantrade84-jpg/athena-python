"""Point-in-time subsystem context for the ablation harness (research-only).

Critical difference from the live `engine_a_v3.quant_context.build_quant_context`:
that builder calls `get_carry_z(display)` / `get_cot_z(display)` WITHOUT an
`as_of_date`, so in a historical backtest it would leak the latest rate onto a
past bar. Here every subsystem is queried point-in-time at the decision bar's
date, and missing/stale lookups are reported as `unavailable` (not silently 0).

Orientation/units are already correct at the feed source (both feeds return a
z-score in [-3,3] whose sign is LONG/SHORT of the displayed pair); we only add
the 4-state classification the scorer needs.
"""

from __future__ import annotations

import json
import math
from typing import Any

ST_AVAILABLE = "available"
ST_NEUTRAL = "neutral"
ST_UNAVAILABLE = "unavailable"
ST_NA = "na"

_NEUTRAL_EPS = 0.05  # |z| below this = applicable-but-neutral (matches quant_context)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _z_to_entry(z: float | None) -> dict[str, Any]:
    """Map a subsystem z-score to the scorer's {signal,quality,state} entry.

    Shared by carry and COT (both z in [-3,3], same orientation contract)."""
    if z is None:
        return {"state": ST_UNAVAILABLE}
    z = float(z)
    if abs(z) < _NEUTRAL_EPS:
        return {"signal": 0.0, "quality": 0.0, "state": ST_NEUTRAL, "z": round(z, 3)}
    return {
        "signal": _clamp(math.tanh(z / 1.5), -1.0, 1.0),
        "quality": _clamp(abs(z) / 2.0, 0.0, 1.0),
        "state": ST_AVAILABLE,
        "z": round(z, 3),
    }


# Cache of full frozen factor series, verified once per (display, factor). The
# production read_frozen_factor_value re-reads + SHA256-verifies the whole file
# on every call; in a per-bar backtest that dominates runtime. We verify once
# and serve point-in-time date lookups from memory (no leakage: we still index
# strictly by the decision-bar date).
_SERIES_CACHE: dict[tuple[str, str, str], dict | None] = {}


def _load_factor_series(display: str, factor: str) -> dict | None:
    from frozen_data import (
        _manifest_record,
        _stable_hash,
        active_data_as_of,
        frozen_root,
    )

    data_as_of = active_data_as_of()
    if not data_as_of:
        return None
    key = (data_as_of, display, factor)
    if key in _SERIES_CACHE:
        return _SERIES_CACHE[key]
    rows: dict | None = None
    try:
        rec = _manifest_record(data_as_of, f"factor_{factor}", display, None, "snapshot")
        path = frozen_root() / str(rec.get("path") or "")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and _stable_hash(loaded) == rec.get("sha256"):
            rows = loaded
    except Exception:
        rows = None
    _SERIES_CACHE[key] = rows
    return rows


def _frozen_z(display: str, factor: str, as_of_date: str) -> tuple[float | None, str]:
    """Read a point-in-time frozen factor value. Returns (z, state).

    state == 'na' when the feed has no formula for this display (structurally
    inapplicable). state == 'unavailable' when applicable but the dated value is
    missing/malformed."""
    # Structural applicability from the feed formula tables.
    if factor == "carry_z":
        from carry_feed import _PAIR_CARRY_FORMULA

        if not _PAIR_CARRY_FORMULA.get(display):
            return None, ST_NA
    elif factor == "cot_z":
        from cot_feed import _PAIR_FORMULA

        if not _PAIR_FORMULA.get(display):
            return None, ST_NA

    rows = _load_factor_series(display, factor)
    if not rows:
        return None, ST_UNAVAILABLE
    value = rows.get(str(as_of_date)[:10])
    if value is None:
        return None, ST_UNAVAILABLE
    try:
        return float(value), ST_AVAILABLE
    except (TypeError, ValueError):
        return None, ST_UNAVAILABLE


def build_pit_context(display: str, as_of_date: str) -> dict[str, Any]:
    """Assemble the point-in-time subsystem context for one decision bar.

    Only carry and sentiment(COT) are sourced this pass (leak-free PIT data with
    verified orientation). intermarket/macro/microstructure have no PIT builder
    yet and are deliberately omitted -> the scorer treats them as weight-0 'na'
    (insufficient coverage; not penalized).
    """
    ctx: dict[str, Any] = {}

    carry_z, carry_state = _frozen_z(display, "carry_z", as_of_date)
    if carry_state == ST_NA:
        ctx["carry"] = {"state": ST_NA}
    elif carry_state == ST_UNAVAILABLE:
        ctx["carry"] = {"state": ST_UNAVAILABLE}
    else:
        ctx["carry"] = _z_to_entry(carry_z)

    cot_z, cot_state = _frozen_z(display, "cot_z", as_of_date)
    if cot_state == ST_NA:
        ctx["sentiment"] = {"state": ST_NA}
    elif cot_state == ST_UNAVAILABLE:
        ctx["sentiment"] = {"state": ST_UNAVAILABLE}
    else:
        ctx["sentiment"] = _z_to_entry(cot_z)

    return ctx
