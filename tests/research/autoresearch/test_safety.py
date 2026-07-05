"""Safety whitelist/denylist tests."""

from __future__ import annotations

from pathlib import Path

from research.autoresearch.safety import (
    check_patch_safety,
    extract_files_from_patch,
)


def _patch(changed_path: str, old: str = "a", new: str = "b") -> str:
    return (
        f"diff --git a/{changed_path} b/{changed_path}\n"
        f"--- a/{changed_path}\n"
        f"+++ b/{changed_path}\n"
        f"@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def test_denied_file_patch_rejected(default_config):
    files = extract_files_from_patch(_patch("live_trading/config.yaml"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is False
    assert any("denied_keyword" in r for r in reasons)


def test_non_whitelisted_file_rejected(default_config):
    files = extract_files_from_patch(_patch("engine_a_v3/scoring.py"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is False
    assert any("not_whitelisted" in r for r in reasons)


def test_whitelisted_research_file_allowed(default_config):
    files = extract_files_from_patch(_patch("research/autoresearch/stub/stub_config.yaml"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is True
    assert reasons == []


def test_execution_path_denied_even_under_research_name(default_config):
    files = extract_files_from_patch(_patch("research/autoresearch/fake_execution_hook.yaml"))
    safe, reasons = check_patch_safety(
        files,
        allowed_paths=default_config["allowed_paths"],
        denied_keywords=default_config["denied_keywords"],
    )
    assert safe is False
    assert any("execution" in r for r in reasons)
