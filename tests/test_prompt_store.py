"""Focused tests for the external prompt store (Phase 0).

Covers: disabled-by-default, fail-closed fallback, file load, hot-reload,
frontmatter stripping, path traversal rejection, hash stability.

Uses tempfile.mkdtemp() directly instead of pytest's tmp_path fixture to
avoid a Windows-specific .pytest_tmp cleanup issue.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

import prompt_store


def _make_temp_dir() -> str:
    d = tempfile.mkdtemp(prefix="athena_prompt_store_")
    os.makedirs(os.path.join(d, "prompts"), exist_ok=True)
    return os.path.join(d, "prompts")


@pytest.fixture
def temp_store(monkeypatch):
    store_dir = _make_temp_dir()
    monkeypatch.setattr(prompt_store, "_is_enabled", lambda: True)
    monkeypatch.setattr(prompt_store, "_resolve_store_dir", lambda: store_dir)
    prompt_store.clear_cache()
    yield store_dir
    prompt_store.clear_cache()
    shutil.rmtree(store_dir, ignore_errors=True)


def test_disabled_returns_fallback_with_disabled_source(monkeypatch):
    monkeypatch.setattr(prompt_store, "_is_enabled", lambda: False)
    prompt_store.clear_cache()
    try:
        text, source, h = prompt_store.load_prompt("any_surface", fallback="FB")
        assert text == "FB"
        assert source == "disabled"
        assert len(h) == 64
    finally:
        prompt_store.clear_cache()


def test_enabled_missing_file_returns_fallback(temp_store):
    text, source, h = prompt_store.load_prompt("nope_not_here", fallback="FB")
    assert text == "FB"
    assert source == "fallback"
    assert len(h) == 64


def test_enabled_loads_file(temp_store):
    p = os.path.join(temp_store, "demo.md")
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("hello world")
    text, source, h = prompt_store.load_prompt("demo", fallback="FB")
    assert text == "hello world"
    assert source == "file"
    assert h == prompt_store.get_prompt_hash("hello world")


def test_strips_yaml_frontmatter(temp_store):
    p = os.path.join(temp_store, "frontmatter.md")
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("---\nsurface: demo\nversion: v1\n---\nbody line 1\nbody line 2\n")
    text, source, _ = prompt_store.load_prompt("frontmatter", fallback="FB")
    assert source == "file"
    assert "surface: demo" not in text
    assert text.startswith("body line 1")
    assert text.rstrip().endswith("body line 2")


def test_file_without_frontmatter_returned_verbatim(temp_store):
    p = os.path.join(temp_store, "plain.md")
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("no frontmatter here\n")
    text, source, _ = prompt_store.load_prompt("plain", fallback="FB")
    assert source == "file"
    assert text == "no frontmatter here\n"


def test_hot_reload_pickup_mtime_change(temp_store):
    p = os.path.join(temp_store, "hot.md")
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("v1")
    text1, _, _ = prompt_store.load_prompt("hot", fallback="FB")
    assert text1 == "v1"
    text1b, _, _ = prompt_store.load_prompt("hot", fallback="FB")
    assert text1b == "v1"
    new_mtime = time.time() + 5
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("v2")
    os.utime(p, (new_mtime, new_mtime))
    text2, _, _ = prompt_store.load_prompt("hot", fallback="FB")
    assert text2 == "v2"


def test_path_traversal_rejected(temp_store):
    text, source, _ = prompt_store.load_prompt("../etc/passwd", fallback="FB")
    assert text == "FB"
    assert source == "fallback"


def test_txt_extension_also_loaded(temp_store):
    p = os.path.join(temp_store, "legacy.txt")
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("legacy text")
    text, source, _ = prompt_store.load_prompt("legacy", fallback="FB")
    assert source == "file"
    assert text == "legacy text"


def test_hash_stable_across_calls(temp_store):
    p = os.path.join(temp_store, "stable.md")
    with open(p, "w", encoding="utf-8") as wf:
        wf.write("stable body")
    _, _, h1 = prompt_store.load_prompt("stable", fallback="FB")
    _, _, h2 = prompt_store.load_prompt("stable", fallback="FB")
    assert h1 == h2 == prompt_store.get_prompt_hash("stable body")


def test_existing_prompt_files_load_cleanly(monkeypatch):
    repo_prompts = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts",
    )
    if not os.path.isdir(repo_prompts):
        pytest.skip("prompts/ directory not found")
    monkeypatch.setattr(prompt_store, "_is_enabled", lambda: True)
    monkeypatch.setattr(prompt_store, "_resolve_store_dir", lambda: repo_prompts)
    prompt_store.clear_cache()
    try:
        for fname in os.listdir(repo_prompts):
            if not fname.endswith(".md") or fname == "README.md":
                continue
            surface = fname[:-3]
            text, source, h = prompt_store.load_prompt(surface, fallback="FB")
            assert source == "file", f"{fname} did not load from file (source={source})"
            assert text and text.strip(), f"{fname} loaded empty body"
            assert len(h) == 64
    finally:
        prompt_store.clear_cache()
