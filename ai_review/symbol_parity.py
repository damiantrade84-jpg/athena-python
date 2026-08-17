"""Hard symbol parity for AI chart review (chart symbol vs engine candidate).

Provider equality alone is insufficient: SI=F (COMEX futures) and XAG/USD (spot
CFD) can both resolve provider=mt5 while pricing different instruments.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ALIAS_PATH = _REPO_ROOT / "configs" / "symbol_aliases.yaml"


def normalize_symbol_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().upper()
    if not raw:
        return ""
    without_provider = raw.split(":")[-1]
    without_yahoo_fx = without_provider.replace("=X", "")
    return re.sub(r"[^A-Z0-9=]", "", without_yahoo_fx)


def _identity_keys(value: Any) -> set[str]:
    """Compact key plus timeframe-policy canonical aliases for one token."""
    keys = {normalize_symbol_key(value)}
    if not isinstance(value, str) or not str(value).strip():
        keys.discard("")
        return keys
    try:
        from timeframe_policy import canonical_symbol

        canonical = canonical_symbol(value)
    except Exception:
        canonical = None
    if canonical:
        keys.add(normalize_symbol_key(canonical))
        keys.add(str(canonical).upper())
    keys.discard("")
    return keys


def _candidate_keys(row: dict[str, Any] | None) -> set[str]:
    if not isinstance(row, dict):
        return set()
    keys: set[str] = set()
    for field in (row.get("symbol"), row.get("display"), row.get("pair")):
        keys |= _identity_keys(field)
    return keys


@lru_cache(maxsize=4)
def _load_alias_entries(path_str: str) -> tuple[dict[str, Any], ...]:
    path = Path(path_str)
    if not path.is_file():
        return ()
    try:
        import yaml  # type: ignore

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ()
    entries = raw.get("aliases") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return ()
    out: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        chart = normalize_symbol_key(item.get("chart"))
        engine = normalize_symbol_key(item.get("engine"))
        if not chart or not engine:
            continue
        try:
            tol = float(item.get("basis_tolerance"))
        except (TypeError, ValueError):
            tol = None
        if tol is None or tol < 0:
            continue
        out.append(
            {
                "chart": chart,
                "engine": engine,
                "basis_tolerance": tol,
                "note": str(item.get("note") or ""),
            }
        )
    return tuple(out)


def load_symbol_aliases(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else _DEFAULT_ALIAS_PATH
    return list(_load_alias_entries(str(p.resolve())))


def find_declared_alias(
    chart_symbol: str,
    engine_keys: set[str],
    *,
    alias_path: str | Path | None = None,
) -> dict[str, Any] | None:
    chart_key = normalize_symbol_key(chart_symbol)
    if not chart_key or not engine_keys:
        return None
    for entry in load_symbol_aliases(alias_path):
        if entry["chart"] == chart_key and entry["engine"] in engine_keys:
            return dict(entry)
        # Allow reverse declaration
        if entry["engine"] == chart_key and entry["chart"] in engine_keys:
            return {
                "chart": chart_key,
                "engine": entry["chart"],
                "basis_tolerance": entry["basis_tolerance"],
                "note": entry.get("note") or "",
            }
    return None


def evaluate_symbol_parity(
    chart_symbol: str,
    origin_signal: dict[str, Any] | None,
    *,
    engine_pair: dict[str, Any] | None = None,
    live_price: float | None = None,
    engine_entry: float | None = None,
    alias_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return parity result. ``ok=False`` means hard reject the review join."""
    chart_key = normalize_symbol_key(chart_symbol)
    chart_keys = _identity_keys(chart_symbol)
    engine_keys = _candidate_keys(origin_signal) | _candidate_keys(engine_pair)
    # Prefer explicit display/symbol on pair when present
    if isinstance(engine_pair, dict):
        engine_keys |= _identity_keys(engine_pair.get("display"))
        engine_keys |= _identity_keys(engine_pair.get("symbol"))
        engine_keys.discard("")

    if not chart_key:
        return {
            "ok": False,
            "reason": "chart_symbol_missing",
            "chart_symbol": chart_symbol,
            "engine_keys": sorted(engine_keys),
            "symbol_alias_applied": None,
        }

    from athena_app.services.commodity_identity import (
        commodity_instrument_identity,
        is_continuous_futures_ticker,
    )

    # Futures chart identity vs spot-CFD execution (SI=F chart, XAGUSD levels).
    # Same catalog row can still be a hard conflict when price_series_kind is
    # spot_cfd and the chart symbol is a continuous futures ticker.
    pair_for_identity = engine_pair if isinstance(engine_pair, dict) else origin_signal
    identity = commodity_instrument_identity(
        pair_for_identity if isinstance(pair_for_identity, dict) else {}
    )
    futures_chart = is_continuous_futures_ticker(chart_symbol) or str(
        chart_symbol or ""
    ).strip().upper().endswith("=F")
    spot_execution = (
        str(identity.get("price_series_kind") or "").lower() == "spot_cfd"
        or (
            identity.get("execution_symbol")
            and not is_continuous_futures_ticker(str(identity.get("execution_symbol")))
        )
    )
    if futures_chart and spot_execution:
        alias_fs = find_declared_alias(chart_key, engine_keys, alias_path=alias_path)
        if alias_fs is None:
            return {
                "ok": False,
                "reason": "futures_spot_identity_conflict",
                "chart_symbol": chart_symbol,
                "chart_key": chart_key,
                "engine_keys": sorted(engine_keys),
                "detail": (
                    f"Chart symbol {chart_symbol!r} is continuous futures while engine "
                    f"execution identity is spot CFD "
                    f"({identity.get('execution_symbol') or identity.get('display')}). "
                    f"Declare an explicit alias with basis_tolerance or refuse the join."
                ),
                "instrument": identity,
                "symbol_alias_applied": None,
            }

    if chart_keys & engine_keys and not (futures_chart and spot_execution):
        return {
            "ok": True,
            "reason": "exact_match",
            "chart_symbol": chart_symbol,
            "chart_key": chart_key,
            "engine_keys": sorted(engine_keys),
            "symbol_alias_applied": None,
        }

    # Continuous futures catalog on a spot display is never an implicit match.
    alias = find_declared_alias(chart_key, engine_keys, alias_path=alias_path)
    if alias is None:
        return {
            "ok": False,
            "reason": "symbol_parity_mismatch",
            "chart_symbol": chart_symbol,
            "chart_key": chart_key,
            "engine_keys": sorted(engine_keys),
            "detail": (
                f"Chart symbol {chart_symbol!r} does not match engine candidate "
                f"{sorted(engine_keys)}; declare an explicit alias with basis_tolerance "
                f"or refuse the join"
            ),
            "symbol_alias_applied": None,
        }

    basis = None
    if live_price is not None and engine_entry is not None:
        try:
            basis = abs(float(live_price) - float(engine_entry))
        except (TypeError, ValueError):
            basis = None
    tol = float(alias["basis_tolerance"])
    if basis is not None and basis > tol + 1e-12:
        return {
            "ok": False,
            "reason": "symbol_alias_basis_exceeded",
            "chart_symbol": chart_symbol,
            "chart_key": chart_key,
            "engine_keys": sorted(engine_keys),
            "detail": f"alias basis {basis:.6f} exceeds tolerance {tol:.6f}",
            "symbol_alias_applied": {
                "chart": alias["chart"],
                "engine": alias["engine"],
                "basis": basis,
                "basis_tolerance": tol,
                "note": alias.get("note") or "",
            },
        }

    return {
        "ok": True,
        "reason": "declared_alias",
        "chart_symbol": chart_symbol,
        "chart_key": chart_key,
        "engine_keys": sorted(engine_keys),
        "symbol_alias_applied": {
            "chart": alias["chart"],
            "engine": alias["engine"],
            "basis": basis,
            "basis_tolerance": tol,
            "note": alias.get("note") or "",
        },
    }
