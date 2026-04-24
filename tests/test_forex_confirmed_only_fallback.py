"""Verify forex analyze_pair routes through Engine A v2, not the legacy forex_scoring path."""

from pathlib import Path

ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"


def test_mt5_forex_fallback_market_state_uses_confirmed_only():
    """
    Forex must route through Engine A v2 (calc_confluence -> factor_scoring).
    athena.py must not call compute_forex_score anywhere — that is the legacy
    forex_scoring.py path which was retired when Engine A v2 went live.
    """
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert "compute_forex_score" not in src, (
        "athena.py still calls compute_forex_score — forex must use Engine A v2 via calc_confluence"
    )
    assert "calc_confluence" in src, (
        "athena.py must call calc_confluence (Engine A v2 entry point)"
    )
