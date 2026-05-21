"""Static regression: Engine A analyze_pair wires bar_time into live and replay paths."""

from pathlib import Path


def _athena_source() -> str:
    return (Path(__file__).resolve().parents[1] / "athena.py").read_text(encoding="utf-8")


def test_bar_time_derivation_before_calc_confluence():
    src = _athena_source()
    confluence_idx = src.index("res = calc_confluence(")
    prefix = src[:confluence_idx]
    assert "_bar_time" in prefix
    assert "_bt_src = _cf_h4c or _cf_d1c or _cf_h1c" in prefix


def test_calc_confluence_passes_bar_time():
    src = _athena_source()
    start = src.index("res = calc_confluence(")
    end = src.index(")", start) + 1
    call_region = src[start:end]
    assert "bar_time=_bar_time" in call_region


def test_check_divergence_passes_bar_time():
    src = _athena_source()
    start = src.index("check_divergence(")
    end = src.index("except Exception as _div_err", start)
    call_region = src[start:end]
    assert "bar_time=_bar_time" in call_region
