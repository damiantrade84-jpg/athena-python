"""Guard legacy Engine A entry points for ASE-promoted families."""

from __future__ import annotations

from typing import Any

from athena_ase.exceptions import LegacyEngineBypassed
from athena_ase.instruments import instrument_by_symbol
from athena_ase.registry.promotion import get_family_state

LEGACY_ENGINE_A_ENTRY_POINTS = ("analyze_pair", "calc_confluence")

_TYPE_TO_FAMILY = {
    "forex": "forex",
    "crypto": "crypto",
    "commodity": "commodity",
    "stock": "equity",
    "index": "index_etf",
    "etf": "index_etf",
    "etf_bond": "index_etf",
}


def resolve_ase_family(pair: dict[str, Any]) -> str | None:
    for key in ("symbol", "display"):
        raw = pair.get(key)
        if not raw:
            continue
        inst = instrument_by_symbol(str(raw))
        if inst is not None:
            return inst.family
    ptype = str(pair.get("type") or "").lower()
    return _TYPE_TO_FAMILY.get(ptype)


def assert_legacy_engine_allowed(
    pair: dict[str, Any],
    *,
    entry_point: str = "unknown",
) -> None:
    family = resolve_ase_family(pair)
    if not family:
        return
    if get_family_state(family) == "DEMO":
        raise LegacyEngineBypassed(
            family,
            symbol=str(pair.get("display") or pair.get("symbol") or ""),
            entry_point=entry_point,
        )
