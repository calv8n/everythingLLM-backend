from typing import Literal

from pydantic import BaseModel

class TaskRequest(BaseModel):
    prompt: str
    provider: Literal["ollama", "groq"] = "ollama"
    groq_api_key: str | None = None
    groq_model: str | None = None
    session_id: str | None = None

class TaskResponse(BaseModel):
    task_id: str
    session_id: str

class FilePayload(BaseModel):
    name: str
    type: str  # MIME type, e.g. "text/csv", "text/html", "image/png"
    encoding: str = "base64"
    data: str  # Base64 encoded file data
    size: int | None = None  # Decoded byte count
    kind: Literal["plotly", "csv", "image"] | None = None
    # Hint for the frontend: render interactive Plotly HTML in a sandboxed
    # iframe, preview CSVs as tables, show images inline; everything else is
    # download-only.

class WSEvent(BaseModel):
    type: str
    content: str
    files: list[FilePayload] | None = None
