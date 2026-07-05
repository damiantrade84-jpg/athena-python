"""Patch safety tests."""

from __future__ import annotations

from research.edgelab.safety import check_patch_safety, extract_files_from_patch


def _patch(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def test_denied_patch_rejected(default_config):
    files = extract_files_from_patch(_patch("execution.py"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is False
    assert any("denied_keyword" in r for r in reasons)


def test_non_whitelisted_patch_rejected(default_config):
    files = extract_files_from_patch(_patch("engine_a_v3/scoring.py"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is False
    assert any("not_whitelisted" in r for r in reasons)


def test_whitelisted_research_patch_allowed(default_config):
    files = extract_files_from_patch(_patch("research/edgelab/stub/stub_config.yaml"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is True
    assert reasons == []
