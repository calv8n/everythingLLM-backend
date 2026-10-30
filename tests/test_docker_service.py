import base64
import io
import tarfile
from types import SimpleNamespace

from backend.app.services.docker_service import DockerExecutionService


def test_execution_archive_contains_app_directory_and_files():
    service = DockerExecutionService.__new__(DockerExecutionService)

    archive_bytes = service._create_tar_achives("print('hello')", ["requests"])

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r") as archive:
        members = {member.name: member for member in archive.getmembers()}
        assert "app" in members
        assert members["app"].isdir()
        assert "app/main.py" in members
        assert "app/run.sh" in members

        code_file = archive.extractfile("app/main.py")
        run_file = archive.extractfile("app/run.sh")
        assert code_file is not None
        assert run_file is not None
        assert code_file.read() == b"print('hello')"
        run_script = run_file.read()
        assert b"python -m venv /app/.venv" in run_script
        assert b"source /app/.venv/bin/activate" in run_script
        assert b"python -m pip install --quiet" in run_script
        assert b"python -u /app/main.py" in run_script
        assert run_script.index(b"python -m pip install") < run_script.index(b"python -u /app/main.py")
        assert members["app/run.sh"].mode & 0o111


def test_dependency_names_are_shell_escaped_in_run_script():
    service = DockerExecutionService.__new__(DockerExecutionService)
    archive_bytes = service._create_tar_achives("print('safe')", ["requests; touch /tmp/unwanted"])

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r") as archive:
        run_file = archive.extractfile("app/run.sh")
        assert run_file is not None
        run_script = run_file.read()

    assert b"'requests; touch /tmp/unwanted'" in run_script


def test_plotly_is_installed_before_execution_when_requirement_is_omitted():
    service = DockerExecutionService.__new__(DockerExecutionService)
    service.task_id = "test-task"
    code = "import plotly.express as px\npx.line(x=[1], y=[2])"
    archive_bytes = service._create_tar_achives(code, [])

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r") as archive:
        run_file = archive.extractfile("app/run.sh")
        assert run_file is not None
        run_script = run_file.read()

    assert b"pip install --quiet --disable-pip-version-check plotly" in run_script
    assert run_script.index(b"pip install") < run_script.index(b"python -u /app/main.py")


def test_requests_is_installed_before_execution_when_requirement_is_omitted():
    service = DockerExecutionService.__new__(DockerExecutionService)
    service.task_id = "test-task"
    archive_bytes = service._create_tar_achives("import requests\nprint('ok')", [])

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r") as archive:
        run_file = archive.extractfile("app/run.sh")
        assert run_file is not None
        run_script = run_file.read()

    assert b"pip install --quiet --disable-pip-version-check requests" in run_script
    assert run_script.index(b"pip install") < run_script.index(b"python -u /app/main.py")


def test_html_output_file_is_serialized_for_frontend_preview():
    html_content = b"<html><body><h1>Plotly graph</h1></body></html>"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
        info = tarfile.TarInfo("india_gdp_graph.html")
        info.size = len(html_content)
        archive.addfile(info, io.BytesIO(html_content))
    archive_buffer.seek(0)
    archive_bytes = archive_buffer.getvalue()

    class FakeContainer:
        def get_archive(self, path):
            assert path == "/output"
            return [archive_bytes], {}

    service = DockerExecutionService.__new__(DockerExecutionService)
    service._docker_errors = SimpleNamespace(NotFound=type("NotFound", (Exception,), {}))

    files = service._extract_output_files(FakeContainer())

    assert files == [{
        "name": "india_gdp_graph.html",
        "type": "text/html",
        "encoding": "base64",
        "data": base64.b64encode(html_content).decode("ascii"),
    }]
