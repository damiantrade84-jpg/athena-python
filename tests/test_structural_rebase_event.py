"""Tests for the F4 structural-rebase observability flag.

The executors (``mt5_execute`` / ``bybit_execute``) parallel-shift SL/TP when
broker price drifts more than 1% from signal price. For Engine A levels this
is correct (distance preserved). For Engine B / naked structural levels the
shift erases structural meaning, so the executor now emits a
``structuralRebase`` event on the result and — only when the config gate
``ATR_FRESHNESS.BLOCK_STRUCTURAL_REBASE`` is true — refuses the order.

Default behaviour MUST stay unchanged. These tests prove that:
1. ``_is_structural_engine_b_execution`` correctly classifies naked / engine_b
   signals so the rebase event is_structural is accurate.
2. The block flag defaults to False in config.yaml.
"""

from __future__ import annotations

from pathlib import Path


def test_is_structural_engine_b_execution_for_naked_signal():
    """Naked / engine_b signals must be flagged structural so F4 fires."""
    import importlib.util
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    execution_py = repo_root / "execution.py"
    spec = importlib.util.spec_from_file_location("execution_under_test", execution_py)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["execution_under_test"] = mod
    spec.loader.exec_module(mod)

    naked = {"is_naked": True}
    engine_b = {"engine_b": {"recommended_stop_loss": 1.0}}
    engine_a_aligned = {
        "engine": "engine_a",
        "engine_b": {"structural_verdict": "CLEAR"},
        "verdict": "ALIGNED",
    }
    engine_a_alone = {
        "engine": "engine_a",
        "engine_b": {"structural_verdict": "CLEAR"},
        "verdict": "NO_SIGNAL",
    }

    assert mod._is_structural_engine_b_execution(naked) is True
    assert mod._is_structural_engine_b_execution(engine_b) is True
    assert mod._is_structural_engine_b_execution(engine_a_aligned) is True
    # Engine A row without an executable verdict must NOT be structural.
    assert mod._is_structural_engine_b_execution(engine_a_alone) is False


def test_config_block_structural_rebase_defaults_false():
    """The behavior-change knob must default to false in config.yaml."""
    repo_root = Path(__file__).resolve().parent.parent
    cfg_path = repo_root / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    # The ATR_FRESHNESS block must exist and both gates default to false.
    assert "ATR_FRESHNESS:" in text
    # ENABLED line within ATR_FRESHNESS must default to false.
    atr_block = text.split("ATR_FRESHNESS:")[1].split("\n\n")[0]
    assert "ENABLED: false" in atr_block
    assert "BLOCK_EXECUTION_ON_STALE_ATR: false" in atr_block
    # BLOCK_STRUCTURAL_REBASE is intentionally absent (default false via .get).
    # If it ever ships true by default this test should fail loudly.
    assert "BLOCK_STRUCTURAL_REBASE: true" not in text
