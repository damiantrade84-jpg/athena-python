"""Engine B ↔ ASE shadow alignment (WO Phase 3) — read-only diagnostics.

Attaches the latest ASE view to Engine B forex cards so an aligned-vs-opposed
expectancy table can be built from the journal. Explicit non-goals: no change
to Engine B score, eligibility, direction, targets, or Cascade Scan behavior;
no cross-engine veto; never blocks a card on ASE availability.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from athena_ase.instruments import instrument_by_symbol
from athena_ase.paths import default_ase_state_root

log = logging.getLogger("ase.context.engine_b_shadow")

# Staleness: 2× bar period per horizon (WO Phase 3 default).
_STALENESS_MS = {"intraday": 2 * 3_600_000, "swing": 2 * 86_400_000}
_STYLE_HORIZON = {"scalp": "intraday", "intraday": "intraday", "swing": "swing"}
_SCHEMA_VERSION = "ase_engine_b_shadow.v2"

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


def _flat_shadow(
    *,
    reason: str = "ase_signal_unavailable",
    horizon: str | None = None,
    support_state: str = "UNAVAILABLE",
) -> dict[str, Any]:
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "direction": "NONE",
        "candidateDirection": None,
        "decisionStatus": None,
        "probabilityPositive": None,
        "expectedNetR": None,
        "alignment": "ASE_FLAT",
        "supportState": support_state,
        "supportEligible": False,
        "reason": reason,
        "horizon": horizon,
        "modelVersion": None,
        "dataQualityOk": None,
        "modelHealthOk": None,
        "fxContextBias": None,
        "triangular": None,
        "asOf": None,
        "ageMs": None,
        "advisoryOnly": True,
        "affectsEngineBScore": False,
    }


def ase_horizon_for_style(style: str | None) -> str | None:
    """Map an Engine B style to the matching ASE decision horizon."""
    return _STYLE_HORIZON.get(str(style or "").strip().lower())


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _safe_int(value: Any) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if out > 0 else None


def _timestamp_ms(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _health_flags(latest: Any) -> tuple[bool, bool]:
    data_quality = _json_mapping(latest.get("dataQuality"))
    model_health = _json_mapping(latest.get("modelHealth"))
    gate_result = _json_mapping(model_health.get("gateResult"))
    monitor = _json_mapping(model_health.get("monitor"))
    data_ok = bool(
        data_quality.get("coreOk") is True
        and not data_quality.get("blocker")
    )
    model_ok = bool(
        gate_result.get("ok") is True
        and not model_health.get("blocker")
        and not model_health.get("errorReason")
        and monitor.get("watchMax") is not True
    )
    return data_ok, model_ok


def ase_shadow_for_card(
    symbol: str,
    engine_b_direction: str,
    *,
    style: str | None = None,
    horizon: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    """aseShadow block for an Engine B forex card; None for non-ASE symbols.

    Never raises and never blocks the card: missing/stale ASE data yields
    direction NONE / alignment ASE_FLAT.
    """
    try:
        inst = instrument_by_symbol(symbol)
        if inst is None or inst.family != "forex":
            return None
        target_horizon = str(horizon or ase_horizon_for_style(style) or "").lower()
        if target_horizon not in _STALENESS_MS:
            return _flat_shadow(reason="ase_horizon_unresolved")

        # Engine B catalog symbols often carry provider suffixes (EURUSD=X),
        # while ASE journals use the canonical instrument id (EURUSD).
        canonical_symbol = inst.symbol
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        df = _load_trade_journal()
        if df is None or getattr(df, "empty", True) or "instrument" not in df.columns:
            return _flat_shadow(reason="ase_journal_empty", horizon=target_horizon)
        rows = df[df["instrument"].astype(str).str.upper() == canonical_symbol.upper()]
        if rows.empty:
            # Compatibility for older/provider-stamped journal rows. Resolve
            # aliases only on the uncommon fallback path, not for every card.
            rows = df[
                df["instrument"].astype(str).map(
                    lambda raw: (
                        (resolved := instrument_by_symbol(raw)) is not None
                        and resolved.symbol == canonical_symbol
                    )
                )
            ]
        if "horizon" not in rows.columns:
            return _flat_shadow(reason="ase_horizon_missing", horizon=target_horizon)
        rows = rows[rows["horizon"].astype(str).str.lower() == target_horizon]
        if rows.empty:
            return _flat_shadow(reason="ase_horizon_signal_missing", horizon=target_horizon)

        # Point-in-time join: an Engine B card may only observe ASE decisions
        # that existed at the card timestamp. Malformed/future rows are ignored.
        eligible_rows: list[tuple[int, Any]] = []
        for _, row in rows.iterrows():
            decision_ms = _safe_int(row.get("decisionTimeMs"))
            if decision_ms is not None and decision_ms <= now:
                eligible_rows.append((decision_ms, row))
        if not eligible_rows:
            return _flat_shadow(reason="ase_asof_signal_missing", horizon=target_horizon)
        decision_ms, latest = max(eligible_rows, key=lambda item: item[0])
        age_ms = now - decision_ms
        if age_ms > _STALENESS_MS[target_horizon]:
            return _flat_shadow(reason="ase_signal_stale", horizon=target_horizon)

        decision_status = str(latest.get("decisionStatus") or "").upper()
        ase_direction = str(latest.get("direction") or "NONE").upper()
        expected_net_r = _safe_float(latest.get("expectedNetR"))
        probability_positive = _safe_float(latest.get("probabilityPositive"))
        data_ok, model_ok = _health_flags(latest)
        base = {
            "schemaVersion": _SCHEMA_VERSION,
            "direction": ase_direction if ase_direction in ("LONG", "SHORT") else "NONE",
            "candidateDirection": ase_direction if ase_direction in ("LONG", "SHORT") else None,
            "decisionStatus": decision_status or None,
            "probabilityPositive": probability_positive,
            "expectedNetR": expected_net_r,
            "alignment": "ASE_FLAT",
            "supportState": "UNAVAILABLE",
            "supportEligible": False,
            "reason": "ase_signal_unavailable",
            "horizon": target_horizon,
            "modelVersion": str(latest.get("modelVersion") or "") or None,
            "dataQualityOk": data_ok,
            "modelHealthOk": model_ok,
            "fxContextBias": _safe_float(_json_mapping(latest.get("fxContext")).get("bias")),
            "triangular": _json_mapping(latest.get("triangular")).get("label"),
            "asOf": datetime.fromtimestamp(
                decision_ms / 1000.0, tz=timezone.utc
            ).isoformat(),
            "ageMs": age_ms,
            "advisoryOnly": True,
            "affectsEngineBScore": False,
        }

        if not data_ok or not model_ok:
            return {
                **base,
                "direction": "NONE",
                "supportState": "UNAVAILABLE",
                "reason": "ase_data_or_model_unhealthy",
            }
        if decision_status == "ERROR":
            return {
                **base,
                "direction": "NONE",
                "supportState": "UNAVAILABLE",
                "reason": "ase_decision_error",
            }
        if decision_status == "FLAT" or ase_direction not in ("LONG", "SHORT"):
            return {
                **base,
                "direction": "NONE",
                "supportState": "NEUTRAL",
                "reason": "ase_decision_flat",
            }
        if expected_net_r is None or probability_positive is None:
            return {
                **base,
                "direction": "NONE",
                "supportState": "UNAVAILABLE",
                "reason": "ase_metrics_invalid",
            }

        eb = str(engine_b_direction or "").upper()
        alignment = "ALIGNED" if eb == ase_direction else "OPPOSED"
        if decision_status == "WATCH":
            return {
                **base,
                "alignment": alignment,
                "supportState": "WATCH_ALIGNED" if alignment == "ALIGNED" else "WATCH_OPPOSED",
                "reason": "ase_watch_only",
            }
        if decision_status != "TRADE":
            return {
                **base,
                "direction": "NONE",
                "supportState": "UNAVAILABLE",
                "reason": "ase_decision_status_unknown",
            }
        if expected_net_r <= 0.0:
            return {
                **base,
                "alignment": alignment,
                "supportState": "NOT_POSITIVE",
                "reason": "ase_expected_net_r_not_positive",
            }
        if alignment == "OPPOSED":
            return {
                **base,
                "alignment": alignment,
                "supportState": "OPPOSED",
                "reason": "ase_trade_opposed",
            }
        return {
            **base,
            "alignment": "ALIGNED",
            "supportState": "SUPPORTED",
            "supportEligible": True,
            "reason": "ase_trade_aligned_positive",
        }
    except Exception as exc:  # noqa: BLE001 — never block the card
        log.debug("ase_shadow_for_card failed for %s: %s", symbol, exc)
        return _flat_shadow(reason="ase_shadow_internal_error")


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def journal_alignment(
    *,
    trade_id: str,
    symbol: str,
    engine_b_direction: str,
    shadow: dict[str, Any],
    engine_b_style: str = "",
) -> None:
    """Append one alignment row (jsonl) so the four-quadrant table is a query."""
    try:
        path = alignment_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        resolved_instrument = instrument_by_symbol(symbol)
        row = {
            "recordedAt": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade_id,
            "instrument": (
                resolved_instrument.symbol
                if resolved_instrument is not None
                else str(symbol or "").replace("/", "").replace(" ", "").upper()
            ),
            "engine_b_direction": str(engine_b_direction or "").upper(),
            "engine_b_style": str(engine_b_style or "").lower(),
            "ase_direction": shadow.get("direction"),
            "ase_horizon": shadow.get("horizon"),
            "ase_decision_status": shadow.get("decisionStatus"),
            "alignment": shadow.get("alignment"),
            "support_state": shadow.get("supportState"),
            "support_eligible": bool(shadow.get("supportEligible")),
            "reason": shadow.get("reason"),
            "expectedNetR": shadow.get("expectedNetR"),
            "probabilityPositive": shadow.get("probabilityPositive"),
            "ase_as_of": shadow.get("asOf"),
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
        style = str(signal.get("style") or signal.get("requestedStyle") or "")
        shadow = ase_shadow_for_card(
            symbol,
            direction,
            style=style,
            now_ms=_timestamp_ms(signal.get("timestamp")),
        )
        if shadow is None:
            return
        signal["aseShadow"] = shadow
        journal_alignment(
            trade_id=str(signal.get("id") or ""),
            symbol=symbol,
            engine_b_direction=direction,
            shadow=shadow,
            engine_b_style=style,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("attach_ase_shadow failed: %s", exc)
