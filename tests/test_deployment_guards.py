"""Tests for the local-vs-production behavior and demo auth."""
import asyncio

import pytest
from fastapi import HTTPException

from backend.app.routers import tasks
from backend.config import settings
from backend.models import TaskRequest


@pytest.fixture
def enable_auth(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "DEMO_TOKENS", "tok-a,tok-b")


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


def test_missing_token_returns_401(enable_auth):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks.create_task(TaskRequest(prompt="hi", provider="groq", groq_api_key="k", groq_model="m")))
    assert exc.value.status_code == 401


def test_valid_bearer_token_passes(enable_auth, monkeypatch):
    _fake_run(monkeypatch)
    resp = asyncio.run(tasks.create_task(
        TaskRequest(prompt="hi", provider="groq", groq_api_key="k", groq_model="m"),
        authorization="Bearer tok-a",
    ))
    assert resp.task_id


def test_production_without_tokens_fails_closed(enable_auth, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEMO_TOKENS", "")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks.create_task(
            TaskRequest(prompt="hi", provider="groq", groq_api_key="k", groq_model="m"),
            authorization="Bearer anything",
        ))
    assert exc.value.status_code == 401


def test_llm_service_refuses_ollama_fallback_in_production(monkeypatch):
    from backend.app.services import llm_service
    from backend.app.services.event_bus import EventBus

    monkeypatch.setattr(llm_service.settings, "ENVIRONMENT", "production")
    with pytest.raises(ValueError):
        asyncio.run(llm_service.generate_text("hi", EventBus(), groq_api_key=None))
