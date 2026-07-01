"""Focused tests for ai_memory local semantic retrieval (Phase 1).

Covers: disabled-by-default, embedding determinism + unit norm, store +
retrieve, per-pair scoping, fail-closed on bad input, context block
formatting. Uses a temp SQLite path; no external API calls.
"""

from __future__ import annotations

import os
import tempfile

import pytest

import ai_memory


@pytest.fixture
def temp_memory(monkeypatch):
    """Enable memory and point at a temp db."""
    d = tempfile.mkdtemp(prefix="athena_ai_memory_")
    db = os.path.join(d, "test_ai_memory.db")
    ai_memory.CONFIG["AI_MEMORY_ENABLED"] = True
    ai_memory.CONFIG["AI_MEMORY_DB_PATH"] = db
    ai_memory.reset_store_for_tests(db)
    yield db
    ai_memory.CONFIG.pop("AI_MEMORY_ENABLED", None)
    ai_memory.CONFIG.pop("AI_MEMORY_DB_PATH", None)
    try:
        if os.path.exists(db):
            os.remove(db)
    except Exception:
        pass
    try:
        os.rmdir(d)
    except Exception:
        pass


def test_disabled_returns_empty():
    ai_memory.CONFIG.pop("AI_MEMORY_ENABLED", None)
    assert ai_memory.retrieve_similar(query_text="anything") == []
    assert ai_memory.store_entry(kind="trade", text="something") is None
    assert ai_memory.build_context_block(query_text="x") == ""


def test_embed_text_deterministic_and_unit_norm():
    v1 = ai_memory.embed_text("BTC breakout retest H4")
    v2 = ai_memory.embed_text("BTC breakout retest H4")
    assert v1 == v2  # deterministic
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6  # unit length


def test_embed_text_empty_returns_zero_vector():
    v = ai_memory.embed_text("")
    assert all(x == 0.0 for x in v)


def test_store_and_retrieve_similar(temp_memory):
    rid = ai_memory.store_entry(
        kind="trade",
        text="BTC breakout retest on H4, long, RR 2.5, trending regime",
        pair="BTCUSDT",
        metadata={"outcome": "win", "r": 2.1},
    )
    assert rid is not None and rid > 0
    results = ai_memory.retrieve_similar(
        query_text="BTC breakout retest H4 long",
        kind="trade",
        top_k=5,
    )
    assert len(results) >= 1
    top = results[0]
    assert top["id"] == rid
    assert top["pair"] == "BTCUSDT"
    assert top["similarity"] > 0.1
    assert top["metadata"]["outcome"] == "win"


def test_retrieve_scores_by_similarity(temp_memory):
    ai_memory.store_entry(kind="trade", text="BTC breakout retest H4 long trending", pair="BTCUSDT")
    ai_memory.store_entry(kind="trade", text="ETH mean reversion London session short", pair="ETHUSDT")
    ai_memory.store_entry(kind="trade", text="XAUUSD fibonacci confluence daily long", pair="XAUUSD")
    results = ai_memory.retrieve_similar(
        query_text="BTC breakout retest long trending H4",
        kind="trade",
        top_k=5,
        min_similarity=0.0,  # include all so we can check ordering
    )
    assert len(results) >= 1
    # BTC entry should be the most similar
    assert "BTC" in results[0]["text"]
    if len(results) > 1:
        assert results[0]["similarity"] >= results[1]["similarity"]


def test_pair_scoping_filters_to_same_pair(temp_memory):
    ai_memory.store_entry(kind="trade", text="BTC breakout retest H4 long trending", pair="BTCUSDT")
    ai_memory.store_entry(kind="trade", text="ETH mean reversion London session short", pair="ETHUSDT")
    results = ai_memory.retrieve_similar(
        query_text="BTC breakout retest long",
        kind="trade",
        pair="BTCUSDT",
        top_k=5,
    )
    assert all(r["pair"] == "BTCUSDT" for r in results)
    assert all("BTC" in r["text"] for r in results)


def test_kind_filter(temp_memory):
    ai_memory.store_entry(kind="trade", text="BTC breakout retest H4 long", pair="BTCUSDT")
    ai_memory.store_entry(kind="journal", text="BTC breakout retest H4 long", pair="BTCUSDT")
    results = ai_memory.retrieve_similar(
        query_text="BTC breakout retest",
        kind="trade",
        top_k=10,
    )
    assert all(r["kind"] == "trade" for r in results)


def test_min_similarity_filter(temp_memory):
    ai_memory.store_entry(kind="trade", text="BTC breakout retest H4 long trending", pair="BTCUSDT")
    results = ai_memory.retrieve_similar(
        query_text="completely different unrelated text about cooking recipes",
        kind="trade",
        top_k=5,
        min_similarity=0.99,
    )
    assert results == []


def test_build_context_block_formats_entries(temp_memory):
    ai_memory.store_entry(kind="trade", text="BTC breakout retest H4 long trending", pair="BTCUSDT", metadata={"r": 2.1})
    block = ai_memory.build_context_block(
        query_text="BTC breakout retest H4 long",
        kind="trade",
        top_k=3,
    )
    assert "SIMILAR HISTORICAL SETUP" in block
    assert "BTC" in block
    assert "similarity=" in block


def test_build_context_block_empty_when_no_matches(temp_memory):
    block = ai_memory.build_context_block(
        query_text="nothing stored yet",
        kind="trade",
        top_k=3,
    )
    assert block == ""


def test_store_entry_empty_text_returns_none(temp_memory):
    assert ai_memory.store_entry(kind="trade", text="") is None
    assert ai_memory.store_entry(kind="trade", text="   ") is None


def test_store_entry_never_raises_on_bad_metadata(temp_memory):
    # metadata with non-serializable object should not crash
    rid = ai_memory.store_entry(
        kind="trade",
        text="BTC breakout",
        pair="BTCUSDT",
        metadata={"obj": object()},  # not JSON-serializable
    )
    # Should either store without metadata or return None; never raise
    assert rid is None or rid > 0
