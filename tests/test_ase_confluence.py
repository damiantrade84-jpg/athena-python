"""Layer-1 confluence feature tests."""

from __future__ import annotations

from athena_ase.inference.predict import _layer1_confluence


def test_layer1_confluence_counts_secondary_agreement():
    signals = [
        {"name": "tsmom", "direction": 1, "rawStrength": 0.8},
        {"name": "carry", "direction": 1, "rawStrength": 0.6},
        {"name": "xsec", "direction": -1, "rawStrength": 0.5},
        {"name": "meanrev", "direction": 1, "rawStrength": 0.2},
    ]
    assert _layer1_confluence(signals, 1) == 1
