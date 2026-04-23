import ast
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"


class _DummyLog:
    def __init__(self):
        self.messages = []

    def warning(self, message):
        self.messages.append(("warning", message))

    def info(self, message):
        self.messages.append(("info", message))


def _load_athena_functions(*names):
    src = ATHENA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    namespace = {"log": _DummyLog()}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            fn_src = ast.get_source_segment(src, node)
            exec(fn_src, namespace)
    return namespace


def test_persist_score_group_overrides_yaml_surfaces_missing_anchor_warning():
    ns = _load_athena_functions("_persist_score_group_overrides_yaml")
    persist = ns["_persist_score_group_overrides_yaml"]

    fd, path = tempfile.mkstemp(suffix=".yaml", dir=ROOT)
    os.close(fd)
    try:
        Path(path).write_text(
            "NAKED_ENGINE:\n"
            "  score_group_overrides:\n"
            "    crypto_btc:\n"
            "      scalp: {min_score: 4.0}\n",
            encoding="utf-8",
        )

        status = persist(path, {"crypto_btc": {"scalp": {"min_score": 4.5}}})

        assert status["saved"] is False
        assert status["updated"] == []
        assert status["warnings"] == [
            "config.yaml: score_group_overrides block not found, skipping update"
        ]
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def test_sync_score_group_override_min_scores_preserves_custom_values():
    ns = _load_athena_functions("_sync_score_group_override_min_scores")
    sync = ns["_sync_score_group_override_min_scores"]

    overrides = {
        "crypto_btc": {
            "scalp": {"min_score": 4.0},
            "swing": {"min_score": 5.0},
        },
        "nat_gas": {
            "swing": {"min_score": 6.0},
        },
    }

    updated, skipped = sync(
        overrides,
        {"scalp": 4.0, "intraday": 5.0, "swing": 5.0},
        {"scalp": 4.5, "intraday": 5.5, "swing": 5.5},
    )

    assert sorted(updated) == ["crypto_btc.scalp", "crypto_btc.swing"]
    assert skipped == ["nat_gas.swing"]
    assert overrides["crypto_btc"]["scalp"]["min_score"] == 4.5
    assert overrides["crypto_btc"]["swing"]["min_score"] == 5.5
    assert overrides["nat_gas"]["swing"]["min_score"] == 6.0
