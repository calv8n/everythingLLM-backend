import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException

from ...config import settings
from ...models import TaskRequest, TaskResponse
from ..services.event_bus import create_bus
from ..services.orchestrator import run_task

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/task", response_model=TaskResponse)
async def create_task(request: TaskRequest):
    """Endpoint to create a new task based on the user prompt."""
    if request.provider == "groq" and not (request.groq_api_key or "").strip():
        raise HTTPException(status_code=422, detail="A Groq API key is required when Groq is selected.")
    if request.provider == "groq" and not (request.groq_model or "").strip():
        raise HTTPException(status_code=422, detail="A Groq model is required when Groq is selected.")

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
    asyncio.create_task(
        run_task(
            request.prompt,
            bus,
            groq_api_key=request.groq_api_key if request.provider == "groq" else None,
            groq_model=request.groq_model if request.provider == "groq" else None,
            session_id=session_id,
            task_id=task_id,
        )
    )

    return TaskResponse(task_id=task_id, session_id=session_id)


