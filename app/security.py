"""Bearer-token authentication for the demo deployment.

Local (ENVIRONMENT=local): a single demo token is auto-generated at startup
and printed once to the server console; users choose Ollama or Groq.
Production (ENVIRONMENT=production, e.g. Oracle Cloud Always Free): tokens
must be supplied via DEMO_TOKENS; if none are configured the API fails
closed with 401 for every request.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, WebSocket

from ..config import settings

logger = logging.getLogger(__name__)


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def validate_token(token: str | None) -> bool:
    if not settings.AUTH_ENABLED:
        return True
    allowed = settings.allowed_tokens
    return bool(token) and token in allowed


def require_http_auth(authorization: str | None) -> None:
    """FastAPI dependency-style guard for REST endpoints."""
    if not settings.AUTH_ENABLED:
        return
    if not validate_token(_extract_bearer(authorization)):
        logger.warning("Rejected unauthenticated HTTP request")
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token.")


def websocket_auth_ok(websocket: WebSocket) -> bool:
    """Guard for WebSocket connections; token may come from a query param
    (browsers cannot set Authorization headers on WebSockets)."""
    if not settings.AUTH_ENABLED:
        return True
    token = websocket.query_params.get("token") or _extract_bearer(
        websocket.headers.get("authorization")
    )
    if not validate_token(token):
        logger.warning("Rejected unauthenticated WebSocket connection")
        return False
    return True
