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
