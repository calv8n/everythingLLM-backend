from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import shlex
import tarfile
import time


from ...config import settings
from .code_validation import ValidationAgent
from .event_bus import EventBus

logger = logging.getLogger(__name__)

class DockerExecutionService:
    def __init__(self, task_id: str | None = None):
        import docker
        
        # Lazy client: constructing DockerExecutionService must not require a
        # live daemon (unit tests, tooling); the daemon is contacted in
        # initialize(), which every execution path calls first.
        self._client = None
        self._docker_errors = docker.errors
        self.image = settings.DOCKER_IMAGE
        self.timeout = settings.EXECUTION_TIMEOUT_SECONDS
        self.mem_limit = settings.CONTAINER_MEMORY_LIMIT
        self._initialized = False
        self.task_id = task_id or "unknown"

    @property
    def client(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def _initialize_sync(self) -> None:
        logger.info("Checking Docker daemon availability task_id=%s", self.task_id)
        self.client.ping()
        try:
            self.client.images.get(self.image)
            logger.info(
                "Docker runtime image already available task_id=%s image=%s",
                self.task_id,
                self.image,
            )
        except self._docker_errors.ImageNotFound:
            logger.info("Pulling Docker runtime image task_id=%s image=%s", self.task_id, self.image)
            self.client.images.pull(self.image)
            logger.info("Docker runtime image pull completed task_id=%s image=%s", self.task_id, self.image)

    async def initialize(self) -> None:
        """Verify Docker is running and ensure the configured runtime image exists."""
        if self._initialized:
            return
        loop = asyncio.get_running_loop()
        started_at = time.monotonic()
        logger.info("Docker initialization started task_id=%s image=%s", self.task_id, self.image)
        await loop.run_in_executor(None, self._initialize_sync)
        self._initialized = True
        logger.info(
            "Docker initialization ready task_id=%s image=%s duration_seconds=%.2f",
            self.task_id,
            self.image,
            time.monotonic() - started_at,
        )

    async def close(self) -> None:
        """Release the Docker HTTP client after the complete task lifecycle."""
        if self._client is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._client.close)
        self._client = None
        logger.debug("Docker client connection pool closed")

    def _create_tar_achives(self, code: str, requirements: list[str]) -> bytes:
        """Create an archive containing the files under an app directory."""
        validation = ValidationAgent().validate(code, requirements)
        package_requirements = validation.requirements
        if len(package_requirements) > len(requirements):
            logger.warning(
                "Added undeclared imported dependencies before pip install task_id=%s added_count=%d",
                getattr(self, "task_id", "unknown"),
                len(package_requirements) - len(requirements),
            )

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tar:
            app_dir = tarfile.TarInfo(name='app')
            app_dir.type = tarfile.DIRTYPE
            app_dir.mode = 0o755
            tar.addfile(app_dir)

            # main.py
            code_bytes = code.encode('utf-8')
            info = tarfile.TarInfo(name='app/main.py')
            info.size = len(code_bytes)
            tar.addfile(tarinfo=info, fileobj=io.BytesIO(code_bytes))

            # run.sh
            pip_line = ""
            if package_requirements:
                pkgs = shlex.join(package_requirements)
                pip_line = (
                    "python -m pip install --quiet --disable-pip-version-check "
                    f"{pkgs} 2>&1 && "
                )
            # Run as nobody:nogroup with a writable tmpfs at /tmp and
            # /output; the venv lives on tmpfs because the bind-free
            # injected /app tree is root-owned and read-only to us.
            run_script = (
                "#!/bin/bash\n"
                "set -e\n"
                "export HOME=/tmp\n"
                "export PIP_CACHE_DIR=/tmp/pip-cache\n"
                "python -m venv /tmp/.venv\n"
                "source /tmp/.venv/bin/activate\n"
                f"{pip_line}python -u /app/main.py"
            )
            run_bytes = run_script.encode('utf-8')
            info = tarfile.TarInfo(name='app/run.sh')
            info.size = len(run_bytes)
            info.mode = 0o755  # Make it executable
            tar.addfile(tarinfo=info, fileobj=io.BytesIO(run_bytes))

        buf.seek(0)
        return buf.read()

    def _create_container(self):
        # Hardened sandbox for untrusted, LLM-generated code:
        # all capabilities dropped, no privilege escalation, non-root user,
        # PID cap. Network egress can be disabled via EXEC_NETWORK_DISABLED
        # (note: run.sh does `pip install`, so disabling network also means
        # generated code cannot fetch PyPI packages).
        kwargs = dict(
            image=self.image,
            command=["bash", "/app/run.sh"],
            mem_limit=self.mem_limit,
            cpu_period=100000,
            cpu_quota=100000,
            pids_limit=256,
            detach=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            user="65534:65534",  # nobody:nogroup in python:3.11-slim
        )
        kwargs["tmpfs"] = {"/tmp": "rw,size=128m,mode=1777", "/output": "rw,size=64m,mode=1777"}
        if settings.EXEC_NETWORK_DISABLED:
            kwargs["network_disabled"] = True
        return self.client.containers.create(**kwargs)

    def _extract_output_files(self, container) -> list[dict]:
        """ Get files from /ouput directiory in the container """
        try:
            bits, _ = container.get_archive("/output")
            tar_stream = io.BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)

            files = []
            with tarfile.open(fileobj=tar_stream, mode='r') as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        f = tar.extractfile(member)
                        if f:
                            content = f.read()
                            name = member.name
                            if name.startswith("output/"):
                                name = name[len("output/"):]
                            mime_type = (
                                mimetypes.guess_type(name)[0]
                                or "application/octet-stream"
                            )
                            files.append(
                                {
                                    "name": name,
                                    "type": mime_type,
                                    "encoding": "base64",
                                    "data": base64.b64encode(content).decode("utf-8"),
                                }
                            )
            return files
        except self._docker_errors.NotFound:
            return []

    async def execute_code(self, code: str, requirements: list[str], bus: EventBus) -> dict:
        """ Run code in a fresh container and stream logs"""
        loop = asyncio.get_event_loop()
        started_at = time.monotonic()
        container = None
        stdout_lines: list[str] = []

        try:
            container = await loop.run_in_executor(None, self._create_container)
            logger.info(
                "Execution container created task_id=%s container_id=%s image=%s code_chars=%d requirement_count=%d",
                self.task_id,
                container.short_id,
                self.image,
                len(code),
                len(requirements),
            )

            # Inject code and requirements into the container
            tar_bytes = self._create_tar_achives(code, requirements)
            await loop.run_in_executor(
                None, container.put_archive, "/", tar_bytes
            )

            # Start the container
            await loop.run_in_executor(None, container.start)
            logger.info("Execution container started task_id=%s container_id=%s", self.task_id, container.short_id)
            await bus.publish(
                {
                    "type": "status",
                    "content": "Container started",
                }
            )

            # Stream logs
            log_queue: asyncio.Queue[str] = asyncio.Queue()

            def _stream_logs():
                try:
                    for line in container.logs(stream=True, follow=True):
                        decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                        asyncio.run_coroutine_threadsafe(
                            log_queue.put(decoded), loop
                        )
                finally:
                    asyncio.run_coroutine_threadsafe(log_queue.put(None), loop)

            loop.run_in_executor(None, _stream_logs)

            # Consume logs and publish

            while True:
                try:
                    line = await asyncio.wait_for(
                        log_queue.get(), timeout=self.timeout
                    )
                except asyncio.TimeoutError:
                    await loop.run_in_executor(None, container.kill)
                    raise
                if line is None:
                    break
                stdout_lines.append(line)
                await bus.publish(
                    {
                        "type": "log",
                        "content": line,
                    }
                )

            # Wait for container to finish
            result = await asyncio.wait_for(
                loop.run_in_executor(None, container.wait), timeout=30
            )

            # Extract output files
            files = await loop.run_in_executor(
                None, self._extract_output_files, container
            )
            logger.info(
                "Execution container finished task_id=%s container_id=%s exit_code=%s duration_seconds=%.2f log_lines=%d output_files=%d",
                self.task_id,
                container.short_id,
                result.get("StatusCode"),
                time.monotonic() - started_at,
                len(stdout_lines),
                len(files),
            )

            return {
                "exit_code": result.get("StatusCode"),
                "stdout": "\n".join(stdout_lines),
                "files": files,
            }

        except asyncio.TimeoutError:
            logger.warning(
                "Execution container timed out task_id=%s container_id=%s timeout_seconds=%d",
                self.task_id,
                container.short_id if container else "not-created",
                self.timeout,
            )
            raise
        except Exception:
            logger.exception(
                "Execution container failed task_id=%s container_id=%s duration_seconds=%.2f",
                self.task_id,
                container.short_id if container else "not-created",
                time.monotonic() - started_at,
            )
            raise

        finally:
            if container is not None:
                try:
                    await loop.run_in_executor(
                        None, lambda: container.remove(force=True)
                    )
                    logger.info("Execution container removed task_id=%s container_id=%s", self.task_id, container.short_id)
                except Exception:
                    logger.exception("Failed to remove execution container task_id=%s container_id=%s", self.task_id, container.short_id)
            