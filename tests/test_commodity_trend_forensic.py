"""Forensic trend_coherence paths for commodity scan blockers (WTI vs XAU)."""

from __future__ import annotations

from config import CONFIG
from engine_a_scoring_profile import resolve_engine_a_scoring_profile
from tests.test_factor_scoring import _score, _snap


def _write_forensic_snapshot(label: str, score_group: str, result: dict) -> None:
    import json
    from pathlib import Path

    tc = result.get("trend_coherence") or {}
    votes = tc.get("vote_components") or tc.get("votes") or {}
    cal_path = Path(CONFIG.get("CALIBRATION_DIAGNOSTICS_PATH") or "logs/calibration_diagnostics/calibration_events.jsonl")
    out_dir = cal_path.parent / "forensics"
    out = out_dir / f"commodity_trend_forensic_{label}.json"
    profile = resolve_engine_a_scoring_profile(
        score_group=score_group,
        asset_type="commodity",
        style="swing",
    )
    payload = {
        "label": label,
        "score_group": score_group,
        "abort_reason": result.get("abort_reason"),
        "direction": result.get("direction"),
        "final_score": result.get("final_score"),
        "trend_score": result.get("trend_score"),
        "trend_coherence": tc,
        "vote_components": votes,
        "trend_timeframes": profile.get("trend_timeframes"),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def test_wti_like_flat_candles_indeterminate_trend_energy_profile():
    """Energy oil: flat multi-TF → indeterminate (matches May 25+ telemetry)."""
    flat = [
        {"open": 70.0, "high": 70.5, "low": 69.5, "close": 70.0, "vol": 1000.0}
        for _ in range(120)
    ]
    d1 = _snap("long")
    h4 = _snap("long")
    h1 = _snap("long")
    pair = {"type": "commodity", "display": "WTI Oil", "symbol": "USOIL"}
    result = _score(
        d1,
        h4,
        h1,
        pair=pair,
        d1_candles=flat,
        h4_candles=flat,
        h1_candles=flat,
    )
    assert result["abort_reason"] == "indeterminate_trend"
    assert result["trend_coherence"].get("reason") in (
        "indeterminate_trend",
        None,
    )
    profile = resolve_engine_a_scoring_profile(
        score_group="energy_oil", asset_type="commodity", style="swing"
    )
    assert profile["trend_timeframes"] == ["H4", "H1"]
    _write_forensic_snapshot("energy_oil_wti", "energy_oil", result)


def test_xau_like_mixed_votes_can_score_below_threshold():
    """Precious: directional votes but score may sit under 1.5 threshold."""
    d1 = _snap("long")
    h4 = _snap("short")
    h1 = _snap("long")
    pair = {"type": "commodity", "display": "XAU/USD", "symbol": "XAUUSD"}
    result = _score(d1, h4, h1, pair=pair)
    _write_forensic_snapshot("precious_xau", "precious_trackers", result)
    if result.get("abort_reason"):
        assert result["abort_reason"] in (
            "indeterminate_trend",
            "adx_hard_abort",
            "min_directional_failed",
        )
    else:
        assert result.get("final_score", 0) >= 0
