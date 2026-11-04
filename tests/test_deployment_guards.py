"""Tests for local-vs-production behavior."""
import asyncio

import pytest
from fastapi import HTTPException

from backend.app.routers import tasks
from backend.config import Settings, settings
from backend.models import TaskRequest


def test_settings_ignore_unrelated_dotenv_entries(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ENVIRONMENT=production\nUNRELATED_SETTING=unrelated\n")

    loaded = Settings(_env_file=env_file)

    assert loaded.ENVIRONMENT == "production"


def _fake_run(monkeypatch):
    async def fake_run_task(prompt, bus, **kw):
        return None

    monkeypatch.setattr(tasks, "run_task", fake_run_task)


def test_production_rejects_ollama_provider(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    _fake_run(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks.create_task(TaskRequest(prompt="hi", provider="ollama")))
    assert exc.value.status_code == 400


def test_local_accepts_ollama_provider(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    _fake_run(monkeypatch)
    resp = asyncio.run(tasks.create_task(TaskRequest(prompt="hi", provider="ollama")))
    assert resp.task_id


def test_groq_task_does_not_require_authentication(monkeypatch):
    _fake_run(monkeypatch)
    resp = asyncio.run(tasks.create_task(TaskRequest(
        prompt="hi",
        provider="groq",
        groq_api_key="k",
        groq_model="m",
    )))
    assert resp.task_id


def test_llm_service_refuses_ollama_fallback_in_production(monkeypatch):
    from backend.app.services import llm_service
    from backend.app.services.event_bus import EventBus

    monkeypatch.setattr(llm_service.settings, "ENVIRONMENT", "production")
    with pytest.raises(ValueError):
        asyncio.run(llm_service.generate_text("hi", EventBus(), groq_api_key=None))
