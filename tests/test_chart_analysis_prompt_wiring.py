from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"


def test_chart_analysis_uses_prompt_builders():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert "build_system_prompt()" in src
    assert "build_single_prompt(" in src
    assert "build_dual_prompt(" in src
    assert "build_triple_prompt(" in src


def test_chart_analysis_has_single_active_prompt_assignments():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    assert src.count("system_prompt = build_system_prompt()") == 1
    assert src.count("user_prompt = build_single_prompt(") == 1
    assert src.count("dual_prompt = build_dual_prompt(") == 1
    assert src.count("triple_prompt = build_triple_prompt(") == 1

