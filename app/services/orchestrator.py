import asyncio
import logging
import time

from ...config import settings
from .agents import OrchestratorAgent
from .agent_sandbox import AgentSandbox
from .code_validation import ValidationAgent
from .docker_service import DockerExecutionService
from .event_bus import EventBus
from .session_memory import append_message, get_messages

logger = logging.getLogger(__name__)


async def prepare_task_prompt(
    prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    sandbox: AgentSandbox,
    task_id: str | None = None,
) -> str:
    """Run planning/research agents and prepare implementation instructions."""
    started_at = time.monotonic()
    logger.info("Planning/research started task_id=%s", task_id or "unknown")
    try:
        implementation_prompt = await OrchestratorAgent(sandbox).prepare_implementation_prompt(
            prompt,
            bus,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
        )
    except Exception:
        logger.exception("Planning/research failed task_id=%s", task_id or "unknown")
        raise
    logger.info(
        "Planning/research completed task_id=%s duration_seconds=%.2f prompt_chars=%d",
        task_id or "unknown",
        time.monotonic() - started_at,
        len(implementation_prompt),
    )
    return implementation_prompt


async def implement_task(
    prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    sandbox: AgentSandbox,
    task_id: str | None = None,
) -> tuple[str, str, list[str]]:
    """Run the implementation agent and return its response, code, requirements."""
    started_at = time.monotonic()
    logger.info("Implementation generation started task_id=%s prompt_chars=%d", task_id or "unknown", len(prompt))
    try:
        response, code, requirements = await OrchestratorAgent(sandbox).implement(
            prompt,
            bus,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
        )
    except Exception:
        logger.exception("Implementation generation failed task_id=%s", task_id or "unknown")
        raise
    logger.info(
        "Implementation generation completed task_id=%s duration_seconds=%.2f response_chars=%d code_chars=%d requirement_count=%d",
        task_id or "unknown",
        time.monotonic() - started_at,
        len(response),
        len(code),
        len(requirements),
    )
    return response, code, requirements


async def execute_task(
    code: str,
    requirements: list[str],
    sandbox: AgentSandbox,
) -> dict:
    """Run generated code through the implementation agent's sandbox tool."""
    return await OrchestratorAgent(sandbox).execute(code, requirements)


def _build_repair_prompt(
    original_prompt: str,
    failed_code: str,
    failure_output: str,
    attempt: int,
    max_attempts: int,
    previous_messages: list[dict[str, str]] | None = None,
) -> str:
    max_log_chars = 16_000
    if len(failure_output) > max_log_chars:
        failure_output = (
            "[Earlier output omitted; showing the final part of the execution log.]\n"
            + failure_output[-max_log_chars:]
        )

    context_limit = max(0, settings.SESSION_MEMORY_MESSAGES)
    context_messages = (previous_messages or [])[-context_limit:] if context_limit else []
    if context_messages:
        context_lines = []
        for message in context_messages:
            content = message.get("content", "")
            if len(content) > 4_000:
                content = content[:4_000] + "\n[Message truncated to preserve context space.]"
            context_lines.append(f"{message.get('role', 'unknown').capitalize()}: {content}")
        prior_context = "\n\n".join(context_lines)
    else:
        prior_context = "[No prior session messages were retained.]"

    return f"""The previous generated script failed. This is repair attempt {attempt} of {max_attempts}.

Previous session messages (most recent six retained messages):
{prior_context}

Original user request:
{original_prompt}

Failed generated Python code:
```python
{failed_code or "[No Python code block was generated.]"}
```

Execution error and logs:
```text
{failure_output or "[No execution output was produced.]"}
```

Diagnose the failure using the code and logs, then generate a corrected, complete replacement script that fulfills the original request. Validate response shapes and check for empty data before indexing. Keep the required output format: brief analysis/plan followed by exactly one complete Python code block with its # REQUIREMENTS line. Do not just describe the fix; provide runnable replacement code."""


def _build_session_prompt(prompt: str, previous_messages: list[dict[str, str]]) -> str:
    if not previous_messages:
        return prompt

    transcript = "\n\n".join(
        f"{message['role'].capitalize()}: {message['content']}"
        for message in previous_messages
    )
    return f"""Recent conversation context (most recent messages):
{transcript}

New user request:
{prompt}

Apply the new request to the previous task/result when relevant. Generate a complete replacement Python script that incorporates the requested changes, rather than only describing what changed."""


def _assistant_memory_content(
    code: str,
    result_text: str,
    file_names: list[str] | None = None,
) -> str:
    max_code_chars = 12_000
    max_result_chars = 4_000
    saved_code = code[:max_code_chars]
    if len(code) > max_code_chars:
        saved_code += "\n[Code truncated in session memory.]"
    saved_result = result_text[:max_result_chars]
    if len(result_text) > max_result_chars:
        saved_result += "\n[Output truncated in session memory.]"
    files = ", ".join(file_names or []) or "none"
    return (
        f"Generated code:\n```python\n{saved_code}\n```\n"
        f"Execution output:\n{saved_result}\nOutput files: {files}"
    )


async def run_task(
    prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
):
    """Main pipeline to generate a plan and code based on the user prompt."""
    task_started_at = time.monotonic()
    docker_service: DockerExecutionService | None = None
    try:
        logger.info(
            "Task pipeline started task_id=%s session_id=%s prompt_chars=%d",
            task_id or "unknown",
            session_id or "none",
            len(prompt),
        )
        # Initialize Docker before asking any agent to plan or research. This makes
        # a missing daemon/image fail immediately instead of after generation.
        docker_service = DockerExecutionService(task_id=task_id)
        logger.info("Docker initialization started task_id=%s image=%s", task_id or "unknown", docker_service.image)
        docker_started_at = time.monotonic()
        await docker_service.initialize()
        logger.info(
            "Docker initialization completed task_id=%s image=%s duration_seconds=%.2f",
            task_id or "unknown",
            docker_service.image,
            time.monotonic() - docker_started_at,
        )
        sandbox = AgentSandbox(docker_service, bus)

        previous_messages: list[dict[str, str]] = []
        if session_id:
            context_limit = max(0, settings.SESSION_MEMORY_MESSAGES)
            if context_limit:
                previous_messages = get_messages(session_id)[-context_limit:]
            append_message(session_id, "user", prompt)

        generation_prompt = _build_session_prompt(prompt, previous_messages)
        implementation_prompt = await prepare_task_prompt(
            generation_prompt,
            bus,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            sandbox=sandbox,
            task_id=task_id,
        )
        max_retries = max(0, settings.CODE_RETRY_ATTEMPTS)
        max_attempts = max_retries + 1
        current_prompt = implementation_prompt
        code = ""
        failure_output = ""
        failure_code = ""
        failure_exit_code: int | str = "unknown"
        validation_agent = ValidationAgent()

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.warning(
                    "Repair retry started task_id=%s attempt=%d max_retries=%d previous_status=%s failure_log_chars=%d",
                    task_id or "unknown",
                    attempt - 1,
                    max_retries,
                    failure_exit_code,
                    len(failure_output),
                )
                await bus.publish({
                    "type": "retry",
                    "content": (
                        f"Attempt {attempt - 1} failed. Sending the code and execution "
                        f"logs to the model for repair (retry {attempt - 1}/{max_retries})."
                    ),
                })
                current_prompt = _build_repair_prompt(
                    prompt,
                    failure_code,
                    failure_output,
                    attempt - 1,
                    max_retries,
                    previous_messages,
                )

            full_response, code, requirements = await implement_task(
                current_prompt,
                bus,
                groq_api_key=groq_api_key,
                groq_model=groq_model,
                sandbox=sandbox,
                task_id=task_id,
            )

            if not code:
                failure_code = ""
                failure_exit_code = "no code generated"
                failure_output = (
                    "The model did not return a fenced Python code block. "
                    f"Its response was:\n{full_response}"
                )
                logger.warning(
                    "Implementation produced no code task_id=%s attempt=%d response_chars=%d",
                    task_id or "unknown",
                    attempt,
                    len(full_response),
                )
            else:
                failure_code = code
                validation = validation_agent.validate(code, requirements)
                if not validation.valid:
                    failure_exit_code = "validation error"
                    failure_output = validation.error or "Generated code failed validation."
                    logger.warning(
                        "Generated code validation failed task_id=%s attempt=%d error=%s",
                        task_id or "unknown",
                        attempt,
                        failure_output,
                    )
                    if attempt < max_attempts:
                        continue
                    await bus.publish({
                        "type": "error",
                        "content": (
                            f"Generated code failed validation after {max_attempts} attempt(s): "
                            f"{failure_output}"
                        ),
                    })
                    break

                requirements = validation.requirements
                logger.info(
                    "Generated code validated task_id=%s attempt=%d discovered_imports=%d pip_packages=%d",
                    task_id or "unknown",
                    attempt,
                    len(validation.discovered_imports),
                    len(requirements),
                )
                execution_started_at = time.monotonic()
                logger.info(
                    "Code execution started task_id=%s attempt=%d code_chars=%d requirement_count=%d",
                    task_id or "unknown",
                    attempt,
                    len(code),
                    len(requirements),
                )
                await bus.publish({"type": "executing", "content": "Running generated code"})
                try:
                    result = await execute_task(code, requirements, sandbox)
                except asyncio.TimeoutError:
                    failure_exit_code = "timeout"
                    failure_output = (
                        f"Execution timed out after {settings.EXECUTION_TIMEOUT_SECONDS} seconds."
                    )
                    logger.warning(
                        "Code execution timed out task_id=%s attempt=%d timeout_seconds=%d",
                        task_id or "unknown",
                        attempt,
                        settings.EXECUTION_TIMEOUT_SECONDS,
                    )
                except Exception as execution_error:
                    failure_exit_code = type(execution_error).__name__
                    failure_output = (
                        f"Execution raised {failure_exit_code}: {execution_error}"
                    )
                    logger.exception(
                        "Code execution raised an exception; preparing repair task_id=%s attempt=%d error_type=%s",
                        task_id or "unknown",
                        attempt,
                        failure_exit_code,
                    )
                else:
                    failure_exit_code = result.get("exit_code", "unknown")
                    logger.info(
                        "Code execution completed task_id=%s attempt=%d exit_code=%s duration_seconds=%.2f output_chars=%d output_file_count=%d",
                        task_id or "unknown",
                        attempt,
                        failure_exit_code,
                        time.monotonic() - execution_started_at,
                        len(result.get("stdout") or ""),
                        len(result.get("files", [])),
                    )
                    if failure_exit_code == 0:
                        result_text = result.get("stdout") or "Task completed successfully."
                        result_files = result.get("files", [])
                        output_validation_error = validation_agent.validate_execution_output(
                            result.get("stdout") or ""
                        )
                        if output_validation_error:
                            failure_exit_code = "output validation error"
                            failure_output = (
                                f"{output_validation_error}\n"
                                f"Complete execution output:\n{result.get('stdout') or '[empty]'}"
                            )
                            logger.warning(
                                "Execution output validation failed; scheduling code repair task_id=%s attempt=%d",
                                task_id or "unknown",
                                attempt,
                            )
                        else:
                            await bus.publish({
                                "type": "result",
                                "content": result_text,
                                "files": result_files,
                            })
                            if session_id:
                                append_message(
                                    session_id,
                                    "assistant",
                                    _assistant_memory_content(
                                        code,
                                        result_text,
                                        [file.get("name", "file") for file in result_files],
                                    ),
                                )
                            logger.info(
                                "Task completed task_id=%s session_id=%s attempt=%d total_duration_seconds=%.2f",
                                task_id or "unknown",
                                session_id or "none",
                                attempt,
                                time.monotonic() - task_started_at,
                            )
                            return
                    else:
                        failure_output = result.get("stdout") or "No execution output was produced."

            if attempt < max_attempts:
                continue

            await bus.publish(
                {
                    "type": "error",
                    "content": (
                        f"Code execution failed after {max_attempts} attempt(s) "
                        f"(last status: {failure_exit_code}). Output: {failure_output}"
                    ),
                }
            )
            logger.error(
                "Task exhausted retries task_id=%s attempts=%d final_status=%s failure_log_chars=%d",
                task_id or "unknown",
                max_attempts,
                failure_exit_code,
                len(failure_output),
            )
            if session_id:
                append_message(
                    session_id,
                    "assistant",
                    _assistant_memory_content(
                        failure_code,
                        f"Execution failed after {max_attempts} attempt(s): {failure_output}",
                    ),
                )

    except ConnectionError:
        logger.exception("Task failed due to connection error task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
        if session_id:
            append_message(session_id, "assistant", "The task failed due to a connection error.")
        await bus.publish({
            "type": "error",
            "content": "Connection error occurred. Please check your network and try again."
        })

    except Exception as e:
        logger.exception("Task failed unexpectedly task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
        if session_id:
            append_message(session_id, "assistant", f"The task failed: {e}")
        await bus.publish({
            "type": "error",
            "content": f"An unexpected error occurred: {str(e)}"
        })

    finally:
        if docker_service is not None:
            try:
                await docker_service.close()
                logger.info("Docker client closed task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
            except Exception:
                logger.exception("Failed to close Docker client task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
        logger.info(
            "Task pipeline finished task_id=%s session_id=%s duration_seconds=%.2f",
            task_id or "unknown",
            session_id or "none",
            time.monotonic() - task_started_at,
        )
        await bus.finish()
