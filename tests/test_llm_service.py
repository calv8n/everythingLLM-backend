import asyncio
import sys
from types import SimpleNamespace

import pytest

from backend.app.services import llm_service
from backend.app.services.event_bus import EventBus


def test_groq_stream_uses_chat_completions_create(monkeypatch):
    captured = {}
    expected_stream = iter(["chunk"])

    def create(**kwargs):
        captured.update(kwargs)
        return expected_stream

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ),
    )
    fake_groq_module = SimpleNamespace(Groq=lambda *, api_key: fake_client)
    monkeypatch.setitem(sys.modules, "groq", fake_groq_module)

    stream = llm_service._stream_groq(
        "user prompt",
        "test-api-key",
        "system instructions",
    )

    assert stream is expected_stream
    assert captured["model"] == llm_service.settings.GROQ_MODEL
    assert captured["stream"] is True
    assert captured["messages"] == [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "user prompt"},
    ]


def test_groq_stream_uses_user_selected_model(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return iter(())

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    monkeypatch.setitem(
        sys.modules,
        "groq",
        SimpleNamespace(Groq=lambda *, api_key: fake_client),
    )

    llm_service._stream_groq("prompt", "test-key", model="custom/model-name")

    assert captured["model"] == "custom/model-name"


def test_llm_stream_exception_is_logged_and_propagated(monkeypatch, caplog):
    def broken_stream(user_prompt):
        raise RuntimeError("simulated model connection failure")

    monkeypatch.setattr(llm_service, "_stream_ollama", broken_stream)

    async def scenario():
        await llm_service.generate_text("private user prompt", EventBus())

    with caplog.at_level("ERROR", logger="backend.app.services.llm_service"):
        with pytest.raises(RuntimeError, match="simulated model connection failure"):
            asyncio.run(scenario())

    assert "LLM stream failed" in caplog.text
    assert "private user prompt" not in caplog.text