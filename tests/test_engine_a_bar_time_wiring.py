"""Static regression: Engine A analyze_pair V3 path wires candle fetch meta."""

from pathlib import Path


def _athena_source() -> str:
    return (Path(__file__).resolve().parents[1] / "athena.py").read_text(encoding="utf-8")


def test_analyze_pair_uses_v3_evaluator():
    src = _athena_source()
    assert "def analyze_pair(" in src
    assert "evaluate_engine_a_v3" in src
    assert "calc_confluence(" not in src.split("def analyze_pair(")[1].split("def _build_style_levels")[0]


def test_analyze_pair_sets_v3_candle_fetch_meta():
    src = _athena_source()
    assert '_v3_signal["candleFetchMeta"]' in src
    assert '"pairSource": pair.get("source")' in src
