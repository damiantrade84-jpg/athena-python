"""Engine A quant scorer — subsystem context assembler.

Maps the app's existing subsystem feeds to the `{signal, quality}` inputs the
quant scorer consumes. Each subsystem returns neutral (omitted) when data is
missing or stale, so coverage gaps never inject noise — carry/COT return 0.0 on
no coverage, and a |value|-based quality makes those non-contributing.

Imports are limited to standalone feeds (carry_feed, cot_feed) to avoid
importing athena.py. Athena-scoped inputs (USD relative strength, the
microstructure cache, forex volume ratio, intermarket snapshot) are passed in by
the caller (athena.analyze_pair, which already has them in scope).

Component directions (sign = LONG/SHORT):
  carry          + = base/asset has interest-rate carry tailwind (FRED-backed)
  sentiment      + = COT speculators net long the base/asset (CFTC-backed)
  microstructure + = bid-heavy book / positive order-flow delta (crypto WS)
  intermarket    + = asset stronger than USD proxy (DXY relative strength)
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _zscore_component(z: float | None) -> dict[str, Any] | None:
    """Shared mapping for carry/COT z-scores (both in [-3,3], 0.0 = no coverage)."""
    if z is None:
        return None
    try:
        z = float(z)
    except (TypeError, ValueError):
        return None
    if abs(z) < 0.05:  # neutral / no coverage -> omit (no contribution)
        return None
    return {
        "signal": _clamp(math.tanh(z / 1.5), -1.0, 1.0),
        "quality": _clamp01(abs(z) / 2.0),
        "z": round(z, 3),
    }


def _carry_component(display: str) -> dict[str, Any] | None:
    try:
        from carry_feed import get_carry_z

        return _zscore_component(get_carry_z(display))
    except Exception:
        return None


def _cot_component(display: str) -> dict[str, Any] | None:
    try:
        from cot_feed import get_cot_z

        return _zscore_component(get_cot_z(display))
    except Exception:
        return None


def _microstructure_component(micro: Any) -> dict[str, Any] | None:
    """Crypto order-book / order-flow read. Sign-based so it is robust to the
    feed's raw scale; quality reflects agreement among available metrics."""
    if not isinstance(micro, Mapping):
        return None
    if time.time() - float(micro.get("_updated_ts", 0) or 0) > 60.0:
        return None  # stale WS data
    votes: list[float] = []
    for key in ("order_book_imbalance", "orderflow_delta", "liquidity_pressure"):
        raw = micro.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != 0.0:
            votes.append(1.0 if value > 0 else -1.0)
    if not votes:
        return None
    signal = sum(votes) / len(votes)
    return {
        "signal": _clamp(signal, -1.0, 1.0),
        "quality": _clamp01(0.3 + 0.4 * abs(signal)),
    }


def _intermarket_component(usd_strength: Any) -> dict[str, Any] | None:
    """USD relative-strength score (±3); >0 = asset stronger than the USD proxy."""
    if not isinstance(usd_strength, Mapping):
        return None
    raw_score = usd_strength.get("score")
    if raw_score is None:
        return None
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return None
    if abs(score) < 0.05:
        return None
    return {
        "signal": _clamp(score / 3.0, -1.0, 1.0),
        "quality": _clamp01(abs(score) / 3.0),
        "score": round(score, 3),
    }


def build_quant_context(
    pair: Mapping[str, Any],
    *,
    micro: Any = None,
    usd_strength: Any = None,
    volume_ratio: Any = None,
) -> dict[str, Any]:
    """Assemble the quant scorer's subsystem context. Only populated keys are
    returned; absent keys are treated as neutral by the scorer."""
    display = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "")
    context: dict[str, Any] = {}

    if volume_ratio is not None:
        try:
            context["volume_ratio"] = float(volume_ratio)
        except (TypeError, ValueError):
            pass

    for key, component in (
        ("carry", _carry_component(display)),
        ("sentiment", _cot_component(display)),
        ("microstructure", _microstructure_component(micro)),
        ("intermarket", _intermarket_component(usd_strength)),
    ):
        if component is not None:
            context[key] = component

    return context
