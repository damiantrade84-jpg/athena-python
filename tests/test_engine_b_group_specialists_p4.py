"""Phase 4: per-family specialist config + hardcoded extraction keys."""

from __future__ import annotations

import config
from engine_b_yaml_loader import translate_engine_b_config
from market_structure import NakedEngine


def test_hardcoded_atr_keys_present_in_naked_engine():
    ne = config.CONFIG.get("NAKED_ENGINE") or {}
    assert float(ne.get("sweep_wick_atr_mult", 0)) == 0.3
    assert float(ne.get("equal_extrema_atr_mult", 0)) == 0.15
    assert float(ne.get("structural_tp_min_distance_atr", 0)) == 0.5
    assert "commodity_swing_force_macro_align" in ne


def test_zone_peak_distance_and_direction_threshold_config():
    peak = config.CONFIG.get("ENGINE_B_ZONE_PEAK_DISTANCE") or {}
    assert int(peak.get("H4", 0)) == 5
    assert int(peak.get("H1", 0)) == 3
    thr = float(config.CONFIG.get("ENGINE_B_INDEPENDENT_DIRECTION_SCORE_THRESHOLD", 0))
    assert thr == 0.15


def test_forex_exotics_min_rr_floor_raised():
    import yaml
    from pathlib import Path

    # config.yaml wins over engine_b_config.yaml for score_group_overrides.
    root = Path(__file__).resolve().parents[1]
    cfg_doc = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    exotics = (
        ((cfg_doc.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {})
        .get("forex_exotics")
        or {}
    )
    assert float(exotics.get("intraday", {}).get("min_rr", 0)) >= 1.5
    assert float(exotics.get("swing", {}).get("min_rr", 0)) >= 2.0
    eb_doc = yaml.safe_load((root / "engine_b_config.yaml").read_text(encoding="utf-8"))
    out = translate_engine_b_config(eb_doc)
    eb_exotics = out["NAKED_ENGINE"]["score_group_overrides"]["forex_exotics"]
    assert float(eb_exotics["intraday"]["min_rr"]) >= 1.5
    assert float(eb_exotics["swing"]["min_rr"]) >= 2.0


def test_energy_and_nat_gas_min_room_from_yaml_loader():
    import yaml
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "engine_b_config.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = translate_engine_b_config(doc)
    overrides = out["NAKED_ENGINE"]["score_group_overrides"]
    assert float(overrides["energy_oil"]["intraday"]["min_room_atr"]) == 0.5
    assert float(overrides["nat_gas"]["swing"]["min_room_atr"]) == 1.0
    assert float(overrides["forex_exotics"]["swing"]["min_rr"]) == 2.0


def test_independent_direction_threshold_respected(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_INDEPENDENT_DIRECTION_SCORE_THRESHOLD", 0.99)
    engine = NakedEngine()
    # Mixed H1/H4 with lone BOS cannot clear a 0.99 threshold → no direction
    out = engine._determine_independent_direction(
        h1_sequence="HH_HL",
        h4_sequence="LH_LL",
        bos_data={"bos_bull": True, "bos_bear": False},
        d1_bos={},
        choch_data={},
        sweep_data={},
    )
    assert out["direction"] is None
