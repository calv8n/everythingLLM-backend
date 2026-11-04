"""Persistent store for files produced by sandbox executions.

Artifacts are written under ``settings.ARTIFACT_DIR`` (a Docker volume in
production) so they survive container removal and can be downloaded or
previewed via REST after the WebSocket ``result`` event.

This module lives in ``app.services`` (not ``app.routers.tasks``) to avoid a
circular import: ``tasks.py`` imports ``orchestrator``, which needs artifact
persistence helpers at module load time.
"""

import base64
import logging
import re
from pathlib import Path

from ...config import settings

logger = logging.getLogger(__name__)

ARTIFACT_ROOT = Path(settings.ARTIFACT_DIR)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_artifact_name(name: str) -> str:
    """Return a filesystem-safe single-segment name for an artifact."""
    return _SAFE_NAME_RE.sub("_", Path(name or "").name) or "file"


def _artifact_dir(task_id: str) -> Path:
    return ARTIFACT_ROOT / task_id


def save_task_files(task_id: str, files: list[dict]) -> None:
    """Persist decoded output files for later download/preview."""
    target = _artifact_dir(task_id)
    try:
        target.mkdir(parents=True, exist_ok=True)
        for entry in files:
            name = safe_artifact_name(entry.get("name", ""))
            data = base64.b64decode(entry.get("data", ""))
            (target / name).write_bytes(data)
        logger.info(
            "Task artifacts saved task_id=%s file_count=%d dir=%s",
            task_id, len(files), target,
        )
    except OSError:
        logger.exception("Failed to persist task artifacts task_id=%s", task_id)


def list_task_files(task_id: str) -> list[dict]:
    target = _artifact_dir(task_id)
    if not target.is_dir():
        return []
    result = []
    for path in sorted(target.iterdir()):
        if path.is_file():
            result.append({
                "name": path.name,
                "url": f"/api/task/{task_id}/files/{path.name}",
                "size": path.stat().st_size,
            })
    return result


def get_artifact_path(task_id: str, file_name: str) -> Path | None:
    """Resolve one artifact safely; returns None if it does not exist."""
    path = _artifact_dir(task_id) / safe_artifact_name(file_name)
    return path if path.is_file() else None
