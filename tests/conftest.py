import sys
from pathlib import Path

# Make the repo root importable as the "backend" package (repo dir has __init__.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch):
    """Keep router/service tests in the local provider mode."""
    from backend.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
