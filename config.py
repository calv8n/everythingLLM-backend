from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        extra="ignore",
    )

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
    # Directory where completed-task output files (CSVs, Plotly HTML charts,
    # images...) are persisted so the frontend can download/preview them via
    # /api/task/{id}/files. Mounted as a Docker volume in production.
    ARTIFACT_DIR: str = "/tmp/backend-artifacts"
    # Disable network for execution containers (blocks pip installs too;
    # enable once wheels are pre-baked or an egress proxy is configured).
    EXEC_NETWORK_DISABLED: bool = False
    # CORS allowed origins. Comma-separated list so several local frontend
    # dev servers can reach a backend hosted elsewhere (e.g. the Oracle Cloud
    # VM at http://129.225.68.122:8080). Override via .env:
    #   FRONTEND_ORIGIN=http://localhost:5173,http://127.0.0.1:5173
    FRONTEND_ORIGIN: str = "http://localhost:5173,http://127.0.0.1:5173"
    GROQ_API_KEY: str | None = None

    @property
    def frontend_origins(self) -> list[str]:
        """Parsed list of allowed CORS origins from FRONTEND_ORIGIN."""
        return [o.strip() for o in (self.FRONTEND_ORIGIN or "").split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"prod", "production", "cloud"}

settings = Settings()
