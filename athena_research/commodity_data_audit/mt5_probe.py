from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EMPIRICAL_BLOCKED_NOT_PROBED = "BLOCKED_NOT_PROBED"
EMPIRICAL_CLEAR_ON_PROBE = "CLEAR_ON_PROBE"
EMPIRICAL_BLOCKED_MT5_UNAVAILABLE = "BLOCKED_MT5_UNAVAILABLE"
EMPIRICAL_BLOCKED_SYMBOL_UNAVAILABLE = "BLOCKED_SYMBOL_UNAVAILABLE"

REASON_MT5_UNAVAILABLE = "MT5_UNAVAILABLE"
REASON_SYMBOL_UNAVAILABLE = "SYMBOL_UNAVAILABLE"


@dataclass(frozen=True)
class Mt5ProbeOutcome:
    ok: bool
    empirical_gate_status: str
    reason_code: str | None
    probe: dict[str, Any]
    unavailable_symbols: tuple[str, ...]


def evaluate_mt5_probe(
    probe: dict[str, Any],
    *,
    expected_displays: list[str],
) -> Mt5ProbeOutcome:
    """Classify a probe payload; fail closed on transport or symbol errors."""
    if probe.get("error"):
        return Mt5ProbeOutcome(
            ok=False,
            empirical_gate_status=EMPIRICAL_BLOCKED_MT5_UNAVAILABLE,
            reason_code=REASON_MT5_UNAVAILABLE,
            probe=probe,
            unavailable_symbols=tuple(),
        )

    unavailable: list[str] = []
    for display in expected_displays:
        row = probe.get(display)
        if not isinstance(row, dict):
            unavailable.append(display)
            continue
        if not row.get("mt5_symbol") or not row.get("available"):
            unavailable.append(display)

    if unavailable:
        return Mt5ProbeOutcome(
            ok=False,
            empirical_gate_status=EMPIRICAL_BLOCKED_SYMBOL_UNAVAILABLE,
            reason_code=REASON_SYMBOL_UNAVAILABLE,
            probe=probe,
            unavailable_symbols=tuple(unavailable),
        )

    return Mt5ProbeOutcome(
        ok=True,
        empirical_gate_status=EMPIRICAL_CLEAR_ON_PROBE,
        reason_code=None,
        probe=probe,
        unavailable_symbols=tuple(),
    )


def run_mt5_symbol_probe(pairs: list[dict[str, Any]]) -> Mt5ProbeOutcome:
    """Probe MT5 symbol_select for each pair without downloading candles."""
    expected = [str(pair.get("display") or "") for pair in pairs]
    try:
        import mt5_executor
    except Exception as exc:
        probe = {"error": f"mt5_executor unavailable: {exc}"}
        return evaluate_mt5_probe(probe, expected_displays=expected)

    if not mt5_executor.mt5_connect():
        probe = {"error": "MT5 not connected"}
        return evaluate_mt5_probe(probe, expected_displays=expected)

    mt5 = mt5_executor._get_mt5()
    if mt5 is None:
        probe = {"error": "MT5 not connected"}
        return evaluate_mt5_probe(probe, expected_displays=expected)

    probe: dict[str, Any] = {}
    for pair in pairs:
        display = str(pair.get("display") or "")
        mt5_symbol = mt5_executor.mt5_map_symbol(display)
        if not mt5_symbol:
            probe[display] = {"mt5_symbol": None, "available": False}
            continue
        available = bool(mt5.symbol_select(mt5_symbol, True))
        probe[display] = {"mt5_symbol": mt5_symbol, "available": available}

    return evaluate_mt5_probe(probe, expected_displays=expected)


def empirical_gate_status(*, probe_mt5_requested: bool, probe_outcome: Mt5ProbeOutcome | None) -> str:
    if not probe_mt5_requested:
        return EMPIRICAL_BLOCKED_NOT_PROBED
    if probe_outcome is None:
        return EMPIRICAL_BLOCKED_MT5_UNAVAILABLE
    return probe_outcome.empirical_gate_status
