from pydantic_settings import BaseSettings

from pathlib import Path
class Settings(BaseSettings):
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:7b"
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    DOCKER_IMAGE: str = "python:3.11-slim"
    EXECUTION_TIMEOUT_SECONDS: int = 300
    CODE_RETRY_ATTEMPTS: int = 5
    SESSION_MEMORY_MESSAGES: int = 6
    CONTAINER_MEMORY_LIMIT: str = "512m"
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    GROQ_API_KEY: str | None = None

    class Config:
        env_file = str(Path(__file__).resolve().parent / ".env")

settings = Settings()
