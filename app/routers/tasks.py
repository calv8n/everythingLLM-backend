import asyncio
import logging
import mimetypes
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...config import settings
from ...models import TaskRequest, TaskResponse
from ..services.artifact_store import get_artifact_path, list_task_files
from ..services.event_bus import create_bus
from ..services.input_guard import MAX_PROMPT_CHARS, validate_prompt
from ..services.orchestrator import run_task

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/task/{task_id}/files")
async def task_file_index(
    task_id: str,
):
    """List downloadable artifacts produced by a completed task."""
    return {"task_id": task_id, "files": list_task_files(task_id)}


@router.get("/api/task/{task_id}/files/{file_name}")
async def download_task_file(
    task_id: str,
    file_name: str,
    inline: bool = False,
):
    """Download one artifact. Use ?inline=true to render Plotly HTML/csv in
    the browser (served with a safe Content-Type and no-store)."""
    path = get_artifact_path(task_id, file_name)
    if path is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    safe_name = path.name
    content = path.read_bytes()
    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        # Interactive Plotly pages are embedded in a *sandboxed* iframe by the
        # frontend; allow that while keeping everything else locked down.
        "Content-Security-Policy": (
            "sandbox allow-scripts; frame-ancestors 'self'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return Response(content=content, media_type=media_type, headers=headers)


@router.post("/api/task", response_model=TaskResponse)
async def create_task(
    request: TaskRequest,
):
    """Endpoint to create a new task based on the user prompt."""
    if validate_prompt(request.prompt) == "block":
        raise HTTPException(
            status_code=422,
            detail=(
                f"The request exceeds the {MAX_PROMPT_CHARS}-character limit or "
                "matches a disallowed activity."
            ),
        )
    # Production (cloud demo): Ollama is not available on the server, so only
    # the Groq provider is accepted. Locally users may choose either provider.
    if settings.is_production and request.provider == "ollama":
        raise HTTPException(
            status_code=400,
            detail="Ollama is not available in this deployment. Use provider 'groq' with your own API key.",
        )
    if request.provider == "groq" and not (request.groq_api_key or "").strip():
        raise HTTPException(status_code=422, detail="A Groq API key is required when Groq is selected.")
    if request.provider == "groq" and not (request.groq_model or "").strip():
        raise HTTPException(status_code=422, detail="A Groq model is required when Groq is selected.")
    if request.web_search and request.search_provider not in {None, "searxng", "duckduckgo", "tavily"}:
        raise HTTPException(
            status_code=422,
            detail="Unsupported search provider. Use one of: searxng, duckduckgo, tavily.",
        )

    task_id = str(uuid.uuid4())
    session_id = request.session_id or str(uuid.uuid4())
    bus = create_bus(task_id)
    logger.info(
        "Task accepted task_id=%s session_id=%s prompt_chars=%d provider=%s",
        task_id,
        session_id,
        len(request.prompt),
        request.provider,
    )

    # Start the task in the background
    task_kwargs = {
        "groq_api_key": request.groq_api_key if request.provider == "groq" else None,
        "groq_model": request.groq_model if request.provider == "groq" else None,
        "session_id": session_id,
        "task_id": task_id,
    }
    if request.web_search or request.search_provider:
        task_kwargs["web_search"] = request.web_search
        task_kwargs["search_provider"] = request.search_provider

    asyncio.create_task(run_task(request.prompt, bus, **task_kwargs))

    return TaskResponse(task_id=task_id, session_id=session_id)
