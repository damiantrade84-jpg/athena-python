"""Live-scan filter for the configured MT5 broker's tradable set.

Mirrors ``athena.py`` ``_apply_mt5_pair_availability`` so ASE does not score
pairs the local broker has disabled (for example USD/INR on ATFX). Crypto is
Bybit-routed and is never blocked by the MT5 disabled list. Historical
training / backfill paths keep the full ``UNIVERSE``.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from athena_ase.universe import Instrument, compact_symbol


def _as_name_set(raw: Any) -> set[str]:
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _cfg() -> Mapping[str, Any]:
    try:
        from config import CONFIG

        return CONFIG
    except BaseException:
        return {}


def _instrument_names(inst: Instrument) -> set[str]:
    names = {
        str(inst.display or "").strip(),
        str(inst.symbol or "").strip(),
        compact_symbol(inst.symbol),
        compact_symbol(inst.display),
    }
    return {name for name in names if name}


def instrument_blocked_by_broker(
    inst: Instrument,
    config: Mapping[str, Any] | None = None,
) -> bool:
    """True when a live ASE scan must not score this MT5 instrument."""
    if inst.family == "crypto":
        return False
    cfg = _cfg() if config is None else config
    disabled = _as_name_set(cfg.get("MT5_DISABLED_PAIRS"))
    enabled = _as_name_set(cfg.get("MT5_ENABLED_PAIRS"))
    names = _instrument_names(inst)
    if names & enabled:
        return False
    if cfg.get("MT5_DISABLE_COMMODITIES") is True and inst.family == "commodity":
        return True
    return bool(names & disabled)


def filter_broker_tradable(
    instruments: Iterable[Instrument],
    config: Mapping[str, Any] | None = None,
) -> tuple[Instrument, ...]:
    cfg = _cfg() if config is None else config
    return tuple(
        inst for inst in instruments if not instrument_blocked_by_broker(inst, cfg)
    )


def broker_excluded(
    instruments: Iterable[Instrument],
    config: Mapping[str, Any] | None = None,
) -> tuple[Instrument, ...]:
    cfg = _cfg() if config is None else config
    return tuple(
        inst for inst in instruments if instrument_blocked_by_broker(inst, cfg)
    )
