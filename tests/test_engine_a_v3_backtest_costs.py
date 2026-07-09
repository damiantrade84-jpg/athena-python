from __future__ import annotations

import pytest

from engine_a_v3.backtest import engine_a_v3_backtest_costs


def _cfg():
    return {
        "ENGINE_A_V3_BACKTEST": {
            "SPREAD_BPS": 2.0,
            "COMMISSION_BPS": 1.0,
            "SLIPPAGE_BPS": 1.0,
            "SWAP_BPS_PER_DAY": 0.5,
            "MAX_HOLD_BARS": 24,
            "BY_ASSET": {
                "crypto": {
                    "COMMISSION_BPS": 5.5,
                    "SLIPPAGE_BPS": 2.0,
                    "SWAP_BPS_PER_DAY": 1.0,
                },
            },
        },
    }


def test_crypto_costs_override_flat_defaults():
    costs = engine_a_v3_backtest_costs("crypto", _cfg())
    assert costs["COMMISSION_BPS"] == 5.5
    assert costs["SLIPPAGE_BPS"] == 2.0
    assert costs["SWAP_BPS_PER_DAY"] == 1.0
    # Non-overridden keys fall through from the base block.
    assert costs["SPREAD_BPS"] == 2.0
    assert costs["MAX_HOLD_BARS"] == 24
    assert "BY_ASSET" not in costs


def test_non_crypto_and_unknown_asset_keep_base_costs():
    for asset in ("forex", "commodity", None, "STOCK"):
        costs = engine_a_v3_backtest_costs(asset, _cfg())
        assert costs["COMMISSION_BPS"] == 1.0
        assert costs["SPREAD_BPS"] == 2.0


def test_live_config_crypto_commission_matches_fee_pct_round_trip():
    """Guard against the cost split-brain: everyday V3 crypto backtests must
    carry the same round-trip commission the config's own FEE_PCT declares.
    COMMISSION_BPS is per leg; the v3 cost model doubles it round trip."""
    from config import CONFIG

    costs = engine_a_v3_backtest_costs("crypto", CONFIG)
    fee_pct = float((CONFIG.get("FEE_PCT") or {}).get("crypto", 0.0))
    assert fee_pct > 0
    assert 2.0 * float(costs.get("COMMISSION_BPS", 0.0)) == pytest.approx(fee_pct * 10_000)


def test_promotion_manifest_crypto_commission_matches_fee_pct_round_trip():
    """The promotion manifest feeds commissionBps straight into the same
    per-leg cost model, so it must also hold the per-leg value."""
    import yaml
    from pathlib import Path

    from config import CONFIG

    manifest = yaml.safe_load(
        Path("configs/engine_a_v3_validation.yaml").read_text(encoding="utf-8")
    )
    fee_pct = float((CONFIG.get("FEE_PCT") or {}).get("crypto", 0.0))
    assert fee_pct > 0
    for name, group in manifest["groups"].items():
        if not name.startswith("crypto_") or group.get("validationEnabled") is not True:
            continue
        commission = float(group["costs"]["commissionBps"])
        assert 2.0 * commission == pytest.approx(fee_pct * 10_000), name


def test_missing_config_block_returns_empty_dict():
    assert engine_a_v3_backtest_costs("crypto", {}) == {}
