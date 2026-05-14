from ai_conversation_store import append_turn, create_or_get_thread, get_recent_turns, init_store


def test_thread_created_turns_appended_and_retrieved(tmp_path):
    db = tmp_path / "audit.db"

    assert init_store(str(db)) is True
    thread_id = create_or_get_thread(None, "BTCUSDT", "trace-1", db_path=str(db))
    assert thread_id.startswith("ai-chat-")

    assert append_turn(thread_id, "user", "Why blocked?", db_path=str(db)) is True
    assert append_turn(
        thread_id,
        "assistant",
        "Blocked by risk.",
        tool_calls=[{"name": "get_open_risk_state"}],
        facts_used=["risk_state"],
        decision="BLOCKED_BY_RISK",
        db_path=str(db),
    ) is True

    turns = get_recent_turns(thread_id, db_path=str(db))
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[-1]["decision"] == "BLOCKED_BY_RISK"
    assert turns[-1]["tool_calls"][0]["name"] == "get_open_risk_state"


def test_unavailable_db_is_non_fatal(tmp_path):
    bad_path = tmp_path / "directory_db"
    bad_path.mkdir()

    thread_id = create_or_get_thread("thread-1", "BTCUSDT", "trace-1", db_path=str(bad_path))

    assert thread_id == "thread-1"
    assert append_turn("thread-1", "user", "hello", db_path=str(bad_path)) is False
    assert get_recent_turns("thread-1", db_path=str(bad_path)) == []
