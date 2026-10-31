import logging

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .app.routers import tasks
from .config import settings
from .app.routers import websockets
from .app.security import require_http_auth

logging.getLogger().setLevel(logging.INFO)

app = FastAPI(title="AI Task Runner")


@app.get("/healthz")
async def healthz():
    """Unauthenticated liveness probe for the cloud deployment."""
    return {"status": "ok", "environment": settings.ENVIRONMENT}


@app.get("/api/config")
async def client_config(authorization: str | None = Header(default=None)):
    """ Tells the frontend which LLM providers are available in this
    deployment. Locally users may pick Ollama or Groq; in production
    (Oracle Cloud Always Free) only Groq with the user's own API key is
    offered, so the frontend can hide the Ollama option automatically."""
    require_http_auth(authorization)
    return {
        "providers": [] if settings.is_production else ["ollama", "groq"],
        "default_provider": "groq" if settings.is_production else "ollama",
        "groq_models": [settings.GROQ_MODEL],
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(websockets.router)


    