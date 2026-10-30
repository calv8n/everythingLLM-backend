# EverythingLLM Backend

FastAPI service that coordinates Ollama or Groq, planning/research/implementation agents, Python validation, isolated Docker execution, session memory, and WebSocket event streaming.

## Requirements

- Python 3.11 or newer for the host service
- Docker Desktop/Engine available to the Docker CLI and Python SDK
- Ollama running locally if using the local provider, with the configured model pulled
- Internet access from Docker when generated scripts need pip packages or public APIs

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Start the API:

```bash
uvicorn backend.main:app --reload
```

The API listens on `http://127.0.0.1:8000`. The Vite frontend proxies `/api` and `/ws` to this address.

## Configuration

Settings are defined in `backend/config.py`; the optional backend environment file is `backend/.env` and is gitignored. Do not commit credentials.

| Setting | Default | Description |
| --- | --- | --- |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint setting |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Local generation model |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Default Groq model for internal calls |
| `DOCKER_IMAGE` | `python:3.11-slim` | Isolated script runtime image |
| `EXECUTION_TIMEOUT_SECONDS` | `300` | Script execution timeout |
| `CODE_RETRY_ATTEMPTS` | `5` | Repairs after the initial code attempt |
| `SESSION_MEMORY_MESSAGES` | `6` | Maximum retained messages per session |
| `CONTAINER_MEMORY_LIMIT` | `512m` | Docker container memory limit |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS allowed origin |

For Groq tasks, the frontend sends the user's API key and selected model per request. The API requires both when `provider` is `groq`. The key is not added to server-side session history.

For Ollama, install/start Ollama and pull the configured model, for example:

```bash
ollama pull qwen2.5-coder:7b
```

Docker Desktop/Engine must be running. At the start of every task, the service pings Docker and ensures the configured image exists, pulling it if necessary.

## Request and event flow

1. `POST /api/task` validates provider fields, allocates a task/session ID, creates an event bus, and starts the async task.
2. The client connects to `WS /ws/{task_id}`.
3. The orchestrator initializes Docker, invokes planning and conditional web research, then asks the implementation agent to generate code.
4. The validation agent parses Python syntax and discovers common third-party imports; missing pip requirements are added before execution.
5. Docker creates a disposable container, creates `/app/.venv`, installs packages, executes the code, streams logs, and collects `/output` artifacts.
6. Only a successful exit with no recognized printed failure message is returned as `result`. Failures are sent back to the LLM with prior session context, failed code, and logs, up to the configured retry count.
7. The WebSocket closes when the event bus is finished.

Task request fields: `prompt`, `provider` (`ollama` or `groq`), optional `groq_api_key`, optional `groq_model`, and optional `session_id`. The response contains `task_id` and `session_id`.

Events include `planning`, `code`, `executing`, `log`, `retry`, `result`, and `error`. Result file objects use `name`, `type` (MIME), `encoding` (`base64`), and `data`.

## Agents and execution

- **Orchestrator Agent** coordinates the task lifecycle.
- **Planning Agent** returns a structured plan and decides whether external research is needed.
- **Research Agent** searches public web results only when requested by the planner; search results are treated as untrusted reference material.
- **Implementation Agent** generates the complete executable Python script.
- **Validation Agent** checks syntax, reconciles imports with pip dependencies, and checks output for common explicit failure messages before a result is published.
- **AgentSandbox** exposes Python execution through the task's isolated Docker service without exposing the Docker client or host filesystem directly.

Generated scripts are injected through an in-memory tar archive, not a host bind mount. The container uses configured memory/CPU limits and execution timeout, and is force-removed in cleanup. It has network connectivity for pip and permitted public API requests; generated code is untrusted, so review it and do not expose secrets in task prompts or container files.

The Docker bootstrap installs declared and statically discovered dependencies into `/app/.venv` before running `/app/main.py`. Python imports are statically inspected; dynamically loaded packages should still be declared in `# REQUIREMENTS`.

Charts should use Plotly and be saved as standalone HTML under `/output/`. CSV, images, HTML, and other files under `/output/` are returned as base64 artifacts.

## Session memory and retries

Session memory is process-local (not persistent across backend restarts) and retains the newest six user/assistant messages by default. Repair prompts include those prior messages, the current user task, failed code, and captured errors/logs. `CODE_RETRY_ATTEMPTS=5` means up to five repairs after the initial attempt.

## Tests and logs

Run the backend tests from the repository root:

```bash
source .venv/bin/activate
python -m pytest backend/tests -q
```

The server logs task IDs, provider/model, agent timings, Docker lifecycle, execution status, and retry details at INFO/WARNING/ERROR. It avoids logging prompts, generated code, and API keys. View logs in the terminal running Uvicorn.

## Backend layout

- `main.py`: FastAPI app, CORS, router registration, logging level
- `app/routers/tasks.py`: task creation endpoint
- `app/routers/websockets.py`: task event streaming
- `app/services/orchestrator.py`: task stages, session context, validation and retries
- `app/services/agents.py`: planning, web research, implementation, coordinating roles
- `app/services/code_validation.py`: syntax/import/output validation
- `app/services/agent_sandbox.py`: restricted sandbox interface
- `app/services/docker_service.py`: image initialization, dependency installation, execution, artifacts
- `app/services/llm_service.py`: Ollama/Groq streaming
- `app/services/event_bus.py`: in-process async event queue
- `app/services/session_memory.py`: bounded process-local session history
- `app/prompts/system_prompt.txt`: generated-script contract
- `tests/`: backend unit and integration-style tests