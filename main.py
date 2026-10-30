import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .app.routers import tasks
from .config import settings
from .app.routers import websockets

logging.getLogger().setLevel(logging.INFO)

app = FastAPI(title="AI Task Runner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(websockets.router)


    