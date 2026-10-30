from __future__ import annotations

from .docker_service import DockerExecutionService
from .event_bus import EventBus


class AgentSandbox:
    """Restricted agent tool interface backed by disposable Docker containers.

    Agents can execute Python in isolated containers but never receive the Docker
    client or host filesystem/socket access directly.
    """

    def __init__(self, docker_service: DockerExecutionService, bus: EventBus) -> None:
        self._docker_service = docker_service
        self._bus = bus

    async def run_python(self, code: str, requirements: list[str] | None = None) -> dict:
        """Run a Python snippet in the task's isolated Docker environment."""
        return await self._docker_service.execute_code(code, requirements or [], self._bus)
