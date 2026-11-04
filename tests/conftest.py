import sys
from pathlib import Path

# Make the repo root importable as the "backend" package (repo dir has __init__.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch):
    """Tests call router/service functions directly: disable bearer auth and
    force local environment so Ollama-accepting requests stay valid."""
    from backend.config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
