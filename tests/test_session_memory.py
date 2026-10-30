from backend.app.services.session_memory import append_message, clear_session, get_messages


def test_session_memory_keeps_only_six_newest_messages(monkeypatch):
    session_id = "memory-cap-test"
    clear_session(session_id)
    monkeypatch.setattr(
        "backend.app.services.session_memory.settings.SESSION_MEMORY_MESSAGES", 6
    )

    for index in range(8):
        append_message(session_id, "user" if index % 2 == 0 else "assistant", f"message-{index}")

    messages = get_messages(session_id)
    assert len(messages) == 6
    assert [message["content"] for message in messages] == [
        "message-2",
        "message-3",
        "message-4",
        "message-5",
        "message-6",
        "message-7",
    ]
    clear_session(session_id)