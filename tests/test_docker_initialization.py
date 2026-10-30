import asyncio
from types import SimpleNamespace

from backend.app.services.docker_service import DockerExecutionService


class FakeImageNotFound(Exception):
    pass


class FakeDockerClient:
    def __init__(self, image_exists: bool):
        self.image_exists = image_exists
        self.ping_count = 0
        self.get_count = 0
        self.pull_count = 0
        self.closed = False
        self.images = self

    def ping(self):
        self.ping_count += 1

    def get(self, image):
        self.get_count += 1
        if not self.image_exists:
            raise FakeImageNotFound(image)
        return image

    def pull(self, image):
        self.pull_count += 1
        self.image_exists = True
        return image

    def close(self):
        self.closed = True


def make_service(image_exists: bool) -> tuple[DockerExecutionService, FakeDockerClient]:
    service = DockerExecutionService.__new__(DockerExecutionService)
    client = FakeDockerClient(image_exists)
    service.client = client
    service._docker_errors = SimpleNamespace(ImageNotFound=FakeImageNotFound)
    service.image = "python:3.11-slim"
    service._initialized = False
    service.task_id = "test-docker-task"
    return service, client


def test_initialize_pulls_missing_image_once_and_ping_docker():
    service, client = make_service(image_exists=False)

    asyncio.run(service.initialize())
    asyncio.run(service.initialize())

    assert client.ping_count == 1
    assert client.get_count == 1
    assert client.pull_count == 1
    assert service._initialized


def test_initialize_does_not_pull_existing_image():
    service, client = make_service(image_exists=True)

    asyncio.run(service.initialize())

    assert client.ping_count == 1
    assert client.get_count == 1
    assert client.pull_count == 0
