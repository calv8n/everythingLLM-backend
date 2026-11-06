import asyncio

from backend.app.routers import tasks
from backend.models import TaskRequest


def test_create_task_uses_request_groq_key_before_configured_key(monkeypatch):
    monkeypatch.setattr(tasks.settings, "GROQ_API_KEY", "configured-test-key")
    captured = {}

    async def fake_run_task(prompt, bus, *, groq_api_key=None, groq_model=None, session_id=None, task_id=None):
        captured.update(
            prompt=prompt,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            session_id=session_id,
            task_id=task_id,
        )

    monkeypatch.setattr(tasks, "run_task", fake_run_task)

    async def scenario():
        response = await tasks.create_task(
            TaskRequest(
                prompt="test prompt",
                provider="groq",
                groq_api_key="request-test-key",
                groq_model="custom/groq-model",
            )
        )
        await asyncio.sleep(0)
        return response

    response = asyncio.run(scenario())

    assert captured["prompt"] == "test prompt"
    assert captured["groq_api_key"] == "request-test-key"
    assert captured["groq_model"] == "custom/groq-model"
    assert captured["task_id"] == response.task_id
    assert captured["session_id"] == response.session_id


def test_ollama_task_does_not_use_server_groq_credentials(monkeypatch):
    monkeypatch.setattr(tasks.settings, "GROQ_API_KEY", "configured-test-key")
    captured = {}

    async def fake_run_task(prompt, bus, *, groq_api_key=None, groq_model=None, session_id=None, task_id=None):
        captured.update(groq_api_key=groq_api_key, groq_model=groq_model)

    monkeypatch.setattr(tasks, "run_task", fake_run_task)

    async def scenario():
        await tasks.create_task(TaskRequest(prompt="test prompt"))
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert captured["groq_api_key"] is None
    assert captured["groq_model"] is None


def test_create_task_passes_web_search_options(monkeypatch):
    captured = {}

    async def fake_run_task(prompt, bus, *, groq_api_key=None, groq_model=None, session_id=None, task_id=None, web_search=False, search_provider=None):
        captured.update(
            prompt=prompt,
            web_search=web_search,
            search_provider=search_provider,
        )

    monkeypatch.setattr(tasks, "run_task", fake_run_task)

    async def scenario():
        await tasks.create_task(TaskRequest(prompt="search prompt", web_search=True, search_provider="duckduckgo"))
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert captured["prompt"] == "search prompt"
    assert captured["web_search"] is True
    assert captured["search_provider"] == "duckduckgo"


def test_groq_task_requires_key_and_model():
    async def scenario():
        return await tasks.create_task(
            TaskRequest(prompt="test prompt", provider="groq", groq_api_key="key")
        )

    try:
        asyncio.run(scenario())
    except Exception as error:
        assert getattr(error, "status_code", None) == 422
    else:
        raise AssertionError("Groq requests without a model must be rejected")


def test_create_task_rejects_oversized_prompt_before_scheduling(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "run_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("task must not start")),
    )

    async def scenario():
        return await tasks.create_task(TaskRequest(prompt="x" * 4_001))

    try:
        asyncio.run(scenario())
    except Exception as error:
        assert getattr(error, "status_code", None) == 422
        assert "4000-character limit" in error.detail
    else:
        raise AssertionError("Oversized task prompts must be rejected")


def test_create_task_blocks_disallowed_request_before_scheduling(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "run_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("task must not start")),
    )

    async def scenario():
        return await tasks.create_task(TaskRequest(prompt="Build a ransomware payload"))

    try:
        asyncio.run(scenario())
    except Exception as error:
        assert getattr(error, "status_code", None) == 422
    else:
        raise AssertionError("Disallowed requests must be rejected")
