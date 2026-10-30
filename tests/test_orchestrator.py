import asyncio

from backend.app.services import orchestrator
from backend.app.services.event_bus import EventBus
from backend.app.services.session_memory import append_message, clear_session, get_messages


def run_task_with_fakes(
    monkeypatch,
    generations,
    execution_results,
    retry_attempts=2,
    session_id=None,
    prompt="Make a population table",
):
    prompts = []
    executed_code = []
    results = iter(execution_results)
    generated = iter(generations)

    async def fake_generate_plan_and_code(prompt, bus, *, groq_api_key=None):
        prompts.append(prompt)
        response, code, requirements = next(generated)
        if code:
            await bus.publish({"type": "code", "content": code})
        return response, code, requirements

    class FakeDockerExecutionService:
        def __init__(self, task_id=None):
            self.initialized = False
            self.task_id = task_id
            self.image = "python:3.11-slim"

        async def initialize(self):
            self.initialized = True

        async def close(self):
            pass

        async def execute_code(self, code, requirements, bus):
            assert self.initialized
            executed_code.append(code)
            result = next(results)
            if isinstance(result, BaseException):
                raise result
            return result

    async def fake_prepare_task_prompt(prompt, bus, *, groq_api_key=None, groq_model=None, sandbox, task_id=None):
        assert sandbox is not None
        assert sandbox._docker_service.initialized
        return prompt

    async def fake_implement_task(prompt, bus, *, groq_api_key=None, groq_model=None, sandbox, task_id=None):
        assert sandbox is not None
        assert sandbox._docker_service.initialized
        return await fake_generate_plan_and_code(prompt, bus, groq_api_key=groq_api_key)

    monkeypatch.setattr(orchestrator, "implement_task", fake_implement_task)
    monkeypatch.setattr(orchestrator, "prepare_task_prompt", fake_prepare_task_prompt)
    monkeypatch.setattr(orchestrator, "DockerExecutionService", FakeDockerExecutionService)
    monkeypatch.setattr(orchestrator.settings, "CODE_RETRY_ATTEMPTS", retry_attempts)

    async def scenario():
        bus = EventBus()
        await orchestrator.run_task(
            prompt, bus, session_id=session_id, task_id="test-task-id"
        )
        events = [event async for event in bus.subscribe()]
        return events

    return asyncio.run(scenario()), prompts, executed_code


def test_failed_code_and_logs_are_sent_to_model_for_retry(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "print(data[1])", ["requests"]),
            ("Corrected plan", "print('recovered')", ["requests"]),
        ],
        execution_results=[
            {
                "exit_code": 1,
                "stdout": "Traceback: IndexError: list index out of range",
                "files": [],
            },
            {"exit_code": 0, "stdout": "recovered", "files": []},
        ],
        retry_attempts=1,
    )

    assert executed_code == ["print(data[1])", "print('recovered')"]
    assert len(prompts) == 2
    assert "Make a population table" in prompts[1]
    assert "print(data[1])" in prompts[1]
    assert "IndexError: list index out of range" in prompts[1]
    assert any(event["type"] == "retry" for event in events)
    assert events[-1]["type"] == "result"
    assert events[-1]["content"] == "recovered"


def test_timeout_is_sent_to_model_for_retry(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "while True: pass", []),
            ("Corrected plan", "print('done')", []),
        ],
        execution_results=[
            asyncio.TimeoutError(),
            {"exit_code": 0, "stdout": "done", "files": []},
        ],
        retry_attempts=1,
    )

    assert executed_code == ["while True: pass", "print('done')"]
    assert "Execution timed out" in prompts[1]
    assert "while True: pass" in prompts[1]
    assert events[-1]["type"] == "result"


def test_execution_index_error_is_sent_to_model_for_retry(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "print(items[1])", []),
            ("Corrected plan", "print('recovered safely')", []),
        ],
        execution_results=[
            IndexError("list index out of range"),
            {"exit_code": 0, "stdout": "recovered safely", "files": []},
        ],
        retry_attempts=1,
    )

    assert executed_code == ["print(items[1])", "print('recovered safely')"]
    assert len(prompts) == 2
    assert "IndexError: list index out of range" in prompts[1]
    assert "print(items[1])" in prompts[1]
    assert any(event["type"] == "retry" for event in events)
    assert events[-1]["type"] == "result"


def test_retry_prompt_includes_previous_six_session_messages_and_error(monkeypatch):
    session_id = "retry-context-session"
    clear_session(session_id)
    prior_messages = [
        ("user", "Earlier request about GDP comparison"),
        ("assistant", "Use World Bank indicator data"),
        ("user", "Compare India and USA"),
        ("assistant", "Use years 2018 through 2023"),
        ("user", "Show GDP in USD"),
        ("assistant", "Save a Plotly graph and CSV"),
    ]
    for role, content in prior_messages:
        append_message(session_id, role, content)

    events, prompts, _ = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "print('first')", []),
            ("Repaired plan", "print('fixed')", []),
        ],
        execution_results=[
            {
                "exit_code": 1,
                "stdout": "Error fetching data: Expecting value: line 1 column 1",
                "files": [],
            },
            {"exit_code": 0, "stdout": "fixed", "files": []},
        ],
        retry_attempts=1,
        session_id=session_id,
        prompt="Retry the GDP comparison with robust JSON validation",
    )

    repair_prompt = prompts[1]
    assert "Earlier request about GDP comparison" in repair_prompt
    assert "Save a Plotly graph and CSV" in repair_prompt
    assert "Retry the GDP comparison with robust JSON validation" in repair_prompt
    assert "Expecting value: line 1 column 1" in repair_prompt
    assert "print('first')" in repair_prompt
    assert events[-1]["type"] == "result"
    clear_session(session_id)


def test_printed_fetch_errors_trigger_retry_even_when_exit_code_is_zero(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "print('fetch data')", ["requests"]),
            ("Repaired plan", "print('GDP data loaded successfully')", ["requests"]),
        ],
        execution_results=[
            {
                "exit_code": 0,
                "stdout": (
                    "Error fetching data for IND: Expecting value: line 1 column 1 (char 0)\n"
                    "Error fetching data for USA: Expecting value: line 1 column 1 (char 0)\n"
                    "Failed to fetch GDP data for one or both countries."
                ),
                "files": [],
            },
            {"exit_code": 0, "stdout": "GDP data loaded successfully", "files": []},
        ],
        retry_attempts=1,
    )

    assert len(prompts) == 2
    assert len(executed_code) == 2
    assert "Error fetching data for IND" in prompts[1]
    assert "Failed to fetch GDP data" in prompts[1]
    assert any(event["type"] == "retry" for event in events)
    assert events[-1]["type"] == "result"
    assert events[-1]["content"] == "GDP data loaded successfully"


def test_execution_exception_is_reported_after_retry_limit(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "print(items[1])", []),
            ("Second plan", "print(items[2])", []),
        ],
        execution_results=[
            IndexError("first index error"),
            IndexError("second index error"),
        ],
        retry_attempts=1,
    )

    assert len(prompts) == 2
    assert executed_code == ["print(items[1])", "print(items[2])"]
    assert events[-1]["type"] == "error"
    assert "IndexError: second index error" in events[-1]["content"]


def test_syntax_validation_triggers_regeneration_before_execution(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "print(items[", []),
            ("Corrected plan", "print('valid')", []),
        ],
        execution_results=[
            {"exit_code": 0, "stdout": "valid", "files": []},
        ],
        retry_attempts=1,
    )

    assert len(prompts) == 2
    assert executed_code == ["print('valid')"]
    assert "syntax validation" in prompts[1]
    assert any(event["type"] == "retry" for event in events)
    assert events[-1]["type"] == "result"


def test_task_reports_error_after_retry_limit(monkeypatch):
    events, prompts, executed_code = run_task_with_fakes(
        monkeypatch,
        generations=[
            ("First plan", "raise RuntimeError('first')", []),
            ("Second plan", "raise RuntimeError('second')", []),
        ],
        execution_results=[
            {"exit_code": 1, "stdout": "RuntimeError: first", "files": []},
            {"exit_code": 1, "stdout": "RuntimeError: second", "files": []},
        ],
        retry_attempts=1,
    )

    assert len(prompts) == 2
    assert len(executed_code) == 2
    assert events[-1]["type"] == "error"
    assert "failed after 2 attempt(s)" in events[-1]["content"]
    assert "RuntimeError: second" in events[-1]["content"]


def test_follow_up_generation_receives_previous_session_context(monkeypatch):
    session_id = "follow-up-test-session"
    clear_session(session_id)
    events, prompts, _ = run_task_with_fakes(
        monkeypatch,
        generations=[("Plan", "print('first version')", [])],
        execution_results=[
            {"exit_code": 0, "stdout": "First result", "files": []}
        ],
        session_id=session_id,
    )

    assert events[-1]["type"] == "result"
    assert len(prompts) == 1
    messages = get_messages(session_id)
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "Make a population table" in messages[0]["content"]
    assert "print('first version')" in messages[1]["content"]
    assert "First result" in messages[1]["content"]

    follow_up_events, follow_up_prompts, _ = run_task_with_fakes(
        monkeypatch,
        generations=[("Revised plan", "print('revised version')", [])],
        execution_results=[
            {"exit_code": 0, "stdout": "Revised result", "files": []}
        ],
        session_id=session_id,
        prompt="Add a totals row",
    )
    assert follow_up_events[-1]["type"] == "result"
    assert "Make a population table" in follow_up_prompts[0]
    assert "print('first version')" in follow_up_prompts[0]
    assert "First result" in follow_up_prompts[0]
    assert "Add a totals row" in follow_up_prompts[0].split("New user request:")[-1]
    clear_session(session_id)
