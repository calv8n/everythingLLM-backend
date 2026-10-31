import secrets

from pydantic_settings import BaseSettings

from pathlib import Path


def _generate_demo_tokens() -> str:
    """Generate one short-lived demo bearer token (used only in local dev)."""
    return f"demo-{secrets.token_urlsafe(16)}"


class Settings(BaseSettings):
    # Runtime environment: "local" -> users choose Ollama or Groq;
    # "production" (e.g. Oracle Cloud Always Free) -> Groq only, no Ollama.
    ENVIRONMENT: str = "local"

    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:7b"
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    DOCKER_IMAGE: str = "python:3.11-slim"
    EXECUTION_TIMEOUT_SECONDS: int = 300
    CODE_RETRY_ATTEMPTS: int = 5
    SESSION_MEMORY_MESSAGES: int = 6
    CONTAINER_MEMORY_LIMIT: str = "512m"
    # Disable network for execution containers (blocks pip installs too;
    # enable once wheels are pre-baked or an egress proxy is configured).
    EXEC_NETWORK_DISABLED: bool = False
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    GROQ_API_KEY: str | None = None

    # Auth: comma-separated list of valid bearer tokens for the demo users.
    # In production leave empty to fail closed (all requests rejected with 401).
    # In local dev a single demo token is generated automatically at startup.
    DEMO_TOKENS: str | None = None
    AUTH_ENABLED: bool = True

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"prod", "production", "cloud"}

    @property
    def allowed_tokens(self) -> frozenset[str]:
        raw = (self.DEMO_TOKENS or "").strip()
        if not raw and not self.is_production:
            raw = _generate_demo_tokens()
            print(
                f"[startup] Local demo token (copy it, printed once): {raw}",
                flush=True,
            )
        return frozenset(t.strip() for t in raw.split(",") if t.strip())

    class Config:
        env_file = str(Path(__file__).resolve().parent / ".env")

settings = Settings()
