"""Engine B ↔ ASE shadow alignment (WO Phase 3) — read-only diagnostics.

Attaches the latest ASE view to Engine B forex cards so an aligned-vs-opposed
expectancy table can be built from the journal. Explicit non-goals: no change
to Engine B score, eligibility, direction, targets, or Cascade Scan behavior;
no cross-engine veto; never blocks a card on ASE availability.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena_ase.data.ingest.common import compact_symbol
from athena_ase.instruments import instrument_by_symbol
from athena_ase.paths import default_ase_state_root

log = logging.getLogger("ase.context.engine_b_shadow")

# Staleness: 2× bar period per horizon (WO Phase 3 default).
_STALENESS_MS = {"intraday": 2 * 3_600_000, "swing": 2 * 86_400_000}

_JOURNAL_CACHE: dict[str, Any] = {"mtime": None, "df": None}


def alignment_journal_path() -> Path:
    return default_ase_state_root() / "ase_engineb_alignment.jsonl"


def _load_trade_journal():
    """ASE trade journal with an mtime cache (cards are built in bursts)."""
    from athena_ase.execution.journal import load_trade_journal
    from athena_ase.paths import trade_journal_path

    path = trade_journal_path()
    mtime = path.stat().st_mtime if path.exists() else None
    if _JOURNAL_CACHE["mtime"] == mtime and _JOURNAL_CACHE["df"] is not None:
        return _JOURNAL_CACHE["df"]
    df = load_trade_journal()
    _JOURNAL_CACHE["mtime"] = mtime
    _JOURNAL_CACHE["df"] = df
    return df


def _flat_shadow() -> dict[str, Any]:
    return {
        "direction": "NONE",
        "probabilityPositive": None,
        "expectedNetR": None,
        "alignment": "ASE_FLAT",
        "fxContextBias": None,
        "triangular": None,
        "asOf": None,
    }


def ase_shadow_for_card(
    symbol: str,
    engine_b_direction: str,
    *,
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    """aseShadow block for an Engine B forex card; None for non-ASE symbols.

    Never raises and never blocks the card: missing/stale ASE data yields
    direction NONE / alignment ASE_FLAT.
    """
    try:
        sym = compact_symbol(symbol)
        inst = instrument_by_symbol(sym)
        if inst is None or inst.family != "forex":
            return None
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        df = _load_trade_journal()
        if df is None or getattr(df, "empty", True) or "instrument" not in df.columns:
            return _flat_shadow()
        rows = df[df["instrument"].astype(str) == sym]
        if rows.empty:
            return _flat_shadow()
        rows = rows.sort_values("decisionTimeMs")
        latest = rows.iloc[-1]
        horizon = str(latest.get("horizon") or "intraday")
        decision_ms = int(latest.get("decisionTimeMs") or 0)
        if now - decision_ms > _STALENESS_MS.get(horizon, _STALENESS_MS["intraday"]):
            return _flat_shadow()
        ase_direction = str(latest.get("direction") or "NONE").upper()
        if ase_direction not in ("LONG", "SHORT"):
            shadow = _flat_shadow()
        else:
            eb = str(engine_b_direction or "").upper()
            alignment = "ALIGNED" if eb == ase_direction else "OPPOSED"
            fx_bias = None
            tri_label = None
            try:
                fx_raw = latest.get("fxContext")
                if isinstance(fx_raw, str) and fx_raw:
                    fx_bias = json.loads(fx_raw).get("bias")
                tri_raw = latest.get("triangular")
                if isinstance(tri_raw, str) and tri_raw:
                    tri_label = json.loads(tri_raw).get("label")
            except (ValueError, AttributeError):
                pass
            shadow = {
                "direction": ase_direction,
                "probabilityPositive": _safe_float(latest.get("probabilityPositive")),
                "expectedNetR": _safe_float(latest.get("expectedNetR")),
                "alignment": alignment,
                "fxContextBias": fx_bias,
                "triangular": tri_label,
                "asOf": datetime.fromtimestamp(
                    decision_ms / 1000.0, tz=timezone.utc
                ).isoformat(),
            }
        return shadow
    except Exception as exc:  # noqa: BLE001 — never block the card
        log.debug("ase_shadow_for_card failed for %s: %s", symbol, exc)
        return _flat_shadow()


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN → None


def journal_alignment(
    *,
    trade_id: str,
    symbol: str,
    engine_b_direction: str,
    shadow: dict[str, Any],
) -> None:
    """Append one alignment row (jsonl) so the four-quadrant table is a query."""
    try:
        path = alignment_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "recordedAt": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade_id,
            "instrument": compact_symbol(symbol),
            "engine_b_direction": str(engine_b_direction or "").upper(),
            "ase_direction": shadow.get("direction"),
            "alignment": shadow.get("alignment"),
            "expectedNetR": shadow.get("expectedNetR"),
            "probabilityPositive": shadow.get("probabilityPositive"),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.debug("alignment journal write failed: %s", exc)


def attach_ase_shadow(signal: dict[str, Any]) -> None:
    """Attach aseShadow to an Engine B forex card dict, in place.

    Read-only with respect to every existing key: only adds the "aseShadow"
    key and journals the alignment. Safe no-op for non-forex/non-ASE symbols.
    """
    try:
        symbol = str(signal.get("symbol") or signal.get("display") or "")
        direction = str(signal.get("direction") or "")
        shadow = ase_shadow_for_card(symbol, direction)
        if shadow is None:
            return
        signal["aseShadow"] = shadow
        journal_alignment(
            trade_id=str(signal.get("id") or ""),
            symbol=symbol,
            engine_b_direction=direction,
            shadow=shadow,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("attach_ase_shadow failed: %s", exc)
