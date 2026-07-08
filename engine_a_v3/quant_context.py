"""Engine A quant scorer — context assembler for volume and subsystem signals."""

from __future__ import annotations

from typing import Any

from engine_a_v3.subsystems import ST_NA, ST_UNAVAILABLE, z_to_subsystem_entry, subsystems_enabled


def _carry_entry(display: str, *, as_of_date: str | None = None) -> dict[str, Any]:
    try:
        from carry_feed import _PAIR_CARRY_FORMULA, get_carry_z

        if not _PAIR_CARRY_FORMULA.get(display):
            return {"state": ST_NA}
        return z_to_subsystem_entry(get_carry_z(display, as_of_date=as_of_date))
    except Exception:
        return {"state": ST_UNAVAILABLE}


def _sentiment_entry(display: str, *, as_of_date: str | None = None) -> dict[str, Any]:
    try:
        from cot_feed import _PAIR_FORMULA, get_cot_z

        if not _PAIR_FORMULA.get(display):
            return {"state": ST_NA}
        return z_to_subsystem_entry(get_cot_z(display, as_of_date=as_of_date))
    except Exception:
        return {"state": ST_UNAVAILABLE}


def build_quant_context(
    *,
    volume_ratio: Any = None,
    display: str | None = None,
    as_of_date: str | None = None,
    subsystem_entries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the quant scorer context. Only populated keys are returned."""
    context: dict[str, Any] = {}
    if volume_ratio is not None:
        try:
            context["volume_ratio"] = float(volume_ratio)
        except (TypeError, ValueError):
            pass

    if subsystem_entries:
        context.update(subsystem_entries)
        return context

    if not subsystems_enabled() or not display:
        return context

    context["carry"] = _carry_entry(display, as_of_date=as_of_date)
    context["sentiment"] = _sentiment_entry(display, as_of_date=as_of_date)
    return context
